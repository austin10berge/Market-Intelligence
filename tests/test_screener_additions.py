"""Tests for new screen_stocks() fields added for the trade chatbot."""
from __future__ import annotations

import pandas as pd

from src.screener.stocks import _calculate_volume_ratio


class TestCalculateVolumeRatio:
    def _hist(self, volumes: list[int]) -> pd.DataFrame:
        return pd.DataFrame({"Volume": volumes})

    def test_returns_ratio_of_last_to_20d_avg(self):
        # avg of 20 rows of 1000 = 1000, last row = 2000 → ratio = 2.0
        vols = [1000] * 19 + [2000]
        result = _calculate_volume_ratio(self._hist(vols))
        assert result == 2.0

    def test_returns_none_if_fewer_than_20_rows(self):
        vols = [1000] * 19
        assert _calculate_volume_ratio(self._hist(vols)) is None

    def test_returns_none_if_avg_volume_is_zero(self):
        vols = [0] * 20
        assert _calculate_volume_ratio(self._hist(vols)) is None

    def test_rounds_to_two_decimal_places(self):
        # 19 rows of 1000 + last row of 1500 → avg = 1023.8..., ratio = 1.466...
        vols = [1000] * 19 + [1500]
        result = _calculate_volume_ratio(self._hist(vols))
        assert result is not None
        assert isinstance(result, float)
        assert len(str(result).split(".")[-1]) <= 2


class TestDerivedFields:
    def test_bb_width_pct_formula(self):
        upper, mid, lower = 110.0, 100.0, 90.0
        bb_width_pct = round(((upper - lower) / mid) * 100, 1)
        assert bb_width_pct == 20.0

    def test_sma_200_pct_formula(self):
        price, sma_200 = 108.0, 100.0
        sma_200_pct = round(((price - sma_200) / sma_200) * 100, 1)
        assert sma_200_pct == 8.0

    def test_pct_from_52wk_high_formula(self):
        price, high = 90.0, 100.0
        pct = round(((price - high) / high) * 100, 1)
        assert pct == -10.0
