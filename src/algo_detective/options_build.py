"""Fetch historical options IV from Alpaca and store in detective_options.

For each (date, ticker) pair in detective_features (SP500 universe), we:
1. Estimate the -0.27 delta put strike using rv20 as IV proxy and Black-Scholes
2. Build OCC symbols for ±1 strike and the nearest 1-2 Friday expirations
3. Batch-query Alpaca historical bars (100 symbols per request)
4. Back-calculate IV by solving Black-Scholes for sigma
5. Store best_iv = max IV found across all queried contracts

Run:
  docker compose run --rm pipeline python -m src.algo_detective.options_build
  docker compose run --rm pipeline python -m src.algo_detective.options_build --all
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from datetime import date, timedelta

import httpx

from ..config import settings
from .store import (
    _get_connection,
    ensure_tables,
    get_all_features,
    get_computed_options_pairs,
    upsert_options_rows,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_RISK_FREE_RATE = 0.05
_TARGET_DELTA = 0.27        # absolute value; we want ~-0.27 delta puts
_BATCH_SIZE = 50            # keep URL short; Alpaca returns 400 on very long symbol lists
_REQUEST_SLEEP = 0.25       # seconds between batches (rate-limit courtesy)


# ── Black-Scholes helpers ────────────────────────────────────────────────────

def _N(x: float) -> float:
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _bs_put(S: float, K: float, T: float, sigma: float, r: float = _RISK_FREE_RATE) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return max(K * math.exp(-r * T) * _N(-d2) - S * _N(-d1), 0.0)


def _solve_iv(S: float, K: float, T: float, market_price: float, r: float = _RISK_FREE_RATE) -> float | None:
    """Bisection solver: find sigma such that BS put price == market_price."""
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(K - S, 0.0)
    if market_price <= intrinsic:
        return None
    lo, hi = 0.001, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        price = _bs_put(S, K, T, mid, r)
        if price > market_price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-5:
            break
    result = (lo + hi) / 2.0
    # Sanity check: discard implausibly extreme values
    return result if 0.01 <= result <= 5.0 else None


def _target_strike(S: float, sigma: float, T: float, r: float = _RISK_FREE_RATE) -> float:
    """Estimate the -TARGET_DELTA put strike using BS delta inverse."""
    if sigma <= 0 or T <= 0:
        return S * 0.97
    # N(-d1) = TARGET_DELTA  →  d1 = N_inv(1 - TARGET_DELTA)
    # Approximate N_inv(0.73) ≈ 0.613
    d1_target = 0.613
    log_moneyness = d1_target * sigma * math.sqrt(T) - (r + 0.5 * sigma ** 2) * T
    return S * math.exp(-log_moneyness)


def _strike_increment(S: float) -> float:
    if S >= 200:
        return 5.0
    if S >= 50:
        return 2.5
    if S >= 20:
        return 1.0
    return 0.5


def _round_to_increment(K: float, inc: float) -> float:
    return round(round(K / inc) * inc, 2)


# ── OCC symbol helpers ───────────────────────────────────────────────────────

def _make_occ(ticker: str, expiration: date, strike: float) -> str:
    """Compact OCC format Alpaca accepts: TICKERYYMMDDP00strike_x1000(8d), no padding."""
    t = ticker.upper()
    exp_str = expiration.strftime("%y%m%d")
    strike_int = round(strike * 1000)
    return f"{t}{exp_str}P{strike_int:08d}"


def _next_fridays(from_date: date, n: int = 2) -> list[date]:
    """Return the next n Friday dates on or after from_date."""
    result = []
    d = from_date
    while len(result) < n:
        if d.weekday() == 4:  # Friday
            result.append(d)
        d += timedelta(days=1)
    return result


def _build_occ_symbols(ticker: str, S: float, rv20: float, scan_date: date) -> list[tuple[str, float, date]]:
    """Return [(occ_symbol, strike, expiration), ...] to query for this row."""
    sigma = max(rv20, 0.05)
    symbols = []
    inc = _strike_increment(S)

    for exp_date in _next_fridays(scan_date + timedelta(days=1), n=2):
        T = (exp_date - scan_date).days / 365.0
        if T <= 0:
            continue
        K_target = _target_strike(S, sigma, T)
        K_base = _round_to_increment(K_target, inc)
        # Query target strike and one on each side
        for K in [K_base - inc, K_base, K_base + inc]:
            if K <= 0:
                continue
            occ = _make_occ(ticker, exp_date, K)
            symbols.append((occ, K, exp_date))

    return symbols


# ── Alpaca fetch ─────────────────────────────────────────────────────────────

def _alpaca_headers() -> dict:
    return {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }


def _fetch_bars_batch(occ_symbols: list[str], start: str, end: str) -> dict[str, list[dict]]:
    """Fetch daily bars for up to _BATCH_SIZE OCC symbols. Returns {occ: [bar, ...]}."""
    if not occ_symbols:
        return {}
    try:
        resp = httpx.get(
            f"{settings.alpaca_data_url}/v1beta1/options/bars",
            headers=_alpaca_headers(),
            params={
                "symbols": ",".join(occ_symbols),
                "timeframe": "1Day",
                "start": start,
                "end": end,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("bars", {})
    except Exception as exc:
        logger.warning("Alpaca bars fetch failed: %s", exc)
        return {}


# ── Main build logic ─────────────────────────────────────────────────────────

def _get_sp500_tickers() -> set[str]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%sp500%'"
        ).fetchall()
        return {r["symbol"] for r in rows}
    finally:
        conn.close()


def build_options_features(skip_existing: bool = True) -> None:
    ensure_tables()

    features = get_all_features()
    sp500 = _get_sp500_tickers()
    already_done = get_computed_options_pairs() if skip_existing else set()

    # Filter to SP500 universe + prime rows
    candidates = [
        f for f in features
        if (f["is_prime"] == 1 or f["ticker"] in sp500)
        and f.get("close_price") and f.get("rv20")
        and (f["date"], f["ticker"]) not in already_done
    ]

    logger.info(
        "Building options features: %d candidate (date, ticker) pairs (%d already done)",
        len(candidates), len(already_done)
    )

    if not candidates:
        logger.info("Nothing to do.")
        return

    # Group by scan date to minimize date range in Alpaca requests
    by_date: dict[str, list[dict]] = {}
    for f in candidates:
        by_date.setdefault(f["date"], []).append(f)

    total_stored = 0

    for scan_date_str in sorted(by_date.keys()):
        scan_date = date.fromisoformat(scan_date_str)
        rows_for_date = by_date[scan_date_str]

        # Build all OCC symbols for this date
        # occ_meta: {occ_symbol: (ticker, strike, expiration_date)}
        occ_meta: dict[str, tuple[str, float, date]] = {}
        for f in rows_for_date:
            for occ, strike, exp in _build_occ_symbols(
                f["ticker"], f["close_price"], f["rv20"], scan_date
            ):
                occ_meta[occ] = (f["ticker"], strike, exp)

        all_occs = list(occ_meta.keys())
        # Fetch start/end: just this one trading day (bars timestamped at 04:00 UTC = EOD prior day)
        fetch_start = scan_date_str
        fetch_end = (scan_date + timedelta(days=1)).isoformat()

        # Batch query Alpaca
        bars_for_date: dict[str, list[dict]] = {}
        for i in range(0, len(all_occs), _BATCH_SIZE):
            chunk = all_occs[i: i + _BATCH_SIZE]
            result = _fetch_bars_batch(chunk, fetch_start, fetch_end)
            bars_for_date.update(result)
            if i + _BATCH_SIZE < len(all_occs):
                time.sleep(_REQUEST_SLEEP)

        # For each (date, ticker): find best IV across all its queried contracts
        ticker_best: dict[str, dict] = {}  # ticker → {best_iv, best_volume, occ_symbol}

        for occ, (ticker, K, exp_date) in occ_meta.items():
            bars = bars_for_date.get(occ, [])
            # Find the bar for this specific scan date
            bar = next(
                (b for b in bars if b["t"].startswith(scan_date_str)),
                None,
            )
            if not bar:
                continue

            # Get the underlying close price for this ticker/date
            feat = next(
                (f for f in rows_for_date if f["ticker"] == ticker),
                None,
            )
            if not feat or not feat.get("close_price"):
                continue

            S = feat["close_price"]
            T = (exp_date - scan_date).days / 365.0
            opt_close = bar.get("c", 0.0)
            volume = bar.get("v", 0)

            iv = _solve_iv(S, K, T, opt_close)
            if iv is None:
                continue

            current_best = ticker_best.get(ticker)
            if current_best is None or iv > current_best["best_iv"]:
                ticker_best[ticker] = {
                    "date": scan_date_str,
                    "ticker": ticker,
                    "best_iv": round(iv, 4),
                    "best_volume": volume,
                    "occ_symbol": occ,
                }

        # For tickers with no options data found, store a null row so we skip them next time
        tickers_with_data = set(ticker_best.keys())
        for f in rows_for_date:
            if f["ticker"] not in tickers_with_data:
                ticker_best[f["ticker"]] = {
                    "date": scan_date_str,
                    "ticker": f["ticker"],
                    "best_iv": None,
                    "best_volume": None,
                    "occ_symbol": None,
                }

        rows_to_store = list(ticker_best.values())
        stored = upsert_options_rows(rows_to_store)
        total_stored += stored

        viable = sum(1 for r in rows_to_store if r["best_iv"] and r["best_iv"] >= 0.20)
        logger.info(
            "%s: queried %d OCC symbols, got bars for %d, stored %d rows (%d viable IV≥20%%)",
            scan_date_str, len(all_occs), len(bars_for_date), stored, viable,
        )

    logger.info("Done. Total rows stored: %d", total_stored)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch historical options IV from Alpaca")
    parser.add_argument("--all", action="store_true", help="Recompute even already-stored pairs")
    args = parser.parse_args()
    build_options_features(skip_existing=not args.all)
