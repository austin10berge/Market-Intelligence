"""Fetch full options chain data to compute per-ticker PCR (put/call ratio).

Two modes:
  1. Snapshot (live/current): Alpaca /v1beta1/options/snapshots endpoint.
     Returns PCR_VOL and PCR_OI for today. Used in daily pipeline.

  2. Historical bars backfill: Alpaca /v1beta1/options/bars endpoint.
     Enumerates all likely puts+calls (±15% of close, next 2 expirations)
     and sums put/call volumes. Used to backfill past dates.
     NOTE: Alpaca retains options data ~7 months. Sep-Oct 2025 is lost;
     Nov-Dec 2025 should still be available as of Jun 2026.

PCR interpretation for CSP sellers:
  PCR_VOL > 1.0 = more put volume than call volume = elevated put buying
  PCR_VOL elevated relative to ticker's own history → IV premium exists → CSP attractive
  PCR_OI  > 1.0 = more open put contracts than calls = sustained bearish hedging

Run historical backfill:
  docker compose run --rm pipeline python -m src.algo_detective.options_chain --backfill
Run snapshot for today:
  docker compose run --rm pipeline python -m src.algo_detective.options_chain --snapshot
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
    get_options_index,
    upsert_options_rows,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_RISK_FREE_RATE = 0.05
_BATCH_SIZE = 100         # Alpaca accepts up to 100 symbols per bars request
_REQUEST_SLEEP = 0.20     # seconds between batches
_STRIKE_RANGE_PUT = 0.18  # scan puts from close × (1 - 0.18) to close × 1.02
_STRIKE_RANGE_CALL = 0.12 # scan calls from close × 0.98 to close × (1 + 0.12)


# ── OCC symbol helpers ────────────────────────────────────────────────────────

def _make_occ(ticker: str, expiration: date, strike: float, option_type: str) -> str:
    """Build OCC symbol. option_type: 'P' or 'C'."""
    t = ticker.upper()
    exp_str = expiration.strftime("%y%m%d")
    strike_int = round(strike * 1000)
    return f"{t}{exp_str}{option_type}{strike_int:08d}"


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


def _next_fridays(from_date: date, n: int = 2) -> list[date]:
    result = []
    d = from_date
    while len(result) < n:
        if d.weekday() == 4:
            result.append(d)
        d += timedelta(days=1)
    return result


def _build_chain_symbols(
    ticker: str,
    close_price: float,
    scan_date: date,
) -> dict[str, str]:
    """Build {occ_symbol: 'P'|'C'} for all near-money puts and calls.

    Covers ±15% of close price for puts, ±12% for calls, next 2 expirations.
    """
    inc = _strike_increment(close_price)
    result: dict[str, str] = {}

    for exp_date in _next_fridays(scan_date + timedelta(days=1), n=2):
        # Puts: from close×(1-PUT_RANGE) to close×1.02 in inc steps
        put_lo = _round_to_increment(close_price * (1 - _STRIKE_RANGE_PUT), inc)
        put_hi = _round_to_increment(close_price * 1.02, inc)
        K = put_lo
        while K <= put_hi + inc / 2:
            if K > 0:
                occ = _make_occ(ticker, exp_date, K, "P")
                result[occ] = "P"
            K = round(K + inc, 6)

        # Calls: from close×0.98 to close×(1+CALL_RANGE) in inc steps
        call_lo = _round_to_increment(close_price * 0.98, inc)
        call_hi = _round_to_increment(close_price * (1 + _STRIKE_RANGE_CALL), inc)
        K = call_lo
        while K <= call_hi + inc / 2:
            if K > 0:
                occ = _make_occ(ticker, exp_date, K, "C")
                result[occ] = "C"
            K = round(K + inc, 6)

    return result


# ── Alpaca helpers ────────────────────────────────────────────────────────────

def _alpaca_headers() -> dict:
    return {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }


def _fetch_bars_batch(occ_symbols: list[str], start: str, end: str) -> dict[str, list[dict]]:
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
        logger.warning("Alpaca bars batch failed: %s", exc)
        return {}


def _fetch_snapshots_batch(underlying_symbols: list[str]) -> dict[str, dict]:
    """Fetch current options snapshots for multiple underlying tickers.

    Returns {underlying_symbol: {pcr_vol, pcr_oi, best_iv, best_volume, occ_symbol}}
    """
    if not underlying_symbols:
        return {}
    try:
        resp = httpx.get(
            f"{settings.alpaca_data_url}/v1beta1/options/snapshots",
            headers=_alpaca_headers(),
            params={
                "underlying_symbols": ",".join(underlying_symbols),
                "feed": "indicative",
                "limit": 10000,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json().get("snapshots", {})
    except Exception as exc:
        logger.warning("Alpaca snapshots failed for %s: %s", underlying_symbols[:3], exc)
        return {}

    results: dict[str, dict] = {}
    for underlying, contracts in data.items():
        put_vol = 0.0
        call_vol = 0.0
        put_oi = 0.0
        call_oi = 0.0
        best_iv: float | None = None
        best_volume = 0
        best_occ: str | None = None

        for occ_symbol, snap in contracts.items():
            # Determine put vs call from OCC symbol character
            option_type = _option_type_from_occ(occ_symbol)
            day_data = snap.get("dailyBar") or snap.get("latestQuote") or {}
            volume = float(day_data.get("v", 0) or 0)
            oi = float(snap.get("openInterest", 0) or 0)
            iv = snap.get("impliedVolatility") or snap.get("greeks", {}).get("iv")
            try:
                iv = float(iv) if iv is not None else None
            except (TypeError, ValueError):
                iv = None

            if option_type == "P":
                put_vol += volume
                put_oi += oi
            elif option_type == "C":
                call_vol += volume
                call_oi += oi

            # Track best IV across puts (for CSP we care about put IV)
            if option_type == "P" and iv is not None and iv > (best_iv or 0):
                best_iv = round(iv, 4)
                best_volume = int(volume)
                best_occ = occ_symbol

        pcr_vol = round(put_vol / call_vol, 4) if call_vol > 0 else None
        pcr_oi  = round(put_oi  / call_oi,  4) if call_oi  > 0 else None

        results[underlying] = {
            "pcr_vol": pcr_vol,
            "pcr_oi": pcr_oi,
            "best_iv": best_iv,
            "best_volume": best_volume,
            "occ_symbol": best_occ,
        }

    return results


def _option_type_from_occ(occ_symbol: str) -> str:
    """Extract 'P' or 'C' from an OCC symbol string."""
    # OCC format: TICKER(6)YYMMDD(6)TYPE(1)STRIKE(8) = 21+ chars
    # Find the first P or C after the expiry date (position ≥ ticker_len + 6)
    for i, ch in enumerate(occ_symbol):
        if ch in ("P", "C") and i > 0:
            # Verify: remaining chars are all digits (strike)
            remaining = occ_symbol[i + 1:]
            if remaining.isdigit():
                return ch
    return "?"


# ── Historical backfill ───────────────────────────────────────────────────────

def backfill_pcr(prime_tickers_only: bool = True) -> None:
    """Backfill pcr_vol for existing detective_options rows using historical bars.

    Alpaca's retention is ~7 months. As of Jun 2026, Nov-Dec 2025 should be
    available. Sep-Oct 2025 data is lost.
    """
    ensure_tables()

    features = get_all_features()
    if prime_tickers_only:
        prime_set = {f["ticker"] for f in features if f["is_prime"] == 1}
        candidates = [f for f in features if f["ticker"] in prime_set and f.get("close_price")]
    else:
        candidates = [f for f in features if f.get("close_price")]

    # Only process rows that don't already have pcr_vol
    existing_options = get_options_index()
    needs_pcr = [
        f for f in candidates
        if existing_options.get((f["date"], f["ticker"]), {}).get("pcr_vol") is None
    ]

    logger.info(
        "PCR backfill: %d candidate rows (%d already have pcr_vol)",
        len(needs_pcr),
        len(candidates) - len(needs_pcr),
    )

    if not needs_pcr:
        logger.info("Nothing to backfill.")
        return

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for f in needs_pcr:
        by_date.setdefault(f["date"], []).append(f)

    total_updated = 0

    for date_str in sorted(by_date.keys()):
        scan_date = date.fromisoformat(date_str)
        rows_for_date = by_date[date_str]

        # Build all OCC symbols for this date
        occ_type_map: dict[str, str] = {}  # occ_symbol → 'P' or 'C'
        occ_ticker_map: dict[str, str] = {}  # occ_symbol → underlying ticker

        for f in rows_for_date:
            chain = _build_chain_symbols(f["ticker"], f["close_price"], scan_date)
            for occ, opt_type in chain.items():
                occ_type_map[occ] = opt_type
                occ_ticker_map[occ] = f["ticker"]

        all_occs = list(occ_type_map.keys())
        fetch_start = date_str
        fetch_end = (scan_date + timedelta(days=1)).isoformat()

        # Batch fetch
        bars_for_date: dict[str, list[dict]] = {}
        for i in range(0, len(all_occs), _BATCH_SIZE):
            chunk = all_occs[i: i + _BATCH_SIZE]
            result = _fetch_bars_batch(chunk, fetch_start, fetch_end)
            bars_for_date.update(result)
            if i + _BATCH_SIZE < len(all_occs):
                time.sleep(_REQUEST_SLEEP)

        # Aggregate volume per ticker
        ticker_put_vol: dict[str, float] = {}
        ticker_call_vol: dict[str, float] = {}

        for occ, bars in bars_for_date.items():
            ticker = occ_ticker_map.get(occ)
            opt_type = occ_type_map.get(occ)
            if not ticker or not opt_type:
                continue

            # Find the bar matching scan_date
            bar = next((b for b in bars if b["t"].startswith(date_str)), None)
            if not bar:
                continue

            volume = float(bar.get("v", 0) or 0)
            if opt_type == "P":
                ticker_put_vol[ticker] = ticker_put_vol.get(ticker, 0.0) + volume
            elif opt_type == "C":
                ticker_call_vol[ticker] = ticker_call_vol.get(ticker, 0.0) + volume

        # Build update rows
        update_rows = []
        for f in rows_for_date:
            ticker = f["ticker"]
            put_vol = ticker_put_vol.get(ticker, 0.0)
            call_vol = ticker_call_vol.get(ticker, 0.0)
            pcr_vol = round(put_vol / call_vol, 4) if call_vol > 0 else None

            # Merge with existing best_iv from detective_options
            existing = existing_options.get((date_str, ticker), {})
            update_rows.append({
                "date": date_str,
                "ticker": ticker,
                "best_iv": existing.get("best_iv"),
                "best_volume": existing.get("best_volume"),
                "occ_symbol": existing.get("occ_symbol"),
                "pcr_vol": pcr_vol,
                "pcr_oi": None,  # OI not available from historical bars
            })

        upsert_options_rows(update_rows)
        total_updated += len(update_rows)

        filled = sum(1 for r in update_rows if r["pcr_vol"] is not None)
        logger.info(
            "%s: %d tickers, %d OCC symbols queried, %d got bars, %d/%d pcr_vol filled",
            date_str,
            len(rows_for_date),
            len(all_occs),
            len(bars_for_date),
            filled,
            len(update_rows),
        )

    logger.info("PCR backfill complete: %d rows updated", total_updated)


# ── Daily snapshot (forward-looking pipeline) ─────────────────────────────────

def fetch_snapshot_pcr(tickers: list[str], scan_date_str: str | None = None) -> int:
    """Fetch current options chain snapshot for tickers, compute PCR, upsert.

    Uses Alpaca's snapshots endpoint — returns today's IV, volume, OI per contract.
    Call this nightly after market close, passing the whitelist tickers.

    Returns number of rows stored.
    """
    ensure_tables()
    if scan_date_str is None:
        scan_date_str = date.today().isoformat()

    logger.info("Fetching options snapshots for %d tickers on %s", len(tickers), scan_date_str)

    # Fetch snapshots in batches of 20 (snapshots can return many contracts per ticker)
    _SNAP_BATCH = 20
    all_results: dict[str, dict] = {}

    for i in range(0, len(tickers), _SNAP_BATCH):
        batch = tickers[i: i + _SNAP_BATCH]
        result = _fetch_snapshots_batch(batch)
        all_results.update(result)
        if i + _SNAP_BATCH < len(tickers):
            time.sleep(_REQUEST_SLEEP)

    # Build upsert rows
    rows = []
    for ticker in tickers:
        snap = all_results.get(ticker, {})
        rows.append({
            "date": scan_date_str,
            "ticker": ticker,
            "best_iv": snap.get("best_iv"),
            "best_volume": snap.get("best_volume"),
            "occ_symbol": snap.get("occ_symbol"),
            "pcr_vol": snap.get("pcr_vol"),
            "pcr_oi": snap.get("pcr_oi"),
        })

    stored = upsert_options_rows(rows)
    filled_iv  = sum(1 for r in rows if r["best_iv"] is not None)
    filled_pcr = sum(1 for r in rows if r["pcr_vol"] is not None)
    logger.info(
        "Snapshot PCR: %d rows stored, %d with IV, %d with PCR",
        stored, filled_iv, filled_pcr,
    )
    return stored


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch options chain PCR data from Alpaca")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill pcr_vol for all historical dates using options bars (slow; ~7-month retention)",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Fetch today's options snapshot PCR for all prime tickers",
    )
    parser.add_argument(
        "--all-tickers",
        action="store_true",
        help="Include non-prime SP500 tickers (default: prime tickers only)",
    )
    args = parser.parse_args()

    if args.backfill:
        backfill_pcr(prime_tickers_only=not args.all_tickers)
    elif args.snapshot:
        from .store import get_all_features as _gaf
        _features = _gaf()
        _prime = sorted({f["ticker"] for f in _features if f["is_prime"] == 1})
        fetch_snapshot_pcr(_prime)
    else:
        parser.print_help()
