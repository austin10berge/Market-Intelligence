"""Tests for src.indicators — technical-indicator helpers shared across the
screener and algo_detective subsystems (previously duplicated in 4 places)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.indicators import compute_adr20_pct


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values)


class TestComputeAdr20Pct:
    def test_constant_daily_range_gives_exact_percentage(self):
        # High=102, Low=98, Close=100 every bar -> (102-98)/100 = 4% range
        high = _series([102.0] * 20)
        low = _series([98.0] * 20)
        close = _series([100.0] * 20)
        assert compute_adr20_pct(high, low, close) == pytest.approx(4.0)

    def test_returns_none_when_any_close_is_zero(self):
        high = _series([102.0] * 20)
        low = _series([98.0] * 20)
        close = _series([100.0] * 19 + [0.0])
        assert compute_adr20_pct(high, low, close) is None
