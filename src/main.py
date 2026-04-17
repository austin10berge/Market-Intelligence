"""Main pipeline orchestrator — fetch → score → synthesize → notify."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date

from .config import settings
from .fetchers.base import close_http_client
from .fetchers.fear_greed import FearGreedFetcher
from .fetchers.put_call import PutCallFetcher
from .fetchers.sector_etf import SectorEtfFetcher
from .fetchers.vix import VixFetcher
from .models import ScoredSignal, Signal
from .notify.home_assistant import send_ha_notification
from .notify.ntfy import send_ntfy
from .processing.preprocessor import compute_composite_score, determine_posture
from .processing.scorer import score_signal
from .synthesis.llm import synthesize
from .synthesis.prompts import build_synthesis_prompt
from . import db

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# All Phase 1 fetchers
FETCHERS = [
    FearGreedFetcher(),
    VixFetcher(),
    PutCallFetcher(),
    SectorEtfFetcher(),
]


async def run_pipeline() -> None:
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

        # ── Step 3: Synthesize via LLM ───────────────────────────
        logger.info("Step 3/4: Synthesizing digest...")
        composite = compute_composite_score(scored_signals)
        posture = determine_posture(composite, scored_signals)
        extreme_count = sum(1 for s in scored_signals if s.extreme)

        system_prompt, user_prompt = build_synthesis_prompt(
            date_str=today.strftime("%A, %B %d, %Y"),
            signal_summaries=[ss.signal.summary for ss in scored_signals],
            composite_score=composite,
            posture=posture.value,
            extreme_count=extreme_count,
        )

        digest_text = await synthesize(system_prompt, user_prompt)

        # Build the full notification text
        header = f"📊 Evening Market Digest — {today.strftime('%b %d, %Y')}\n\n"
        full_text = header + digest_text

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
        await _notify(
            title=f"📊 Market Digest — {posture.value}",
            message=full_text,
            priority=4 if extreme_count > 0 else 3,
        )

        logger.info("✅ Pipeline complete!")
        print("\n" + "=" * 60)
        print(full_text)
        print("=" * 60)

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
    """Entry point for the pipeline."""
    logger.info(f"Schedule time configured: {settings.schedule_time}")
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
