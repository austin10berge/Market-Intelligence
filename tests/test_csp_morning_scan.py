"""Tests for csp_morning_scan — NASDAQ 100 morning scan, no LLM scoring."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.screener.csp_morning_scan import (
    build_scan_params,
    compute_watchlist_technicals,
    fetch_brief_watchlist,
    filter_bad_data,
    filter_out_watchlist,
    top_unique_by_ticker,
    write_morning_scan,
)


class TestBuildScanParams:
    def test_returns_scanner_params_with_nasdaq100_defaults(self):
        params = build_scan_params()
        assert params.min_days_to_earnings is not None
        assert params.min_days_to_earnings >= 21
        assert params.adr20_pct_min is not None
        assert params.adr20_pct_min > 0

    def test_dte_window_21_to_30(self):
        params = build_scan_params()
        assert params.min_dte == 21
        assert params.max_dte == 30

    def test_fundamental_gates_disabled(self):
        params = build_scan_params()
        assert params.min_market_cap_b == 0.0
        assert params.min_beta == 0.0
        assert params.max_beta >= 100.0
        assert params.min_fcf_b is None
        assert params.max_debt_to_equity is None
        assert params.min_revenue_growth is None

    def test_watchlist_only_false(self):
        params = build_scan_params()
        assert params.watchlist_only is False

    def test_uses_regime_delta_cap(self, tmp_path):
        import json
        regime_file = tmp_path / "regime-status.json"
        regime_file.write_text(json.dumps({"delta_cap": 0.25}))
        params = build_scan_params(regime_path=regime_file)
        assert params.max_delta == pytest.approx(0.25)

    def test_defaults_delta_when_regime_missing(self, tmp_path):
        missing = tmp_path / "no-such-file.json"
        params = build_scan_params(regime_path=missing)
        assert params.max_delta == pytest.approx(0.30)

    def test_defaults_delta_when_regime_has_no_delta_cap(self, tmp_path):
        import json
        regime_file = tmp_path / "regime-status.json"
        regime_file.write_text(json.dumps({"regime_from_note": "sideways"}))
        params = build_scan_params(regime_path=regime_file)
        assert params.max_delta == pytest.approx(0.30)


class TestFilterBadData:
    def test_removes_null_delta(self):
        candidates = [
            {"symbol": "A", "delta": None, "impliedVolatility": 35.0},
            {"symbol": "B", "delta": 0.25, "impliedVolatility": 35.0},
        ]
        result = filter_bad_data(candidates)
        assert len(result) == 1
        assert result[0]["symbol"] == "B"

    def test_removes_zero_iv(self):
        candidates = [
            {"symbol": "A", "delta": 0.25, "impliedVolatility": 0.0},
            {"symbol": "B", "delta": 0.25, "impliedVolatility": 32.0},
        ]
        result = filter_bad_data(candidates)
        assert len(result) == 1
        assert result[0]["symbol"] == "B"

    def test_passes_valid_rows(self):
        candidates = [{"symbol": "A", "delta": 0.24, "impliedVolatility": 40.0}]
        assert filter_bad_data(candidates) == candidates


class TestFilterOutWatchlist:
    def test_removes_tickers_already_on_watchlist(self):
        candidates = [
            {"symbol": "AAPL", "annualized_roc": 30.0},
            {"symbol": "SOFI", "annualized_roc": 25.0},
            {"symbol": "MSFT", "annualized_roc": 20.0},
        ]
        watchlist = ["SOFI", "NVDA"]
        result = filter_out_watchlist(candidates, watchlist)
        tickers = [c["symbol"] for c in result]
        assert "SOFI" not in tickers
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_empty_watchlist_returns_all(self):
        candidates = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        result = filter_out_watchlist(candidates, [])
        assert len(result) == 2

    def test_empty_candidates_returns_empty(self):
        result = filter_out_watchlist([], ["SOFI"])
        assert result == []


class TestTopUniqueByTicker:
    def test_keeps_only_best_row_per_ticker(self):
        candidates = [
            {"symbol": "AAPL", "annualized_roc": 30.0},
            {"symbol": "AAPL", "annualized_roc": 22.0},
            {"symbol": "MSFT", "annualized_roc": 18.0},
        ]
        result = top_unique_by_ticker(candidates, limit=5)
        assert len(result) == 2
        aapl = next(c for c in result if c["symbol"] == "AAPL")
        assert aapl["annualized_roc"] == pytest.approx(30.0)

    def test_limit_respected(self):
        candidates = [{"symbol": str(i), "annualized_roc": float(i)} for i in range(10)]
        result = top_unique_by_ticker(candidates, limit=5)
        assert len(result) == 5

    def test_sorted_by_annualized_roc_descending(self):
        candidates = [
            {"symbol": "A", "annualized_roc": 10.0},
            {"symbol": "B", "annualized_roc": 30.0},
            {"symbol": "C", "annualized_roc": 20.0},
        ]
        result = top_unique_by_ticker(candidates, limit=5)
        rocs = [c["annualized_roc"] for c in result]
        assert rocs == sorted(rocs, reverse=True)


class TestWriteMorningScan:
    def test_writes_valid_json_with_required_fields(self, tmp_path):
        out_path = tmp_path / "morning-scan.json"
        candidates = [
            {
                "symbol": "AAPL",
                "strike": 200.0,
                "expiration": "2026-10-16",
                "dte": 44,
                "delta": 0.24,
                "impliedVolatility": 32.5,
                "annualized_roc": 28.0,
                "sector": "Technology",
                "wheel_score": 75,
                "wheel_thesis": "Strong FCF and low debt.",
            }
        ]
        write_morning_scan(candidates, out_path)
        data = json.loads(out_path.read_text())
        assert "date" in data
        assert "candidates" in data
        assert len(data["candidates"]) == 1
        c = data["candidates"][0]
        assert c["ticker"] == "AAPL"
        assert c["strike"] == 200.0
        assert c["ann_roc_pct"] == pytest.approx(28.0)
        assert c["wheel_score"] == 75
        assert c["wheel_thesis"] == "Strong FCF and low debt."

    def test_writes_wheel_score_zero_when_absent(self, tmp_path):
        out_path = tmp_path / "morning-scan.json"
        candidates = [{"symbol": "AAPL", "strike": 200.0, "annualized_roc": 28.0}]
        write_morning_scan(candidates, out_path)
        data = json.loads(out_path.read_text())
        c = data["candidates"][0]
        assert c["wheel_score"] == 0
        assert c["wheel_thesis"] == ""

    def test_writes_empty_candidates_list(self, tmp_path):
        out_path = tmp_path / "morning-scan.json"
        write_morning_scan([], out_path)
        data = json.loads(out_path.read_text())
        assert data["candidates"] == []

    def test_creates_parent_directories(self, tmp_path):
        out_path = tmp_path / "sub" / "dir" / "morning-scan.json"
        write_morning_scan([], out_path)
        assert out_path.exists()


class TestFetchBriefWatchlist:
    def _resp(self, payload, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload
        resp.raise_for_status.side_effect = (
            None if status < 400 else Exception(f"HTTP {status}")
        )
        return resp

    def test_returns_prod_watchlist(self):
        payload = {"watchlist": ["GTLB", "NVDA", "IONQ"]}
        with patch("requests.get", return_value=self._resp(payload)) as get:
            result = fetch_brief_watchlist("https://example.test/api/watchlist")
        assert result == ["GTLB", "NVDA", "IONQ"]
        assert get.call_args.args[0] == "https://example.test/api/watchlist"

    def test_returns_none_on_http_error(self):
        with patch("requests.get", return_value=self._resp({}, status=502)):
            assert fetch_brief_watchlist("https://example.test/api/watchlist") is None

    def test_returns_none_on_network_error(self):
        with patch("requests.get", side_effect=OSError("connection refused")):
            assert fetch_brief_watchlist("https://example.test/api/watchlist") is None

    def test_returns_none_on_malformed_payload(self):
        with patch("requests.get", return_value=self._resp({"tickers": ["A"]})):
            assert fetch_brief_watchlist("https://example.test/api/watchlist") is None

    def test_returns_none_on_empty_watchlist(self):
        with patch("requests.get", return_value=self._resp({"watchlist": []})):
            assert fetch_brief_watchlist("https://example.test/api/watchlist") is None


def _fake_download(histories: dict[str, int]):
    """Build a yf.download-shaped frame: MultiIndex ("Close", TICKER) columns."""
    import pandas as pd

    length = max(histories.values())
    idx = pd.bdate_range(end="2026-09-18", periods=length)
    cols = {}
    for ticker, n in histories.items():
        values = [float("nan")] * (length - n) + [100.0 + i % 7 for i in range(n)]
        cols[("Close", ticker)] = values
    return pd.DataFrame(cols, index=idx)


class TestComputeWatchlistTechnicals:
    def test_sma_200_null_when_history_shorter_than_200_days(self, tmp_path):
        out = tmp_path / "watchlist-technicals.json"
        frame = _fake_download({"NEWIPO": 120, "QQQ": 250})
        with patch("yfinance.download", return_value=frame):
            compute_watchlist_technicals(["NEWIPO"], out_path=out)
        tech = json.loads(out.read_text())["technicals"]
        assert tech["NEWIPO"]["sma_200"] is None
        assert tech["NEWIPO"]["sma_50"] is not None
        assert tech["QQQ"]["sma_200"] is not None

    def test_writes_every_requested_ticker(self, tmp_path):
        out = tmp_path / "watchlist-technicals.json"
        frame = _fake_download({"GTLB": 250, "IONQ": 250, "QQQ": 250})
        with patch("yfinance.download", return_value=frame):
            compute_watchlist_technicals(["GTLB", "IONQ"], out_path=out)
        data = json.loads(out.read_text())
        assert set(data["technicals"]) == {"GTLB", "IONQ", "QQQ"}
        assert "watchlist_error" not in data

    def test_records_watchlist_error_and_still_computes_qqq(self, tmp_path):
        out = tmp_path / "watchlist-technicals.json"
        frame = _fake_download({"QQQ": 250})
        with patch("yfinance.download", return_value=frame):
            compute_watchlist_technicals(
                [], out_path=out, watchlist_error="prod watchlist API unavailable"
            )
        data = json.loads(out.read_text())
        assert data["watchlist_error"] == "prod watchlist API unavailable"
        assert set(data["technicals"]) == {"QQQ"}
        assert data["technicals"]["QQQ"]["bb_pct_b"] is not None
