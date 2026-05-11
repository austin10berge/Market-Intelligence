"""Unit tests for the balance sheet / profitability gate logic
in _fundamental_filter_from_store."""

from __future__ import annotations
import pytest
from src.screener.csp_scanner import ScannerParams, _fundamental_filter_from_store


def _row(**kwargs) -> dict:
    """Build a minimal store row that passes all gates by default."""
    base = {
        "symbol": "TEST",
        "market_cap_b": 50.0,
        "price": 80.0,
        "beta": 1.2,
        "iv_pct": 35.0,
        "fcf": 5.0,
        "debt_to_equity": 0.8,
        "revenue_growth": 0.10,
        "earnings_growth": 0.05,
        "dividend_yield": 0.02,
    }
    base.update(kwargs)
    return base


def _run(rows: list[dict], **param_kwargs) -> list[str]:
    """Run _fundamental_filter_from_store and return passing symbols."""
    params = ScannerParams(**param_kwargs)
    store_lookup = {r["symbol"]: r for r in rows}
    passing, _ = _fundamental_filter_from_store(list(store_lookup.keys()), params, store_lookup)
    return passing


class TestFcfGate:
    def test_positive_fcf_passes_default_gate(self):
        result = _run([_row(symbol="A", fcf=1.0)])
        assert "A" in result

    def test_negative_fcf_fails_default_gate(self):
        result = _run([_row(symbol="A", fcf=-1.0)])
        assert "A" not in result

    def test_none_fcf_passes_gate(self):
        result = _run([_row(symbol="A", fcf=None)])
        assert "A" in result

    def test_gate_disabled_when_param_is_none(self):
        result = _run([_row(symbol="A", fcf=-999.0)], min_fcf_b=None)
        assert "A" in result


class TestDebtToEquityGate:
    def test_low_de_passes(self):
        result = _run([_row(symbol="A", debt_to_equity=1.0)])
        assert "A" in result

    def test_high_de_fails(self):
        result = _run([_row(symbol="A", debt_to_equity=3.0)])
        assert "A" not in result

    def test_none_de_passes(self):
        result = _run([_row(symbol="A", debt_to_equity=None)])
        assert "A" in result

    def test_gate_disabled_when_param_is_none(self):
        result = _run([_row(symbol="A", debt_to_equity=999.0)], max_debt_to_equity=None)
        assert "A" in result


class TestRevenueGrowthGate:
    def test_positive_growth_passes(self):
        result = _run([_row(symbol="A", revenue_growth=0.10)])
        assert "A" in result

    def test_severe_decline_fails(self):
        result = _run([_row(symbol="A", revenue_growth=-0.15)])
        assert "A" not in result

    def test_mild_decline_passes(self):
        result = _run([_row(symbol="A", revenue_growth=-0.05)])
        assert "A" in result

    def test_none_growth_passes(self):
        result = _run([_row(symbol="A", revenue_growth=None)])
        assert "A" in result

    def test_gate_disabled_when_param_is_none(self):
        result = _run([_row(symbol="A", revenue_growth=-0.99)], min_revenue_growth=None)
        assert "A" in result


class TestEarningsGrowthGate:
    def test_gate_off_by_default(self):
        result = _run([_row(symbol="A", earnings_growth=-0.80)])
        assert "A" in result

    def test_gate_active_when_set(self):
        result = _run([_row(symbol="A", earnings_growth=-0.50)], min_earnings_growth=-0.20)
        assert "A" not in result

    def test_none_data_passes_active_gate(self):
        result = _run([_row(symbol="A", earnings_growth=None)], min_earnings_growth=-0.20)
        assert "A" in result


class TestDividendYieldGate:
    def test_gate_off_by_default(self):
        result = _run([_row(symbol="A", dividend_yield=0.0)])
        assert "A" in result

    def test_gate_active_when_set(self):
        result = _run([_row(symbol="A", dividend_yield=0.005)], min_dividend_yield=0.02)
        assert "A" not in result

    def test_none_data_passes_active_gate(self):
        result = _run([_row(symbol="A", dividend_yield=None)], min_dividend_yield=0.02)
        assert "A" in result
