"""Main pipeline orchestrator — fetch → score → synthesize → notify."""

from __future__ import annotations

import asyncio
import logging
import sys

from datetime import date

from .config import settings
from .fetchers.base import close_http_client
from .fetchers.fear_greed import FearGreedFetcher
from .fetchers.gex import GexFetcher
from .fetchers.credit_spreads import CreditSpreadsFetcher
from .fetchers.liquidity import LiquidityFetcher
from .fetchers.put_call import PutCallFetcher
from .fetchers.sector_etf import SectorEtfFetcher
from .fetchers.vix import VixFetcher
from .fetchers.insider_trading import InsiderTradingFetcher
from .fetchers.congressional_trades import CongressionalTradesFetcher
from .fetchers.unusual_volume import UnusualVolumeFetcher
from .models import ScoredSignal, Signal
from .notify.home_assistant import send_ha_notification
from .notify.ntfy import send_ntfy
from .processing.preprocessor import compute_composite_score, determine_posture
from .processing.scorer import score_signal, check_convergence
from .screener.stocks import screen_stocks
from .synthesis.llm import synthesize
from .synthesis.prompts import build_synthesis_prompt
from . import db

import argparse
import json

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# All Phase 1 and 2 fetchers
FETCHERS = [
    FearGreedFetcher(),
    VixFetcher(),
    PutCallFetcher(),
    SectorEtfFetcher(),
    GexFetcher(),
    CreditSpreadsFetcher(),
    LiquidityFetcher(),
    InsiderTradingFetcher(),
    CongressionalTradesFetcher(),
    UnusualVolumeFetcher(),
]


async def run_pipeline(output_mode: str = "notify") -> dict | None:
    """Execute the full evening market sentiment pipeline."""
    today = date.today()
    logger.info(f"{'='*60}")
    logger.info(f"📊 Evening Market Sentiment Pipeline — {today.isoformat()}")
    logger.info(f"{'='*60}")

    try:
        # ── Step 1: Fetch all signals in parallel ────────────────
        logger.info("Step 1/4: Fetching market data...")
        signals = await _fetch_all()

        if not signals:
            logger.error("No signals fetched — aborting pipeline")
            await _notify(
                "⚠️ Market Intelligence — Pipeline Failed",
                "No data sources returned results. Check logs.",
                priority=4,
            )
            return

        logger.info(f"Fetched {len(signals)}/{len(FETCHERS)} signals successfully")

        # ── Step 2: Score signals ────────────────────────────────
        logger.info("Step 2/4: Scoring signals...")
        scored_signals = [score_signal(s) for s in signals]

        for ss in scored_signals:
            logger.info(f"  {ss.signal.source.value}: {ss.score:+d} ({ss.direction.value})")
            # Store each signal to DB
            db.store_signal(
                signal_date=today,
                source=ss.signal.source.value,
                raw_value=ss.signal.value,
                scored_value=ss.score,
                direction=ss.direction.value,
                extreme=ss.extreme,
                metadata=ss.signal.metadata,
                summary=ss.signal.summary,
            )

        # Persist one ATM IV snapshot per stock each daily run so IV Rank can build over time.
        logger.info("Capturing stock IV history snapshots...")
        try:
            screen_stocks(persist_history=True)
        except Exception as exc:
            logger.warning("Stock IV snapshot capture failed: %s", exc)

        # ── Step 3: Synthesize via LLM ───────────────────────────
        logger.info("Step 3/4: Synthesizing digest...")
        composite = compute_composite_score(scored_signals)
        posture = determine_posture(composite, scored_signals)
        extreme_count = sum(1 for s in scored_signals if s.extreme)

        # Check for insider + congressional convergence on the same tickers
        convergence_alerts = check_convergence(scored_signals)

        system_prompt, user_prompt = build_synthesis_prompt(
            date_str=today.strftime("%A, %B %d, %Y"),
            signal_summaries=[ss.signal.summary for ss in scored_signals],
            composite_score=composite,
            posture=posture.value,
            extreme_count=extreme_count,
            convergence_alerts=convergence_alerts,
        )

        digest_text = await synthesize(system_prompt, user_prompt)

        # Build the full notification text
        header = f"📊 Evening Market Digest — {today.strftime('%b %d, %Y')}\n\n"
        
        # Format the raw signals nicely
        raw_signals_block = "\n".join(f"• {ss.signal.summary}" for ss in scored_signals)
        raw_signals_block += f"\n\nComposite Score: {composite:+.3f} (Range: -1.0 to +1.0)"
        
        if digest_text and "Unable to generate" not in digest_text and "LLM unavailable" not in digest_text:
            llm_section = f"\n\n🤖 AI Analysis:\n{digest_text}"
        else:
            llm_section = f"\n\n⚠️ {digest_text}"

        full_text = header + raw_signals_block + llm_section

        # Store digest to DB
        db.store_digest(
            digest_date=today,
            composite_score=composite,
            posture=posture.value,
            llm_summary=digest_text,
            full_text=full_text,
        )

        logger.info(f"Composite score: {composite:+.3f} | Posture: {posture.value}")

        # ── Step 4: Notify ───────────────────────────────────────
        logger.info("Step 4/4: Sending notification...")
        if output_mode == "notify":
            await _notify(
                title=f"📊 Market Digest — {posture.value}",
                message=full_text,
                priority=4 if extreme_count > 0 else 3,
            )

        logger.info("✅ Pipeline complete!")

        # Return structured data (used by Discord / on-demand path)
        return {
            "status": "complete",
            "date": today.isoformat(),
            "posture": posture.value,
            "composite_score": round(composite, 3),
            "extreme_count": extreme_count,
            "signals": [
                {
                    "source": ss.signal.source.value,
                    "score": ss.score,
                    "direction": ss.direction.value,
                    "extreme": ss.extreme,
                    "summary": ss.signal.summary,
                    "reasoning": ss.reasoning,
                }
                for ss in scored_signals
            ],
            "llm_summary": digest_text,
        }

    finally:
        await close_http_client()


async def _fetch_all() -> list[Signal]:
    """Fetch all signals in parallel, returning only successful results."""
    tasks = [fetcher.safe_fetch() for fetcher in FETCHERS]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


async def _notify(title: str, message: str, priority: int = 3) -> None:
    """Send notification via NTFY (primary), fall back to Home Assistant."""
    # Try NTFY first
    sent = await send_ntfy(title, message, priority=priority, tags="chart")
    if sent:
        return

    # Fallback to Home Assistant
    logger.info("Falling back to Home Assistant notification...")
    sent = await send_ha_notification(title, message)
    if not sent:
        logger.error("All notification methods failed!")


def main() -> None:
    """Entry point — supports both scheduled and on-demand (Discord) runs."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["scheduled", "on-demand"],
        default="scheduled",
        help="Run mode: 'scheduled' (normal) or 'on-demand' (triggered via Discord/API)",
    )
    parser.add_argument(
        "--output",
        choices=["notify", "json"],
        default="notify",
        help="Output mode: 'notify' sends NTFY, 'json' prints structured result to stdout",
    )
    args = parser.parse_args()

    logger.info(f"Schedule time configured: {settings.schedule_time}")
    logger.info(f"Run mode: {args.mode} | Output: {args.output}")

    result = asyncio.run(run_pipeline(output_mode=args.output))

    if args.output == "json" and result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
