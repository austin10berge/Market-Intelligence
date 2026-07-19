"""One-time (but safely re-runnable) backfill of mLabs recap trade labels
and control-universe features across the full scrapeable history.

Phase 1 scrapes every results_boring_puts_* post found on the index
(regardless of the checkpoint table — unlike the nightly sync_new_labels,
this re-processes everything, which is safe since upserts are
idempotent) into is_prime=1 rows, collecting the distinct dates touched.
Phase 2 runs a historical control-universe sync for each of those dates,
so every prime date also gets a full control-universe comparison set.

Run: docker compose --profile pipeline run --rm pipeline python3 -m \
    src.algo_detective.backfill_mlabs
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from .build import compute_and_store_for_date
from .control_sync import sync_control_universe
from .label_sync import _ohlcv_fallback
from .mlabs_scraper import fetch_post_index, fetch_recap_trades
from .store import get_computed_pairs, record_scraped_post

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def run_backfill() -> dict:
    """Backfill every scrapeable mLabs recap post (Phase 1) and the
    control universe for every date it touches (Phase 2). Returns
    {"prime_rows_written": int, "dates_backfilled": int, "control_rows_written": int}."""
    slugs = fetch_post_index()
    logger.info("Phase 1: backfilling %d recap posts", len(slugs))

    prime_rows_written = 0
    dates_touched: set[str] = set()

    for slug in slugs:
        try:
            trades = fetch_recap_trades(slug)
        except Exception:
            logger.warning("Failed to parse %s during backfill, skipping", slug, exc_info=True)
            continue

        by_date: dict[str, list[str]] = defaultdict(list)
        for trade in trades:
            by_date[trade["open_date"]].append(trade["ticker"])

        computed_pairs = get_computed_pairs()
        for date, tickers in by_date.items():
            ticker_flags = [(t, 1) for t in sorted(set(tickers))]
            rows = compute_and_store_for_date(
                date,
                ticker_flags,
                computed_pairs,
                ohlcv_fallback_fn=_ohlcv_fallback,
            )
            prime_rows_written += len(rows)
            dates_touched.add(date)

        record_scraped_post(slug, trades_found=len(trades))

    logger.info(
        "Phase 1 complete: %d prime rows written across %d distinct dates",
        prime_rows_written,
        len(dates_touched),
    )

    logger.info("Phase 2: backfilling control universe for %d dates", len(dates_touched))
    control_rows_written = 0
    for date in sorted(dates_touched):
        control_rows_written += sync_control_universe(date)

    logger.info("Phase 2 complete: %d control rows written", control_rows_written)

    return {
        "prime_rows_written": prime_rows_written,
        "dates_backfilled": len(dates_touched),
        "control_rows_written": control_rows_written,
    }


if __name__ == "__main__":
    summary = run_backfill()
    print(f"\nBackfill complete: {summary}")
