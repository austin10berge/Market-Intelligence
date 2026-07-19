"""Step 7 of the nightly algo_detective pipeline: computes technical/
fundamental features for the tracked control universe on a given date,
storing them as is_prime=0. Reused by backfill_mlabs.py's Phase 2 to
backfill control-universe features for every historical date the mLabs
recap backfill (Task 5) surfaces a prime label for.

Must run after label_sync.py's sync_new_labels() in the same pipeline
pass — excludes today's already-labeled prime tickers so a freshly
discovered is_prime=1 row is never downgraded back to a control row.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

import logging

from .build import compute_and_store_for_date
from .store import _get_connection, get_computed_pairs
from .universe import get_control_tickers

logger = logging.getLogger(__name__)


def _get_todays_primes(date: str) -> set[str]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker FROM detective_features WHERE date = ? AND is_prime = 1",
            (date,),
        ).fetchall()
        return {r["ticker"] for r in rows}
    finally:
        conn.close()


def sync_control_universe(date: str) -> int:
    """Compute + upsert is_prime=0 rows for the tracked universe on date,
    excluding tickers already labeled is_prime=1 that date. Returns the
    number of rows written."""
    todays_primes = _get_todays_primes(date)
    control_tickers = get_control_tickers(date, exclude=todays_primes)
    computed_pairs = get_computed_pairs()

    ticker_flags = [(t, 0) for t in control_tickers]
    rows = compute_and_store_for_date(date, ticker_flags, computed_pairs)
    logger.info(
        "Control sync %s: %d rows written (%d requested)",
        date,
        len(rows),
        len(ticker_flags),
    )
    return len(rows)
