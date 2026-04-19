"""Congressional trading fetcher — House and Senate Stock Watcher APIs.

Both APIs are free, require no authentication, and are updated daily from
STOCK Act disclosures. Covers all 535 members of Congress including
high-profile traders like Nancy Pelosi, Tommy Tuberville, and others.

Looks back 14 days for recency — congressional disclosures can lag up to
45 days from the actual transaction date, so we cast a wider net.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from ..db import get_stock_watchlist
from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client

logger = logging.getLogger(__name__)

HOUSE_API = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
SENATE_API = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"

# Politicians to always call out by name if they appear
HIGH_PROFILE = {
    "nancy pelosi", "paul pelosi",
    "tommy tuberville", "josh gottheimer",
    "michael mccaul", "virginia foxx",
    "brian higgins", "pat fallon",
}

LOOKBACK_DAYS = 30  # Wide window to catch delayed disclosures


class CongressionalTradesFetcher(BaseFetcher):
    """Fetch recent congressional trades from House and Senate Stock Watcher APIs."""

    @property
    def name(self) -> str:
        return "Congressional Trades"

    async def fetch(self) -> Signal | None:
        watchlist = set(t.upper() for t in get_stock_watchlist())
        if not watchlist:
            logger.warning("Congressional Trades: watchlist is empty")
            return None

        client = await get_http_client()
        since = date.today() - timedelta(days=LOOKBACK_DAYS)

        house_trades = await self._fetch_source(client, HOUSE_API, "House", since, watchlist)
        senate_trades = await self._fetch_source(client, SENATE_API, "Senate", since, watchlist)

        all_trades = house_trades + senate_trades

        if not all_trades:
            return Signal(
                source=SignalSource.CONGRESSIONAL_TRADES,
                value=0.0,
                metadata={"trades": [], "lookback_days": LOOKBACK_DAYS},
                summary=f"Congressional Trades: no watchlist activity in past {LOOKBACK_DAYS}d",
            )

        # Aggregate by ticker
        buys_by_ticker: dict[str, list[dict]] = defaultdict(list)
        sells_by_ticker: dict[str, list[dict]] = defaultdict(list)
        high_profile_trades: list[str] = []

        for trade in all_trades:
            ticker = trade["ticker"]
            tx_type = trade["type"]
            politician = trade["politician"].lower()

            if tx_type == "purchase":
                buys_by_ticker[ticker].append(trade)
            elif tx_type == "sale":
                sells_by_ticker[ticker].append(trade)

            if politician in HIGH_PROFILE:
                action = "bought" if tx_type == "purchase" else "sold"
                high_profile_trades.append(
                    f"{trade['politician']} {action} {ticker} (~{trade.get('amount', 'undisclosed')})"
                )

        total_buy_tickers = len(buys_by_ticker)
        total_sell_tickers = len(sells_by_ticker)
        total_buys = sum(len(v) for v in buys_by_ticker.values())
        total_sells = sum(len(v) for v in sells_by_ticker.values())

        # Check for convergence: tickers that appear in both insider buys AND congress buys
        # This is stored in metadata for the scorer to use cross-signal
        top_buy_tickers = sorted(buys_by_ticker.items(), key=lambda x: len(x[1]), reverse=True)[:3]
        top_sell_tickers = sorted(sells_by_ticker.items(), key=lambda x: len(x[1]), reverse=True)[:3]

        buy_str = ", ".join(
            f"{sym} ({len(t)} pol.)" for sym, t in top_buy_tickers
        ) if top_buy_tickers else "none"

        sell_str = ", ".join(
            f"{sym} ({len(t)} pol.)" for sym, t in top_sell_tickers
        ) if top_sell_tickers else "none"

        hp_str = (" | Notable: " + "; ".join(high_profile_trades[:3])) if high_profile_trades else ""

        net = total_buy_tickers - total_sell_tickers
        score_value = round(net / max(total_buy_tickers + total_sell_tickers, 1), 3)

        summary = (
            f"Congressional Trades ({LOOKBACK_DAYS}d): "
            f"{total_buys} purchase{'s' if total_buys != 1 else ''} ({buy_str}), "
            f"{total_sells} sale{'s' if total_sells != 1 else ''} ({sell_str})"
            f"{hp_str}"
        )

        return Signal(
            source=SignalSource.CONGRESSIONAL_TRADES,
            value=score_value,
            metadata={
                "buys_by_ticker": {k: v for k, v in buys_by_ticker.items()},
                "sells_by_ticker": {k: v for k, v in sells_by_ticker.items()},
                "high_profile_trades": high_profile_trades,
                "total_buys": total_buys,
                "total_sells": total_sells,
                "buy_ticker_count": total_buy_tickers,
                "sell_ticker_count": total_sell_tickers,
                "lookback_days": LOOKBACK_DAYS,
            },
            summary=summary,
        )

    async def _fetch_source(
        self,
        client,
        url: str,
        chamber: str,
        since: date,
        watchlist: set[str],
    ) -> list[dict]:
        """Fetch and filter trades from one chamber's API."""
        try:
            resp = await client.get(url, timeout=20.0)
            resp.raise_for_status()
            all_records = resp.json()
        except Exception as exc:
            logger.warning("Congressional Trades: %s API failed: %s", chamber, exc)
            return []

        trades = []
        for record in all_records:
            # Normalise ticker
            ticker = (record.get("ticker") or record.get("asset_description") or "").strip().upper()
            if not ticker or ticker == "--" or ticker not in watchlist:
                continue

            # Normalise transaction type
            tx_type_raw = (record.get("type") or record.get("transaction_type") or "").lower()
            if "purchase" in tx_type_raw or "buy" in tx_type_raw:
                tx_type = "purchase"
            elif "sale" in tx_type_raw or "sell" in tx_type_raw or "exchange" in tx_type_raw:
                tx_type = "sale"
            else:
                continue  # Skip options, asset exchanges, etc.

            # Normalise date — use disclosure_date if transaction_date missing
            date_str = (
                record.get("transaction_date")
                or record.get("disclosure_date")
                or ""
            )
            try:
                txn_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            if txn_date < since:
                continue

            politician = (
                record.get("representative")
                or record.get("senator")
                or record.get("name")
                or "Unknown"
            ).strip()

            trades.append({
                "ticker": ticker,
                "type": tx_type,
                "politician": politician,
                "chamber": chamber,
                "date": date_str[:10],
                "amount": record.get("amount", "undisclosed"),
            })

        logger.info("Congressional Trades: %s — %d watchlist trades found", chamber, len(trades))
        return trades
