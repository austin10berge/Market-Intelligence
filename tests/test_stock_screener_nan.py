"""Regression tests for the NaN-Close row that broke /api/screener/stocks.

yfinance appends an in-progress row for the current session with a populated
Volume but a NaN Close. Left in place it poisons current_price and every
derived field, and the resulting NaN breaks JSONResponse (allow_nan=False)
with an opaque 500.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.screener.stocks import _drop_incomplete_rows, _pct_from


def _frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": closes, "Volume": [1_000] * len(closes)},
        index=pd.date_range("2026-09-01", periods=len(closes), freq="D"),
    )


def test_drops_trailing_nan_close_row():
    hist = _frame([330.0, 331.0, 332.41, float("nan")])
    result = _drop_incomplete_rows(hist)
    assert len(result) == 3
    assert result["Close"].iloc[-1] == 332.41


def test_drops_interior_nan_close_rows():
    hist = _frame([330.0, float("nan"), 332.41])
    result = _drop_incomplete_rows(hist)
    assert len(result) == 2
    assert list(result["Close"]) == [330.0, 332.41]


def test_leaves_clean_frame_unchanged():
    hist = _frame([330.0, 331.0, 332.41])
    result = _drop_incomplete_rows(hist)
    assert len(result) == 3
    assert result["Close"].iloc[-1] == 332.41


def test_all_nan_frame_becomes_empty():
    hist = _frame([float("nan"), float("nan")])
    assert _drop_incomplete_rows(hist).empty


def test_frame_without_close_column_is_returned_unchanged():
    hist = pd.DataFrame({"Volume": [1, 2]})
    assert len(_drop_incomplete_rows(hist)) == 2


def test_pct_from_computes_percent_difference():
    assert _pct_from(332.41, 285.38) == 16.5


def test_pct_from_returns_none_when_value_is_nan():
    assert _pct_from(float("nan"), 285.38) is None
    assert _pct_from(np.nan, 285.38) is None


def test_pct_from_returns_none_when_value_is_none():
    assert _pct_from(None, 285.38) is None


def test_pct_from_returns_none_when_base_is_missing_or_nonpositive():
    assert _pct_from(332.41, None) is None
    assert _pct_from(332.41, float("nan")) is None
    assert _pct_from(332.41, 0) is None
    assert _pct_from(332.41, -5) is None


def test_pct_from_is_negative_below_base():
    assert _pct_from(90.0, 100.0) == -10.0
