"""Implied-volatility lookup for option backtests.

Backtests need a defensible IV per entry. The previous engine used 20-day
realized vol (RV20) as a proxy, which systematically underprices premium
because RV is typically a few vol points below IV (variance risk premium).

This module prefers historical ATM IV from the `stock_iv_history` table when
available, and falls back to RV20 × `RV_TO_IV_FACTOR` with a floor.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# Conservative scaling so that backtested premiums approximate live IV
RV_TO_IV_FACTOR = 1.15
IV_FLOOR = 0.15
IV_CEILING = 2.50
# How far back to accept a stored IV snapshot when looking up a specific date
IV_LOOKUP_TOLERANCE_DAYS = 7


def build_iv_lookup(symbol: str) -> dict[date, float]:
    """Return a {date: atm_iv} map from the stock_iv_history table.

    Returns an empty dict if the table or the symbol's history is unavailable —
    the engine will fall back to scaled RV20.
    """
    try:
        from ..db import get_stock_iv_history
    except Exception as exc:
        logger.debug("stock_iv_history unavailable: %s", exc)
        return {}

    try:
        # Pull a generous window — most backtests look at recent years
        rows = get_stock_iv_history(symbol, lookback_days=365 * 5)
    except Exception as exc:
        logger.debug("get_stock_iv_history failed for %s: %s", symbol, exc)
        return {}

    out: dict[date, float] = {}
    for row in rows:
        try:
            d = datetime.strptime(row["date"], "%Y-%m-%d").date()
            iv = float(row["atm_iv"])
            if iv > 0:
                out[d] = max(IV_FLOOR, min(iv, IV_CEILING))
        except Exception:
            continue
    return out


def lookup_iv(
    iv_map: dict[date, float],
    bar_date: date,
    rv20: float | None,
) -> float:
    """Resolve the IV to use for an option opened at `bar_date`.

    Order of preference:
      1. Stored ATM IV on the bar date itself.
      2. Stored ATM IV from up to `IV_LOOKUP_TOLERANCE_DAYS` earlier.
      3. Scaled RV20 from the OHLCV history.
      4. The IV floor.
    """
    if iv_map:
        if bar_date in iv_map:
            return iv_map[bar_date]
        for delta in range(1, IV_LOOKUP_TOLERANCE_DAYS + 1):
            prior = bar_date - timedelta(days=delta)
            if prior in iv_map:
                return iv_map[prior]

    if rv20 is not None and not pd.isna(rv20) and rv20 > 0:
        return max(IV_FLOOR, min(rv20 * RV_TO_IV_FACTOR, IV_CEILING))

    return IV_FLOOR
