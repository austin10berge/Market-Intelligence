"""Tests for csp_scan_nightly — regime parsing, drift detection, JSON output."""
from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

import pytest

from src.screener.csp_scan_nightly import RegimeCaps, compute_drift, parse_regime_caps


SAMPLE_NOTE = """\
## Monthly Wheel Trading Plan (Exhibit 2E)

**Regime:** Bull — SPY above 200 SMA, low VIX.

**Trading Parameters:**
- Max delta (CSP): 0.25
- IV range target: 35–55%
- Max position size: 10% per ticker
- DTE range: 25–35
- Sectors to avoid: None
"""

BEAR_NOTE = """\
**Regime:** Bear — SPY below 200 SMA.

**Trading Parameters:**
- Max delta (CSP): 0.18
- IV range target: 30–45%
- DTE range: 25–35
"""


class TestParseRegimeCaps:
    def test_bull_regime(self):
        caps = parse_regime_caps(SAMPLE_NOTE)
        assert caps.regime == "bull"
        assert caps.delta_cap == pytest.approx(0.25)
        assert caps.iv_cap == pytest.approx(55.0)

    def test_bear_regime(self):
        caps = parse_regime_caps(BEAR_NOTE)
        assert caps.regime == "bear"
        assert caps.delta_cap == pytest.approx(0.18)
        assert caps.iv_cap == pytest.approx(45.0)

    def test_missing_note_returns_defaults(self):
        caps = parse_regime_caps("")
        assert caps.regime == "bull"
        assert caps.delta_cap == pytest.approx(0.30)
        assert caps.iv_cap == pytest.approx(55.0)
        assert caps.vix_threshold == pytest.approx(25.0)


class TestComputeDrift:
    def test_no_drift_when_above_sma_low_vix(self):
        snap = {"spy_price": 570.0, "spy_sma200": 540.0, "vix": 16.0}
        caps = RegimeCaps(vix_threshold=25.0)
        assert compute_drift(snap, caps) is False

    def test_drift_when_spy_below_sma200(self):
        snap = {"spy_price": 530.0, "spy_sma200": 540.0, "vix": 14.0}
        caps = RegimeCaps(vix_threshold=25.0)
        assert compute_drift(snap, caps) is True

    def test_drift_when_vix_above_threshold(self):
        snap = {"spy_price": 570.0, "spy_sma200": 540.0, "vix": 28.0}
        caps = RegimeCaps(vix_threshold=25.0)
        assert compute_drift(snap, caps) is True
