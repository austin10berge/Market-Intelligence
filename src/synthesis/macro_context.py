"""Shared macro context fetching — Exhibit 2C.

Fetches:
- SPY / VIX snapshot with moving average data
- Wikipedia "YEAR in the United States" current events
- Alpaca Data API news (primary; per-ticker and market-wide)
- Alpha Vantage macro news headlines (fallback; requires ALPHA_VANTAGE_API_KEY)

Used by both wheel_scorer.py (per-scan) and macro_note.py (monthly note).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import date

import yfinance as yf

logger = logging.getLogger(__name__)


# ── SPY / VIX snapshot ────────────────────────────────────────────────────────

def _pct(a: float, b: float) -> str:
    return f"{(a - b) / b * 100:+.2f}%"


def fetch_spy_vix_snapshot() -> dict:
    """Return a dict with SPY and VIX metrics for today."""
    result: dict = {}
    try:
        spy_hist = yf.Ticker("SPY").history(period="1y")
        if not spy_hist.empty:
            spy_close = spy_hist["Close"].dropna()
            price = float(spy_close.iloc[-1])
            result["spy_price"] = round(price, 2)
            if len(spy_close) >= 2:
                result["spy_1d_ret"] = _pct(spy_close.iloc[-1], spy_close.iloc[-2])
            if len(spy_close) >= 6:
                result["spy_5d_ret"] = _pct(spy_close.iloc[-1], spy_close.iloc[-6])
            if len(spy_close) >= 50:
                sma50 = float(spy_close.tail(50).mean())
                result["spy_sma50"] = round(sma50, 2)
                result["spy_vs_sma50"] = _pct(price, sma50)
            if len(spy_close) >= 200:
                sma200 = float(spy_close.tail(200).mean())
                result["spy_sma200"] = round(sma200, 2)
                result["spy_vs_sma200"] = _pct(price, sma200)
    except Exception as exc:
        logger.debug("SPY snapshot failed: %s", exc)

    try:
        vix_hist = yf.Ticker("^VIX").history(period="5d")
        if not vix_hist.empty:
            vix = float(vix_hist["Close"].iloc[-1])
            result["vix"] = round(vix, 1)
            if vix < 15:
                result["vix_regime"] = "low vol — favorable for premium selling"
            elif vix < 20:
                result["vix_regime"] = "normal — standard premium"
            elif vix < 25:
                result["vix_regime"] = "elevated — widen strikes, size down"
            else:
                result["vix_regime"] = "high vol / stress — defensive only"
    except Exception as exc:
        logger.debug("VIX snapshot failed: %s", exc)

    return result


def format_spy_vix_str(snapshot: dict) -> str:
    spy = snapshot.get("spy_price", "N/A")
    r1d = snapshot.get("spy_1d_ret", "N/A")
    r5d = snapshot.get("spy_5d_ret", "N/A")
    vs200 = snapshot.get("spy_vs_sma200", "N/A")
    vix = snapshot.get("vix", "N/A")
    regime = snapshot.get("vix_regime", "")
    return (
        f"SPY: ${spy} ({r1d} 1D, {r5d} 5D, {vs200} vs 200 SMA) | "
        f"VIX: {vix} — {regime}"
    )


# ── Wikipedia current events ──────────────────────────────────────────────────

def fetch_wiki_events(year: int | None = None) -> str:
    """Return intro text from Wikipedia 'YEAR in the United States'."""
    if year is None:
        year = date.today().year
    try:
        title = f"{year}_in_the_United_States"
        url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&titles={title}&prop=extracts"
            "&exintro=true&exsentences=12&format=json&redirects=1"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "MarketIntelligenceBot/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        pages = data.get("query", {}).get("pages", {})
        extract = next(iter(pages.values()), {}).get("extract", "")
        extract = re.sub(r"<[^>]+>", " ", extract)
        extract = re.sub(r"\s+", " ", extract).strip()

        # Also try the plain year article for global context
        global_title = str(year)
        url2 = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&titles={global_title}&prop=extracts"
            "&exintro=true&exsentences=6&format=json&redirects=1"
        )
        req2 = urllib.request.Request(url2, headers={"User-Agent": "MarketIntelligenceBot/1.0"})
        with urllib.request.urlopen(req2, timeout=12) as resp2:
            data2 = json.loads(resp2.read().decode())
        pages2 = data2.get("query", {}).get("pages", {})
        extract2 = next(iter(pages2.values()), {}).get("extract", "")
        extract2 = re.sub(r"<[^>]+>", " ", extract2)
        extract2 = re.sub(r"\s+", " ", extract2).strip()

        def _cut_at_sentence(text: str, max_chars: int) -> str:
            if len(text) <= max_chars:
                return text
            truncated = text[:max_chars]
            last = max(truncated.rfind(". "), truncated.rfind(".\n"))
            return (truncated[: last + 1] if last > 0 else truncated).strip()

        combined = ""
        if extract2:
            combined += _cut_at_sentence(extract2, 500) + "\n\n"
        if extract:
            combined += _cut_at_sentence(extract, 1000)
        return combined.strip()
    except Exception as exc:
        logger.debug("Wikipedia fetch failed: %s", exc)
        return ""


# ── Alpaca Data API news ──────────────────────────────────────────────────────

async def fetch_alpaca_ticker_news(symbol: str, limit: int = 8) -> str:
    """Return recent news headlines+summaries for a single ticker via Alpaca."""
    return await _fetch_alpaca_news(symbols=[symbol], limit=limit)


async def fetch_alpaca_macro_news(limit: int = 15) -> str:
    """Return recent market-wide news via Alpaca (SPY/QQQ/VXX/TLT basket)."""
    return await _fetch_alpaca_news(symbols=["SPY", "QQQ", "VXX", "TLT"], limit=limit)


async def _fetch_alpaca_news(symbols: list[str] | None, limit: int) -> str:
    """Shared Alpaca /v1beta1/news fetch. Returns empty string on any failure."""
    try:
        from ..config import settings

        key = getattr(settings, "alpaca_api_key", "")
        secret = getattr(settings, "alpaca_api_secret", "")
        if not key or not secret:
            return ""

        base_url = getattr(settings, "alpaca_data_url", "https://data.alpaca.markets")
        params: dict[str, str] = {"limit": str(limit), "sort": "desc"}
        if symbols:
            params["symbols"] = ",".join(symbols)

        url = f"{base_url}/v1beta1/news?" + urllib.parse.urlencode(params)
        headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=headers)
        raw = await asyncio.to_thread(
            lambda: urllib.request.urlopen(req, timeout=15).read()  # noqa: B023
        )
        articles = json.loads(raw.decode()).get("news", [])
        lines: list[str] = []
        for a in articles[:limit]:
            headline = a.get("headline", "")
            if not headline:
                continue
            summary = (a.get("summary") or "").strip()
            if summary and summary != headline:
                lines.append(f"• {headline} — {summary[:200]}")
            else:
                lines.append(f"• {headline}")
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("Alpaca news fetch failed (symbols=%s): %s", symbols, exc)
        return ""


# ── Alpha Vantage macro news ──────────────────────────────────────────────────

async def fetch_av_macro_news(limit: int = 15) -> str:
    """Return recent macro news headlines from Alpha Vantage (if key available)."""
    try:
        from ..config import settings

        key = getattr(settings, "alpha_vantage_api_key", None)
        if not key:
            return ""

        url = (
            "https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT&apikey={key}"
            f"&sort=LATEST&limit={limit}"
            "&topics=financial_markets,economy_monetary,economy_macro"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "MarketIntelligenceBot/1.0"})
        raw = await asyncio.to_thread(
            lambda: urllib.request.urlopen(req, timeout=15).read()  # noqa: B023
        )
        feed = json.loads(raw.decode()).get("feed", [])
        headlines = [item.get("title", "") for item in feed if item.get("title")]
        return "\n".join(f"• {h}" for h in headlines[:10])
    except Exception as exc:
        logger.debug("AV macro news fetch failed: %s", exc)
        return ""


# ── Combined macro context string ─────────────────────────────────────────────

async def build_macro_context_str() -> str:
    """Return a single combined macro context string for LLM prompts."""
    snapshot, wiki, alpaca_news, av_news = await asyncio.gather(
        asyncio.to_thread(fetch_spy_vix_snapshot),
        asyncio.to_thread(fetch_wiki_events),
        fetch_alpaca_macro_news(),
        fetch_av_macro_news(),
    )

    parts = [f"Market snapshot: {format_spy_vix_str(snapshot)}"]
    if wiki:
        parts.append(f"Current events ({date.today().year} US):\n{wiki}")
    # Alpaca is primary; AV fills in when Alpaca key is absent
    market_news = "\n".join(filter(None, [alpaca_news, av_news]))
    if market_news:
        parts.append(f"Recent market headlines:\n{market_news}")

    return "\n\n".join(parts)
