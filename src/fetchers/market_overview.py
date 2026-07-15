"""Live market overview fetcher — sectors, VIX, GEX, and breadth."""

from __future__ import annotations

import asyncio
import csv
import logging
from io import StringIO

import httpx
import yfinance as yf

from ..config import settings
from .thematic_etf import BASKET_THEMES, SINGLE_TICKER_THEMES

logger = logging.getLogger(__name__)

GEX_CSV_URL = "https://squeezemetrics.com/monitor/static/DIX.csv"

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLP": "Cons. Staples",
    "XLY": "Cons. Discret.",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}


DEFENSIVE = {"XLU", "XLP", "XLV", "XLRE"}
CYCLICAL = {"XLK", "XLY", "XLF", "XLI", "XLE", "XLB", "XLC"}


def _pct_change(series, lookback: int) -> float | None:
    """Percentage change from `lookback` bars ago to the last bar.

    Returns None if insufficient data.
    """
    if len(series) < lookback + 1:
        return None
    prev = float(series.iloc[-(lookback + 1)])
    curr = float(series.iloc[-1])
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def _gex_bucket(value_b: float) -> tuple[str, str]:
    """Return (label, bucket) for a GEX value in billions."""
    if value_b < 0:
        return "Negative — High volatility risk", "negative"
    if value_b < 3:
        return "Low Positive — Muted hedging", "low"
    if value_b < 7:
        return "Moderate Positive — Normal pinning", "moderate"
    if value_b < 12:
        return "High Positive — Strong pinning", "high"
    return "Extreme — Max pinning, expect low realized vol", "extreme"


def _gex_trend(current_b: float, avg_b: float) -> str:
    """Return Rising / Falling / Flat based on current vs 20d average."""
    if avg_b == 0:
        return "Flat"
    diff_pct = (current_b - avg_b) / abs(avg_b)
    if diff_pct > 0.1:
        return "Rising"
    if diff_pct < -0.1:
        return "Falling"
    return "Flat"


_YF_RETRIES = 2
_YF_RETRY_BACKOFF_S = 1.5


async def _download_with_retry(*args, **kwargs):
    """Retry a yf.download call a couple of times before giving up.

    Yahoo Finance intermittently drops tickers or errors out entirely under
    rate limiting; a short retry with backoff self-heals most of these without
    adding meaningful latency to the request.
    """
    last_exc: Exception | None = None
    for attempt in range(_YF_RETRIES + 1):
        try:
            return await asyncio.to_thread(yf.download, *args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < _YF_RETRIES:
                logger.warning(
                    "market_overview: yf.download failed (attempt %d/%d), retrying: %s",
                    attempt + 1, _YF_RETRIES + 1, exc,
                )
                await asyncio.sleep(_YF_RETRY_BACKOFF_S * (attempt + 1))
    raise last_exc


async def _fetch_sectors() -> tuple[dict, str | None]:
    tickers = list(SECTOR_ETFS.keys())
    raw = await _download_with_retry(
        " ".join(tickers),
        period="30d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )
    result = {}
    for ticker, name in SECTOR_ETFS.items():
        try:
            closes = raw[ticker]["Close"].dropna()
        except (KeyError, TypeError):
            continue
        if closes.empty:
            continue
        result[ticker] = {
            "name": name,
            "pct_1d": _pct_change(closes, 1),
            "pct_1w": _pct_change(closes, 5),
            "pct_1m": _pct_change(closes, 21),
        }

    # Compute rotation label
    defensive_1d = [
        v["pct_1d"] for k, v in result.items() if k in DEFENSIVE and v["pct_1d"] is not None
    ]
    cyclical_1d = [
        v["pct_1d"] for k, v in result.items() if k in CYCLICAL and v["pct_1d"] is not None
    ]

    if defensive_1d and cyclical_1d:
        def_avg = sum(defensive_1d) / len(defensive_1d)
        cyc_avg = sum(cyclical_1d) / len(cyclical_1d)
        if cyc_avg > def_avg + 0.1:
            rotation = "Risk-on (cyclical leading)"
        elif def_avg > cyc_avg + 0.1:
            rotation = "Risk-off (defensive leading)"
        else:
            rotation = "Neutral (no clear rotation)"
    else:
        rotation = None

    return result, rotation


async def _fetch_vix() -> dict:
    raw = await _download_with_retry(
        "^VIX ^VIX3M",
        period="10d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )
    try:
        vix_closes = raw["^VIX"]["Close"].dropna()
        vix3m_closes = raw["^VIX3M"]["Close"].dropna()
    except KeyError as exc:
        raise ValueError(f"VIX data missing from download: {exc}") from exc
    if vix_closes.empty or vix3m_closes.empty:
        raise ValueError("VIX or VIX3M returned no data")

    spot = round(float(vix_closes.iloc[-1]), 2)
    vix3m_val = round(float(vix3m_closes.iloc[-1]), 2)
    spread = round(float(vix3m_closes.iloc[-1]) - float(vix_closes.iloc[-1]), 2)

    if spread > 0.25:
        term_structure = "Contango"
        stress_note = "normal, calm"
    elif spread < -0.25:
        term_structure = "Backwardation"
        stress_note = "elevated stress"
    else:
        term_structure = "Flat"
        stress_note = "transitioning"

    return {
        "spot": spot,
        "pct_1d": _pct_change(vix_closes, 1),
        "pct_1w": _pct_change(vix_closes, 5),
        "vix3m": vix3m_val,
        "term_structure": term_structure,
        "spread": spread,
        "stress_note": stress_note,
    }


async def _fetch_gex() -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(GEX_CSV_URL)
        resp.raise_for_status()
        csv_text = resp.text

    rows = list(csv.reader(StringIO(csv_text)))
    data_rows = rows[1:]  # skip header
    if not data_rows:
        raise ValueError("GEX CSV is empty")

    gex_values: list[float] = []
    for row in data_rows:
        try:
            gex_values.append(float(row[3]))
        except (ValueError, IndexError):
            continue

    if not gex_values:
        raise ValueError("No GEX values parsed from CSV")

    current_b = round(gex_values[-1] / 1e9, 2)
    last_20 = gex_values[-20:]
    rolling_b = round((sum(last_20) / len(last_20)) / 1e9, 2)

    label, bucket = _gex_bucket(current_b)
    trend = _gex_trend(current_b, rolling_b)

    return {
        "value_b": current_b,
        "rolling_20d_avg_b": rolling_b,
        "trend": trend,
        "label": label,
        "bucket": bucket,
    }


def _fetch_breadth() -> dict | None:
    import sqlite3

    db_path = settings.db_path
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute("""
                WITH ranked AS (
                    SELECT symbol,
                           close,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn,
                           COUNT(*) OVER (PARTITION BY symbol) AS total_rows
                    FROM universe_daily_ohlcv
                ),
                ma AS (
                    SELECT symbol,
                           AVG(close) FILTER (WHERE rn <= 200) AS ma200,
                           MAX(close) FILTER (WHERE rn = 1)    AS latest_close,
                           MAX(total_rows)                      AS total_rows
                    FROM ranked
                    GROUP BY symbol
                )
                SELECT symbol,
                       CASE WHEN latest_close > ma200 THEN 1 ELSE 0 END AS above
                FROM ma
                WHERE total_rows >= 200
            """).fetchall()

            qualifying = len(rows)
            if qualifying < 50:
                logger.warning(
                    "Breadth: only %d tickers with 200d history, returning None", qualifying
                )
                return None

            above = sum(r[1] for r in rows)
            pct_above = round(above / qualifying * 100, 1)

            latest_date = conn.execute("SELECT MAX(date) FROM universe_daily_ohlcv").fetchone()[0]
            prev_rows = conn.execute(
                "SELECT DISTINCT date FROM universe_daily_ohlcv"
                " WHERE date < ? ORDER BY date DESC LIMIT 1",
                (latest_date,),
            ).fetchone()
            if not prev_rows:
                return {
                    "pct_above_200ma": pct_above,
                    "advancing": 0,
                    "declining": 0,
                    "ad_ratio": None,
                }
            prev_date = prev_rows[0]

            today_prices = dict(
                conn.execute(
                    "SELECT symbol, close FROM universe_daily_ohlcv WHERE date = ?",
                    (latest_date,),
                ).fetchall()
            )
            yesterday_prices = dict(
                conn.execute(
                    "SELECT symbol, close FROM universe_daily_ohlcv WHERE date = ?",
                    (prev_date,),
                ).fetchall()
            )

            advancing = 0
            declining = 0
            for sym in today_prices:
                if sym not in yesterday_prices:
                    continue
                if today_prices[sym] > yesterday_prices[sym]:
                    advancing += 1
                elif today_prices[sym] < yesterday_prices[sym]:
                    declining += 1

            ad_ratio = round(advancing / declining, 2) if declining > 0 else None

            return {
                "pct_above_200ma": pct_above,
                "advancing": advancing,
                "declining": declining,
                "ad_ratio": ad_ratio,
            }
    except Exception as exc:
        logger.warning("Breadth fetch failed: %s", exc)
        return None


_THEME_CHUNK_SIZE = 6


def _chunk_tickers(items: list[str], size: int) -> list[list[str]]:
    """Split into chunks of `size`, merging a trailing remainder of exactly 1
    into the prior chunk.

    yfinance returns a flat, non-ticker-grouped DataFrame for single-ticker
    downloads (group_by='ticker' only takes effect with 2+ tickers), which
    the per-ticker extraction below can't parse — so no chunk may end up
    with exactly one ticker.
    """
    chunks = [items[i:i + size] for i in range(0, len(items), size)]
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        chunks[-2].extend(chunks.pop())
    return chunks


async def _fetch_themes() -> dict:
    all_tickers = list(SINGLE_TICKER_THEMES.values()) + [
        t for tickers in BASKET_THEMES.values() for t in tickers
    ]
    chunks = _chunk_tickers(all_tickers, _THEME_CHUNK_SIZE)
    chunk_results = await asyncio.gather(
        *[
            _download_with_retry(
                " ".join(chunk), period="30d", group_by="ticker",
                progress=False, auto_adjust=True,
            )
            for chunk in chunks
        ],
        return_exceptions=True,
    )

    closes: dict[str, object] = {}
    for chunk, result in zip(chunks, chunk_results):
        if isinstance(result, Exception):
            logger.warning("Thematic ETF: chunk %s failed: %s", chunk, result)
            continue
        for ticker in chunk:
            try:
                series = result[ticker]["Close"].dropna()
            except (KeyError, TypeError):
                continue
            if not series.empty:
                closes[ticker] = series

    def _extract(ticker: str) -> dict | None:
        series = closes.get(ticker)
        if series is None:
            return None
        return {
            "pct_1d": _pct_change(series, 1),
            "pct_1w": _pct_change(series, 5),
            "pct_1m": _pct_change(series, 21),
        }

    singles: dict[str, dict] = {}
    for label, ticker in SINGLE_TICKER_THEMES.items():
        data = _extract(ticker)
        if data is not None:
            singles[label] = {"ticker": ticker, **data}

    baskets: dict[str, dict] = {}
    for label, tickers in BASKET_THEMES.items():
        ticker_data: dict[str, dict] = {}
        for t in tickers:
            data = _extract(t)
            if data is not None:
                ticker_data[t] = data
        if not ticker_data:
            continue

        def _avg(key: str, td: dict = ticker_data) -> float | None:
            vals = [v[key] for v in td.values() if v.get(key) is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        baskets[label] = {
            "tickers": ticker_data,
            "avg_1d": _avg("pct_1d"),
            "avg_1w": _avg("pct_1w"),
            "avg_1m": _avg("pct_1m"),
        }

    return {"singles": singles, "baskets": baskets}


_INDEPENDENT_FETCH_FIELDS = ("sectors", "vix", "gex", "breadth", "themes")


def has_partial_failure(data: dict) -> bool:
    """Return True if any independent sub-fetch in a market-overview payload failed.

    Excludes 'rotation', which is derived from sectors and can legitimately
    be None (no clear defensive/cyclical split) even when every fetch succeeds.

    A `sectors` dict with fewer than all expected tickers also counts as a
    partial failure — _fetch_sectors() skips individual tickers it can't parse
    out of the batch download rather than failing the whole fetch, so a
    short-staffed sectors payload wouldn't otherwise be caught here.
    """
    if any(data.get(field) is None for field in _INDEPENDENT_FETCH_FIELDS):
        return True
    sectors = data.get("sectors")
    if isinstance(sectors, dict) and len(sectors) < len(SECTOR_ETFS):
        return True
    return False


async def fetch_market_overview() -> dict:
    sectors_res, vix_res, gex_res, breadth_res, themes_res = await asyncio.gather(
        _fetch_sectors(),
        _fetch_vix(),
        _fetch_gex(),
        asyncio.to_thread(_fetch_breadth),
        _fetch_themes(),
        return_exceptions=True,
    )

    def _unwrap(res, name: str):
        if isinstance(res, Exception):
            logger.warning("market_overview: %s fetch failed: %s", name, res)
            return None
        return res

    sectors_raw = _unwrap(sectors_res, "sectors")
    if isinstance(sectors_raw, tuple):
        sectors, rotation = sectors_raw
    elif sectors_raw is None:
        sectors, rotation = None, None
    else:
        sectors, rotation = sectors_raw, None

    vix = _unwrap(vix_res, "vix")
    gex = _unwrap(gex_res, "gex")
    breadth = _unwrap(breadth_res, "breadth")
    themes = _unwrap(themes_res, "themes")

    return {
        "sectors": sectors,
        "rotation": rotation,
        "vix": vix,
        "gex": gex,
        "breadth": breadth,
        "themes": themes,
    }
