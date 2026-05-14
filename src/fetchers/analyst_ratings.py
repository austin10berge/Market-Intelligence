"""Analyst consensus and recent upgrades/downgrades via Finnhub.

Tracks upgrade/downgrade actions in the past 30 days and the most recent
analyst consensus breakdown (strong buy / buy / hold / sell / strong sell)
for each watchlist ticker. Net upgrades are a confirming bullish signal;
net downgrades are a confirming bearish signal.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from ..config import settings
from ..db import get_stock_watchlist, get_analyst_cache, set_analyst_cache
from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
LOOKBACK_DAYS = 30


class AnalystRatingsFetcher(BaseFetcher):
    """Fetch analyst consensus and upgrades/downgrades for watchlist tickers via Finnhub."""

    @property
    def name(self) -> str:
        return "Analyst Ratings"

    async def fetch(self) -> Signal | None:
        if not settings.finnhub_api_key:
            logger.warning("Analyst Ratings: FINNHUB_API_KEY not set — skipping")
            return None

        tickers = get_stock_watchlist()
        if not tickers:
            logger.warning("Analyst Ratings: watchlist is empty")
            return None

        cached = get_analyst_cache(max_age_hours=12)
        if cached:
            logger.info("Analyst Ratings: using cached data from %s", cached.get("cached_at", "?"))
            return Signal(
                source=SignalSource.ANALYST_RATINGS,
                value=cached["score_value"],
                metadata=cached["metadata"],
                summary=cached["summary"],
            )

        client = await get_http_client()
        since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

        upgrade_tickers: dict[str, list[dict]] = defaultdict(list)
        downgrade_tickers: dict[str, list[dict]] = defaultdict(list)
        consensus_by_ticker: dict[str, dict] = {}
        errors = 0

        for symbol in tickers:
            try:
                resp = await client.get(
                    f"{FINNHUB_BASE}/stock/upgrade-downgrade",
                    params={"symbol": symbol, "from": since, "token": settings.finnhub_api_key},
                )
                resp.raise_for_status()
                for change in resp.json():
                    action = change.get("action", "").lower()
                    entry = {
                        "firm": change.get("company", "Unknown"),
                        "from": change.get("fromGrade", ""),
                        "to": change.get("toGrade", ""),
                    }
                    if action == "up":
                        upgrade_tickers[symbol].append(entry)
                    elif action == "down":
                        downgrade_tickers[symbol].append(entry)
            except Exception as exc:
                logger.debug("Analyst Ratings: upgrade-downgrade failed for %s: %s", symbol, exc)
                errors += 1

            try:
                resp = await client.get(
                    f"{FINNHUB_BASE}/stock/recommendation",
                    params={"symbol": symbol, "token": settings.finnhub_api_key},
                )
                resp.raise_for_status()
                recs = resp.json()
                if recs:
                    latest = recs[0]
                    sb = latest.get("strongBuy", 0) or 0
                    b = latest.get("buy", 0) or 0
                    h = latest.get("hold", 0) or 0
                    s = latest.get("sell", 0) or 0
                    ss = latest.get("strongSell", 0) or 0
                    total = sb + b + h + s + ss
                    if total > 0:
                        consensus_by_ticker[symbol] = {
                            "strong_buy": sb,
                            "buy": b,
                            "hold": h,
                            "sell": s,
                            "strong_sell": ss,
                            "total": total,
                            "buy_pct": round((sb + b) / total * 100),
                            "sell_pct": round((s + ss) / total * 100),
                            "period": latest.get("period", ""),
                        }
            except Exception as exc:
                logger.debug("Analyst Ratings: consensus failed for %s: %s", symbol, exc)
                errors += 1

        total_upgrade_tickers = len(upgrade_tickers)
        total_downgrade_tickers = len(downgrade_tickers)
        total_upgrades = sum(len(v) for v in upgrade_tickers.values())
        total_downgrades = sum(len(v) for v in downgrade_tickers.values())

        if total_upgrades == 0 and total_downgrades == 0:
            score_value = 0.0
            summary = f"Analyst Ratings ({LOOKBACK_DAYS}d): no rating changes across watchlist"
        else:
            net = total_upgrade_tickers - total_downgrade_tickers
            score_value = round(net / max(total_upgrade_tickers + total_downgrade_tickers, 1), 3)

            top_ups = sorted(upgrade_tickers.items(), key=lambda x: len(x[1]), reverse=True)[:3]
            top_downs = sorted(downgrade_tickers.items(), key=lambda x: len(x[1]), reverse=True)[:3]

            up_str = ", ".join(f"{sym} ({len(ch)}↑)" for sym, ch in top_ups) if top_ups else "none"
            down_str = ", ".join(f"{sym} ({len(ch)}↓)" for sym, ch in top_downs) if top_downs else "none"

            summary = (
                f"Analyst Ratings ({LOOKBACK_DAYS}d): "
                f"{total_upgrades} upgrade{'s' if total_upgrades != 1 else ''} "
                f"across {total_upgrade_tickers} ticker{'s' if total_upgrade_tickers != 1 else ''} "
                f"({up_str}), "
                f"{total_downgrades} downgrade{'s' if total_downgrades != 1 else ''} "
                f"across {total_downgrade_tickers} ticker{'s' if total_downgrade_tickers != 1 else ''} "
                f"({down_str})"
            )

        metadata = {
            "upgrade_tickers": dict(upgrade_tickers),
            "downgrade_tickers": dict(downgrade_tickers),
            "consensus_by_ticker": consensus_by_ticker,
            "total_upgrades": total_upgrades,
            "total_downgrades": total_downgrades,
            "upgrade_ticker_count": total_upgrade_tickers,
            "downgrade_ticker_count": total_downgrade_tickers,
            "lookback_days": LOOKBACK_DAYS,
            "errors": errors,
        }

        set_analyst_cache({"score_value": score_value, "metadata": metadata, "summary": summary})

        return Signal(
            source=SignalSource.ANALYST_RATINGS,
            value=score_value,
            metadata=metadata,
            summary=summary,
        )
