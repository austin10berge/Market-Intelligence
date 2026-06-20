"""Sector ETF daily performance fetcher."""

from __future__ import annotations

import logging
import math

import yfinance as yf

from ..models import Signal, SignalSource
from .base import BaseFetcher

logger = logging.getLogger(__name__)

# S&P 500 SPDR sector ETFs
SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

# Defensive sectors — outperformance signals risk-off rotation
DEFENSIVE = {"XLU", "XLP", "XLV", "XLRE"}
CYCLICAL = {"XLK", "XLY", "XLF", "XLI", "XLE", "XLB", "XLC"}


class SectorEtfFetcher(BaseFetcher):
    """Fetch daily sector ETF performance and detect rotation signals."""

    @property
    def name(self) -> str:
        return "Sector ETF Performance"

    async def fetch(self) -> Signal | None:
        # Download 2 days of data so we can compute daily % change
        tickers_str = " ".join(SECTOR_ETFS.keys())
        data = yf.download(tickers_str, period="2d", group_by="ticker", progress=False)

        if data.empty:
            logger.warning("Sector ETFs: no data returned")
            return None

        performances: dict[str, float] = {}
        for ticker, sector_name in SECTOR_ETFS.items():
            try:
                ticker_data = data[ticker] if ticker in data.columns.get_level_values(0) else None
                if ticker_data is None or ticker_data.empty:
                    continue

                close_col = ticker_data["Close"]
                if len(close_col) >= 2:
                    prev_close = float(close_col.iloc[-2])
                    curr_close = float(close_col.iloc[-1])
                    if prev_close > 0 and not math.isnan(curr_close) and not math.isnan(prev_close):
                        pct_change = round(((curr_close - prev_close) / prev_close) * 100, 2)
                        performances[ticker] = pct_change
            except Exception:
                logger.debug(f"Sector ETFs: failed to process {ticker}", exc_info=True)
                continue

        if not performances:
            logger.warning("Sector ETFs: unable to compute any sector performance")
            return None

        # Sort by performance
        sorted_perf = sorted(performances.items(), key=lambda x: x[1], reverse=True)
        leaders = sorted_perf[:3]
        laggards = sorted_perf[-3:]

        # Rotation analysis: are defensive sectors outperforming cyclicals?
        def_avg = _sector_group_avg(performances, DEFENSIVE)
        cyc_avg = _sector_group_avg(performances, CYCLICAL)

        if def_avg is not None and cyc_avg is not None:
            rotation_spread = round(def_avg - cyc_avg, 2)
            if rotation_spread > 0.5:
                rotation = "Risk-off (defensive leading)"
            elif rotation_spread < -0.5:
                rotation = "Risk-on (cyclical leading)"
            else:
                rotation = "Neutral rotation"
        else:
            rotation = "Insufficient data"
            rotation_spread = 0.0

        # Build summary
        leader_lines = "\n".join(f"  {SECTOR_ETFS[t]} ({t}): {p:+.2f}%" for t, p in leaders)
        laggard_lines = "\n".join(f"  {SECTOR_ETFS[t]} ({t}): {p:+.2f}%" for t, p in laggards)

        summary = (
            f"Sectors: {rotation}\n"
            f"Leaders:\n{leader_lines}\n"
            f"Laggards:\n{laggard_lines}"
        )

        return Signal(
            source=SignalSource.SECTOR_ETF,
            value=round(sum(performances.values()) / len(performances), 2),
            metadata={
                "performances": performances,
                "leaders": dict(leaders),
                "laggards": dict(laggards),
                "rotation": rotation,
                "rotation_spread": rotation_spread,
                "defensive_avg": def_avg,
                "cyclical_avg": cyc_avg,
            },
            summary=summary,
        )


def _sector_group_avg(performances: dict[str, float], group: set[str]) -> float | None:
    """Average performance of a sector group."""
    vals = [performances[t] for t in group if t in performances]
    return round(sum(vals) / len(vals), 3) if vals else None
