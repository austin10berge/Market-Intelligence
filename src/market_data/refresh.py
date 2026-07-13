"""Daily refresh job — downloads OHLCV + fundamentals for the S&P 500, NASDAQ 100, NASDAQ large-cap (>=$2B), and NYSE large-cap (>=$2B) universe.

Usage (from Docker):
    python -m src.market_data.refresh          # incremental (last 5 trading days)
    python -m src.market_data.refresh --full   # full backfill (2 years)

The key speed trick: yf.download() accepts a list of tickers and fetches them
in a single bulk HTTP request, vs. 600 individual yf.Ticker().history() calls.
"""

from __future__ import annotations

import logging
import math
import sys
import time
from collections import defaultdict

import pandas as pd
import yfinance as yf

from ..screener.csp_scanner import (
    fetch_nasdaq100_tickers,
    fetch_nasdaq_large_cap_tickers,
    fetch_nyse_large_cap_tickers,
    fetch_sp500_tickers,
)
from .store import (
    bulk_upsert_fundamentals,
    bulk_upsert_ohlcv_multi,
    ensure_tables,
    get_stale_fundamental_tickers,
    get_store_status,
    get_universe_tickers,
    prune_stale_fundamentals,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_FUNDAMENTAL_BATCH_SIZE = 50
_FUNDAMENTAL_BATCH_SLEEP_S = 1.0
_FUNDAMENTAL_TICKER_SLEEP_S = 0.3  # between individual .info calls to avoid rate limits

# yf.download batch size — avoids URL-length limits on huge ticker lists
_OHLCV_DOWNLOAD_BATCH_SIZE = 100

# Fundamentals older than this after a refresh run are flagged as permanently
# stuck (7 days — several missed daily runs).
_STALE_FUNDAMENTAL_WARN_HOURS = 168


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_fundamentals_batch(symbols: list[str]) -> list[dict]:
    """Fetch fundamentals via yf.Ticker().info for a batch of symbols."""
    rows: list[dict] = []
    for symbol in symbols:
        try:
            info = yf.Ticker(symbol).info
            if info.get("quoteType", "").upper() != "EQUITY":
                continue

            market_cap_b = (_to_float(info.get("marketCap")) or 0.0) / 1e9
            price = _to_float(info.get("currentPrice") or info.get("regularMarketPrice")) or 0.0
            beta = _to_float(info.get("beta"))
            iv_raw = _to_float(info.get("impliedVolatility"))
            iv_pct = round(iv_raw * 100, 2) if iv_raw is not None else None

            # Balance sheet / profitability fields
            fcf_raw = _to_float(info.get("freeCashflow"))
            fcf = round(fcf_raw / 1e9, 4) if fcf_raw is not None else None  # stored in billions

            debt_to_equity = _to_float(info.get("debtToEquity"))
            revenue_growth = _to_float(info.get("revenueGrowth"))
            earnings_growth = _to_float(info.get("earningsGrowth"))
            dividend_yield = _to_float(info.get("dividendYield"))
            forward_pe = _to_float(info.get("forwardPE"))
            peg_ratio = _to_float(info.get("trailingPegRatio"))
            sector = info.get("sector") or None

            rows.append({
                "symbol": symbol,
                "market_cap_b": round(market_cap_b, 2),
                "price": round(price, 2),
                "beta": round(beta, 2) if beta is not None else None,
                "iv_pct": iv_pct,
                "fcf": fcf,
                "debt_to_equity": round(debt_to_equity, 4) if debt_to_equity is not None else None,
                "revenue_growth": round(revenue_growth, 4) if revenue_growth is not None else None,
                "earnings_growth": round(earnings_growth, 4) if earnings_growth is not None else None,
                "dividend_yield": round(dividend_yield, 4) if dividend_yield is not None else None,
                "forward_pe": round(forward_pe, 2) if forward_pe is not None else None,
                "peg_ratio": round(peg_ratio, 2) if peg_ratio is not None else None,
                "sector": sector,
            })
        except Exception as exc:
            logger.warning("Fundamental fetch failed for %s: %s", symbol, exc)
        finally:
            time.sleep(_FUNDAMENTAL_TICKER_SLEEP_S)
    return rows


def _download_ohlcv_batch(
    symbols: list[str],
    period: str = "5d",
) -> dict[str, pd.DataFrame]:
    """Download OHLCV data for a batch of symbols using yf.download().

    Returns {symbol: DataFrame} mapping.
    """
    result: dict[str, pd.DataFrame] = {}

    if not symbols:
        return result

    try:
        # yf.download() with multiple tickers returns a MultiIndex DataFrame
        # with (Price, Ticker) column levels
        raw = yf.download(
            tickers=symbols,
            period=period,
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        if raw.empty:
            logger.warning("yf.download returned empty for %d symbols", len(symbols))
            return result

        if isinstance(raw.columns, pd.MultiIndex):
            # yfinance 1.x always returns MultiIndex (Price, Ticker) — even for
            # a single ticker. Use .xs() to flatten to per-ticker DataFrames.
            for sym in symbols:
                try:
                    ticker_df = raw.xs(sym, level="Ticker", axis=1)
                    if not ticker_df.empty:
                        ticker_df = ticker_df.dropna(how="all")
                        if not ticker_df.empty:
                            result[sym] = ticker_df
                except (KeyError, ValueError):
                    continue
        else:
            # yfinance 0.2.x with a single ticker returns flat columns
            sym = symbols[0]
            if not raw.empty:
                result[sym] = raw.dropna(how="all")

    except Exception as exc:
        logger.error("yf.download failed for batch of %d: %s", len(symbols), exc)

    return result


# ── Main refresh entry point ─────────────────────────────────────────────────

def refresh_universe(full: bool = False) -> dict:
    """Download and store OHLCV + fundamentals for the S&P 500, NASDAQ 100, and NASDAQ large-cap (>=$2B) universe.

    Args:
        full: If True, download 2 years of history (initial backfill).
              If False, download last 5 trading days (incremental update).

    Returns a summary dict with counts and timing.
    """
    t0 = time.time()
    ensure_tables()

    # 1. Fetch universe and build membership map
    logger.info("Fetching universe constituent lists...")
    sp500        = fetch_sp500_tickers()
    nasdaq100    = fetch_nasdaq100_tickers()
    nasdaq_large = fetch_nasdaq_large_cap_tickers()
    nyse_large   = fetch_nyse_large_cap_tickers()

    membership: dict[str, set[str]] = defaultdict(set)
    for sym in sp500:        membership[sym].add("sp500")
    for sym in nasdaq100:    membership[sym].add("nasdaq100")
    for sym in nasdaq_large: membership[sym].add("nasdaq_large")
    for sym in nyse_large:   membership[sym].add("nyse_large")

    universe = sorted(membership.keys())
    logger.info(
        "Universe: %d S&P500 + %d NDX100 + %d NASDAQ>=$2B + %d NYSE>=$2B = %d unique tickers",
        len(sp500), len(nasdaq100), len(nasdaq_large), len(nyse_large), len(universe),
    )

    if not universe:
        logger.error("Empty universe — aborting refresh")
        return {"error": "Empty universe", "elapsed_s": round(time.time() - t0, 1)}

    # 2. Download OHLCV in batches — new tickers always get a full 2y backfill
    if full:
        new_tickers: list[str] = []
        existing_tickers: list[str] = universe
        logger.info("OHLCV download mode: FULL (period=2y)")
    else:
        stored = set(get_universe_tickers())
        new_tickers = [t for t in universe if t not in stored]
        existing_tickers = [t for t in universe if t in stored]
        logger.info(
            "OHLCV download mode: INCREMENTAL — %d existing (5d) + %d new (2y backfill)",
            len(existing_tickers), len(new_tickers),
        )

    total_ohlcv_rows = 0

    def _download_batches(tickers: list[str], period: str) -> None:
        nonlocal total_ohlcv_rows
        n = math.ceil(len(tickers) / _OHLCV_DOWNLOAD_BATCH_SIZE)
        for i, start in enumerate(range(0, len(tickers), _OHLCV_DOWNLOAD_BATCH_SIZE)):
            batch = tickers[start: start + _OHLCV_DOWNLOAD_BATCH_SIZE]
            logger.info("OHLCV batch %d/%d (%d tickers, period=%s)...", i + 1, n, len(batch), period)
            batch_data = _download_ohlcv_batch(batch, period=period)
            if batch_data:
                rows_upserted = bulk_upsert_ohlcv_multi(batch_data)
                total_ohlcv_rows += rows_upserted
                logger.info("  → %d tickers, %d rows upserted", len(batch_data), rows_upserted)

    if new_tickers:
        _download_batches(new_tickers, "2y")
    if existing_tickers:
        _download_batches(existing_tickers, "2y" if full else "5d")

    ohlcv_elapsed = round(time.time() - t0, 1)
    logger.info("OHLCV download complete: %d total rows in %.1fs", total_ohlcv_rows, ohlcv_elapsed)

    # 3. Download fundamentals in batches
    t1 = time.time()
    total_fund_rows = 0
    n_fund_batches = math.ceil(len(universe) / _FUNDAMENTAL_BATCH_SIZE)
    logger.info("Fundamentals: %d tickers in %d batches", len(universe), n_fund_batches)

    for batch_idx, batch_start in enumerate(range(0, len(universe), _FUNDAMENTAL_BATCH_SIZE)):
        batch = universe[batch_start: batch_start + _FUNDAMENTAL_BATCH_SIZE]
        logger.info("Fundamentals batch %d/%d (%d tickers)...", batch_idx + 1, n_fund_batches, len(batch))

        fund_rows = _fetch_fundamentals_batch(batch)
        for row in fund_rows:
            row["universes"] = ",".join(sorted(membership.get(row["symbol"], set())))
        if fund_rows:
            upserted = bulk_upsert_fundamentals(fund_rows)
            total_fund_rows += upserted
            logger.info("  → %d fundamentals upserted", upserted)

        if batch_start + _FUNDAMENTAL_BATCH_SIZE < len(universe):
            time.sleep(_FUNDAMENTAL_BATCH_SLEEP_S)

    fund_elapsed = round(time.time() - t1, 1)

    # 4. Prune zombie rows — tickers removed from universe keep stale updated_at
    #    timestamps that cause MIN(updated_at) to always appear old.
    pruned = prune_stale_fundamentals(universe)
    if pruned > 0:
        logger.info("Pruned %d fundamentals rows for tickers removed from universe", pruned)

    # 4b. Surface any tickers that are still in the universe but never got a fresh
    #     write this run (delisted / renamed / reclassified so they fail the
    #     quoteType == "EQUITY" check and get silently skipped every run). These
    #     are the zombie rows that used to make the whole store look ancient.
    stale_tickers = get_stale_fundamental_tickers(threshold_hours=_STALE_FUNDAMENTAL_WARN_HOURS)
    if stale_tickers:
        logger.warning(
            "%d fundamentals ticker(s) still stale beyond %dh (likely permanently "
            "failing their fetch): %s",
            len(stale_tickers),
            _STALE_FUNDAMENTAL_WARN_HOURS,
            ", ".join(f"{t['symbol']}({t['updated_at']})" for t in stale_tickers),
        )

    total_elapsed = round(time.time() - t0, 1)

    # 5. Summary
    status = get_store_status()
    summary = {
        "universe_size": len(universe),
        "ohlcv_rows_upserted": total_ohlcv_rows,
        "fundamentals_upserted": total_fund_rows,
        "fundamentals_pruned": pruned,
        "ohlcv_elapsed_s": ohlcv_elapsed,
        "fundamentals_elapsed_s": fund_elapsed,
        "total_elapsed_s": total_elapsed,
        "mode": "full" if full else "incremental",
        "new_tickers": len(new_tickers),
        "stuck_tickers": [t["symbol"] for t in stale_tickers],
        "store_status": status,
    }
    logger.info(
        "Refresh complete in %.1fs — OHLCV: %d rows (%.1fs), Fundamentals: %d rows (%.1fs)",
        total_elapsed, total_ohlcv_rows, ohlcv_elapsed, total_fund_rows, fund_elapsed,
    )
    return summary


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    full_mode = "--full" in sys.argv
    result = refresh_universe(full=full_mode)
    print(f"\n{'='*60}")
    print(f"Refresh {'(FULL)' if full_mode else '(INCREMENTAL)'} complete")
    for k, v in result.items():
        if k != "store_status":
            print(f"  {k}: {v}")
    print(f"{'='*60}")
