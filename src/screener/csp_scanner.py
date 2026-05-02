"""CSP Scanner — broad universe screener for Cash-Secured Put candidates.

Fetches the S&P 500 + NASDAQ 100 constituent lists, applies fundamental,
volatility, and optional technical-condition pre-filters, then hands qualifying
tickers to the existing screen_csp_candidates() options screening pipeline.

Pre-filter pipeline
-------------------
  Stage 1 — Fundamental filter (market cap, price, beta) + IV capture
  Stage 2 — Volatility gate (IV ≥ min_vol primary, RV-20 fallback)
  Stage 3 — Technical conditions (optional, stackable, user-selected)
  Stage 4 — Options screener (RSI/ADX/Alpaca pricing pipeline)

All scanner parameters are passed in as a ScannerParams dataclass so the
API layer can accept them as query parameters and vary the cache key per
unique param combination.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from typing import TypedDict

import pandas as pd
import yfinance as yf

from .options import screen_csp_candidates
from ..market_data.store import (
    get_all_fundamentals,
    get_ohlcv,
    get_store_status,
    ensure_tables,
)

logger = logging.getLogger(__name__)

# ── Default pre-filter values ─────────────────────────────────────────────────

DEFAULT_MIN_MARKET_CAP_B = 10.0
DEFAULT_MAX_PRICE        = 150.0
DEFAULT_MIN_BETA         = 0.8
DEFAULT_MAX_BETA         = 2.4
DEFAULT_MIN_VOL_PCT      = 30.0

# yfinance informal rate-limit
_INFO_BATCH_SIZE    = 50
_INFO_BATCH_SLEEP_S = 1.0

# Source URLs
_SP500_WIKI_URL    = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NASDAQ100_API_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"

_WIKI_USER_AGENT = "Mozilla/5.0 (compatible; MarketIntelligenceBot/1.0; +https://github.com/)"
_NASDAQ_HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


# ── Available technical conditions ───────────────────────────────────────────
# These are the stackable conditions users can enable in the UI.
# Each entry: id, label, description
AVAILABLE_CONDITIONS: list[dict] = [
    {
        "id": "sma50_above_sma200",
        "label": "50 SMA > 200 SMA",
        "description": "Bullish long-term trend — 50-day moving average above 200-day",
        "group": "Trend",
    },
    {
        "id": "sma50_above_sma20",
        "label": "50 SMA > 20 SMA",
        "description": "Medium-term above short-term MA — slower trend dominant (note: 20 SMA > 50 SMA signals short-term bullish momentum)",
        "group": "Trend",
    },
    {
        "id": "sma20_above_sma50",
        "label": "20 SMA > 50 SMA",
        "description": "Short-term momentum above medium-term",
        "group": "Trend",
    },
    {
        "id": "price_above_sma50",
        "label": "Price > 50 SMA",
        "description": "Price trading above medium-term support",
        "group": "Price vs MA",
    },
    {
        "id": "price_above_sma200",
        "label": "Price > 200 SMA",
        "description": "Price trading above long-term support",
        "group": "Price vs MA",
    },
    {
        "id": "price_near_lower_bb",
        "label": "Near Lower Bollinger Band",
        "description": "Price within 2% of lower Bollinger Band (20-period, 2σ) — mean reversion setup",
        "group": "Bollinger Bands",
    },
    {
        "id": "price_below_sma50",
        "label": "Price < 50 SMA",
        "description": "Price pulled back below medium-term MA — potential CSP entry on weakness",
        "group": "Price vs MA",
    },
    {
        "id": "rsi_oversold_bounce",
        "label": "RSI Oversold Bounce",
        "description": "RSI(14) between 30–45 — pulled back from overbought, not in freefall",
        "group": "Momentum",
    },
]

# Index by id for fast lookup
_CONDITION_IDS = {c["id"] for c in AVAILABLE_CONDITIONS}


# ── ScannerParams dataclass ───────────────────────────────────────────────────

@dataclass
class ScannerParams:
    """All user-configurable scanner parameters.

    Passed from the API layer so that different param combinations produce
    distinct cache keys without touching module-level state.
    """
    min_market_cap_b: float = DEFAULT_MIN_MARKET_CAP_B
    max_price:        float = DEFAULT_MAX_PRICE
    min_beta:         float = DEFAULT_MIN_BETA
    max_beta:         float = DEFAULT_MAX_BETA
    min_vol_pct:      float = DEFAULT_MIN_VOL_PCT
    # Sorted list of active condition IDs — order doesn't affect logic
    conditions: list[str] = field(default_factory=list)

    def cache_key_suffix(self) -> str:
        """Return a short deterministic hash of the params for cache keying."""
        payload = json.dumps(asdict(self), sort_keys=True)
        return hashlib.md5(payload.encode()).hexdigest()[:10]

    @classmethod
    def from_query(
        cls,
        min_cap: float | None = None,
        max_price: float | None = None,
        min_beta: float | None = None,
        max_beta: float | None = None,
        min_vol: float | None = None,
        conditions: str | None = None,
    ) -> "ScannerParams":
        """Build ScannerParams from API query parameters (all optional)."""
        parsed_conditions: list[str] = []
        if conditions:
            parsed_conditions = [
                c.strip() for c in conditions.split(",")
                if c.strip() in _CONDITION_IDS
            ]
        return cls(
            min_market_cap_b = min_cap   if min_cap   is not None else DEFAULT_MIN_MARKET_CAP_B,
            max_price        = max_price if max_price is not None else DEFAULT_MAX_PRICE,
            min_beta         = min_beta  if min_beta  is not None else DEFAULT_MIN_BETA,
            max_beta         = max_beta  if max_beta  is not None else DEFAULT_MAX_BETA,
            min_vol_pct      = min_vol   if min_vol   is not None else DEFAULT_MIN_VOL_PCT,
            conditions       = sorted(parsed_conditions),
        )


# ── Type helpers ──────────────────────────────────────────────────────────────

class FilterSummary(TypedDict):
    sp500_count: int
    nasdaq100_count: int
    combined_unique: int
    fundamental_passed: int
    vol_passed: int
    technical_passed: int
    options_screener_returned: int


# ── Index constituent fetchers ────────────────────────────────────────────────

def fetch_sp500_tickers() -> list[str]:
    """Fetch S&P 500 constituents from Wikipedia static HTML."""
    try:
        import requests as _requests
        resp = _requests.get(_SP500_WIKI_URL, headers={"User-Agent": _WIKI_USER_AGENT}, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        logger.info("S&P 500 Wikipedia: found %d tables", len(tables))
        df = next(
            (t for t in tables
             if any(str(c).strip().lower() in ("symbol", "ticker") for c in t.columns)
             and len(t) > 400),
            None,
        )
        if df is None:
            candidates = [t for t in tables if any(str(c).strip().lower() in ("symbol", "ticker") for c in t.columns)]
            df = max(candidates, key=len) if candidates else None
        if df is None:
            logger.error("S&P 500: could not locate constituent table")
            return []
        ticker_col = next((c for c in df.columns if str(c).strip().lower() in ("symbol", "ticker")), None)
        tickers = (
            df[ticker_col].dropna().astype(str)
            .str.upper().str.strip().str.replace(".", "-", regex=False).tolist()
        )
        logger.info("Fetched %d S&P 500 tickers", len(tickers))
        return tickers
    except Exception as exc:
        logger.error("Failed to fetch S&P 500 tickers: %s", exc, exc_info=True)
        return []


def fetch_nasdaq100_tickers() -> list[str]:
    """Fetch NASDAQ-100 constituents from the official NASDAQ JSON API."""
    try:
        import requests as _requests
        resp = _requests.get(_NASDAQ100_API_URL, headers=_NASDAQ_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", {}).get("data", {}).get("rows", [])
        if not rows:
            logger.error("NASDAQ-100 API returned no rows. Keys: %s", list(data.keys()))
            return []
        tickers = [
            str(r.get("symbol", "")).upper().strip().replace(".", "-")
            for r in rows if r.get("symbol")
        ]
        logger.info("Fetched %d NASDAQ-100 tickers from NASDAQ API", len(tickers))
        return tickers
    except Exception as exc:
        logger.error("Failed to fetch NASDAQ-100 tickers: %s", exc, exc_info=True)
        return []


def fetch_universe() -> list[str]:
    """Return the deduplicated union of S&P 500 and NASDAQ 100 tickers."""
    sp500  = fetch_sp500_tickers()
    nasdaq = fetch_nasdaq100_tickers()
    combined = sorted(set(sp500) | set(nasdaq))
    logger.info("Universe: %d S&P500 + %d NDX100 = %d unique", len(sp500), len(nasdaq), len(combined))
    return combined


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _calculate_rv20(hist: pd.DataFrame) -> float | None:
    closes = hist.get("Close")
    if closes is None or len(closes) < 21:
        return None
    daily_returns = closes.pct_change().dropna()
    if len(daily_returns) < 20:
        return None
    rv20 = daily_returns.tail(20).std(ddof=1) * math.sqrt(252) * 100
    if pd.isna(rv20) or rv20 <= 0:
        return None
    return round(float(rv20), 2)


# ── Stage 1: Fundamental filter ───────────────────────────────────────────────

def apply_fundamental_filter(
    tickers: list[str],
    params: ScannerParams,
) -> tuple[list[str], list[dict]]:
    """Apply market-cap, price, beta gates; capture IV.

    Reads from the local universe_fundamentals table when available.
    Falls back to live yfinance calls if the store is empty.
    """
    # ── Try local store first ─────────────────────────────────────────────
    store_rows = get_all_fundamentals()
    store_lookup = {r["symbol"]: r for r in store_rows}
    use_store = len(store_lookup) >= 50  # sanity: need a reasonable fill

    if use_store:
        logger.info(
            "Fundamental filter: using local store (%d tickers available)",
            len(store_lookup),
        )
        return _fundamental_filter_from_store(tickers, params, store_lookup)

    logger.info("Fundamental filter: local store empty — falling back to yfinance")
    return _fundamental_filter_from_yfinance(tickers, params)


def _fundamental_filter_from_store(
    tickers: list[str],
    params: ScannerParams,
    store_lookup: dict[str, dict],
) -> tuple[list[str], list[dict]]:
    """Filter fundamentals from the pre-populated local store."""
    passing_tickers: list[str] = []
    fundamental_rows: list[dict] = []

    for symbol in tickers:
        row = store_lookup.get(symbol)
        if row is None:
            continue

        market_cap_b = row.get("market_cap_b") or 0.0
        price = row.get("price") or 0.0
        beta = row.get("beta")
        iv_pct = row.get("iv_pct")

        if market_cap_b <= params.min_market_cap_b:
            continue
        if price <= 0 or price >= params.max_price:
            continue
        if beta is None or not (params.min_beta <= beta <= params.max_beta):
            continue

        passing_tickers.append(symbol)
        fundamental_rows.append({
            "symbol":       symbol,
            "market_cap_b": round(market_cap_b, 2),
            "price":        round(price, 2),
            "beta":         round(beta, 2),
            "iv":           iv_pct,
        })

    logger.info("Fundamental filter (store): %d/%d tickers passed", len(passing_tickers), len(tickers))
    return passing_tickers, fundamental_rows


def _fundamental_filter_from_yfinance(
    tickers: list[str],
    params: ScannerParams,
) -> tuple[list[str], list[dict]]:
    """Fallback: fetch fundamentals live from yfinance (slow, per-ticker)."""
    passing_tickers: list[str] = []
    fundamental_rows: list[dict] = []

    total_batches = math.ceil(len(tickers) / _INFO_BATCH_SIZE)
    logger.info("Fundamental filter (yfinance fallback): %d tickers, %d batches", len(tickers), total_batches)

    for batch_idx, batch_start in enumerate(range(0, len(tickers), _INFO_BATCH_SIZE)):
        batch = tickers[batch_start : batch_start + _INFO_BATCH_SIZE]
        logger.debug("Fundamental batch %d/%d (%d tickers)", batch_idx + 1, total_batches, len(batch))

        for symbol in batch:
            try:
                info = yf.Ticker(symbol).info
                if info.get("quoteType", "").upper() != "EQUITY":
                    continue

                market_cap_b = (_to_float(info.get("marketCap")) or 0.0) / 1e9
                price = _to_float(info.get("currentPrice") or info.get("regularMarketPrice")) or 0.0
                beta  = _to_float(info.get("beta"))

                if market_cap_b <= params.min_market_cap_b:
                    continue
                if price <= 0 or price >= params.max_price:
                    continue
                if beta is None or not (params.min_beta <= beta <= params.max_beta):
                    continue

                iv_raw = _to_float(info.get("impliedVolatility"))
                iv_pct = round(iv_raw * 100, 2) if iv_raw is not None else None

                passing_tickers.append(symbol)
                fundamental_rows.append({
                    "symbol":       symbol,
                    "market_cap_b": round(market_cap_b, 2),
                    "price":        round(price, 2),
                    "beta":         round(beta, 2),
                    "iv":           iv_pct,
                })
            except Exception as exc:
                logger.warning("Fundamental fetch failed for %s: %s", symbol, exc)

        if batch_start + _INFO_BATCH_SIZE < len(tickers):
            time.sleep(_INFO_BATCH_SLEEP_S)

    logger.info("Fundamental filter (yfinance): %d/%d tickers passed", len(passing_tickers), len(tickers))
    return passing_tickers, fundamental_rows


# ── Stage 2: Volatility filter ────────────────────────────────────────────────

def apply_vol_filter(
    fundamental_rows: list[dict],
    params: ScannerParams,
) -> tuple[list[str], list[dict]]:
    """IV ≥ min_vol_pct primary gate; RV-20 fallback if IV is None.
    RV-20 always computed for the IV/RV ratio.

    Reads OHLCV from the local store when available, falls back to yfinance.
    """
    passing_tickers: list[str] = []
    passing_rows: list[dict] = []

    logger.info("Vol filter: %d tickers — IV primary (≥%.0f%%), RV-20 fallback", len(fundamental_rows), params.min_vol_pct)

    for row in fundamental_rows:
        symbol = row["symbol"]
        iv     = row.get("iv")

        rv20: float | None = None
        try:
            # Try local store first (only need ~25 bars for RV-20)
            hist = get_ohlcv(symbol, lookback_days=30)
            if hist.empty:
                # Fallback to yfinance
                hist = yf.Ticker(symbol).history(period="1mo")
            if not hist.empty:
                rv20 = _calculate_rv20(hist)
        except Exception as exc:
            logger.warning("RV-20 fetch failed for %s: %s", symbol, exc)

        row["rv20"] = rv20
        row["iv_rv_ratio"] = (
            round(iv / rv20, 3) if (iv is not None and rv20 is not None and rv20 > 0) else None
        )

        if iv is not None:
            if iv >= params.min_vol_pct:
                row["vol_gate"] = "iv"
                passing_tickers.append(symbol)
                passing_rows.append(row)
            else:
                row["vol_gate"] = None
        else:
            if rv20 is not None and rv20 >= params.min_vol_pct:
                row["vol_gate"] = "rv_fallback"
                passing_tickers.append(symbol)
                passing_rows.append(row)
            else:
                row["vol_gate"] = None

    logger.info(
        "Vol filter: %d/%d passed (IV: %d, RV fallback: %d)",
        len(passing_tickers), len(fundamental_rows),
        sum(1 for r in passing_rows if r.get("vol_gate") == "iv"),
        sum(1 for r in passing_rows if r.get("vol_gate") == "rv_fallback"),
    )
    return passing_tickers, passing_rows


# ── Stage 3: Technical conditions filter ─────────────────────────────────────

def _compute_technical_indicators(symbol: str, hist: pd.DataFrame) -> dict | None:
    """Compute all indicators needed for the condition checks from a price history df.

    Requires at least 200 bars for SMA-200. Called only when conditions are active.
    Returns None if history is insufficient.
    """
    try:
        import pandas_ta as ta  # noqa: F401
        if hist.empty or len(hist) < 50:
            return None

        hist = hist.copy()
        if hist.index.tz is not None:
            hist.index = hist.index.tz_convert(None)

        close = hist["Close"]
        last_price = float(close.iloc[-1])

        # SMAs
        sma20  = float(ta.sma(close, length=20).iloc[-1])  if len(hist) >= 20  else None
        sma50  = float(ta.sma(close, length=50).iloc[-1])  if len(hist) >= 50  else None
        sma200 = float(ta.sma(close, length=200).iloc[-1]) if len(hist) >= 200 else None

        # Bollinger Bands (20-period, 2σ)
        bb_lower: float | None = None
        bb_pct_from_lower: float | None = None
        if len(hist) >= 20:
            bbands = ta.bbands(close, length=20, std=2)
            if bbands is not None and not bbands.empty:
                lower_col = [c for c in bbands.columns if "LB" in c or "lower" in c.lower() or "BBL" in c]
                if lower_col:
                    bb_lower = float(bbands[lower_col[0]].iloc[-1])
                    bb_pct_from_lower = round(((last_price - bb_lower) / bb_lower) * 100, 2) if bb_lower and bb_lower > 0 else None

        # RSI(14)
        rsi: float | None = None
        if len(hist) >= 15:
            rsi_series = ta.rsi(close, length=14)
            if rsi_series is not None and not rsi_series.empty:
                rsi = float(rsi_series.iloc[-1])

        return {
            "price":               round(last_price, 2),
            "sma20":               round(sma20, 2)  if sma20  is not None else None,
            "sma50":               round(sma50, 2)  if sma50  is not None else None,
            "sma200":              round(sma200, 2) if sma200 is not None else None,
            "bb_lower":            round(bb_lower, 2) if bb_lower is not None else None,
            "bb_pct_from_lower":   bb_pct_from_lower,
            "rsi":                 round(rsi, 2) if rsi is not None else None,
        }
    except Exception as exc:
        logger.warning("Technical indicators failed for %s: %s", symbol, exc)
        return None


def _check_conditions(indicators: dict, conditions: list[str]) -> tuple[bool, dict[str, bool]]:
    """Evaluate all active conditions against computed indicators.

    Returns (all_passed, per_condition_results).
    If a required indicator is None (insufficient history) the condition
    evaluates to False — the ticker is dropped. This ensures conditions
    actually filter rather than silently passing everything.
    Unknown condition IDs are also treated as False (fail-safe).
    """
    results: dict[str, bool] = {}

    p     = indicators.get("price")
    s20   = indicators.get("sma20")
    s50   = indicators.get("sma50")
    s200  = indicators.get("sma200")
    bb_d  = indicators.get("bb_pct_from_lower")   # % above lower band
    rsi   = indicators.get("rsi")

    for cond in conditions:
        if cond == "sma50_above_sma200":
            results[cond] = bool(s50 is not None and s200 is not None and s50 > s200)
        elif cond == "sma50_above_sma20":
            results[cond] = bool(s50 is not None and s20 is not None and s50 > s20)
        elif cond == "sma20_above_sma50":
            results[cond] = bool(s20 is not None and s50 is not None and s20 > s50)
        elif cond == "price_above_sma50":
            results[cond] = bool(p is not None and s50 is not None and p > s50)
        elif cond == "price_above_sma200":
            results[cond] = bool(p is not None and s200 is not None and p > s200)
        elif cond == "price_below_sma50":
            results[cond] = bool(p is not None and s50 is not None and p < s50)
        elif cond == "price_near_lower_bb":
            # Within 2% above the lower band
            results[cond] = bool(bb_d is not None and 0.0 <= bb_d <= 2.0)
        elif cond == "rsi_oversold_bounce":
            results[cond] = bool(rsi is not None and 30.0 <= rsi <= 45.0)
        else:
            logger.warning("Unknown condition id '%s' — treating as failed", cond)
            results[cond] = False  # unknown condition — fail-safe

    all_passed = all(results.values())
    return all_passed, results


def apply_technical_conditions(
    vol_rows: list[dict],
    conditions: list[str],
) -> tuple[list[str], list[dict]]:
    """Apply all active stacked technical conditions.

    Reads OHLCV from the local store (2y of data). Falls back to yfinance
    if a ticker has no local data.
    """
    if not conditions:
        # No conditions active — pass everyone through
        tickers = [r["symbol"] for r in vol_rows]
        for row in vol_rows:
            row["technical_conditions"] = {}
        return tickers, vol_rows

    passing_tickers: list[str] = []
    passing_rows: list[dict] = []

    logger.info("Technical conditions filter: %d tickers, %d active conditions: %s",
                len(vol_rows), len(conditions), conditions)

    for row in vol_rows:
        symbol = row["symbol"]

        # Try local store first (504 trading days ≈ 2y)
        hist = get_ohlcv(symbol, lookback_days=504)
        if hist.empty:
            # Fallback to yfinance
            try:
                hist = yf.Ticker(symbol).history(period="2y")
            except Exception as exc:
                logger.warning("History fetch failed for %s — excluding from results: %s", symbol, exc)
                row["technical_conditions"] = {}
                continue

        indicators = _compute_technical_indicators(symbol, hist)
        if indicators is None:
            logger.debug("Insufficient history for %s — excluding from results", symbol)
            row["technical_indicators"] = None
            row["technical_conditions"] = {}
            continue

        all_passed, results = _check_conditions(indicators, conditions)
        row["technical_indicators"] = indicators
        row["technical_conditions"] = results

        if all_passed:
            passing_tickers.append(symbol)
            passing_rows.append(row)
        else:
            failed = [k for k, v in results.items() if not v]
            logger.debug("Conditions failed for %s: %s", symbol, failed)

    logger.info(
        "Technical conditions: %d/%d tickers passed",
        len(passing_tickers), len(vol_rows),
    )
    return passing_tickers, passing_rows


# ── Main scanner entry point ──────────────────────────────────────────────────

def run_csp_scan(params: ScannerParams | None = None) -> dict:
    """Full pipeline: universe → fundamental → vol → technical conditions → options screen.

    Reads from the local OHLCV/fundamentals store when populated, falling back
    to live yfinance calls when the store is empty. Includes a staleness warning
    if the local data is more than 48 hours old.

    Returns a dict ready for JSON serialisation.
    """
    if params is None:
        params = ScannerParams()

    # Ensure store tables exist (no-op if already created)
    ensure_tables()

    # Check store freshness
    store_status = get_store_status()
    data_warnings: list[str] = []
    if store_status["is_stale"]:
        hours = store_status.get("stale_hours", "?")
        data_warnings.append(
            f"Local market data is {hours}h old (>48h). "
            f"Run a data refresh for accurate results."
        )
        logger.warning("Local store is stale (%.1fh old)", store_status.get("stale_hours", 0))

    # 1. Universe
    sp500_tickers  = fetch_sp500_tickers()
    nasdaq_tickers = fetch_nasdaq100_tickers()
    universe       = sorted(set(sp500_tickers) | set(nasdaq_tickers))
    logger.info("CSP scan started — universe: %d tickers, params: %s", len(universe), asdict(params))

    empty_summary = {
        "sp500_count":               len(sp500_tickers),
        "nasdaq100_count":           len(nasdaq_tickers),
        "combined_unique":           len(universe),
        "fundamental_passed":        0,
        "vol_passed":                0,
        "technical_passed":          0,
        "options_screener_returned": 0,
    }

    # 2. Fundamental filter
    fundamental_passing, fundamental_rows = apply_fundamental_filter(universe, params)
    if not fundamental_passing:
        logger.warning("No tickers passed fundamental filter")
        return {"candidates": [], "filter_summary": empty_summary, "fundamental_data": [], "warnings": data_warnings}

    # 3. Vol filter
    vol_passing, vol_rows = apply_vol_filter(fundamental_rows, params)
    if not vol_passing:
        logger.warning("No tickers passed vol filter")
        return {
            "candidates": [],
            "filter_summary": {**empty_summary, "fundamental_passed": len(fundamental_passing)},
            "fundamental_data": fundamental_rows,
            "warnings": data_warnings,
        }

    # 4. Technical conditions (only runs if conditions are selected)
    tech_passing, tech_rows = apply_technical_conditions(vol_rows, params.conditions)
    if not tech_passing:
        logger.warning("No tickers passed technical conditions filter")
        return {
            "candidates": [],
            "filter_summary": {
                **empty_summary,
                "fundamental_passed": len(fundamental_passing),
                "vol_passed": len(vol_passing),
            },
            "fundamental_data": vol_rows,
            "warnings": data_warnings,
        }

    # 5. Options screener
    logger.info("Passing %d tickers to CSP options screener", len(tech_passing))
    candidates = screen_csp_candidates(tickers=tech_passing)

    filter_summary: FilterSummary = {
        "sp500_count":               len(sp500_tickers),
        "nasdaq100_count":           len(nasdaq_tickers),
        "combined_unique":           len(universe),
        "fundamental_passed":        len(fundamental_passing),
        "vol_passed":                len(vol_passing),
        "technical_passed":          len(tech_passing),
        "options_screener_returned": len(candidates),
    }

    logger.info(
        "CSP scan complete — %d universe → %d fundamental → %d vol → %d technical → %d candidates",
        len(universe), len(fundamental_passing), len(vol_passing), len(tech_passing), len(candidates),
    )

    return {
        "candidates":      candidates,
        "filter_summary":  filter_summary,
        "fundamental_data": tech_rows,
        "params":          asdict(params),
        "data_source":     "local_store" if store_status["fundamentals_count"] >= 50 else "yfinance_live",
        "warnings":        data_warnings,
    }
