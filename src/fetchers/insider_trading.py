"""Corporate insider trading fetcher — SEC Form 4 via Finnhub.

Tracks purchases and sales by C-suite executives, directors, and 10%+ owners
across the watchlist. Flags clusters of insider buying as bullish and clusters
of insider selling as bearish.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from ..config import settings
from ..db import get_stock_watchlist, get_insider_cache, set_insider_cache
from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
LOOKBACK_DAYS = 7  # How many days back to scan Form 4 filings

# Transaction codes that indicate deliberate open-market buys (strong signal)
BUY_CODES = {"P"}   # Open-market purchase
# Transaction codes that indicate deliberate open-market sells (bearish signal)
SELL_CODES = {"S"}  # Open-market sale
# Codes to ignore — grants, awards, option exercises (not sentiment-driven)
IGNORE_CODES = {"A", "M", "G", "D", "F", "J", "C", "W"}


def _fmt_insider_name(raw_name: str, raw_title: str) -> str:
    """Format a Finnhub insider name + title into 'First Last (Title)'.

    Finnhub returns names in 'LAST FIRST' all-caps form, e.g. 'PAPERMASTER MARK'.
    We flip to 'First Last' title-case and append the officer title if present.

    Examples:
        'PAPERMASTER MARK', 'Chief Technology Officer' → 'Mark Papermaster (CTO)'
        'COOK TIMOTHY D', 'Chief Executive Officer'    → 'Timothy D Cook (CEO)'
        'SMITH JOHN', ''                               → 'John Smith'
    """
    # ── Normalise title abbreviations ──────────────────────────────────────
    TITLE_MAP = {
        "chief executive officer":        "CEO",
        "chief financial officer":        "CFO",
        "chief operating officer":        "COO",
        "chief technology officer":       "CTO",
        "chief information officer":      "CIO",
        "chief marketing officer":        "CMO",
        "chief revenue officer":          "CRO",
        "chief product officer":          "CPO",
        "chief people officer":           "CPO",
        "chief legal officer":            "CLO",
        "chief accounting officer":       "CAO",
        "chief human resources officer":  "CHRO",
        "chief strategy officer":         "CSO",
        "chief commercial officer":       "CCO",
        "chief compliance officer":       "CCO",
        "chief risk officer":             "CRO",
        "chief data officer":             "CDO",
        "chief scientific officer":       "CSO",
        "chief medical officer":          "CMO",
        "president":                      "President",
        "chairman":                       "Chairman",
        "chairperson":                    "Chairman",
        "executive chairman":             "Exec. Chairman",
        "vice president":                 "VP",
        "senior vice president":          "SVP",
        "executive vice president":       "EVP",
        "general counsel":                "General Counsel",
        "director":                       "Director",
        "independent director":           "Ind. Director",
        "10% owner":                      "10% Owner",
        "10 percent owner":               "10% Owner",
    }

    # ── Re-order LAST FIRST → First Last ───────────────────────────────────
    parts = raw_name.strip().split()
    if len(parts) >= 2:
        # Finnhub format: first token is the surname, rest are given name(s)
        surname = parts[0].capitalize()
        given   = " ".join(p.capitalize() for p in parts[1:])
        display = f"{given} {surname}"
    else:
        display = raw_name.strip().title() or "Unknown"

    # ── Append abbreviated title if available ──────────────────────────────
    title_clean = raw_title.strip()
    if title_clean:
        abbreviated = TITLE_MAP.get(title_clean.lower())
        title_str = abbreviated if abbreviated else title_clean
        display = f"{display} ({title_str})"

    return display


class InsiderTradingFetcher(BaseFetcher):
    """Fetch recent SEC Form 4 insider transactions for watchlist tickers via Finnhub."""

    @property
    def name(self) -> str:
        return "Insider Trading (Form 4)"

    async def fetch(self) -> Signal | None:
        if not settings.finnhub_api_key:
            logger.warning("Insider Trading: FINNHUB_API_KEY not set — skipping")
            return None

        tickers = get_stock_watchlist()
        if not tickers:
            logger.warning("Insider Trading: watchlist is empty")
            return None

        # Return cached data if fresh (avoids hammering Finnhub on every pipeline run)
        cached = get_insider_cache(max_age_hours=12)
        if cached:
            logger.info("Insider Trading: using cached data from %s", cached.get("cached_at", "?"))
            return Signal(
                source=SignalSource.INSIDER_TRADING,
                value=cached["score_value"],
                metadata=cached["metadata"],
                summary=cached["summary"],
            )

        client = await get_http_client()
        since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

        # Per-ticker buy/sell tallies
        buy_tickers: dict[str, list[dict]] = defaultdict(list)   # ticker → list of buy txns
        sell_tickers: dict[str, list[dict]] = defaultdict(list)  # ticker → list of sell txns
        errors = 0

        for symbol in tickers:
            try:
                resp = await client.get(
                    f"{FINNHUB_BASE}/stock/insider-transactions",
                    params={
                        "symbol": symbol,
                        "from": since,
                        "token": settings.finnhub_api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                for txn in data.get("data", []):
                    code = txn.get("transactionCode", "")
                    shares = txn.get("change", 0) or 0
                    value = abs(txn.get("transactionPrice", 0) or 0) * abs(shares)
                    name = txn.get("name", "Unknown")
                    txn_date = txn.get("transactionDate", "")

                    title_raw = txn.get("officerTitle", "") or ""
                    display_name = _fmt_insider_name(name, title_raw)

                    if code in BUY_CODES and shares > 0:
                        buy_tickers[symbol].append({
                            "name": display_name,
                            "shares": int(abs(shares)),
                            "value": round(value),
                            "date": txn_date,
                        })
                    elif code in SELL_CODES and shares < 0:
                        sell_tickers[symbol].append({
                            "name": display_name,
                            "shares": int(abs(shares)),
                            "value": round(value),
                            "date": txn_date,
                        })

            except Exception as exc:
                logger.debug("Insider Trading: failed for %s: %s", symbol, exc)
                errors += 1

        total_buy_tickers = len(buy_tickers)
        total_sell_tickers = len(sell_tickers)
        total_buys = sum(len(v) for v in buy_tickers.values())
        total_sells = sum(len(v) for v in sell_tickers.values())

        if total_buys == 0 and total_sells == 0:
            return Signal(
                source=SignalSource.INSIDER_TRADING,
                value=0.0,
                metadata={
                    "buy_tickers": {},
                    "sell_tickers": {},
                    "lookback_days": LOOKBACK_DAYS,
                    "errors": errors,
                },
                summary=f"Insider Trading: no open-market buys or sells in past {LOOKBACK_DAYS}d",
            )

        # Build top movers summary
        top_buys = sorted(buy_tickers.items(), key=lambda x: len(x[1]), reverse=True)[:3]
        top_sells = sorted(sell_tickers.items(), key=lambda x: len(x[1]), reverse=True)[:3]

        buy_summary = ", ".join(
            f"{sym} ({len(txns)} buy{'s' if len(txns) > 1 else ''})"
            for sym, txns in top_buys
        ) if top_buys else "none"

        sell_summary = ", ".join(
            f"{sym} ({len(txns)} sell{'s' if len(txns) > 1 else ''})"
            for sym, txns in top_sells
        ) if top_sells else "none"

        # Composite score: ratio of buy tickers to sell tickers
        net = total_buy_tickers - total_sell_tickers
        score_value = round(net / max(total_buy_tickers + total_sell_tickers, 1), 3)

        summary = (
            f"Insider Trading ({LOOKBACK_DAYS}d): "
            f"{total_buys} buy{'s' if total_buys != 1 else ''} across {total_buy_tickers} ticker{'s' if total_buy_tickers != 1 else ''} "
            f"({buy_summary}), "
            f"{total_sells} sell{'s' if total_sells != 1 else ''} across {total_sell_tickers} ticker{'s' if total_sell_tickers != 1 else ''} "
            f"({sell_summary})"
        )

        metadata = {
            "buy_tickers": dict(buy_tickers),
            "sell_tickers": dict(sell_tickers),
            "total_buys": total_buys,
            "total_sells": total_sells,
            "buy_ticker_count": total_buy_tickers,
            "sell_ticker_count": total_sell_tickers,
            "lookback_days": LOOKBACK_DAYS,
            "errors": errors,
        }

        # Write to cache so /insider command and subsequent pipeline runs can reuse it
        set_insider_cache({"score_value": score_value, "metadata": metadata, "summary": summary})

        return Signal(
            source=SignalSource.INSIDER_TRADING,
            value=score_value,
            metadata=metadata,
            summary=summary,
        )