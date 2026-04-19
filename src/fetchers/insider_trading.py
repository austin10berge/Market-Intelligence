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
from ..db import get_stock_watchlist
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

                    if code in BUY_CODES and shares > 0:
                        buy_tickers[symbol].append({
                            "name": name,
                            "shares": int(abs(shares)),
                            "value": round(value),
                            "date": txn_date,
                        })
                    elif code in SELL_CODES and shares < 0:
                        sell_tickers[symbol].append({
                            "name": name,
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

        return Signal(
            source=SignalSource.INSIDER_TRADING,
            value=score_value,
            metadata={
                "buy_tickers": dict(buy_tickers),
                "sell_tickers": dict(sell_tickers),
                "total_buys": total_buys,
                "total_sells": total_sells,
                "buy_ticker_count": total_buy_tickers,
                "sell_ticker_count": total_sell_tickers,
                "lookback_days": LOOKBACK_DAYS,
                "errors": errors,
            },
            summary=summary,
        )
