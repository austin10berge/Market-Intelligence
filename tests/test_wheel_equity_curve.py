"""Tests for wt_equity_curve table and store helpers."""
from __future__ import annotations

import sqlite3
import pytest
from src.wheel_tracker.store import ensure_wheel_tables, write_equity_curve, read_equity_curve


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    ensure_wheel_tables(c)
    return c


def test_write_and_read_equity_curve(conn):
    rows = [
        {"date": "2026-01-02", "equity": 20000.0, "cash": 20000.0, "deposits": 20000.0, "spy_close": 480.0},
        {"date": "2026-01-03", "equity": 20050.0, "cash": 19800.0, "deposits": 20000.0, "spy_close": 481.5},
    ]
    write_equity_curve(conn, rows)
    result = read_equity_curve(conn, "2026-01-01")
    assert len(result) == 2
    assert result[0]["date"] == "2026-01-02"
    assert result[0]["equity"] == 20000.0
    assert result[1]["spy_close"] == 481.5


def test_write_replaces_existing(conn):
    rows_v1 = [{"date": "2026-01-02", "equity": 100.0, "cash": 100.0, "deposits": 0.0, "spy_close": 480.0}]
    rows_v2 = [{"date": "2026-01-02", "equity": 200.0, "cash": 200.0, "deposits": 0.0, "spy_close": 482.0}]
    write_equity_curve(conn, rows_v1)
    write_equity_curve(conn, rows_v2)
    result = read_equity_curve(conn, "2026-01-01")
    assert len(result) == 1
    assert result[0]["equity"] == 200.0


def test_read_filters_by_date(conn):
    rows = [
        {"date": "2025-12-30", "equity": 19000.0, "cash": 19000.0, "deposits": 20000.0, "spy_close": 475.0},
        {"date": "2026-01-02", "equity": 20000.0, "cash": 20000.0, "deposits": 20000.0, "spy_close": 480.0},
    ]
    write_equity_curve(conn, rows)
    result = read_equity_curve(conn, "2026-01-01")
    assert len(result) == 1
    assert result[0]["date"] == "2026-01-02"


def test_read_empty_table(conn):
    result = read_equity_curve(conn, "2026-01-01")
    assert result == []
