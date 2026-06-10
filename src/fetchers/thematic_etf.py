"""Thematic sub-sector ETF and basket performance fetcher.

Tracks specific investment themes (semis, nuclear, SaaS, cybersecurity, etc.)
that cut across the broad SPDR sector ETFs. Signal value is always 0.0 —
this is context for LLM narrative, not a mechanical composite score input.
"""

from __future__ import annotations

import asyncio
import logging

import yfinance as yf

from ..models import Signal, SignalSource
from .base import BaseFetcher

logger = logging.getLogger(__name__)

# Single-ticker themes: label → ETF ticker
SINGLE_TICKER_THEMES: dict[str, str] = {
    "Semis/Memory": "SMH",
    "Nuclear": "NLR",
    "Uranium": "URA",
    "SaaS": "IGV",
    "Fintech": "IPAY",
    "Biotech": "XBI",
    "Defense": "ITA",
    "Consumer Retail": "XRT",
}

# Basket themes: label → list of tickers
BASKET_THEMES: dict[str, list[str]] = {
    "Hyperscalers": ["AMZN", "MSFT", "GOOGL", "META"],
    "Cybersecurity": ["CRWD", "ZS", "S", "PANW", "FTNT", "NET"],
    "Crypto Proxy": ["MSTR", "COIN", "IBIT"],
}


def _pct_change(series, lookback: int) -> float | None:
    if len(series) < lookback + 1:
        return None
    prev = float(series.iloc[-(lookback + 1)])
    curr = float(series.iloc[-1])
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


class ThematicEtfFetcher(BaseFetcher):
    """Fetch daily performance for thematic ETFs and individual-stock baskets."""

    @property
    def name(self) -> str:
        return "Thematic ETF"

    async def fetch(self) -> Signal | None:
        all_tickers = list(SINGLE_TICKER_THEMES.values()) + [
            t for tickers in BASKET_THEMES.values() for t in tickers
        ]

        raw = await asyncio.to_thread(
            yf.download,
            " ".join(all_tickers),
            period="30d",
            group_by="ticker",
            progress=False,
            auto_adjust=True,
        )

        if raw.empty:
            logger.warning("Thematic ETF: no data returned")
            return None

        def _extract(ticker: str) -> dict | None:
            try:
                closes = raw[ticker]["Close"].dropna()
            except (KeyError, TypeError):
                return None
            if closes.empty:
                return None
            return {
                "pct_1d": _pct_change(closes, 1),
                "pct_1w": _pct_change(closes, 5),
                "pct_1m": _pct_change(closes, 21),
            }

        # Build single-ticker theme results
        singles: dict[str, dict] = {}
        for label, ticker in SINGLE_TICKER_THEMES.items():
            data = _extract(ticker)
            if data is not None:
                singles[label] = {"ticker": ticker, **data}

        # Build basket theme results
        baskets: dict[str, dict] = {}
        for label, tickers in BASKET_THEMES.items():
            ticker_data: dict[str, dict] = {}
            for t in tickers:
                data = _extract(t)
                if data is not None:
                    ticker_data[t] = data

            if not ticker_data:
                continue

            def _avg(key: str) -> float | None:
                vals = [v[key] for v in ticker_data.values() if v.get(key) is not None]
                return round(sum(vals) / len(vals), 2) if vals else None

            baskets[label] = {
                "tickers": ticker_data,
                "avg_1d": _avg("pct_1d"),
                "avg_1w": _avg("pct_1w"),
                "avg_1m": _avg("pct_1m"),
            }

        if not singles and not baskets:
            logger.warning("Thematic ETF: no usable data parsed")
            return None

        # Build a compact summary for the signal log line
        parts = []
        for label, d in singles.items():
            if d["pct_1d"] is not None:
                parts.append(f"{label}:{d['pct_1d']:+.1f}%")
        for label, d in baskets.items():
            if d["avg_1d"] is not None:
                parts.append(f"{label} avg:{d['avg_1d']:+.1f}%")

        summary = "Thematic ETF: " + " | ".join(parts) if parts else "Thematic ETF: no 1D data"

        return Signal(
            source=SignalSource.THEMATIC_ETF,
            value=0.0,
            metadata={"singles": singles, "baskets": baskets},
            summary=summary,
        )
