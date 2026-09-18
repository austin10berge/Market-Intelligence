"""Wheel candidate scorer — DeepSeek-style per-stock LLM scoring.

Mirrors the GPT Portfolio methodology (Exhibit 1-2C):
- Full yfinance Exhibit 2B financials per ticker
- Recent per-stock news headlines (yfinance)
- Macro context: SPY/VIX + Alpha Vantage macro news + Wikipedia current events
- Individual LLM call per stock (Claude CLI → Gemini fallback), parallelised
- Output: wheel suitability score 1-100 + short report per candidate
"""

from __future__ import annotations

import asyncio
import logging
import re

import yfinance as yf

from ..synthesis.llm import synthesize
from ..synthesis.macro_context import build_macro_context_str, fetch_alpaca_ticker_news

logger = logging.getLogger(__name__)

# Limit simultaneous LLM calls to avoid rate limits
_LLM_CONCURRENCY = 1

# ── System prompt (mirrors DeepSeek Exhibit 1 style) ─────────────────────────

_SYSTEM_PROMPT = """\
You are a professional options trader with deep expertise in the \
Cash-Secured Put and Covered Call Wheel strategy.
Speak in the third person. You do not mention your credentials.
You do not recommend alternatives.
Do not use the word "provided" — use "recent" or "latest" instead.
Do not speak directly to investors or recommend actions.\
"""

# ── Exhibit 2B: full yfinance financials ─────────────────────────────────────

_INFO_FIELDS: list[tuple[str, str]] = [
    # Price
    ("previousClose",                "Prev Close"),
    ("open",                         "Open"),
    ("dayLow",                       "Day Low"),
    ("dayHigh",                      "Day High"),
    ("fiftyTwoWeekLow",              "52W Low"),
    ("fiftyTwoWeekHigh",             "52W High"),
    ("fiftyDayAverage",              "50D MA"),
    ("twoHundredDayAverage",         "200D MA"),
    ("52WeekChange",                 "52W Chg%"),
    # Volume & liquidity
    ("volume",                       "Volume"),
    ("averageVolume",                "Avg Volume"),
    ("averageVolume10days",          "Avg Vol 10D"),
    ("bid",                          "Bid"),
    ("ask",                          "Ask"),
    # Market structure
    ("marketCap",                    "Mkt Cap"),
    ("enterpriseValue",              "EV"),
    ("floatShares",                  "Float"),
    ("sharesOutstanding",            "Shares Out"),
    # Valuation
    ("trailingPE",                   "P/E (TTM)"),
    ("forwardPE",                    "Fwd P/E"),
    ("priceToSalesTrailing12Months", "P/S (TTM)"),
    ("priceToBook",                  "P/B"),
    ("enterpriseToRevenue",          "EV/Revenue"),
    ("enterpriseToEbitda",           "EV/EBITDA"),
    ("pegRatio",                     "PEG"),
    # Dividends
    ("dividendYield",                "Div Yield"),
    ("trailingAnnualDividendRate",   "Div Rate (TTM)"),
    ("payoutRatio",                  "Payout Ratio"),
    ("fiveYearAvgDividendYield",     "5Y Avg Div Yield"),
    # Earnings & growth
    ("trailingEps",                  "EPS (TTM)"),
    ("forwardEps",                   "EPS (Fwd)"),
    ("earningsQuarterlyGrowth",      "Earnings Growth (Q)"),
    ("earningsGrowth",               "Earnings Growth"),
    ("revenueGrowth",                "Revenue Growth"),
    ("netIncomeToCommon",            "Net Income"),
    # Profitability
    ("profitMargins",                "Profit Margin"),
    ("grossMargins",                 "Gross Margin"),
    ("ebitdaMargins",                "EBITDA Margin"),
    ("operatingMargins",             "Op Margin"),
    ("returnOnAssets",               "ROA"),
    ("returnOnEquity",               "ROE"),
    ("ebitda",                       "EBITDA"),
    # Revenue
    ("totalRevenue",                 "Revenue"),
    ("revenuePerShare",              "Rev/Share"),
    # Cash flow
    ("freeCashflow",                 "FCF"),
    ("operatingCashflow",            "Op Cash Flow"),
    ("totalCash",                    "Cash"),
    ("totalCashPerShare",            "Cash/Share"),
    # Debt & liquidity
    ("totalDebt",                    "Total Debt"),
    ("debtToEquity",                 "D/E"),
    ("quickRatio",                   "Quick Ratio"),
    ("currentRatio",                 "Current Ratio"),
    # Book value
    ("bookValue",                    "Book Value/Share"),
    # Short interest
    ("sharesShort",                  "Shares Short"),
    ("sharesShortPriorMonth",        "Shares Short (Prior Mo)"),
    ("shortPercentOfFloat",          "Short % Float"),
    ("shortRatio",                   "Short Ratio"),
    # Ownership
    ("heldPercentInsiders",          "Insider Hold%"),
    ("heldPercentInstitutions",      "Inst Hold%"),
    # Analyst consensus
    ("targetHighPrice",              "Analyst High Target"),
    ("targetLowPrice",               "Analyst Low Target"),
    ("targetMeanPrice",              "Analyst Mean Target"),
    ("targetMedianPrice",            "Analyst Median Target"),
    ("recommendationMean",           "Rec Mean"),
    ("recommendationKey",            "Rec Key"),
    ("numberOfAnalystOpinions",      "Analyst Count"),
    # Governance risk
    ("auditRisk",                    "Audit Risk"),
    ("boardRisk",                    "Board Risk"),
    ("compensationRisk",             "Comp Risk"),
    ("shareHolderRightsRisk",        "SH Rights Risk"),
    ("overallRisk",                  "Overall Risk"),
    # Volatility
    ("beta",                         "Beta"),
]

_PCT_FIELDS = {
    "52WeekChange", "dividendYield", "payoutRatio",
    "earningsQuarterlyGrowth", "earningsGrowth", "revenueGrowth",
    "profitMargins", "grossMargins", "ebitdaMargins", "operatingMargins",
    "returnOnAssets", "returnOnEquity", "shortPercentOfFloat",
    "heldPercentInsiders", "heldPercentInstitutions",
    "fiveYearAvgDividendYield",
}

_LARGE_FIELDS = {
    "marketCap", "enterpriseValue", "floatShares", "sharesOutstanding",
    "sharesShort", "sharesShortPriorMonth", "totalRevenue", "freeCashflow",
    "operatingCashflow", "totalCash", "totalDebt", "netIncomeToCommon", "ebitda",
}


def _fmt_value(key: str, val: object) -> str:
    if val is None:
        return "N/A"
    try:
        f = float(val)
    except (TypeError, ValueError):
        return str(val)

    if key in _PCT_FIELDS:
        return f"{f * 100:.1f}%"
    if key in _LARGE_FIELDS:
        if abs(f) >= 1e12:
            return f"${f / 1e12:.2f}T"
        if abs(f) >= 1e9:
            return f"${f / 1e9:.2f}B"
        if abs(f) >= 1e6:
            return f"${f / 1e6:.1f}M"
        return f"${f:.0f}"
    return f"{f:.2f}"


def _extract_financials_block(info: dict) -> str:
    """Format all Exhibit 2B fields as a compact text block."""
    lines: list[str] = []
    for yf_key, label in _INFO_FIELDS:
        val = info.get(yf_key)
        lines.append(f"{label}: {_fmt_value(yf_key, val)}")
    return "\n".join(lines)


def _extract_news_block(news: list[dict], max_items: int = 8) -> str:
    """Format recent news headlines (and summaries when available) for the prompt."""
    if not news:
        return "No recent news available."
    lines: list[str] = []
    for item in news[:max_items]:
        title = item.get("title", "")
        if not title:
            continue
        summary = (item.get("summary") or "").strip()
        if summary and summary != title:
            lines.append(f"• {title} — {summary[:200]}")
        else:
            lines.append(f"• {title}")
    return "\n".join(lines) if lines else "No recent news available."


# ── Per-ticker data fetch ─────────────────────────────────────────────────────

def _fetch_ticker_data(symbol: str) -> tuple[dict, list[dict]]:
    """Fetch yfinance .info and .news for a ticker. Synchronous — run in thread."""
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        news = t.news or []
        return info, news
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", symbol, exc)
        return {}, []


# ── Per-stock prompt (DeepSeek Exhibit 1 style) ───────────────────────────────

def _build_stock_user_prompt(
    candidate: dict,
    info: dict,
    macro_context: str,
    financials_block: str,
    news_block: str,
) -> str:
    symbol = candidate.get("symbol", "?")
    sector = info.get("sector") or candidate.get("sector") or "N/A"
    company = info.get("shortName") or info.get("longName") or symbol

    # Append wheel-specific options data (not in yfinance .info)
    iv = candidate.get("impliedVolatility")
    rsi = candidate.get("rsi")
    adx = candidate.get("adx")
    adr = candidate.get("adr20_pct")
    ret5d = candidate.get("return_5d")
    strike = candidate.get("strike")
    dte = candidate.get("dte")
    ann_roc = candidate.get("annualized_roc")
    delta = candidate.get("delta")
    sma200 = candidate.get("sma200")

    def f(v, suffix="", decimals=2):
        return f"{float(v):.{decimals}f}{suffix}" if v is not None else "N/A"

    options_block = (
        f"Options / Wheel specific:\n"
        f"IV (options chain): {f(iv, '%', 1)}\n"
        f"RSI(14): {f(rsi, decimals=1)}\n"
        f"ADX(14): {f(adx, decimals=1)}\n"
        f"ADR20 (daily range%): {f(adr, '%', 1)}\n"
        f"5-day return: {f(ret5d, '%', 1)}\n"
        f"200 SMA: {f(sma200, '$', 2)}\n"
        f"Best CSP: ${f(strike, decimals=0)}P {dte}DTE "
        f"AnnROC:{f(ann_roc, '%', 1)} Δ{f(delta, decimals=3)}\n"
    )

    return (
        f"Macro-economic context:\n{macro_context}\n\n"
        f"Based on the recent financial data and news headlines, assign a wheel "
        f"suitability score (1 to 100) reflecting the potential value of "
        f"{company} ({symbol}) in the {sector} sector as a CSP/Wheel trade "
        f"for the next month.\n\n"
        f"Scoring rubric — "
        f"IV quality (25 pts): 35-55% IV earns the most; <25% = poor premium; >70% = lottery risk. "
        f"Trend health (25 pts): price above 200 SMA, 50 SMA above 200 SMA. "
        f"Entry timing (20 pts): RSI < 50, near lower Bollinger Band preferred. "
        f"Fundamental quality (20 pts): positive FCF, Fwd PE < 40, durable sector. "
        f"Assignment safety (10 pts): beta < 1.5, no near-term earnings.\n\n"
        f"Financial data:\n{financials_block}\n\n"
        f"{options_block}\n"
        f"Recent news:\n{news_block}\n\n"
        f"First, write a short wheel opportunity report about {company}.\n"
        f"Include sections on: premium quality, technical setup, fundamentals, and key risk.\n"
        f"Do not recommend alternatives. Do not speak directly to investors.\n"
        f"Start with 'Wheel Report:'\n"
        f"Finally, on a new line, output: Score: X"
    )


# ── Score a single candidate ──────────────────────────────────────────────────

def _parse_score_and_report(output: str) -> tuple[int, str]:
    """Extract (score, report) from LLM output."""
    score = 0
    m = re.search(r"Score:\s*(\d+)", output, re.IGNORECASE)
    if m:
        score = max(1, min(100, int(m.group(1))))

    # Report is everything up to the Score line
    report = re.split(r"\nScore:\s*\d+", output, maxsplit=1)[0].strip()
    return score, report


async def _score_one(
    candidate: dict,
    info: dict,
    yf_news: list[dict],
    alpaca_news: str,
    macro_context: str,
    sem: asyncio.Semaphore,
) -> dict:
    symbol = candidate.get("symbol", "?")
    financials_block = _extract_financials_block(info)
    # Prefer Alpaca news (has summaries); fall back to yfinance titles
    news_block = alpaca_news if alpaca_news else _extract_news_block(yf_news)
    user_prompt = _build_stock_user_prompt(
        candidate, info, macro_context, financials_block, news_block
    )

    async with sem:
        logger.debug("Wheel scorer: scoring %s", symbol)
        output = await synthesize(_SYSTEM_PROMPT, user_prompt)

    score, report = _parse_score_and_report(output)
    logger.debug("Wheel scorer: %s → score=%d", symbol, score)

    result = dict(candidate)
    result["wheel_score"] = score
    result["wheel_report"] = report
    # First sentence of report as a compact thesis
    clean = report.replace("Wheel Report:", "").strip()
    first_sentence = re.split(r"(?<=[.!?])\s", clean, maxsplit=1)
    result["wheel_thesis"] = first_sentence[0].strip() if first_sentence else ""
    return result


# ── Public entry point ────────────────────────────────────────────────────────

async def score_wheel_candidates(
    candidates: list[dict],
    top_n: int = 30,
    macro_context: str | None = None,
) -> list[dict]:
    """Score the top_n wheel candidates 1-100 using full Exhibit 2B data + LLM.

    Each candidate gets an individual LLM call (DeepSeek-style) with:
    - All yfinance Exhibit 2B financials
    - Per-stock news: Alpaca Data API (headline+summary) with yfinance as fallback
    - Macro context (SPY/VIX + Wikipedia events + Alpaca/AV macro news)

    LLM calls are parallelised with a concurrency limit of _LLM_CONCURRENCY.

    Args:
        candidates:    List from run_csp_scan()["candidates"].
        top_n:         How many candidates to score (default 30).
        macro_context: Override macro string. Fetched automatically if None.

    Returns:
        Full candidates list with ``wheel_score``, ``wheel_report``, and
        ``wheel_thesis`` fields, sorted by wheel_score descending.
    """
    if not candidates:
        return candidates

    to_score = candidates[:top_n]
    remainder = candidates[top_n:]

    # Build macro context once for all candidates
    if macro_context is None:
        logger.info("Wheel scorer: fetching macro context")
        macro_context = await build_macro_context_str()

    # Fetch yfinance data + Alpaca news for all candidates in parallel
    logger.info("Wheel scorer: fetching data for %d tickers", len(to_score))
    ticker_data, alpaca_news_list = await asyncio.gather(
        asyncio.gather(*[
            asyncio.to_thread(_fetch_ticker_data, c["symbol"])
            for c in to_score
        ]),
        asyncio.gather(*[
            fetch_alpaca_ticker_news(c["symbol"])
            for c in to_score
        ]),
    )

    # Score all candidates with bounded concurrency
    sem = asyncio.Semaphore(_LLM_CONCURRENCY)
    logger.info(
        "Wheel scorer: scoring %d candidates (concurrency=%d)", len(to_score), _LLM_CONCURRENCY
    )
    scored = await asyncio.gather(*[
        _score_one(candidate, info, news, alpaca_news, macro_context, sem)
        for candidate, (info, news), alpaca_news
        in zip(to_score, ticker_data, alpaca_news_list)
    ])

    # Remainder gets null scores
    for c in remainder:
        c["wheel_score"] = 0
        c["wheel_report"] = ""
        c["wheel_thesis"] = ""

    all_candidates = list(scored) + remainder
    all_candidates.sort(key=lambda c: c.get("wheel_score", 0), reverse=True)

    logger.info(
        "Wheel scorer: complete — top score %d (%s)",
        all_candidates[0].get("wheel_score", 0) if all_candidates else 0,
        all_candidates[0].get("symbol", "?") if all_candidates else "?",
    )
    return all_candidates
