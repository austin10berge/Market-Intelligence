"""Tests for trading-day gating (src.cache.is_trading_day, src.main main() gate)."""

from __future__ import annotations

import sys
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.cache import is_trading_day


def test_is_trading_day_weekday():
    assert is_trading_day(date(2026, 7, 6)) is True  # Monday


def test_is_trading_day_weekend():
    assert is_trading_day(date(2026, 7, 4)) is False  # Saturday
    assert is_trading_day(date(2026, 7, 5)) is False  # Sunday


def test_is_trading_day_nyse_holiday():
    assert is_trading_day(date(2026, 1, 1)) is False  # New Year's Day
    assert is_trading_day(date(2026, 12, 25)) is False  # Christmas


@pytest.mark.parametrize("argv_mode", [[], ["--mode", "scheduled"]])
def test_main_skips_run_pipeline_on_non_trading_day(monkeypatch, argv_mode):
    from src import main as main_module

    monkeypatch.setattr(sys, "argv", ["main.py", *argv_mode])
    with patch.object(main_module, "is_trading_day", return_value=False) as mock_check:
        with patch.object(main_module, "run_pipeline", new=AsyncMock()) as mock_run:
            main_module.main()

    mock_check.assert_called_once()
    mock_run.assert_not_called()


def test_main_runs_on_demand_even_on_non_trading_day(monkeypatch):
    from src import main as main_module

    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", "on-demand"])
    with patch.object(main_module, "is_trading_day", return_value=False):
        with patch.object(
            main_module, "run_pipeline", new=AsyncMock(return_value=None)
        ) as mock_run:
            main_module.main()

    mock_run.assert_called_once()
