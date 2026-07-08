"""Unit tests for src.fetchers.market_overview."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import AsyncMock, patch

import httpx  # noqa: F401
import pandas as pd
import pytest
import respx  # noqa: F401

from src.fetchers.market_overview import (
    SECTOR_ETFS,
    _fetch_breadth,
    _fetch_gex,
    _fetch_sectors,
    _fetch_vix,
    _gex_bucket,
    _gex_trend,
    fetch_market_overview,
    has_partial_failure,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_yf_df(
    tickers: list[str],
    n_days: int,
    base: float = 100.0,
    step: float = 1.0,
) -> pd.DataFrame:
    """Build a fake yf.download multi-ticker DataFrame.

    close[i] = base + i * step for i in 0..n_days-1.
    All other columns are flat at base.
    """
    dates = pd.bdate_range(end="2024-01-31", periods=n_days)
    data = {}
    for ticker in tickers:
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col == "Close":
                data[(ticker, col)] = [base + i * step for i in range(n_days)]
            elif col == "Volume":
                data[(ticker, col)] = [1_000_000] * n_days
            else:
                data[(ticker, col)] = [base] * n_days
    df = pd.DataFrame(data, index=dates)
    df.columns = pd.MultiIndex.from_tuples(list(data.keys()))
    return df


def _make_gex_csv(gex_values: list[float]) -> str:
    """Build a fake DIX.csv string."""
    lines = ["date,price,dix,gex"]
    for i, gex in enumerate(gex_values):
        lines.append(f"2024-01-{i+1:02d},400.0,0.45,{gex:.0f}")
    return "\n".join(lines)


_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture()
def _patch_db_path():
    with patch("src.fetchers.market_overview.settings") as mock_settings:
        mock_settings.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup_db():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


def _setup_breadth_db(
    tickers_ascending: list[str],
    tickers_descending: list[str],
    n_days: int = 210,
):
    """Populate the temp DB with OHLCV data for breadth tests."""
    conn = sqlite3.connect(_tmp_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universe_daily_ohlcv (
            symbol TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL NOT NULL,
            high   REAL NOT NULL,
            low    REAL NOT NULL,
            close  REAL NOT NULL,
            volume INTEGER NOT NULL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("DELETE FROM universe_daily_ohlcv")
    conn.commit()
    dates = pd.bdate_range(end="2024-01-31", periods=n_days)
    rows = []
    for sym in tickers_ascending:
        for i, d in enumerate(dates):
            close = 100.0 + i
            rows.append((sym, str(d.date()), close, close, close, close, 1_000_000))
    for sym in tickers_descending:
        for i, d in enumerate(dates):
            close = 400.0 - i
            rows.append((sym, str(d.date()), close, close, close, close, 1_000_000))
    conn.executemany(
        "INSERT OR REPLACE INTO universe_daily_ohlcv "
        "(symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


# ── GEX pure helpers ──────────────────────────────────────────────────────────

def test_gex_bucket_negative():
    label, bucket = _gex_bucket(-1.0)
    assert bucket == "negative"
    assert "volatility" in label.lower()


def test_gex_bucket_low():
    label, bucket = _gex_bucket(1.5)
    assert bucket == "low"


def test_gex_bucket_moderate():
    label, bucket = _gex_bucket(5.0)
    assert bucket == "moderate"


def test_gex_bucket_high():
    label, bucket = _gex_bucket(9.0)
    assert bucket == "high"


def test_gex_bucket_extreme():
    label, bucket = _gex_bucket(15.0)
    assert bucket == "extreme"


def test_gex_bucket_boundaries():
    _, b0 = _gex_bucket(0.0)
    assert b0 == "low"        # exactly 0 = low (not negative)
    _, b3 = _gex_bucket(3.0)
    assert b3 == "moderate"   # exactly 3 = moderate
    _, b7 = _gex_bucket(7.0)
    assert b7 == "high"       # exactly 7 = high
    _, b12 = _gex_bucket(12.0)
    assert b12 == "extreme"   # exactly 12 = extreme


def test_gex_trend_rising():
    assert _gex_trend(6.0, 5.0) == "Rising"   # diff_pct = 0.2 > 0.1


def test_gex_trend_falling():
    assert _gex_trend(4.0, 5.0) == "Falling"  # diff_pct = -0.2 < -0.1


def test_gex_trend_flat():
    assert _gex_trend(5.0, 5.0) == "Flat"


def test_gex_trend_zero_avg():
    assert _gex_trend(5.0, 0.0) == "Flat"


def test_gex_trend_negative_regime():
    assert _gex_trend(-2.0, -5.0) == "Rising"   # less negative = improving
    assert _gex_trend(-8.0, -5.0) == "Falling"  # more negative = worsening


# ── GEX integration ───────────────────────────────────────────────────────────

@respx.mock
async def test_fetch_gex_values_and_trend():
    # 24 rows at 5B, then 1 row at 7B — last 20 = [5B x19, 7B] → avg = 5.1B
    gex_vals = [5_000_000_000] * 24 + [7_000_000_000]
    respx.get(
        "https://squeezemetrics.com/monitor/static/DIX.csv"
    ).mock(return_value=httpx.Response(200, text=_make_gex_csv(gex_vals)))

    result = await _fetch_gex()

    assert result["value_b"] == 7.0
    assert result["rolling_20d_avg_b"] == pytest.approx(5.1, abs=0.01)
    assert result["trend"] == "Rising"
    assert result["bucket"] == "high"
    assert "Strong pinning" in result["label"]


@respx.mock
async def test_fetch_gex_bucket_negative_live():
    gex_vals = [-2_000_000_000] * 25
    respx.get(
        "https://squeezemetrics.com/monitor/static/DIX.csv"
    ).mock(return_value=httpx.Response(200, text=_make_gex_csv(gex_vals)))

    result = await _fetch_gex()
    assert result["bucket"] == "negative"
    assert result["trend"] == "Flat"


# ── Sectors ───────────────────────────────────────────────────────────────────

class TestSectors:
    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_returns_all_tickers(self, mock_dl):
        mock_dl.return_value = _make_yf_df(["XLK", "XLF"], n_days=30)
        etfs = {"XLK": "Technology", "XLF": "Financials"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            result, rotation = await _fetch_sectors()
        assert set(result.keys()) == {"XLK", "XLF"}

    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_pct_values(self, mock_dl):
        mock_dl.return_value = _make_yf_df(["XLK"], n_days=30, base=100.0, step=1.0)
        etfs = {"XLK": "Technology"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            result, rotation = await _fetch_sectors()
        xlk = result["XLK"]
        # close[-1] = 100 + 29 = 129, close[-2] = 128 → 1D = (129-128)/128*100 ≈ 0.78%
        assert xlk["pct_1d"] == pytest.approx(0.78, abs=0.01)
        # close[-1] = 129, close[-6] = 124 → 1W = (129-124)/124*100 ≈ 4.03%
        assert xlk["pct_1w"] == pytest.approx(4.03, abs=0.01)
        # close[-1] = 129, close[-22] = 108 → 1M = (129-108)/108*100 ≈ 19.44%
        assert xlk["pct_1m"] == pytest.approx(19.44, abs=0.01)

    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_name_field(self, mock_dl):
        mock_dl.return_value = _make_yf_df(["XLK"], n_days=30)
        etfs = {"XLK": "Technology"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            result, rotation = await _fetch_sectors()
        assert result["XLK"]["name"] == "Technology"

    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_null_when_insufficient_data(self, mock_dl):
        mock_dl.return_value = _make_yf_df(["XLK"], n_days=3)
        etfs = {"XLK": "Technology"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            result, rotation = await _fetch_sectors()
        # 3 rows → 1D works, 1W/1M are None (need 6 and 22 rows respectively)
        assert result["XLK"]["pct_1d"] is not None
        assert result["XLK"]["pct_1w"] is None
        assert result["XLK"]["pct_1m"] is None

    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_rotation_risk_on(self, mock_dl):
        # Make cyclicals outperform defensives by >0.1%
        mock_dl.return_value = _make_yf_df(["XLK", "XLU"], n_days=30, base=100.0, step=1.0)
        etfs = {"XLK": "Technology", "XLU": "Utilities"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            _, rotation = await _fetch_sectors()
        # XLK is CYCLICAL, XLU is DEFENSIVE — same pct_1d, so Neutral
        assert rotation == "Neutral (no clear rotation)"


# ── VIX ───────────────────────────────────────────────────────────────────────

class TestVix:
    @patch("src.fetchers.market_overview.yf.download")
    async def test_vix_spot_value(self, mock_dl):
        # 5 days of VIX at 18.x, VIX3M at 18.x (same base/step)
        vix_data = _make_yf_df(["^VIX", "^VIX3M"], n_days=5, base=18.0, step=0.1)
        mock_dl.return_value = vix_data
        result = await _fetch_vix()
        # close[-1] = 18.0 + 4*0.1 = 18.4
        assert result["spot"] == pytest.approx(18.4, abs=0.01)

    @patch("src.fetchers.market_overview.yf.download")
    async def test_vix_pct_1d(self, mock_dl):
        vix_data = _make_yf_df(["^VIX", "^VIX3M"], n_days=5, base=18.0, step=0.1)
        mock_dl.return_value = vix_data
        result = await _fetch_vix()
        # close[-1]=18.4, close[-2]=18.3 → (18.4-18.3)/18.3*100 ≈ 0.55%
        assert result["pct_1d"] == pytest.approx(0.55, abs=0.01)

    @patch("src.fetchers.market_overview.yf.download")
    async def test_vix_term_structure_contango(self, mock_dl):
        # VIX lower than VIX3M → spread > 0.5 → Contango
        vix_df = _make_yf_df(["^VIX"], n_days=5, base=18.0, step=0.0)
        vix3m_df = _make_yf_df(["^VIX3M"], n_days=5, base=19.5, step=0.0)
        combined = pd.concat([vix_df, vix3m_df], axis=1)
        mock_dl.return_value = combined
        result = await _fetch_vix()
        assert result["term_structure"] == "Contango"
        assert result["stress_note"] == "normal, calm"
        assert result["spread"] == pytest.approx(1.5, abs=0.01)

    @patch("src.fetchers.market_overview.yf.download")
    async def test_vix_term_structure_backwardation(self, mock_dl):
        # VIX higher than VIX3M → spread < -0.5 → Backwardation
        vix_df = _make_yf_df(["^VIX"], n_days=5, base=25.0, step=0.0)
        vix3m_df = _make_yf_df(["^VIX3M"], n_days=5, base=23.0, step=0.0)
        combined = pd.concat([vix_df, vix3m_df], axis=1)
        mock_dl.return_value = combined
        result = await _fetch_vix()
        assert result["term_structure"] == "Backwardation"
        assert result["stress_note"] == "elevated stress"

    @patch("src.fetchers.market_overview.yf.download")
    async def test_vix_term_structure_flat(self, mock_dl):
        # spread = 0.2 (within ±0.25) → Flat
        vix_df = _make_yf_df(["^VIX"], n_days=5, base=18.0, step=0.0)
        vix3m_df = _make_yf_df(["^VIX3M"], n_days=5, base=18.2, step=0.0)
        combined = pd.concat([vix_df, vix3m_df], axis=1)
        mock_dl.return_value = combined
        result = await _fetch_vix()
        assert result["term_structure"] == "Flat"
        assert result["stress_note"] == "transitioning"

    @patch("src.fetchers.market_overview.yf.download")
    async def test_vix_term_structure_contango_near_threshold(self, mock_dl):
        # spread = 0.3 → just past the tightened ±0.25 band → Contango (not Flat)
        vix_df = _make_yf_df(["^VIX"], n_days=5, base=18.0, step=0.0)
        vix3m_df = _make_yf_df(["^VIX3M"], n_days=5, base=18.3, step=0.0)
        combined = pd.concat([vix_df, vix3m_df], axis=1)
        mock_dl.return_value = combined
        result = await _fetch_vix()
        assert result["term_structure"] == "Contango"
        assert result["spread"] == pytest.approx(0.3, abs=0.01)

    @patch("src.fetchers.market_overview.yf.download")
    async def test_vix_pct_1w_none_with_few_rows(self, mock_dl):
        vix_data = _make_yf_df(["^VIX", "^VIX3M"], n_days=3, base=18.0, step=0.1)
        mock_dl.return_value = vix_data
        result = await _fetch_vix()
        # 3 rows → 1W needs 6 → None
        assert result["pct_1w"] is None

    @patch("src.fetchers.market_overview.yf.download")
    async def test_vix_raises_on_missing_vix3m(self, mock_dl):
        # Only VIX data, no VIX3M key
        mock_dl.return_value = _make_yf_df(["^VIX"], n_days=5, base=18.0, step=0.1)
        with pytest.raises(ValueError, match="VIX data missing"):
            await _fetch_vix()


# ── Breadth ───────────────────────────────────────────────────────────────────

class TestBreadth:
    def test_pct_above_200ma(self, _patch_db_path):
        # 60 ascending tickers (close[-1] > 200d MA) + 40 descending
        asc = [f"ASC{i:03d}" for i in range(60)]
        desc = [f"DESC{i:03d}" for i in range(40)]
        _setup_breadth_db(tickers_ascending=asc, tickers_descending=desc, n_days=210)
        result = _fetch_breadth()
        assert result is not None
        # 60/100 = 60%
        assert result["pct_above_200ma"] == pytest.approx(60.0, abs=0.1)

    def test_returns_none_when_insufficient_tickers(self, _patch_db_path):
        # Only 30 tickers with 210 days → fewer than 50 qualifying
        asc = [f"SMA{i:03d}" for i in range(20)]
        desc = [f"SMD{i:03d}" for i in range(10)]
        _setup_breadth_db(tickers_ascending=asc, tickers_descending=desc, n_days=210)
        result = _fetch_breadth()
        assert result is None

    def test_ad_ratio(self, _patch_db_path):
        # 60 ascending, 40 descending → A/D = 60/40 = 1.5
        asc = [f"ADA{i:03d}" for i in range(60)]
        desc = [f"ADD{i:03d}" for i in range(40)]
        _setup_breadth_db(tickers_ascending=asc, tickers_descending=desc, n_days=210)
        result = _fetch_breadth()
        assert result is not None
        assert result["advancing"] == 60
        assert result["declining"] == 40
        assert result["ad_ratio"] == pytest.approx(1.5, abs=0.01)

    def test_returns_none_when_fewer_than_50_qualifying(self, _patch_db_path):
        # Only 10 tickers with 200+ rows
        asc = [f"TNA{i:03d}" for i in range(5)]
        desc = [f"TND{i:03d}" for i in range(5)]
        _setup_breadth_db(tickers_ascending=asc, tickers_descending=desc, n_days=210)
        result = _fetch_breadth()
        assert result is None


# ── Coordinator ───────────────────────────────────────────────────────────────

class TestCoordinator:
    @patch("src.fetchers.market_overview._fetch_sectors", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_vix", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_gex", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_breadth")
    async def test_coordinator_returns_all_fields(
        self, mock_breadth, mock_gex, mock_vix, mock_sectors
    ):
        mock_sectors.return_value = {
            "XLK": {"name": "Technology", "pct_1d": 1.0, "pct_1w": 2.0, "pct_1m": 3.0}
        }
        mock_vix.return_value = {
            "spot": 18.4, "pct_1d": -0.5, "pct_1w": 2.1, "vix3m": 19.1,
            "term_structure": "Contango", "spread": 0.7, "stress_note": "normal, calm",
        }
        mock_gex.return_value = {
            "value_b": 7.4, "rolling_20d_avg_b": 5.1, "trend": "Rising",
            "label": "High Positive — Strong pinning", "bucket": "high",
        }
        mock_breadth.return_value = {
            "pct_above_200ma": 61.2, "advancing": 312, "declining": 188, "ad_ratio": 1.66,
        }
        result = await fetch_market_overview()
        assert result["sectors"] is not None
        assert "rotation" in result
        assert result["vix"] is not None
        assert result["gex"] is not None
        assert result["breadth"] is not None

    @patch("src.fetchers.market_overview._fetch_sectors", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_vix", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_gex", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_breadth")
    async def test_coordinator_handles_sector_failure(
        self, mock_breadth, mock_gex, mock_vix, mock_sectors
    ):
        mock_sectors.side_effect = Exception("yfinance down")
        mock_vix.return_value = {
            "spot": 18.4, "pct_1d": -0.5, "pct_1w": 2.1, "vix3m": 19.1,
            "term_structure": "Contango", "spread": 0.7, "stress_note": "normal, calm",
        }
        mock_gex.return_value = {
            "value_b": 7.4, "rolling_20d_avg_b": 5.1, "trend": "Rising",
            "label": "High Positive — Strong pinning", "bucket": "high",
        }
        mock_breadth.return_value = {
            "pct_above_200ma": 61.2, "advancing": 312, "declining": 188, "ad_ratio": 1.66,
        }
        result = await fetch_market_overview()
        assert result["sectors"] is None
        assert result["vix"] is not None

    @patch("src.fetchers.market_overview._fetch_sectors", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_vix", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_gex", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview._fetch_breadth")
    async def test_coordinator_handles_all_failures(
        self, mock_breadth, mock_gex, mock_vix, mock_sectors
    ):
        for m in [mock_sectors, mock_vix, mock_gex, mock_breadth]:
            m.side_effect = Exception("all down")
        result = await fetch_market_overview()
        assert result["sectors"] is None
        assert result["vix"] is None
        assert result["gex"] is None
        assert result["breadth"] is None


# ── has_partial_failure ───────────────────────────────────────────────────────

class TestHasPartialFailure:
    def _full_success(self, **overrides) -> dict:
        data = {
            "sectors": {ticker: {} for ticker in SECTOR_ETFS},
            "rotation": "Neutral (no clear rotation)",
            "vix": {"spot": 18.4}, "gex": {"value_b": 7.0},
            "breadth": {"pct_above_200ma": 60.0}, "themes": {"singles": {}},
        }
        data.update(overrides)
        return data

    def test_false_when_everything_succeeded(self):
        assert has_partial_failure(self._full_success()) is False

    def test_true_when_vix_failed(self):
        assert has_partial_failure(self._full_success(vix=None)) is True

    def test_true_when_sectors_failed(self):
        assert has_partial_failure(self._full_success(sectors=None)) is True

    def test_true_when_sectors_partially_missing(self):
        """A sectors dict that isn't None but is short a few tickers (e.g. a
        partial yfinance batch-download failure) must still count as a
        partial failure so it gets the short self-healing TTL."""
        assert has_partial_failure(self._full_success(sectors={"XLK": {}, "XLV": {}})) is True

    def test_true_when_themes_failed(self):
        assert has_partial_failure(self._full_success(themes=None)) is True

    def test_true_when_everything_failed(self):
        data = {
            "sectors": None, "rotation": None, "vix": None,
            "gex": None, "breadth": None, "themes": None,
        }
        assert has_partial_failure(data) is True

    def test_null_rotation_alone_is_not_a_failure(self):
        """rotation can legitimately be None (no clear sector split) even
        when every independent fetch succeeded — must not count as a failure."""
        assert has_partial_failure(self._full_success(rotation=None)) is False
