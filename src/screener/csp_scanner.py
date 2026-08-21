"""CSP Scanner — broad universe screener for Cash-Secured Put candidates.

Fetches the S&P 500, NASDAQ 100, and NASDAQ large-cap (≥$2B) constituent lists, applies fundamental,
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
import re
import time
from dataclasses import asdict, dataclass, field
from typing import TypedDict

import pandas as pd
import yfinance as yf

from ..db import get_watchlist
from ..indicators import compute_adr20_pct
from ..market_data.store import (
    ensure_tables,
    get_all_fundamentals,
    get_ohlcv,
    get_store_status,
)
from ._yf_timeout import call_with_timeout
from .options import screen_csp_candidates

logger = logging.getLogger(__name__)

# ── Default pre-filter values ─────────────────────────────────────────────────

DEFAULT_MIN_MARKET_CAP_B = 10.0
DEFAULT_MAX_PRICE        = 150.0
DEFAULT_MIN_BETA         = 0.8
DEFAULT_MAX_BETA         = 2.4
DEFAULT_MIN_VOL_PCT      = 30.0
DEFAULT_MAX_RSI          = 50.0
DEFAULT_MIN_DTE          = 3
DEFAULT_MAX_DTE          = 46
DEFAULT_MIN_ADX          = 15.0
DEFAULT_MAX_ADX          = 50.0

DEFAULT_MIN_FCF_B:           float | None = 0.0
DEFAULT_MAX_DEBT_TO_EQUITY:  float | None = 2.0
DEFAULT_MIN_REVENUE_GROWTH:  float | None = -0.10
DEFAULT_MIN_EARNINGS_GROWTH: float | None = None
DEFAULT_MIN_DIVIDEND_YIELD:  float | None = None

# yfinance informal rate-limit
_INFO_BATCH_SIZE    = 50
_INFO_BATCH_SLEEP_S = 1.0

# Source URLs
_SP500_WIKI_URL        = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NASDAQ100_API_URL     = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
_NASDAQ_SCREENER_URL   = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange=nasdaq"
_NYSE_SCREENER_URL     = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange=nyse"
_NASDAQ_LARGE_CAP_MIN_B = 2.0

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
        "id": "sma50_above_sma150",
        "label": "50 SMA > 150 SMA",
        "description": "Medium-term above long-term MA — healthy trend structure (20 > 50 > 150 > 200 is the full bullish stack)",
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
        "label": "Below BB Midline",
        "description": "Price below Bollinger Band midline (20 SMA) — includes below lower band; broad mean-reversion setup",
        "group": "Bollinger Bands",
    },
    {
        "id": "price_below_sma50",
        "label": "Price < 50 SMA",
        "description": "Price pulled back below medium-term MA — potential CSP entry on weakness",
        "group": "Price vs MA",
    },
    {
        "id": "price_below_sma200_and_sma50_below_sma200",
        "label": "Price & 50 SMA below 200 SMA",
        "description": "Price AND 50-day SMA are both below the 200-day SMA (bearish trend)",
        "group": "Trend",
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
    max_rsi:          float = DEFAULT_MAX_RSI
    min_dte:          int   = DEFAULT_MIN_DTE
    max_dte:          int   = DEFAULT_MAX_DTE
    min_adx:          float = DEFAULT_MIN_ADX
    max_adx:          float = DEFAULT_MAX_ADX
    min_fcf_b:           float | None = DEFAULT_MIN_FCF_B
    max_debt_to_equity:  float | None = DEFAULT_MAX_DEBT_TO_EQUITY
    min_revenue_growth:  float | None = DEFAULT_MIN_REVENUE_GROWTH
    min_earnings_growth: float | None = DEFAULT_MIN_EARNINGS_GROWTH
    min_dividend_yield:  float | None = DEFAULT_MIN_DIVIDEND_YIELD
    max_forward_pe:          float | None = None
    max_peg_ratio:           float | None = None
    min_gross_margin:        float | None = None
    min_interest_coverage:   float | None = None
    max_dividend_yield:      float | None = None   # e.g. 0.025 → 2.5%
    rv20_max:                float | None = None   # e.g. 45.0 (annualised %)
    bb_width_pct_min:        float | None = None   # e.g. 4.0
    bb_width_pct_max:        float | None = None   # e.g. 21.0
    volume_ratio_max:        float | None = None   # e.g. 1.15 (ratio vs 20d avg)
    pct_from_52wk_high_max:  float | None = None   # e.g. 12.0 (% below 52wk high)
    adr20_pct_max:           float | None = None   # e.g. 4.0
    price_vs_ema200_pct_min: float | None = None   # e.g. 5.0 (% above EMA200)
    # Sorted list of active condition IDs — order doesn't affect logic
    conditions: list[str] = field(default_factory=list)
    restrict_to_watchlist_universe: bool = False
    # When True, scan only the user's CSP watchlist — skip the broad
    # S&P 500 / NASDAQ 100 / large-cap universe entirely.
    watchlist_only: bool = False
    # Sector filter — empty list means no filter (all sectors pass)
    sectors: list[str] = field(default_factory=list)

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
        max_rsi: float | None = None,
        min_dte: int | None = None,
        max_dte: int | None = None,
        min_adx: float | None = None,
        max_adx: float | None = None,
        conditions: str | None = None,
        min_fcf_b: float | None = DEFAULT_MIN_FCF_B,
        max_debt_to_equity: float | None = DEFAULT_MAX_DEBT_TO_EQUITY,
        min_revenue_growth: float | None = DEFAULT_MIN_REVENUE_GROWTH,
        min_earnings_growth: float | None = DEFAULT_MIN_EARNINGS_GROWTH,
        min_dividend_yield: float | None = DEFAULT_MIN_DIVIDEND_YIELD,
        max_forward_pe: float | None = None,
        max_peg_ratio: float | None = None,
        min_gross_margin: float | None = None,
        min_interest_coverage: float | None = None,
        max_dividend_yield: float | None = None,
        rv20_max: float | None = None,
        bb_width_pct_min: float | None = None,
        bb_width_pct_max: float | None = None,
        volume_ratio_max: float | None = None,
        pct_from_52wk_high_max: float | None = None,
        adr20_pct_max: float | None = None,
        price_vs_ema200_pct_min: float | None = None,
        restrict_to_watchlist_universe: bool = False,
        watchlist_only: bool = False,
        sectors: str | None = None,
    ) -> ScannerParams:
        """Build ScannerParams from API query parameters (all optional)."""
        parsed_conditions: list[str] = []
        if conditions:
            parsed_conditions = [
                c.strip() for c in conditions.split(",")
                if c.strip() in _CONDITION_IDS
            ]
        parsed_sectors: list[str] = [s.strip() for s in sectors.split(",") if s.strip()] if sectors else []
        return cls(
            min_market_cap_b = min_cap   if min_cap   is not None else DEFAULT_MIN_MARKET_CAP_B,
            max_price        = max_price if max_price is not None else DEFAULT_MAX_PRICE,
            min_beta         = min_beta  if min_beta  is not None else DEFAULT_MIN_BETA,
            max_beta         = max_beta  if max_beta  is not None else DEFAULT_MAX_BETA,
            min_vol_pct      = min_vol   if min_vol   is not None else DEFAULT_MIN_VOL_PCT,
            max_rsi          = max_rsi   if max_rsi   is not None else DEFAULT_MAX_RSI,
            min_dte          = int(min_dte) if min_dte is not None else DEFAULT_MIN_DTE,
            max_dte          = int(max_dte) if max_dte is not None else DEFAULT_MAX_DTE,
            min_adx          = min_adx   if min_adx   is not None else DEFAULT_MIN_ADX,
            max_adx          = max_adx   if max_adx   is not None else DEFAULT_MAX_ADX,
            conditions       = sorted(parsed_conditions),
            min_fcf_b            = min_fcf_b,
            max_debt_to_equity   = max_debt_to_equity,
            min_revenue_growth   = min_revenue_growth,
            min_earnings_growth  = min_earnings_growth,
            min_dividend_yield   = min_dividend_yield,
            max_forward_pe       = max_forward_pe,
            max_peg_ratio        = max_peg_ratio,
            min_gross_margin      = min_gross_margin,
            min_interest_coverage = min_interest_coverage,
            max_dividend_yield   = max_dividend_yield,
            rv20_max             = rv20_max,
            bb_width_pct_min     = bb_width_pct_min,
            bb_width_pct_max     = bb_width_pct_max,
            volume_ratio_max     = volume_ratio_max,
            pct_from_52wk_high_max  = pct_from_52wk_high_max,
            adr20_pct_max        = adr20_pct_max,
            price_vs_ema200_pct_min = price_vs_ema200_pct_min,
            restrict_to_watchlist_universe = restrict_to_watchlist_universe,
            watchlist_only   = watchlist_only,
            sectors          = sorted(parsed_sectors),
        )


# ── Type helpers ──────────────────────────────────────────────────────────────

class FilterSummary(TypedDict):
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


def fetch_nasdaq_large_cap_tickers() -> list[str]:
    """Fetch all NASDAQ-listed stocks with market cap ≥ $2B from the NASDAQ screener API."""
    try:
        import requests as _requests
        resp = _requests.get(_NASDAQ_SCREENER_URL, headers=_NASDAQ_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", {}).get("table", {}).get("rows", [])
        if not rows:
            logger.error("NASDAQ screener API returned no rows. Keys: %s", list(data.get("data", {}).keys()))
            return []

        tickers: list[str] = []
        min_cap = _NASDAQ_LARGE_CAP_MIN_B * 1e9
        for r in rows:
            sym = str(r.get("symbol", "")).upper().strip().replace(".", "-")
            if not sym:
                continue
            try:
                cap = float(str(r.get("marketCap") or "0").replace(",", ""))
            except (TypeError, ValueError):
                continue
            if cap >= min_cap:
                tickers.append(sym)

        tickers = sorted(set(tickers))
        logger.info(
            "Fetched %d NASDAQ large-cap tickers (>=$%.0fB) from NASDAQ screener",
            len(tickers), _NASDAQ_LARGE_CAP_MIN_B,
        )
        return tickers
    except Exception as exc:
        logger.error("Failed to fetch NASDAQ large-cap tickers: %s", exc, exc_info=True)
        return []


def fetch_nyse_large_cap_tickers() -> list[str]:
    """Fetch all NYSE-listed stocks with market cap ≥ $2B from the NASDAQ screener API."""
    try:
        import requests as _requests
        resp = _requests.get(_NYSE_SCREENER_URL, headers=_NASDAQ_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", {}).get("table", {}).get("rows", [])
        if not rows:
            logger.error("NYSE screener API returned no rows. Keys: %s", list(data.get("data", {}).keys()))
            return []

        tickers: list[str] = []
        min_cap = _NASDAQ_LARGE_CAP_MIN_B * 1e9
        for r in rows:
            sym = str(r.get("symbol", "")).upper().strip().replace(".", "-")
            if not sym:
                continue
            try:
                cap = float(str(r.get("marketCap") or "0").replace(",", ""))
            except (TypeError, ValueError):
                continue
            if cap >= min_cap:
                tickers.append(sym)

        tickers = sorted(set(tickers))
        logger.info(
            "Fetched %d NYSE large-cap tickers (>=$%.0fB) from NYSE screener",
            len(tickers), _NASDAQ_LARGE_CAP_MIN_B,
        )
        return tickers
    except Exception as exc:
        logger.error("Failed to fetch NYSE large-cap tickers: %s", exc, exc_info=True)
        return []


def fetch_universe() -> list[str]:
    """Return the deduplicated union of S&P 500, NASDAQ 100, NASDAQ large-cap, and NYSE large-cap tickers."""
    sp500        = fetch_sp500_tickers()
    nasdaq100    = fetch_nasdaq100_tickers()
    nasdaq_large = fetch_nasdaq_large_cap_tickers()
    nyse_large   = fetch_nyse_large_cap_tickers()
    combined = sorted(set(sp500) | set(nasdaq100) | set(nasdaq_large) | set(nyse_large))
    logger.info(
        "Universe: %d S&P500 + %d NDX100 + %d NASDAQ≥$2B + %d NYSE≥$2B = %d unique",
        len(sp500), len(nasdaq100), len(nasdaq_large), len(nyse_large), len(combined),
    )
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

    if not use_store:
        logger.info("Fundamental filter: local store empty — falling back to yfinance")
        return _fundamental_filter_from_yfinance(tickers, params)

    store_tickers   = [t for t in tickers if t in store_lookup]
    missing_tickers = [t for t in tickers if t not in store_lookup]

    logger.info(
        "Fundamental filter: using local store (%d tickers available, %d store hits, %d misses)",
        len(store_lookup), len(store_tickers), len(missing_tickers),
    )

    passing_tickers, fundamental_rows = _fundamental_filter_from_store(store_tickers, params, store_lookup)

    if missing_tickers:
        logger.info(
            "Fundamental filter: %d tickers missing from store — fetching live from yfinance",
            len(missing_tickers),
        )
        miss_pass, miss_rows = _fundamental_filter_from_yfinance(missing_tickers, params)
        passing_tickers.extend(miss_pass)
        fundamental_rows.extend(miss_rows)

    return passing_tickers, fundamental_rows


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

        # The user's own CSP watchlist is a deliberate, curated ticker list —
        # skip every universe/fundamentals screening gate below and let the
        # tickers through as-is to the vol/technical stages.
        if not params.watchlist_only:
            # Universe membership gate
            if params.restrict_to_watchlist_universe:
                universes = row.get("universes") or ""
                if "sp500" not in universes and "nasdaq100" not in universes:
                    continue

            if market_cap_b <= params.min_market_cap_b:
                continue
            if price <= 0 or price >= params.max_price:
                continue
            if beta is None or not (params.min_beta <= beta <= params.max_beta):
                continue

            # FCF gate (stored in billions)
            fcf = row.get("fcf")
            if params.min_fcf_b is not None and fcf is not None:
                if fcf < params.min_fcf_b:
                    continue

            # Debt-to-equity gate
            debt_to_equity = row.get("debt_to_equity")
            if params.max_debt_to_equity is not None and debt_to_equity is not None:
                if debt_to_equity > params.max_debt_to_equity:
                    continue

            # Revenue growth gate
            revenue_growth = row.get("revenue_growth")
            if params.min_revenue_growth is not None and revenue_growth is not None:
                if revenue_growth < params.min_revenue_growth:
                    continue

            # Earnings growth gate
            earnings_growth = row.get("earnings_growth")
            if params.min_earnings_growth is not None and earnings_growth is not None:
                if earnings_growth < params.min_earnings_growth:
                    continue

            # Dividend yield gates (min and max)
            dividend_yield = row.get("dividend_yield")
            if params.min_dividend_yield is not None and dividend_yield is not None:
                if dividend_yield < params.min_dividend_yield:
                    continue
            if params.max_dividend_yield is not None and dividend_yield is not None:
                if dividend_yield > params.max_dividend_yield:
                    continue

            # Forward PE gate
            forward_pe = row.get("forward_pe")
            if params.max_forward_pe is not None and forward_pe is not None:
                if forward_pe > params.max_forward_pe:
                    continue

            # PEG ratio gate
            peg_ratio = row.get("peg_ratio")
            if params.max_peg_ratio is not None and peg_ratio is not None:
                if peg_ratio > params.max_peg_ratio:
                    continue

            # Gross margin gate
            gross_margin = row.get("gross_margin")
            if params.min_gross_margin is not None and gross_margin is not None:
                if gross_margin < params.min_gross_margin:
                    continue

            # Interest coverage gate
            interest_coverage = row.get("interest_coverage")
            if params.min_interest_coverage is not None and interest_coverage is not None:
                if interest_coverage < params.min_interest_coverage:
                    continue

            # Sector filter
            if params.sectors:
                row_sector = row.get("sector") or ""
                if row_sector not in params.sectors:
                    continue

        passing_tickers.append(symbol)
        fundamental_rows.append({
            "symbol":       symbol,
            "market_cap_b": round(market_cap_b, 2),
            "price":        round(price, 2),
            "beta":         round(beta, 2) if beta is not None else None,
            "iv":           iv_pct,
            "fcf":          row.get("fcf"),
            "forward_pe":   row.get("forward_pe"),
            "peg_ratio":    row.get("peg_ratio"),
            "gross_margin":      row.get("gross_margin"),
            "interest_coverage": row.get("interest_coverage"),
            "sector":       row.get("sector"),
        })

    logger.info("Fundamental filter (store): %d/%d tickers passed", len(passing_tickers), len(tickers))
    return passing_tickers, fundamental_rows


def _fundamental_filter_from_yfinance(
    tickers: list[str],
    params: ScannerParams,
) -> tuple[list[str], list[dict]]:
    """Fallback: fetch fundamentals live from yfinance (slow, per-ticker)."""
    if params.restrict_to_watchlist_universe:
        logger.warning(
            "restrict_to_watchlist_universe=True has no effect in the yfinance fallback path "
            "(no universe membership data available without a populated local store)"
        )

    passing_tickers: list[str] = []
    fundamental_rows: list[dict] = []

    total_batches = math.ceil(len(tickers) / _INFO_BATCH_SIZE)
    logger.info("Fundamental filter (yfinance fallback): %d tickers, %d batches", len(tickers), total_batches)
    logger.warning("Balance-sheet gates (FCF, D/E, growth, yield) are not applied in the yfinance fallback path")

    for batch_idx, batch_start in enumerate(range(0, len(tickers), _INFO_BATCH_SIZE)):
        batch = tickers[batch_start : batch_start + _INFO_BATCH_SIZE]
        logger.debug("Fundamental batch %d/%d (%d tickers)", batch_idx + 1, total_batches, len(batch))

        for symbol in batch:
            try:
                info = call_with_timeout(lambda: yf.Ticker(symbol).info, label=f"info:{symbol}")
                if info is None:
                    continue
                if info.get("quoteType", "").upper() != "EQUITY":
                    continue

                market_cap_b = (_to_float(info.get("marketCap")) or 0.0) / 1e9
                price = _to_float(info.get("currentPrice") or info.get("regularMarketPrice")) or 0.0
                beta  = _to_float(info.get("beta"))

                # See _fundamental_filter_from_store: the user's own CSP
                # watchlist is curated on purpose — don't second-guess it here.
                if not params.watchlist_only:
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
                    "beta":         round(beta, 2) if beta is not None else None,
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
                hist = call_with_timeout(
                    lambda: yf.Ticker(symbol).history(period="1mo"), label=f"history:{symbol}"
                )
            if hist is not None and not hist.empty:
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
        sma150 = float(ta.sma(close, length=150).iloc[-1]) if len(hist) >= 150 else None
        sma200 = float(ta.sma(close, length=200).iloc[-1]) if len(hist) >= 200 else None

        # EMA 200 and price distance from it
        ema200: float | None = None
        price_vs_ema200_pct: float | None = None
        if len(hist) >= 200:
            ema200_series = ta.ema(close, length=200)
            if ema200_series is not None and not ema200_series.empty:
                ema200 = float(ema200_series.iloc[-1])
                if ema200 and ema200 > 0:
                    price_vs_ema200_pct = round((last_price - ema200) / ema200 * 100, 2)

        # Bollinger Bands (20-period, 2σ)
        bb_lower: float | None = None
        bb_upper: float | None = None
        bb_mid: float | None = None
        bb_pct_from_lower: float | None = None
        bb_width_pct: float | None = None
        if len(hist) >= 20:
            bbands = ta.bbands(close, length=20, std=2)
            if bbands is not None and not bbands.empty:
                lower_col = next((c for c in bbands.columns if "BBL" in c), None)
                upper_col = next((c for c in bbands.columns if "BBU" in c), None)
                mid_col   = next((c for c in bbands.columns if "BBM" in c), None)
                if lower_col:
                    bb_lower = float(bbands[lower_col].iloc[-1])
                    bb_pct_from_lower = (
                        round((last_price - bb_lower) / bb_lower * 100, 2)
                        if bb_lower and bb_lower > 0 else None
                    )
                if upper_col:
                    bb_upper = float(bbands[upper_col].iloc[-1])
                if mid_col:
                    bb_mid = float(bbands[mid_col].iloc[-1])
                if bb_upper is not None and bb_lower is not None and bb_mid is not None and bb_mid > 0:
                    bb_width_pct = round((bb_upper - bb_lower) / bb_mid * 100, 2)

        # Volume ratio: today's volume vs 20-day average
        volume_ratio: float | None = None
        if "Volume" in hist.columns and len(hist) >= 21:
            vol = hist["Volume"]
            last_vol = float(vol.iloc[-1])
            avg_vol_20 = float(vol.iloc[-21:-1].mean())
            if avg_vol_20 > 0 and last_vol >= 0:
                volume_ratio = round(last_vol / avg_vol_20, 3)

        # % below 52-week high (positive = below high — used as a pct_from_52wk_high_max
        # gate threshold; this is the opposite sign convention from stocks.py's
        # chat-display-only field of the same name, which is negative-when-below).
        pct_from_52wk_high: float | None = None
        if len(hist) >= 252:
            high_52w = float(close.tail(252).max())
            if high_52w > 0:
                pct_from_52wk_high = round((high_52w - last_price) / high_52w * 100, 2)

        # ADR20 — average daily range as % of close
        adr20_pct: float | None = None
        if len(hist) >= 20:
            adr20_raw = compute_adr20_pct(hist["High"].iloc[-20:], hist["Low"].iloc[-20:], hist["Close"].iloc[-20:])
            adr20_pct = round(adr20_raw, 2) if adr20_raw is not None else None

        # RSI(14)
        rsi: float | None = None
        if len(hist) >= 15:
            rsi_series = ta.rsi(close, length=14)
            if rsi_series is not None and not rsi_series.empty:
                rsi = float(rsi_series.iloc[-1])

        # ADX(14) — needs High/Low/Close; 2× period (28 bars) to warm up
        adx: float | None = None
        if len(hist) >= 28:
            adx_df = ta.adx(hist["High"], hist["Low"], hist["Close"], length=14)
            if adx_df is not None and not adx_df.empty:
                adx_col = [c for c in adx_df.columns if re.match(r"ADX_\d+$", c.upper())]
                if adx_col:
                    adx = round(float(adx_df[adx_col[0]].iloc[-1]), 2)

        # ── 5-day return — used by options.py's pullback_mode gate ──────────────────
        return_5d: float | None = None
        if len(hist) >= 6:
            v = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100)
            return_5d = None if math.isnan(v) else v

        return {
            "price":                round(last_price, 2),
            "sma20":                round(sma20, 2)  if sma20  is not None else None,
            "sma50":                round(sma50, 2)  if sma50  is not None else None,
            "sma150":               round(sma150, 2) if sma150 is not None else None,
            "sma200":               round(sma200, 2) if sma200 is not None else None,
            "ema200":               round(ema200, 2) if ema200 is not None else None,
            "price_vs_ema200_pct":  price_vs_ema200_pct,
            "bb_lower":             round(bb_lower, 2) if bb_lower is not None else None,
            "bb_upper":             round(bb_upper, 2) if bb_upper is not None else None,
            "bb_width_pct":         bb_width_pct,
            "bb_pct_from_lower":    bb_pct_from_lower,
            "volume_ratio":         volume_ratio,
            "pct_from_52wk_high":   pct_from_52wk_high,
            "adr20_pct":            adr20_pct,
            "rsi":                  round(rsi, 2) if rsi is not None else None,
            "adx":                  adx,
            "return_5d":            return_5d,
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
    s150  = indicators.get("sma150")
    s200  = indicators.get("sma200")
    bb_d  = indicators.get("bb_pct_from_lower")   # % above lower band
    rsi   = indicators.get("rsi")

    for cond in conditions:
        if cond == "sma50_above_sma200":
            results[cond] = bool(s50 is not None and s200 is not None and s50 > s200)
        elif cond == "sma50_above_sma150":
            results[cond] = bool(s50 is not None and s150 is not None and s50 > s150)
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
            results[cond] = bool(p is not None and s20 is not None and p < s20)
        elif cond == "price_below_sma200_and_sma50_below_sma200":
            results[cond] = bool(
                p is not None and s50 is not None and s200 is not None
                and p < s200 and s50 < s200
            )
        else:
            logger.warning("Unknown condition id '%s' — treating as failed", cond)
            results[cond] = False  # unknown condition — fail-safe

    all_passed = all(results.values())
    return all_passed, results


def apply_technical_conditions(
    vol_rows: list[dict],
    params: ScannerParams,
) -> tuple[list[str], list[dict]]:
    """Apply stacked technical conditions plus RSI, ADX, and numeric range gates.

    Reads OHLCV from the local store (2y of data). Falls back to yfinance
    if a ticker has no local data.
    """
    conditions = params.conditions
    max_rsi    = params.max_rsi
    min_adx    = params.min_adx
    max_adx    = params.max_adx

    rsi_filter_active = max_rsi < 100.0
    adx_filter_active = min_adx > 0 or max_adx < 100.0

    # Numeric technical gates that require indicator computation
    numeric_tech_gates = any([
        params.rv20_max is not None,
        params.bb_width_pct_min is not None,
        params.bb_width_pct_max is not None,
        params.volume_ratio_max is not None,
        params.pct_from_52wk_high_max is not None,
        params.adr20_pct_max is not None,
        params.price_vs_ema200_pct_min is not None,
    ])

    needs_indicators = bool(conditions) or rsi_filter_active or adx_filter_active or numeric_tech_gates

    if not needs_indicators:
        tickers = [r["symbol"] for r in vol_rows]
        for row in vol_rows:
            row["technical_conditions"] = {}
        return tickers, vol_rows

    passing_tickers: list[str] = []
    passing_rows: list[dict] = []

    logger.info(
        "Technical filter: %d tickers, conditions=%s, RSI<%.0f, ADX %.0f-%.0f",
        len(vol_rows), conditions, max_rsi, min_adx, max_adx,
    )

    for row in vol_rows:
        symbol = row["symbol"]

        # rv20 is already in row from the vol-filter stage (annualised %)
        if params.rv20_max is not None:
            rv20_val = row.get("rv20")
            if rv20_val is not None and rv20_val > params.rv20_max:
                logger.debug("rv20_max failed for %s: %.1f > %.1f", symbol, rv20_val, params.rv20_max)
                row["technical_conditions"] = {}
                continue

        # Try local store first (504 trading days ≈ 2y)
        hist = get_ohlcv(symbol, lookback_days=504)
        if hist.empty:
            try:
                hist = call_with_timeout(
                    lambda: yf.Ticker(symbol).history(period="2y"), label=f"history:{symbol}"
                )
                if hist is None:
                    row["technical_conditions"] = {}
                    continue
            except Exception as exc:
                logger.warning("History fetch failed for %s — excluding: %s", symbol, exc)
                row["technical_conditions"] = {}
                continue

        indicators = _compute_technical_indicators(symbol, hist)
        if indicators is None:
            logger.debug("Insufficient history for %s — excluding", symbol)
            row["technical_indicators"] = None
            row["technical_conditions"] = {}
            continue

        all_passed, results = _check_conditions(indicators, conditions)

        rsi_passed = True
        if rsi_filter_active:
            rsi_val = indicators.get("rsi")
            rsi_passed = rsi_val is not None and rsi_val < max_rsi

        adx_passed = True
        if adx_filter_active:
            adx_val = indicators.get("adx")
            adx_passed = adx_val is not None and min_adx <= adx_val <= max_adx

        # Numeric range gates (None value = pass, consistent with _apply_criteria convention)
        failed_gates: list[str] = []

        def _gate_max(key: str, limit: float | None) -> bool:
            if limit is None:
                return True
            val = indicators.get(key)
            if val is None:
                return True   # NULL passes max gate
            if val > limit:
                failed_gates.append(f"{key}>{limit}")
                return False
            return True

        def _gate_min(key: str, limit: float | None) -> bool:
            if limit is None:
                return True
            val = indicators.get(key)
            if val is None:
                return False  # NULL fails min gate
            if val < limit:
                failed_gates.append(f"{key}<{limit}")
                return False
            return True

        numeric_passed = all([
            _gate_min("bb_width_pct",       params.bb_width_pct_min),
            _gate_max("bb_width_pct",       params.bb_width_pct_max),
            _gate_max("volume_ratio",        params.volume_ratio_max),
            _gate_max("pct_from_52wk_high",  params.pct_from_52wk_high_max),
            _gate_max("adr20_pct",           params.adr20_pct_max),
            _gate_min("price_vs_ema200_pct", params.price_vs_ema200_pct_min),
        ])

        row["technical_indicators"] = indicators
        row["technical_conditions"] = results

        if all_passed and rsi_passed and adx_passed and numeric_passed:
            passing_tickers.append(symbol)
            passing_rows.append(row)
        else:
            all_failed = [k for k, v in results.items() if not v]
            if not rsi_passed:
                all_failed.append(f"rsi_max({max_rsi})")
            if not adx_passed:
                all_failed.append(f"adx_range({min_adx}-{max_adx})")
            all_failed.extend(failed_gates)
            logger.debug("Technical failed for %s: %s", symbol, all_failed)

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
    if params.watchlist_only:
        # Skip the broad S&P 500 / NASDAQ 100 / large-cap pull entirely —
        # scan only the user's own CSP watchlist.
        universe = sorted(set(get_watchlist()))
        if not universe:
            logger.warning("watchlist_only=True but the CSP watchlist is empty")
            return {
                "candidates": [],
                "filter_summary": {
                    "combined_unique":           0,
                    "fundamental_passed":        0,
                    "vol_passed":                0,
                    "technical_passed":          0,
                    "options_screener_returned": 0,
                },
                "fundamental_data": [],
                "data_source": "watchlist_only",
                "warnings": [
                    "Your CSP watchlist is empty — add tickers to it (or turn off "
                    "\"Scan my CSP watchlist only\") before scanning.",
                ],
            }
    else:
        universe = fetch_universe()

        # Merge watchlist tickers so manually tracked stocks are always scanned
        watchlist = get_watchlist()
        watchlist_extras = [t for t in watchlist if t not in set(universe)]
        if watchlist_extras:
            logger.info("Injecting %d watchlist tickers not already in universe: %s", len(watchlist_extras), watchlist_extras)
            universe = sorted(set(universe) | set(watchlist_extras))

    logger.info("CSP scan started — universe: %d tickers, params: %s", len(universe), asdict(params))

    empty_summary = {
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
        return {
            "candidates": [],
            "filter_summary": empty_summary,
            "fundamental_data": [],
            "data_source": "local_store" if store_status["fundamentals_count"] >= 50 else "yfinance_live",
            "warnings": data_warnings,
        }

    # 3. Vol filter
    vol_passing, vol_rows = apply_vol_filter(fundamental_rows, params)
    if not vol_passing:
        logger.warning("No tickers passed vol filter")
        return {
            "candidates": [],
            "filter_summary": {**empty_summary, "fundamental_passed": len(fundamental_passing)},
            "fundamental_data": fundamental_rows,
            "data_source": "local_store" if store_status["fundamentals_count"] >= 50 else "yfinance_live",
            "warnings": data_warnings,
        }

    # 4. Technical conditions + RSI/ADX + numeric range gates
    tech_passing, tech_rows = apply_technical_conditions(vol_rows, params)
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
            "data_source": "local_store" if store_status["fundamentals_count"] >= 50 else "yfinance_live",
            "warnings": data_warnings,
        }

    # 5. Options screener
    logger.info("Passing %d tickers to CSP options screener", len(tech_passing))
    # Stage 3 already computed RSI/ADX/SMA50/etc. from the local OHLCV store —
    # reuse it instead of letting screen_csp_candidates() re-fetch live yfinance
    # history per ticker.
    precomputed_technicals = {
        row["symbol"]: row["technical_indicators"]
        for row in tech_rows
        if row.get("technical_indicators")
    }
    candidates = screen_csp_candidates(
        tickers=tech_passing,
        min_dte=params.min_dte,
        max_dte=params.max_dte,
        min_rsi=0.0,
        max_rsi=params.max_rsi,
        min_adx=params.min_adx,
        max_adx=params.max_adx,
        precomputed_technicals=precomputed_technicals,
    )

    # Merge fundamental fields (fcf, forward_pe) into each candidate
    fundamentals_by_symbol = {r["symbol"]: r for r in tech_rows}
    for c in candidates:
        fund = fundamentals_by_symbol.get(c["symbol"], {})
        c["fcf"]        = fund.get("fcf")
        c["forward_pe"] = fund.get("forward_pe")
        c["peg_ratio"]  = fund.get("peg_ratio")

    filter_summary: FilterSummary = {
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
