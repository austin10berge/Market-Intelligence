"""US Treasury yield curve and US Dollar Index fetcher via yfinance.

Tracks 3-month, 5-year, 10-year, and 30-year Treasury yields plus DXY.
Computes the 3mo/10yr spread as the primary yield-curve recession signal.
Signal value = 10yr yield; score reflects yield level relative to a neutral
anchor and flags inversion as an extreme bearish condition.
"""

from __future__ import annotations

import logging

from ..models import Signal, SignalSource
from ._yf_lock import download_with_retry
from .base import BaseFetcher

logger = logging.getLogger(__name__)

_YIELD_TICKERS = {
    "^IRX": "3mo",
    "^FVX": "5yr",
    "^TNX": "10yr",
    "^TYX": "30yr",
}
_DXY_TICKER = "DX-Y.NYB"


def _pct_change(series, lookback: int) -> float | None:
    if len(series) < lookback + 1:
        return None
    prev = float(series.iloc[-(lookback + 1)])
    curr = float(series.iloc[-1])
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def _bps_change(series, lookback: int) -> float | None:
    """Return basis-point change for yield series (not pct change)."""
    if len(series) < lookback + 1:
        return None
    prev = float(series.iloc[-(lookback + 1)])
    curr = float(series.iloc[-1])
    return round((curr - prev) * 100, 1)


class TreasuryYieldsFetcher(BaseFetcher):
    """Fetch Treasury yield curve levels and DXY via yfinance."""

    @property
    def name(self) -> str:
        return "Treasury Yields & DXY"

    async def fetch(self) -> Signal | None:
        all_tickers = list(_YIELD_TICKERS.keys()) + [_DXY_TICKER]
        raw = await download_with_retry(
            " ".join(all_tickers),
            period="30d",
            group_by="ticker",
            progress=False,
            auto_adjust=True,
        )

        if raw.empty:
            logger.warning("Treasury Yields: no data returned from yfinance")
            return None

        yields: dict[str, dict] = {}
        for ticker, label in _YIELD_TICKERS.items():
            try:
                closes = raw[ticker]["Close"].dropna()
            except (KeyError, TypeError):
                continue
            if closes.empty:
                continue
            yields[label] = {
                "level": round(float(closes.iloc[-1]), 3),
                "bps_1d": _bps_change(closes, 1),
                "bps_1w": _bps_change(closes, 5),
            }

        if "10yr" not in yields:
            logger.warning("Treasury Yields: 10yr yield unavailable — skipping signal")
            return None

        # DXY
        dxy: dict | None = None
        try:
            dxy_closes = raw[_DXY_TICKER]["Close"].dropna()
            if not dxy_closes.empty:
                dxy = {
                    "level": round(float(dxy_closes.iloc[-1]), 2),
                    "pct_1d": _pct_change(dxy_closes, 1),
                    "pct_1w": _pct_change(dxy_closes, 5),
                    "pct_1m": _pct_change(dxy_closes, 21),
                }
        except (KeyError, TypeError):
            pass

        # Yield curve spread: 3mo/10yr (Estrella-Mishkin recession indicator)
        curve_spread: float | None = None
        curve_label: str = "N/A"
        if "3mo" in yields and "10yr" in yields:
            spread = round(yields["10yr"]["level"] - yields["3mo"]["level"], 3)
            curve_spread = spread
            if spread < -0.5:
                curve_label = f"Deeply Inverted ({spread:+.2f}%)"
            elif spread < 0:
                curve_label = f"Inverted ({spread:+.2f}%)"
            elif spread < 0.5:
                curve_label = f"Flat ({spread:+.2f}%)"
            else:
                curve_label = f"Normal ({spread:+.2f}%)"

        ten_yr = yields["10yr"]["level"]
        bps_1d = yields["10yr"].get("bps_1d")
        bps_str = f" ({bps_1d:+.0f}bps 1D)" if bps_1d is not None else ""
        dxy_str = f" | DXY {dxy['level']:.2f} ({dxy['pct_1d']:+.1f}% 1D)" if dxy else ""

        summary = f"Treasury: 10yr {ten_yr:.2f}%{bps_str} | Curve: {curve_label}{dxy_str}"

        return Signal(
            source=SignalSource.TREASURY_YIELDS,
            value=ten_yr,
            metadata={
                "yields": yields,
                "curve_spread": curve_spread,
                "curve_label": curve_label,
                "dxy": dxy,
            },
            summary=summary,
        )
