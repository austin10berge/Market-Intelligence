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
