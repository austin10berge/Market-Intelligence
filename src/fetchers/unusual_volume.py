"""Unusual volume fetcher — compares today's volume to the 20-day average.

Uses yfinance which is already a project dependency. No additional API key needed.

A spike >2x the 20-day average on above-average price movement is a meaningful
signal. Multiple tickers spiking on the same day is even more significant — it
can indicate institutional positioning ahead of news.
"""

from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

from ..db import get_stock_watchlist
from ..models import Signal, SignalSource
from .base import BaseFetcher

logger = logging.getLogger(__name__)

# Volume must be this many times the 20-day average to qualify as "unusual"
VOLUME_SPIKE_THRESHOLD = 2.0
# Minimum absolute volume to avoid noise on illiquid tickers
MIN_VOLUME = 500_000


class UnusualVolumeFetcher(BaseFetcher):
    """Detect unusual trading volume spikes across the stock watchlist."""

    @property
    def name(self) -> str:
        return "Unusual Volume"

    async def fetch(self) -> Signal | None:
        tickers = get_stock_watchlist()
        if not tickers:
            logger.warning("Unusual Volume: watchlist is empty")
            return None

        spikes: list[dict] = []
        errors = 0

        for symbol in tickers:
            try:
                ticker = yf.Ticker(symbol)
                # Fetch 25 trading days — enough for 20d avg + today
                hist = ticker.history(period="25d")

                if hist.empty or len(hist) < 5:
                    continue

                # Today's (most recent) bar
                today_row = hist.iloc[-1]
                today_vol = today_row.get("Volume", 0)
                today_close = today_row.get("Close", 0)
                prev_close = hist.iloc[-2].get("Close", 0)

                if not today_vol or today_vol < MIN_VOLUME:
                    continue

                # 20-day average volume (excluding today)
                avg_vol_20d = hist["Volume"].iloc[:-1].tail(20).mean()
                if pd.isna(avg_vol_20d) or avg_vol_20d == 0:
                    continue

                ratio = today_vol / avg_vol_20d

                if ratio >= VOLUME_SPIKE_THRESHOLD:
                    pct_change = 0.0
                    if prev_close and prev_close > 0:
                        pct_change = round(((today_close - prev_close) / prev_close) * 100, 2)

                    direction = "up" if pct_change > 0 else "down" if pct_change < 0 else "flat"

                    spikes.append({
                        "symbol": symbol,
                        "volume": int(today_vol),
                        "avg_volume_20d": int(avg_vol_20d),
                        "ratio": round(ratio, 2),
                        "price_change_pct": pct_change,
                        "direction": direction,
                        "close": round(float(today_close), 2),
                    })

            except Exception as exc:
                logger.debug("Unusual Volume: failed for %s: %s", symbol, exc)
                errors += 1

        if not spikes:
            return Signal(
                source=SignalSource.UNUSUAL_VOLUME,
                value=0.0,
                metadata={"spikes": [], "threshold": VOLUME_SPIKE_THRESHOLD, "errors": errors},
                summary=f"Unusual Volume: no spikes >{VOLUME_SPIKE_THRESHOLD}x avg across watchlist",
            )

        # Sort by ratio descending
        spikes.sort(key=lambda x: x["ratio"], reverse=True)

        # Bullish if most spikes are on up days, bearish if down days
        up_spikes = [s for s in spikes if s["direction"] == "up"]
        down_spikes = [s for s in spikes if s["direction"] == "down"]
        net_direction = len(up_spikes) - len(down_spikes)
        score_value = round(net_direction / len(spikes), 3)

        # Format top spikes for summary
        top = spikes[:4]
        spike_strs = [
            f"{s['symbol']} {s['ratio']:.1f}x ({'+' if s['price_change_pct'] >= 0 else ''}{s['price_change_pct']}%)"
            for s in top
        ]
        more = f" +{len(spikes) - 4} more" if len(spikes) > 4 else ""

        summary = (
            f"Unusual Volume: {len(spikes)} spike{'s' if len(spikes) != 1 else ''} "
            f">{VOLUME_SPIKE_THRESHOLD}x avg — {', '.join(spike_strs)}{more}"
        )

        return Signal(
            source=SignalSource.UNUSUAL_VOLUME,
            value=score_value,
            metadata={
                "spikes": spikes,
                "spike_count": len(spikes),
                "up_spikes": len(up_spikes),
                "down_spikes": len(down_spikes),
                "threshold": VOLUME_SPIKE_THRESHOLD,
                "errors": errors,
            },
            summary=summary,
        )
