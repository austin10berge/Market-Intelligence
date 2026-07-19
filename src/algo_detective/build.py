from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..market_data.store import get_fundamentals_for_tickers
from .features import compute_features
from .ingest import get_prime_tickers_for_date, get_unique_dates, load_prime_tickers
from .macro_context import compute_macro_for_date
from .store import (
    _get_connection,
    backfill_fundamentals,
    ensure_tables,
    get_computed_pairs,
    get_feature_counts,
    upsert_feature_rows_bulk,
    upsert_macro_row,
)
from .universe import get_control_tickers, load_ohlcv_batch_for_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_CSV_PATH = Path(__file__).parent.parent.parent / "data" / "detective" / "prime_tickers.csv"


def compute_and_store_for_date(
    date: str,
    ticker_flags: list[tuple[str, int]],
    computed_pairs: set[tuple[str, str]],
    ohlcv_fallback_fn: Callable[[str], pd.DataFrame | None] | None = None,
) -> list[dict]:
    """Compute features and upsert detective_features rows for the given
    (ticker, is_prime) pairs on one date, skipping any pair already in
    computed_pairs. If a ticker has no OHLCV in the tracked universe batch
    and ohlcv_fallback_fn is given, it's called as a last resort (used by
    label_sync.py for prime tickers outside the tracked universe).

    Returns the rows that were computed and upserted — callers needing
    per-row post-processing (e.g. run_build()'s CSV cross-validation) read
    them from the return value rather than recomputing.
    """
    to_compute = [(t, f) for t, f in ticker_flags if (date, t) not in computed_pairs]
    if not to_compute:
        return []

    all_syms = [t for t, _ in to_compute]
    fund_rows = get_fundamentals_for_tickers(all_syms)
    sector_map = {r["symbol"]: r.get("sector") for r in fund_rows}
    fund_map = {
        r["symbol"]: {
            "market_cap_b": r.get("market_cap_b"),
            "beta": r.get("beta"),
            "forward_pe": r.get("forward_pe"),
            "peg_ratio": r.get("peg_ratio"),
            "revenue_growth": r.get("revenue_growth"),
            "earnings_growth": r.get("earnings_growth"),
            "debt_to_equity": r.get("debt_to_equity"),
            "dividend_yield": r.get("dividend_yield"),
            "fcf": r.get("fcf"),
        }
        for r in fund_rows
    }

    macro = compute_macro_for_date(date)
    if macro:
        upsert_macro_row(macro)

    ohlcv_map = load_ohlcv_batch_for_date(all_syms, date)
    now = datetime.now(timezone.utc).isoformat()
    rows_to_insert: list[dict] = []

    for ticker, is_prime in to_compute:
        df = ohlcv_map.get(ticker)
        if (df is None or df.empty) and ohlcv_fallback_fn is not None:
            df = ohlcv_fallback_fn(ticker)
        if df is None or df.empty:
            logger.warning("No OHLCV for %s on %s", ticker, date)
            continue

        feats = compute_features(ticker, date, df, sector=sector_map.get(ticker))
        if feats is None:
            logger.debug("Insufficient history for %s on %s", ticker, date)
            continue

        rows_to_insert.append(
            {
                "date": date,
                "ticker": ticker,
                "is_prime": is_prime,
                **feats,
                **fund_map.get(ticker, {}),
                "computed_at": now,
            }
        )

    upsert_feature_rows_bulk(rows_to_insert)
    return rows_to_insert


def run_build(csv_path: Path = _CSV_PATH) -> None:
    ensure_tables()

    records = load_prime_tickers(csv_path)
    logger.info("Loaded %d prime records", len(records))

    computed_pairs = get_computed_pairs()
    dates = get_unique_dates(records)
    logger.info("Processing %d unique dates", len(dates))

    for date in dates:
        prime_tickers = get_prime_tickers_for_date(records, date)
        prime_set = set(prime_tickers)
        control_tickers = get_control_tickers(date, exclude=prime_set)

        ticker_flags = [(t, 1) for t in prime_tickers] + [(t, 0) for t in control_tickers]
        rows = compute_and_store_for_date(date, ticker_flags, computed_pairs)

        for row in rows:
            if row["is_prime"]:
                _cross_validate(row["ticker"], date, row, records)

        logger.info(
            "Date %s: computed %d of %d requested tickers",
            date,
            len(rows),
            len(ticker_flags),
        )

    counts = get_feature_counts()
    logger.info(
        "Build complete — total: %d  prime: %d  control: %d  macro_dates: %d",
        counts["total"],
        counts["prime"],
        counts["control"],
        counts["macro_dates"],
    )


def _cross_validate(ticker: str, date: str, feats: dict, records: list) -> None:
    csv_rec = next((r for r in records if r.date == date and r.ticker == ticker), None)
    if not csv_rec:
        return
    for field, computed in [("rsi", feats.get("rsi")), ("adx", feats.get("adx"))]:
        csv_val = getattr(csv_rec, field)
        if computed is not None and abs(computed - csv_val) > 5:
            logger.warning(
                "Cross-validation: %s on %s — %s computed=%.1f csv=%.1f (diff=%.1f)",
                ticker,
                date,
                field,
                computed,
                csv_val,
                abs(computed - csv_val),
            )


def run_inspect(date: str) -> None:
    ensure_tables()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker, is_prime, rsi, adx, price_above_ema50, "
            "ema20_above_ema50, rv20, bb_pct_b, sector "
            "FROM detective_features WHERE date = ? "
            "ORDER BY is_prime DESC, rsi DESC",
            (date,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"No data for {date} — run build first.")
        return

    prime_count = sum(1 for r in rows if r["is_prime"])
    print(f"\n=== {date}: {prime_count} prime / {len(rows) - prime_count} control ===\n")
    fmt = "{:<10} {:<6} {:<7} {:<7} {:<8} {:<10} {:<8} {:<8} {}"
    print(
        fmt.format("TICKER", "PRIME", "RSI", "ADX", "EMA50+", "EMA20>50", "RV20", "BB%B", "SECTOR")
    )
    print("-" * 80)
    for r in rows[:60]:
        print(
            fmt.format(
                r["ticker"],
                "YES" if r["is_prime"] else "-",
                f"{r['rsi']:.1f}" if r["rsi"] else "N/A",
                f"{r['adx']:.1f}" if r["adx"] else "N/A",
                str(r["price_above_ema50"]) if r["price_above_ema50"] is not None else "N/A",
                str(r["ema20_above_ema50"]) if r["ema20_above_ema50"] is not None else "N/A",
                f"{r['rv20']:.3f}" if r["rv20"] else "N/A",
                f"{r['bb_pct_b']:.2f}" if r["bb_pct_b"] else "N/A",
                r["sector"] or "",
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the algo detective feature matrix")
    parser.add_argument("--inspect", metavar="DATE", help="Print feature summary for YYYY-MM-DD")
    parser.add_argument("--csv", default=str(_CSV_PATH), help="Path to prime_tickers.csv")
    parser.add_argument(
        "--backfill-fundamentals",
        action="store_true",
        help="Migrate schema and backfill fundamental columns for all existing rows",
    )
    args = parser.parse_args()

    if args.inspect:
        run_inspect(args.inspect)
    elif args.backfill_fundamentals:
        ensure_tables()  # adds new columns via ALTER TABLE
        count = backfill_fundamentals()
        logger.info("Backfilled fundamentals for %d rows", count)
    else:
        run_build(Path(args.csv))
