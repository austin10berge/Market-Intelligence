"""Main pipeline orchestrator — fetch → score → synthesize → notify."""

from __future__ import annotations

import asyncio
import logging
import sys

from datetime import datetime

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
from .fetchers.news import NewsFetcher
from .fetchers.thematic_etf import ThematicEtfFetcher
from .fetchers.treasury_yields import TreasuryYieldsFetcher
from .fetchers.cme_fedwatch import CmeFedWatchFetcher
from .fetchers.policy_news import PolicyNewsFetcher
from .fetchers.earnings_calendar import EarningsCalendarFetcher
from .models import ScoredSignal, Signal
from .notify.discord import send_discord_digest
from .notify.home_assistant import send_ha_notification
from .notify.ntfy import send_ntfy
from .processing.preprocessor import compute_composite_score, determine_posture
from .processing.scorer import score_signal, check_convergence
from .screener.options import screen_csp_candidates
from .screener.stocks import screen_stocks
from .synthesis.llm import synthesize
from .synthesis.prompts import build_synthesis_prompt
from .cache import (
    ET,
    KEY_SCREENER_CSP,
    KEY_SCREENER_STOCKS,
    cache_get,
    cache_set,
    invalidate_market_posture,
    is_trading_day,
    screener_ttl,
)
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
    NewsFetcher(),
    ThematicEtfFetcher(),
    TreasuryYieldsFetcher(),
    CmeFedWatchFetcher(),
    PolicyNewsFetcher(),
    EarningsCalendarFetcher(),
]


async def run_pipeline(output_mode: str = "notify") -> dict | None:
    """Execute the full evening market sentiment pipeline."""
    today = datetime.now(ET).date()
    logger.info(f"{'=' * 60}")
    logger.info(f"📊 Evening Market Sentiment Pipeline — {today.isoformat()}")
    logger.info(f"{'=' * 60}")

    try:
        # ── Step 1: Fetch all signals in parallel ────────────────
        logger.info("Step 1/5: Fetching market data...")
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
        logger.info("Step 2/5: Scoring signals...")

        # Build cross-signal context for regime-aware scorers.
        # VIX is fetched independently of the signal being scored, so we
        # extract it here and pass it as shared context to every scorer.
        vix_signal = next((s for s in signals if s.source.value == "vix"), None)
        scoring_context = {"vix": vix_signal.value if vix_signal else None}

        scored_signals = [score_signal(s, scoring_context) for s in signals]

        for ss in scored_signals:
            logger.info(f"  {ss.signal.source.value}: {ss.score:+.3f} ({ss.direction.value})")
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

        # Watchlist stock data for the LLM — check Redis first so /scan reuses the
        # dashboard's already-fetched data instead of making redundant yfinance calls.
        logger.info("Loading watchlist stock data...")
        watchlist_stocks: list[dict] = []
        try:
            stocks_envelope = await cache_get(KEY_SCREENER_STOCKS)
            if stocks_envelope and stocks_envelope.get("data"):
                watchlist_stocks = stocks_envelope["data"]
                logger.info(
                    "Watchlist stocks loaded from cache (%d tickers)", len(watchlist_stocks)
                )
            else:
                watchlist_stocks = await asyncio.to_thread(screen_stocks, None, True)
                if watchlist_stocks:
                    await cache_set(KEY_SCREENER_STOCKS, watchlist_stocks, screener_ttl())
        except Exception as exc:
            logger.warning("Watchlist stock data fetch failed: %s", exc)

        # CSP candidates for the LLM — same cache-first approach.
        logger.info("Loading CSP candidates...")
        csp_candidates: list[dict] = []
        try:
            csp_envelope = await cache_get(KEY_SCREENER_CSP)
            if csp_envelope and csp_envelope.get("data"):
                csp_candidates = csp_envelope["data"]
                logger.info("CSP candidates loaded from cache (%d results)", len(csp_candidates))
            else:
                csp_candidates = await asyncio.to_thread(screen_csp_candidates)
                if csp_candidates:
                    await cache_set(KEY_SCREENER_CSP, csp_candidates, screener_ttl())
        except Exception as exc:
            logger.warning("CSP candidate fetch for LLM failed: %s", exc)

        # ── Step 3: Synthesize via LLM ───────────────────────────
        logger.info("Step 3/5: Synthesizing digest...")
        composite = compute_composite_score(scored_signals)
        posture = determine_posture(composite, scored_signals)
        extreme_count = sum(1 for s in scored_signals if s.extreme)

        # Check for insider + congressional convergence on the same tickers
        convergence_alerts = check_convergence(scored_signals)

        # Extract sector and news metadata for enriched LLM prompt
        sector_signal = next(
            (ss for ss in scored_signals if "sector" in ss.signal.source.value.lower()), None
        )
        sector_metadata = sector_signal.signal.metadata if sector_signal else None

        news_signal = next((ss for ss in scored_signals if ss.signal.source.value == "news"), None)
        news_headlines = news_signal.signal.metadata.get("headlines", []) if news_signal else []

        thematic_signal = next(
            (ss for ss in scored_signals if ss.signal.source.value == "thematic_etf"), None
        )
        thematic_metadata = thematic_signal.signal.metadata if thematic_signal else None

        treasury_signal = next(
            (ss for ss in scored_signals if ss.signal.source.value == "treasury_yields"), None
        )
        treasury_metadata = treasury_signal.signal.metadata if treasury_signal else None

        fedwatch_signal = next(
            (ss for ss in scored_signals if ss.signal.source.value == "cme_fedwatch"), None
        )
        fedwatch_metadata = fedwatch_signal.signal.metadata if fedwatch_signal else None

        policy_signal = next(
            (ss for ss in scored_signals if ss.signal.source.value == "policy_news"), None
        )
        policy_headlines = (
            policy_signal.signal.metadata.get("headlines", []) if policy_signal else []
        )

        earnings_signal = next(
            (ss for ss in scored_signals if ss.signal.source.value == "earnings_calendar"), None
        )
        earnings_upcoming = (
            earnings_signal.signal.metadata.get("upcoming", []) if earnings_signal else []
        )

        system_prompt, user_prompt = build_synthesis_prompt(
            date_str=today.strftime("%A, %B %d, %Y"),
            signal_summaries=[ss.signal.summary for ss in scored_signals],
            composite_score=composite,
            posture=posture.value,
            extreme_count=extreme_count,
            convergence_alerts=convergence_alerts,
            watchlist_stocks=watchlist_stocks,
            csp_candidates=csp_candidates,
            sector_metadata=sector_metadata,
            thematic_metadata=thematic_metadata,
            treasury_metadata=treasury_metadata,
            fedwatch_metadata=fedwatch_metadata,
            policy_headlines=policy_headlines,
            earnings_upcoming=earnings_upcoming,
            news_headlines=news_headlines,
        )

        digest_text = await synthesize(system_prompt, user_prompt)

        # Build the full notification text
        header = f"📊 Evening Market Digest — {today.strftime('%b %d, %Y')}\n\n"

        # Format the raw signals nicely
        raw_signals_block = "\n".join(f"• {ss.signal.summary}" for ss in scored_signals)
        raw_signals_block += f"\n\nComposite Score: {composite:+.3f} (Range: -1.0 to +1.0)"

        if (
            digest_text
            and "Unable to generate" not in digest_text
            and "LLM unavailable" not in digest_text
        ):
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

        # Invalidate the market-posture Redis cache so the dashboard picks up the new digest
        await invalidate_market_posture()

        logger.info(f"Composite score: {composite:+.3f} | Posture: {posture.value}")

        # ── Step 4: Notify ───────────────────────────────────────
        logger.info("Step 4/5: Sending notification...")
        if output_mode == "notify":
            # Send signals digest first (keeps ntfy body under attachment threshold)
            await _notify(
                title=f"📊 Market Digest — {posture.value}",
                message=header + raw_signals_block,
                priority=4 if extreme_count > 0 else 3,
                posture=posture.value,
                composite_score=composite,
            )
            # Send AI analysis as a separate notification
            if (
                digest_text
                and "Unable to generate" not in digest_text
                and "LLM unavailable" not in digest_text
            ):
                await _notify(
                    title=f"🤖 AI Analysis — {today.strftime('%b %d, %Y')}",
                    message=digest_text,
                    priority=3,
                    posture=posture.value,
                    composite_score=composite,
                    tags="robot",
                )

        # ── Step 5: Algo-detective options snapshot ──────────────
        logger.info("Step 5/5: Collecting algo-detective options snapshot...")
        try:
            from .algo_detective.options_chain import fetch_snapshot_pcr
            from .algo_detective.store import get_all_features as _get_detective_features

            _features = await asyncio.to_thread(_get_detective_features)
            _prime = sorted({f["ticker"] for f in _features if f["is_prime"] == 1})
            if _prime:
                stored = await asyncio.to_thread(fetch_snapshot_pcr, _prime, today.isoformat())
                logger.info("Options snapshot: %d rows stored for %d prime tickers", stored, len(_prime))
        except Exception as _exc:
            logger.warning("Algo-detective options snapshot failed (non-fatal): %s", _exc)

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


async def _notify(
    title: str,
    message: str,
    priority: int = 3,
    posture: str = "",
    composite_score: float = 0.0,
    tags: str = "chart",
) -> None:
    """Send via NTFY (primary) and Discord (parallel); fall back to Home Assistant if NTFY fails."""
    ntfy_task = send_ntfy(title, message, priority=priority, tags=tags)
    discord_task = send_discord_digest(title, message, posture, composite_score)
    results = await asyncio.gather(ntfy_task, discord_task, return_exceptions=True)
    ntfy_sent = results[0] if not isinstance(results[0], Exception) else False
    discord_sent = results[1] if not isinstance(results[1], Exception) else False

    if not ntfy_sent:
        logger.info("NTFY failed — falling back to Home Assistant notification...")
        ha_sent = await send_ha_notification(title, message)
        if not ha_sent:
            logger.error("All notification methods failed!")

    if not discord_sent:
        logger.warning("Discord digest delivery failed (non-fatal)")


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

    today = datetime.now(ET).date()
    if args.mode == "scheduled" and not is_trading_day(today):
        logger.info(f"{today.isoformat()} is not a trading day — skipping scheduled run")
        return

    result = asyncio.run(run_pipeline(output_mode=args.output))

    if args.output == "json" and result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
