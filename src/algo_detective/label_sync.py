"""Step 6 of the nightly algo_detective pipeline: discovers new mLabs
recap posts, parses them into (ticker, open_date) pairs (real trades =
authoritative is_prime=1 ground truth, replacing manual Reddit
transcription), computes features, and upserts.

Must run before control_sync.py's sync_control_universe() in the same
pipeline pass, so a freshly discovered prime label is never overwritten
back to a control row. See
docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ..backtester.data_provider import get_historical_data
from .build import compute_and_store_for_date
from .mlabs_scraper import fetch_post_index, fetch_recap_trades
from .store import get_computed_prime_pairs, get_scraped_slugs, record_scraped_post

logger = logging.getLogger(__name__)


def _ohlcv_fallback(ticker: str):
    """Last-resort OHLCV source for a prime ticker outside the tracked
    universe — reuses the backtester's on-demand fetch+cache."""
    df = get_historical_data(symbol=ticker)
    return df if not df.empty else None


def sync_new_labels() -> int:
    """Scrape any mLabs recap post not yet in the checkpoint table, and
    upsert its CSP trades as is_prime=1 rows. Returns count of rows
    written across all newly-processed posts."""
    known = get_scraped_slugs()
    new_slugs = [s for s in fetch_post_index() if s not in known]

    total_written = 0
    for slug in new_slugs:
        try:
            trades = fetch_recap_trades(slug)
        except Exception:
            logger.warning(
                "Failed to parse recap post %s, will retry next run", slug, exc_info=True
            )
            continue

        by_date: dict[str, list[str]] = defaultdict(list)
        for trade in trades:
            by_date[trade["open_date"]].append(trade["ticker"])

        computed_pairs = get_computed_prime_pairs()
        for date, tickers in by_date.items():
            # de-dupe tickers within the same date (e.g. two lots of the same name)
            ticker_flags = [(t, 1) for t in sorted(set(tickers))]
            rows = compute_and_store_for_date(
                date,
                ticker_flags,
                computed_pairs,
                ohlcv_fallback_fn=_ohlcv_fallback,
            )
            total_written += len(rows)

        record_scraped_post(slug, trades_found=len(trades))
        logger.info("Processed %s: %d CSP trades found", slug, len(trades))

    return total_written
