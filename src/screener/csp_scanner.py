"""CSP Scanner — broad universe screener for Cash-Secured Put candidates.

Fetches the S&P 500 + NASDAQ 100 constituent lists, applies fundamental and
volatility pre-filters, then hands qualifying tickers to the existing
screen_csp_candidates() options screening pipeline.

Index universe source
---------------------
We use the ``yfinance`` ``Ticker.info`` approach on well-known index ETFs
(^GSPC components via SPY, QQQ) as a fallback, but the most reliable
zero-dependency approach for constituent lists is the S&P 500 Wikipedia
table (which IS in static HTML, identified by id="constituents") combined
with a hardcoded NASDAQ-100 fetch via the NASDAQ website CSV export.

Pre-filter criteria
-------------------
  • Index membership : S&P 500 or NASDAQ 100
  • Quote type       : EQUITY only (ETFs excluded)
  • Market cap       : > $10B
  • Price            : < $150
  • Beta             : 0.8 – 2.4
  • Volatility gate  : IV ≥ 30% (primary); falls back to RV-20 ≥ 30% if IV unavailable

Both IV and RV-20 are always computed and surfaced in the response so callers
can inspect the IV/RV ratio (vol premium).
"""

from __future__ import annotations

import logging
import math
import time
from typing import TypedDict

import pandas as pd
import yfinance as yf

from .options import screen_csp_candidates

logger = logging.getLogger(__name__)

# ── Pre-filter constants ──────────────────────────────────────────────────────

MIN_MARKET_CAP_B  = 10.0    # $10 billion
MAX_PRICE         = 150.0   # $150 per share
MIN_BETA          = 0.8
MAX_BETA          = 2.4
MIN_VOL_PCT       = 30.0    # ≥ 30% — applied to IV first, RV-20 fallback

# yfinance informal rate-limit: batch info calls and sleep between batches
_INFO_BATCH_SIZE    = 50
_INFO_BATCH_SLEEP_S = 1.0   # seconds between fundamental + IV batches

# Source URLs
# S&P 500: Wikipedia static HTML table (id="constituents") — confirmed static
# NASDAQ-100: NASDAQ.com CSV export — no JS rendering required
_SP500_WIKI_URL     = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NASDAQ100_CSV_URL  = "https://raw.githubusercontent.com/datasets/nasdaq-listings/master/data/nasdaq-listed.csv"

# We use the official NASDAQ API endpoint for NDX constituents instead.
# This returns a JSON payload of current index members.
_NASDAQ100_API_URL  = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"

_WIKI_USER_AGENT = "Mozilla/5.0 (compatible; MarketIntelligenceBot/1.0; +https://github.com/)"
_NASDAQ_HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


# ── Type helpers ──────────────────────────────────────────────────────────────

class FilterSummary(TypedDict):
    sp500_count: int
    nasdaq100_count: int
    combined_unique: int
    fundamental_passed: int
    vol_passed: int
    options_screener_returned: int


# ── Index constituent fetchers ────────────────────────────────────────────────

def fetch_sp500_tickers() -> list[str]:
    """Fetch the current S&P 500 constituent list.

    Source: Wikipedia static HTML. We parse all tables and find the one
    containing a 'Symbol' or 'Ticker' column rather than relying on a
    specific table id, which changes across Wikipedia page revisions.
    """
    try:
        import requests as _requests
        resp = _requests.get(
            _SP500_WIKI_URL,
            headers={"User-Agent": _WIKI_USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        import io
        tables = pd.read_html(io.StringIO(resp.text))
        logger.info("S&P 500 Wikipedia: found %d tables", len(tables))

        # Find the constituent table: must have a symbol-like column and
        # enough rows to plausibly be a 500-stock list (>400)
        df = next(
            (
                t for t in tables
                if any(str(c).strip().lower() in ("symbol", "ticker") for c in t.columns)
                and len(t) > 400
            ),
            None,
        )
        if df is None:
            # Fallback: just take the largest table that has a symbol-like column
            candidates = [
                t for t in tables
                if any(str(c).strip().lower() in ("symbol", "ticker") for c in t.columns)
            ]
            df = max(candidates, key=len) if candidates else None

        if df is None:
            logger.error("S&P 500: could not locate constituent table")
            return []

        ticker_col = next(
            (c for c in df.columns if str(c).strip().lower() in ("symbol", "ticker")), None
        )
        tickers = (
            df[ticker_col]
            .dropna()
            .astype(str)
            .str.upper()
            .str.strip()
            .str.replace(".", "-", regex=False)
            .tolist()
        )
        logger.info("Fetched %d S&P 500 tickers", len(tickers))
        return tickers
    except Exception as exc:
        logger.error("Failed to fetch S&P 500 tickers: %s", exc, exc_info=True)
        return []


def fetch_nasdaq100_tickers() -> list[str]:
    """Fetch the current NASDAQ-100 constituent list.

    Source: Official NASDAQ API endpoint which returns JSON — no HTML/JS
    rendering involved. Falls back to [] on any error.
    """
    try:
        import requests as _requests
        resp = _requests.get(_NASDAQ100_API_URL, headers=_NASDAQ_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # Response shape: {"data": {"data": {"rows": [{"symbol": "AAPL", ...}, ...]}}}
        rows = (
            data.get("data", {})
                .get("data", {})
                .get("rows", [])
        )
        if not rows:
            logger.error("NASDAQ-100 API returned no rows. Full response keys: %s", list(data.keys()))
            return []
        tickers = [
            str(r.get("symbol", "")).upper().strip().replace(".", "-")
            for r in rows
            if r.get("symbol")
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
    logger.info(
        "Universe: %d S&P 500 + %d NASDAQ 100 = %d unique tickers",
        len(sp500), len(nasdaq), len(combined),
    )
    return combined


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(value: object) -> float | None:
    """Safely coerce loosely-typed yfinance values to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _calculate_rv20(hist: pd.DataFrame) -> float | None:
    """Compute 20-day annualized realized volatility from daily close-to-close returns.

    Returns annualized vol as a percentage (e.g. 35.2 means 35.2%), or None if
    there is insufficient price history.
    """
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


# ── Stage 1: fundamental filter (market cap, price, beta) + IV collection ────

def apply_fundamental_filter(
    tickers: list[str],
) -> tuple[list[str], list[dict]]:
    """Fetch fundamentals and IV for every ticker; return those passing the
    market-cap / price / beta criteria.

    IV (``impliedVolatility`` from yfinance ``Ticker.info``) is captured here
    at no extra cost — it lives in the same ``info`` dict as market cap and
    beta. It is stored in ``fundamental_rows`` for use in the downstream
    volatility filter, avoiding a second round of ``info`` calls.

    Note: yfinance's ``impliedVolatility`` field is the 30-day at-the-money
    implied vol computed by Yahoo Finance. It is frequently ``None`` outside
    U.S. market hours or for thinly-traded names; the vol filter handles this
    with an RV-20 fallback.

    Returns
    -------
    passing_tickers : list[str]
    fundamental_rows : list[dict]
        One entry per passing ticker containing:
        ``symbol``, ``market_cap_b``, ``price``, ``beta``, ``iv``
    """
    passing_tickers: list[str] = []
    fundamental_rows: list[dict] = []

    total_batches = math.ceil(len(tickers) / _INFO_BATCH_SIZE)
    logger.info(
        "Fundamental filter: %d tickers, %d batches of %d",
        len(tickers), total_batches, _INFO_BATCH_SIZE,
    )

    for batch_idx, batch_start in enumerate(range(0, len(tickers), _INFO_BATCH_SIZE)):
        batch = tickers[batch_start : batch_start + _INFO_BATCH_SIZE]
        logger.debug("Fundamental batch %d/%d (%d tickers)", batch_idx + 1, total_batches, len(batch))

        for symbol in batch:
            try:
                info = yf.Ticker(symbol).info

                # Skip non-equity instruments (ETFs, trusts, preferred shares)
                if info.get("quoteType", "").upper() != "EQUITY":
                    logger.debug("Skip %s — quoteType=%s", symbol, info.get("quoteType"))
                    continue

                market_cap_b = (_to_float(info.get("marketCap")) or 0.0) / 1e9
                price = _to_float(info.get("currentPrice") or info.get("regularMarketPrice")) or 0.0
                beta  = _to_float(info.get("beta"))

                # ── Criteria ──────────────────────────────────────────────────
                if market_cap_b <= MIN_MARKET_CAP_B:
                    logger.debug("Filter %s: market_cap=%.1fB", symbol, market_cap_b)
                    continue
                if price <= 0 or price >= MAX_PRICE:
                    logger.debug("Filter %s: price=%.2f", symbol, price)
                    continue
                if beta is None or not (MIN_BETA <= beta <= MAX_BETA):
                    logger.debug("Filter %s: beta=%s", symbol, beta)
                    continue

                # yfinance returns IV as a decimal (0.35 = 35%); convert to pct
                iv_raw = _to_float(info.get("impliedVolatility"))
                iv_pct = round(iv_raw * 100, 2) if iv_raw is not None else None

                passing_tickers.append(symbol)
                fundamental_rows.append({
                    "symbol":       symbol,
                    "market_cap_b": round(market_cap_b, 2),
                    "price":        round(price, 2),
                    "beta":         round(beta, 2),
                    "iv":           iv_pct,   # may be None — vol filter handles it
                })

            except Exception as exc:
                logger.warning("Fundamental fetch failed for %s: %s", symbol, exc)

        if batch_start + _INFO_BATCH_SIZE < len(tickers):
            time.sleep(_INFO_BATCH_SLEEP_S)

    logger.info(
        "Fundamental filter: %d/%d tickers passed",
        len(passing_tickers), len(tickers),
    )
    return passing_tickers, fundamental_rows


# ── Stage 2: volatility filter (IV primary, RV-20 fallback) ──────────────────

def apply_vol_filter(
    fundamental_rows: list[dict],
) -> tuple[list[str], list[dict]]:
    """Apply the volatility gate and compute IV/RV-20 for every ticker.

    Gate logic (per ticker):
      1. If ``iv`` is present and ≥ MIN_VOL_PCT  → PASS  (IV gate)
      2. If ``iv`` is None, compute RV-20:
           • RV-20 ≥ MIN_VOL_PCT                 → PASS  (RV fallback)
           • RV-20 < MIN_VOL_PCT                 → FAIL
      3. If ``iv`` is present but < MIN_VOL_PCT  → FAIL
         (We could also compute RV here to check for a divergence, but we
          don't want to pass a low-IV ticker just because it had a historic
          vol spike — IV is the signal to sell premium against.)

    RV-20 is always fetched for IV-gated tickers too, purely so the
    IV/RV ratio can be surfaced in the API response and UI.

    Mutates ``fundamental_rows`` in place, adding:
      ``rv20``        — 20-day annualized realized vol (%)
      ``iv_rv_ratio`` — IV / RV-20 (>1 = elevated premium; None if either missing)
      ``vol_gate``    — ``"iv"`` | ``"rv_fallback"`` (which signal passed the gate)

    Returns
    -------
    passing_tickers : list[str]
    passing_rows    : list[dict]  (subset of fundamental_rows that passed)
    """
    passing_tickers: list[str] = []
    passing_rows: list[dict] = []

    logger.info(
        "Vol filter: %d tickers — IV primary (≥%.0f%%), RV-20 fallback",
        len(fundamental_rows), MIN_VOL_PCT,
    )

    for row in fundamental_rows:
        symbol = row["symbol"]
        iv     = row.get("iv")   # pct or None

        # Always fetch price history for RV-20 — needed for ratio even if IV passes
        rv20: float | None = None
        try:
            hist = yf.Ticker(symbol).history(period="1mo")
            if not hist.empty:
                rv20 = _calculate_rv20(hist)
        except Exception as exc:
            logger.warning("RV-20 fetch failed for %s: %s", symbol, exc)

        row["rv20"] = rv20
        row["iv_rv_ratio"] = (
            round(iv / rv20, 3) if (iv is not None and rv20 is not None and rv20 > 0)
            else None
        )

        # ── Gate ──────────────────────────────────────────────────────────────
        if iv is not None:
            if iv >= MIN_VOL_PCT:
                row["vol_gate"] = "iv"
                passing_tickers.append(symbol)
                passing_rows.append(row)
                logger.debug("Pass %s via IV gate: iv=%.1f%%", symbol, iv)
            else:
                row["vol_gate"] = None
                logger.debug("Filter %s: iv=%.1f%% < %.0f%%", symbol, iv, MIN_VOL_PCT)
        else:
            # IV unavailable — fall back to RV-20
            if rv20 is not None and rv20 >= MIN_VOL_PCT:
                row["vol_gate"] = "rv_fallback"
                passing_tickers.append(symbol)
                passing_rows.append(row)
                logger.debug("Pass %s via RV fallback: rv20=%.1f%%", symbol, rv20)
            else:
                row["vol_gate"] = None
                logger.debug(
                    "Filter %s: iv=None, rv20=%s (min %.0f%%)",
                    symbol, rv20, MIN_VOL_PCT,
                )

    logger.info(
        "Vol filter: %d/%d tickers passed (IV gate: %d, RV fallback: %d)",
        len(passing_tickers),
        len(fundamental_rows),
        sum(1 for r in passing_rows if r.get("vol_gate") == "iv"),
        sum(1 for r in passing_rows if r.get("vol_gate") == "rv_fallback"),
    )
    return passing_tickers, passing_rows


# ── Main scanner entry point ──────────────────────────────────────────────────

def run_csp_scan() -> dict:
    """Full pipeline: universe → fundamental filter → vol filter → options screen.

    Returns a dict ready for direct JSON serialisation:

    {
        "candidates":      list[dict],     # from screen_csp_candidates()
        "filter_summary":  FilterSummary,
        "fundamental_data": list[dict],    # one row per vol-passing ticker with all
                                           # filter metrics (iv, rv20, iv_rv_ratio…)
    }
    """
    # 1. Universe ──────────────────────────────────────────────────────────────
    sp500_tickers  = fetch_sp500_tickers()
    nasdaq_tickers = fetch_nasdaq100_tickers()
    universe       = sorted(set(sp500_tickers) | set(nasdaq_tickers))
    logger.info("CSP scan started — universe: %d tickers", len(universe))

    # 2. Fundamental filter ────────────────────────────────────────────────────
    fundamental_passing, fundamental_rows = apply_fundamental_filter(universe)

    if not fundamental_passing:
        logger.warning("No tickers passed fundamental filter")
        return {
            "candidates": [],
            "filter_summary": {
                "sp500_count":               len(sp500_tickers),
                "nasdaq100_count":           len(nasdaq_tickers),
                "combined_unique":           len(universe),
                "fundamental_passed":        0,
                "vol_passed":                0,
                "options_screener_returned": 0,
            },
            "fundamental_data": [],
        }

    # 3. Volatility filter (IV primary, RV-20 fallback) ────────────────────────
    vol_passing, vol_rows = apply_vol_filter(fundamental_rows)

    if not vol_passing:
        logger.warning("No tickers passed vol filter")
        return {
            "candidates": [],
            "filter_summary": {
                "sp500_count":               len(sp500_tickers),
                "nasdaq100_count":           len(nasdaq_tickers),
                "combined_unique":           len(universe),
                "fundamental_passed":        len(fundamental_passing),
                "vol_passed":                0,
                "options_screener_returned": 0,
            },
            "fundamental_data": fundamental_rows,
        }

    # 4. Options screener ──────────────────────────────────────────────────────
    logger.info("Passing %d tickers to CSP options screener", len(vol_passing))
    candidates = screen_csp_candidates(tickers=vol_passing)

    filter_summary: FilterSummary = {
        "sp500_count":               len(sp500_tickers),
        "nasdaq100_count":           len(nasdaq_tickers),
        "combined_unique":           len(universe),
        "fundamental_passed":        len(fundamental_passing),
        "vol_passed":                len(vol_passing),
        "options_screener_returned": len(candidates),
    }

    logger.info(
        "CSP scan complete — funnel: %d universe → %d fundamental → %d vol → %d candidates",
        len(universe), len(fundamental_passing), len(vol_passing), len(candidates),
    )

    return {
        "candidates":      candidates,
        "filter_summary":  filter_summary,
        "fundamental_data": vol_rows,
    }
