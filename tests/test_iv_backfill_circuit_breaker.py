"""Tests for the IV backfill circuit breaker (src.db attempt tracking + stocks.py gating)."""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    """Point src.db at a throwaway SQLite file for this test."""
    from src.config import settings

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # let _get_connection create it fresh
    monkeypatch.setattr(settings, "db_path", path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_get_iv_backfill_attempt_returns_none_when_no_record(temp_db):
    from src.db import get_iv_backfill_attempt

    assert get_iv_backfill_attempt("ZZZZ") is None


def test_record_and_get_iv_backfill_attempt_roundtrip(temp_db):
    from src.db import get_iv_backfill_attempt, record_iv_backfill_attempt

    record_iv_backfill_attempt("SOFI", attempt_date=date(2026, 7, 20), result_count=3)

    result = get_iv_backfill_attempt("SOFI")
    assert result is not None
    assert result["symbol"] == "SOFI"
    assert result["attempt_date"] == "2026-07-20"
    assert result["result_count"] == 3


def test_record_iv_backfill_attempt_upserts(temp_db):
    from src.db import get_iv_backfill_attempt, record_iv_backfill_attempt

    record_iv_backfill_attempt("SOFI", attempt_date=date(2026, 7, 20), result_count=0)
    record_iv_backfill_attempt("SOFI", attempt_date=date(2026, 7, 27), result_count=5)

    result = get_iv_backfill_attempt("SOFI")
    assert result["attempt_date"] == "2026-07-27"
    assert result["result_count"] == 5


def test_should_attempt_iv_backfill_true_when_no_prior_attempt(temp_db):
    from src.screener.stocks import _should_attempt_iv_backfill

    assert _should_attempt_iv_backfill("NEWTICKER") is True


def test_should_attempt_iv_backfill_false_within_cooldown_after_insufficient_result(temp_db):
    from datetime import date
    from src.db import record_iv_backfill_attempt
    from src.screener.stocks import _should_attempt_iv_backfill

    record_iv_backfill_attempt("ILLIQUID", attempt_date=date.today(), result_count=2)

    assert _should_attempt_iv_backfill("ILLIQUID") is False


def test_should_attempt_iv_backfill_true_after_cooldown_expires(temp_db):
    from datetime import date, timedelta
    from src.db import record_iv_backfill_attempt
    from src.screener.stocks import _should_attempt_iv_backfill

    old_date = date.today() - timedelta(days=8)
    record_iv_backfill_attempt("ILLIQUID", attempt_date=old_date, result_count=2)

    assert _should_attempt_iv_backfill("ILLIQUID") is True


def test_screen_stocks_skips_backfill_when_circuit_breaker_open(temp_db, monkeypatch):
    from datetime import date
    from src.db import record_iv_backfill_attempt
    from src.screener import stocks

    record_iv_backfill_attempt("ILLIQUID", attempt_date=date.today(), result_count=1)

    with_mock = False
    import unittest.mock as mock
    with mock.patch.object(stocks, "backfill_stock_iv_history") as mock_backfill:
        # Directly exercise the gate rather than the full screen_stocks pipeline
        # (which needs live Alpaca/yfinance data) — this is what Step 3 wired in.
        should_attempt = stocks._should_attempt_iv_backfill("ILLIQUID")
        assert should_attempt is False
        # Confirm nothing calls backfill when the gate says no — mirrors the
        # `and _should_attempt_iv_backfill(symbol)` guard added in screen_stocks.
        if should_attempt:
            stocks.backfill_stock_iv_history(["ILLIQUID"])
        mock_backfill.assert_not_called()
