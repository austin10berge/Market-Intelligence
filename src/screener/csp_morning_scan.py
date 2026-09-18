"""Morning CSP scan — live NASDAQ 100 scan, no LLM scoring.

Runs stages 1-4 of the CSP scanner (fundamental → vol → technical → options)
against the NASDAQ 100 universe only. Skips wheel_scorer to avoid any LLM/Hermes
dependency. Writes data/morning-scan.json for consumption by the morning brief.

Also pre-computes watchlist technicals (50d/200d SMA, RSI) into
data/watchlist-technicals.json so the morning brief Claude session does not need
to make ~40 sequential Schwab MCP + RSI MCP calls per run.

The watchlist comes from the prod API (the same endpoint the brief scores
against), NOT the local DB — the ai-dev DB holds a different, stale list.

Usage (from docker compose):
    docker compose run --rm pipeline python3 -m src.screener.csp_morning_scan
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

from .csp_scanner import ScannerParams, fetch_nasdaq100_tickers, run_csp_scan
from .wheel_scorer import score_wheel_candidates

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parents[2] / "data"
_MORNING_SCAN_PATH = _DATA_DIR / "morning-scan.json"
_WATCHLIST_TECHNICALS_PATH = _DATA_DIR / "watchlist-technicals.json"
_REGIME_PATH = _DATA_DIR / "regime-status.json"
_BRIEF_WATCHLIST_URL = os.environ.get(
    "BRIEF_WATCHLIST_URL", "https://market.austin10berge.com/api/watchlist"
)

_DEFAULT_LIMIT = 5
_DEFAULT_MAX_DELTA = 0.30


def _read_regime_delta_cap(regime_path: Path) -> float:
    try:
        data = json.loads(regime_path.read_text(encoding="utf-8"))
        cap = data.get("delta_cap")
        if isinstance(cap, (int, float)):
            return float(cap)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return _DEFAULT_MAX_DELTA


def build_scan_params(regime_path: Path = _REGIME_PATH) -> ScannerParams:
    """Return scanner params tuned for the morning brief scan.

    Beta, market cap, FCF, and D/E gates are disabled — the NASDAQ 100
    is already a curated large-cap universe so these pre-filters add noise.
    The regime delta cap is read from regime-status.json so the scanner
    finds lower-delta (further OTM) strikes rather than filtering after the fact.
    """
    delta_cap = _read_regime_delta_cap(regime_path)
    return ScannerParams(
        min_market_cap_b=0.0,
        min_beta=0.0,
        max_beta=100.0,
        min_fcf_b=None,
        max_debt_to_equity=None,
        min_revenue_growth=None,
        adr20_pct_min=3.0,
        min_days_to_earnings=21,
        min_dte=21,
        max_dte=30,
        max_delta=delta_cap,
    )


def fetch_brief_watchlist(url: str = _BRIEF_WATCHLIST_URL, timeout: float = 15) -> list[str] | None:
    """Return the prod watchlist the morning brief scores, or None on any failure.

    There is deliberately no fallback to the local DB: on ai-dev it holds a
    different list, and scoring the wrong tickers is worse than scoring none.
    """
    import requests

    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        watchlist = resp.json().get("watchlist")
    except Exception as exc:
        logger.error("Brief watchlist: fetch from %s failed: %s", url, exc)
        return None
    if not isinstance(watchlist, list) or not watchlist:
        logger.error("Brief watchlist: %s returned no usable 'watchlist' array", url)
        return None
    return [str(t) for t in watchlist]


def filter_bad_data(candidates: list[dict]) -> list[dict]:
    """Drop rows with missing delta or zero IV — bad Alpaca snapshots."""
    return [
        c for c in candidates
        if c.get("delta") is not None and (c.get("impliedVolatility") or 0.0) > 0.0
    ]


def filter_out_watchlist(candidates: list[dict], watchlist: list[str]) -> list[dict]:
    """Remove candidates whose ticker is already on the user's watchlist."""
    wl_set = set(watchlist)
    return [c for c in candidates if c.get("symbol") not in wl_set]


def top_unique_by_ticker(candidates: list[dict], limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """Deduplicate by ticker (keep highest annualized_roc row), sort desc, cap at limit."""
    best: dict[str, dict] = {}
    for c in candidates:
        sym = c.get("symbol", "")
        roc = c.get("annualized_roc") or 0.0
        if sym not in best or roc > (best[sym].get("annualized_roc") or 0.0):
            best[sym] = c
    ranked = sorted(best.values(), key=lambda c: c.get("annualized_roc") or 0.0, reverse=True)
    return ranked[:limit]


def write_morning_scan(candidates: list[dict], out_path: Path = _MORNING_SCAN_PATH) -> Path:
    """Serialise candidates to JSON and write to out_path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in candidates:
        rows.append({
            "ticker":        c.get("symbol", ""),
            "strike":        c.get("strike"),
            "expiry":        c.get("expiration", ""),
            "dte":           c.get("dte"),
            "delta":         c.get("delta"),
            "iv_pct":        c.get("impliedVolatility"),
            "adr20_pct":     c.get("adr20_pct"),
            "ann_roc_pct":   c.get("annualized_roc"),
            "sector":        c.get("sector", ""),
            "wheel_score":   c.get("wheel_score", 0),
            "wheel_thesis":  c.get("wheel_thesis", ""),
        })
    payload = {
        "date":       date.today().isoformat(),
        "universe":   "nasdaq100",
        "candidates": rows,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Morning scan: wrote %d candidates to %s", len(rows), out_path)
    return out_path


def _compute_rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    val = float(rsi.iloc[-1])
    return None if math.isnan(val) else round(val, 1)


def _compute_bollinger(closes: pd.Series, period: int = 20) -> dict:
    if len(closes) < period:
        return {"bb_upper": None, "bb_lower": None, "bb_pct_b": None, "bb_bandwidth": None}
    window = closes.iloc[-period:]
    sma = float(window.mean())
    sigma = float(window.std(ddof=0))
    upper = sma + 2 * sigma
    lower = sma - 2 * sigma
    last = float(closes.iloc[-1])
    band_range = upper - lower
    pct_b = round((last - lower) / band_range * 100, 1) if band_range > 0 else None
    bandwidth = round(band_range / sma * 100, 2) if sma > 0 else None
    return {
        "bb_upper": round(upper, 4),
        "bb_lower": round(lower, 4),
        "bb_pct_b": pct_b,
        "bb_bandwidth": bandwidth,
    }


def compute_watchlist_technicals(
    watchlist: list[str],
    out_path: Path = _WATCHLIST_TECHNICALS_PATH,
    watchlist_error: str | None = None,
) -> Path:
    """Fetch 1-year daily closes for watchlist + QQQ and write technicals JSON.

    Computes per-ticker: sma_50, sma_200, rsi_14, and for QQQ also Bollinger
    Band values (bb_upper, bb_lower, bb_pct_b, bb_bandwidth). Claude reads this
    file instead of making ~42 sequential Schwab + RSI MCP calls per morning brief.

    sma_50 / sma_200 are null when the ticker has fewer than 50 / 200 closes
    (recent IPOs) — a shorter-window mean labelled as a 200-day SMA misleads.
    If watchlist_error is set (prod watchlist unavailable), it is written into
    the file so the brief can report why no watchlist ticker has technicals;
    QQQ is still computed for the regime section.
    """
    tickers = sorted(set(watchlist + ["QQQ"]))
    logger.info("Watchlist technicals: fetching price history for %d tickers", len(tickers))
    try:
        raw = yf.download(
            tickers,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.warning("Watchlist technicals: yfinance download failed: %s", exc)
        return out_path

    # yf.download returns MultiIndex columns when multiple tickers are passed.
    # Close prices live at ("Close", TICKER).
    if isinstance(raw.columns, pd.MultiIndex):
        close_df = raw["Close"]
    else:
        # Single ticker — yf returns flat columns named after the field.
        close_df = raw[["Close"]].rename(columns={"Close": tickers[0]})

    results: dict[str, dict] = {}
    for ticker in tickers:
        if ticker not in close_df.columns:
            logger.warning("Watchlist technicals: no data for %s", ticker)
            results[ticker] = {"sma_50": None, "sma_200": None, "rsi_14": None}
            continue
        closes = close_df[ticker].dropna()
        n = len(closes)
        sma_50 = round(float(closes.iloc[-50:].mean()), 4) if n >= 50 else None
        sma_200 = round(float(closes.iloc[-200:].mean()), 4) if n >= 200 else None
        rsi = _compute_rsi(closes)
        entry: dict = {"sma_50": sma_50, "sma_200": sma_200, "rsi_14": rsi}
        if ticker == "QQQ":
            entry.update(_compute_bollinger(closes))
        results[ticker] = entry

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"date": date.today().isoformat(), "technicals": results}
    if watchlist_error:
        payload["watchlist_error"] = watchlist_error
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Watchlist technicals: wrote %d tickers to %s", len(results), out_path)
    return out_path


async def run_morning_scan(out_path: Path = _MORNING_SCAN_PATH) -> Path:
    """Full pipeline: fetch NASDAQ 100, scan, score with financials, write JSON."""
    tickers = fetch_nasdaq100_tickers()
    if not tickers:
        logger.warning("Morning scan: NASDAQ 100 fetch returned no tickers")
        return write_morning_scan([], out_path)

    logger.info("Morning scan: %d NASDAQ 100 tickers", len(tickers))
    params = build_scan_params()

    import src.screener.csp_scanner as _scanner_mod
    _orig_fetch = _scanner_mod.fetch_universe
    _scanner_mod.fetch_universe = lambda: tickers
    try:
        result = await asyncio.to_thread(run_csp_scan, params)
    finally:
        _scanner_mod.fetch_universe = _orig_fetch

    candidates = result.get("candidates", [])
    logger.info("Morning scan: scanner returned %d raw candidates", len(candidates))

    watchlist = fetch_brief_watchlist()
    watchlist_error = None
    if watchlist is None:
        watchlist_error = f"prod watchlist API unavailable ({_BRIEF_WATCHLIST_URL})"
        watchlist = []
    else:
        logger.info("Morning scan: prod watchlist has %d tickers", len(watchlist))
    candidates = filter_bad_data(candidates)
    candidates = filter_out_watchlist(candidates, watchlist)
    candidates = top_unique_by_ticker(candidates, limit=_DEFAULT_LIMIT)
    logger.info("Morning scan: %d candidates after dedup/filter, scoring top %d", len(candidates), _DEFAULT_LIMIT)

    candidates = await score_wheel_candidates(candidates, top_n=_DEFAULT_LIMIT)
    logger.info("Morning scan: scoring complete — top wheel_score %d", candidates[0].get("wheel_score", 0) if candidates else 0)

    # Pre-compute watchlist technicals (SMA, RSI, Bollinger for QQQ) so the
    # morning brief Claude session reads a single file instead of ~42 MCP calls.
    await asyncio.to_thread(
        compute_watchlist_technicals, watchlist, watchlist_error=watchlist_error
    )

    return write_morning_scan(candidates, out_path)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = asyncio.run(run_morning_scan())
    print(f"Written to {out}", file=sys.stderr)
