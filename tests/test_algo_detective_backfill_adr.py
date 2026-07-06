from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pandas as pd
import pytest

from src.algo_detective.backfill_adr import _compute_adr20, run_backfill
from src.algo_detective.store import _get_connection, ensure_tables, upsert_feature_rows_bulk

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db():
    with patch("src.algo_detective.store.settings") as mock:
        mock.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


def _make_ohlcv(n: int = 25, high_pct: float = 1.03, low_pct: float = 0.98) -> pd.DataFrame:
    """Constant-ratio OHLCV so the daily range % is exactly known: (high_pct - low_pct)."""
    dates = pd.bdate_range("2026-01-02", periods=n)
    close = pd.Series([100.0] * n, index=dates)
    return pd.DataFrame(
        {"High": close * high_pct, "Low": close * low_pct, "Close": close},
        index=dates,
    )


def _make_feature_row(date="2026-02-10", ticker="GE"):
    return {
        "date": date, "ticker": ticker, "is_prime": 1,
        "close_price": 100.0, "volume": 1_000_000,
        "computed_at": "2026-02-10T00:00:00+00:00",
    }


class TestComputeAdr20:
    def test_exact_value_for_known_range(self):
        df = _make_ohlcv(n=25, high_pct=1.03, low_pct=0.98)
        result = _compute_adr20(df, "2026-01-30")
        assert result == pytest.approx(5.0, abs=1e-3)

    def test_none_for_insufficient_bars(self):
        df = _make_ohlcv(n=10)
        result = _compute_adr20(df, df.index[-1].strftime("%Y-%m-%d"))
        assert result is None

    def test_none_when_close_is_zero(self):
        df = _make_ohlcv(n=25)
        df.loc[df.index[-1], "Close"] = 0.0
        result = _compute_adr20(df, df.index[-1].strftime("%Y-%m-%d"))
        assert result is None

    def test_respects_as_of_date_cutoff(self):
        """Rows after as_of_date must not affect the computed value (no lookahead)."""
        df = _make_ohlcv(n=25, high_pct=1.03, low_pct=0.98)
        cutoff = df.index[19].strftime("%Y-%m-%d")  # only first 20 rows visible
        result = _compute_adr20(df, cutoff)
        assert result == pytest.approx(5.0, abs=1e-3)

        # Append post-cutoff rows with a wildly different range — must not change result
        extra_dates = pd.bdate_range(df.index[-1] + pd.Timedelta(days=1), periods=5)
        extra = pd.DataFrame(
            {"High": [500.0] * 5, "Low": [1.0] * 5, "Close": [100.0] * 5}, index=extra_dates
        )
        df_extended = pd.concat([df, extra])
        result_with_future = _compute_adr20(df_extended, cutoff)
        assert result_with_future == result


class TestRunBackfill:
    def test_skips_rows_already_populated(self):
        ensure_tables()
        row = _make_feature_row(ticker="ALREADY_DONE")
        row["adr20_pct"] = 2.5
        upsert_feature_rows_bulk([row])

        with patch("src.algo_detective.backfill_adr.load_ohlcv_batch_for_date") as mock_load:
            run_backfill()

        mock_load.assert_not_called()

    def test_backfills_null_rows_from_ohlcv(self):
        ensure_tables()
        row = _make_feature_row(date="2026-03-05", ticker="NEEDS_BACKFILL")
        upsert_feature_rows_bulk([row])

        ohlcv = _make_ohlcv(n=25, high_pct=1.02, low_pct=0.99)  # 3% range
        with patch(
            "src.algo_detective.backfill_adr.load_ohlcv_batch_for_date",
            return_value={"NEEDS_BACKFILL": ohlcv},
        ):
            run_backfill()

        conn = _get_connection()
        try:
            result = conn.execute(
                "SELECT adr20_pct FROM detective_features WHERE date = ? AND ticker = ?",
                ("2026-03-05", "NEEDS_BACKFILL"),
            ).fetchone()
        finally:
            conn.close()
        assert result["adr20_pct"] == pytest.approx(3.0, abs=1e-3)

    def test_missing_ohlcv_leaves_row_null(self):
        ensure_tables()
        row = _make_feature_row(date="2026-03-06", ticker="NO_OHLCV_DATA")
        upsert_feature_rows_bulk([row])

        with patch(
            "src.algo_detective.backfill_adr.load_ohlcv_batch_for_date",
            return_value={},
        ):
            run_backfill()

        conn = _get_connection()
        try:
            result = conn.execute(
                "SELECT adr20_pct FROM detective_features WHERE date = ? AND ticker = ?",
                ("2026-03-06", "NO_OHLCV_DATA"),
            ).fetchone()
        finally:
            conn.close()
        assert result["adr20_pct"] is None

    def test_noop_when_nothing_needs_backfill(self):
        ensure_tables()
        conn = _get_connection()
        try:
            conn.execute("DELETE FROM detective_features")
            conn.commit()
        finally:
            conn.close()

        with patch("src.algo_detective.backfill_adr.load_ohlcv_batch_for_date") as mock_load:
            run_backfill()

        mock_load.assert_not_called()
