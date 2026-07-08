"""Tests for new screen_stocks() fields added for the trade chatbot."""
from __future__ import annotations

import pandas as pd

from src.screener.stocks import _calculate_volume_ratio


class TestCalculateVolumeRatio:
    """avg_vol_20d must average a true 20 trailing days (excluding today), to
    match csp_scanner.py's volume_ratio calculation for the same metric."""

    def _hist(self, volumes: list[int]) -> pd.DataFrame:
        return pd.DataFrame({"Volume": volumes})

    def test_returns_ratio_of_last_to_20d_avg(self):
        # avg of 20 prior rows of 1000 = 1000, last row = 2000 → ratio = 2.0
        vols = [1000] * 20 + [2000]
        result = _calculate_volume_ratio(self._hist(vols))
        assert result == 2.0

    def test_returns_none_if_fewer_than_21_rows(self):
        vols = [1000] * 20
        assert _calculate_volume_ratio(self._hist(vols)) is None

    def test_returns_none_if_avg_volume_is_zero(self):
        vols = [0] * 21
        assert _calculate_volume_ratio(self._hist(vols)) is None

    def test_rounds_to_two_decimal_places(self):
        # 20 prior rows of 1000 + last row of 1500 → avg = 1000, ratio = 1.5
        vols = [1000] * 20 + [1500]
        result = _calculate_volume_ratio(self._hist(vols))
        assert result is not None
        assert isinstance(result, float)
        assert len(str(result).split(".")[-1]) <= 2

    def test_excludes_todays_bar_from_the_average(self):
        """A volume spike on the current bar must not skew its own average —
        iloc[-21:-1] excludes the last row, iloc[-20:] would not."""
        vols = [1000] * 20 + [100000]
        result = _calculate_volume_ratio(self._hist(vols))
        assert result == 100.0


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
        """Negative = below high (opposite of csp_scanner.py's pct_from_52wk_high,
        which is positive = below high). This is intentional here — this field
        only feeds chat.py's signed "+/-X.X%" display — see the comment on this
        formula in src/screener/stocks.py."""
        price, high = 90.0, 100.0
        pct = round(((price - high) / high) * 100, 1)
        assert pct == -10.0
