"""Technical-indicator helpers shared across the screener and algo_detective
subsystems."""

from __future__ import annotations

import pandas as pd


def compute_adr20_pct(high: pd.Series, low: pd.Series, close: pd.Series) -> float | None:
    """20-day average daily range as a percentage of close.

    Callers are responsible for passing exactly the window they want
    (e.g. the last 20 rows) and for any minimum-history check.
    """
    if (close == 0).any():
        return None
    return float(((high - low) / close).mean() * 100)
