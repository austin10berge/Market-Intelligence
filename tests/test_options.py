"""Unit tests for src.screener.options — screen_csp_candidates().

Note: not named test_options_lookup.py (that file covers the unrelated
src/screener/options_lookup.py chat-driven options-grid feature).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_screen_csp_candidates_uses_precomputed_technicals(monkeypatch):
    from src.screener import options

    precomputed = {
        "AAPL": {"rsi": 45.0, "adx": 22.0, "sma50": 190.0, "return_5d": -1.2},
    }

    # No option chain data → screen_csp_candidates returns [] quickly, but we only
    # care whether _compute_technicals was called.
    monkeypatch.setattr(
        options,
        "get_csp_settings",
        lambda: {
            "min_dte": 7,
            "max_dte": 45,
            "min_rsi": 0,
            "max_rsi": 70,
            "min_adx": 15,
            "max_adx": 60,
            "pullback_mode": False,
        },
    )

    with patch.object(options, "_compute_technicals") as mock_compute, \
         patch.object(options.yf, "Ticker") as mock_ticker:
        mock_ticker.return_value.options = ()  # no expirations → loop exits cleanly
        options.screen_csp_candidates(
            tickers=["AAPL"], precomputed_technicals=precomputed
        )
        mock_compute.assert_not_called()


def test_screen_csp_candidates_falls_back_without_precomputed_technicals(monkeypatch):
    """Callers that don't pass precomputed_technicals (e.g. /api/screener/csp)
    must keep hitting _compute_technicals exactly as before."""
    from src.screener import options

    monkeypatch.setattr(
        options,
        "get_csp_settings",
        lambda: {
            "min_dte": 7,
            "max_dte": 45,
            "min_rsi": 0,
            "max_rsi": 70,
            "min_adx": 15,
            "max_adx": 60,
            "pullback_mode": False,
        },
    )

    with patch.object(options, "_compute_technicals") as mock_compute, \
         patch.object(options.yf, "Ticker") as mock_ticker:
        mock_compute.return_value = None  # short-circuits before touching yfinance
        options.screen_csp_candidates(tickers=["AAPL"])
        mock_compute.assert_called_once_with("AAPL")
