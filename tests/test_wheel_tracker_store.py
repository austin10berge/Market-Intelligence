"""Tests for wheel tracker DB store functions."""
from __future__ import annotations

import sqlite3
import tempfile
import os
import pytest
from unittest.mock import patch

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_path = _tmp.name
_tmp.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    with patch("src.db.settings") as m:
        m.db_path = _tmp_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup():
    yield
    try:
        os.unlink(_tmp_path)
    except OSError:
        pass


def _conn():
    conn = sqlite3.connect(_tmp_path)
    conn.row_factory = sqlite3.Row
    from src.wheel_tracker.store import ensure_wheel_tables

    ensure_wheel_tables(conn)
    return conn


def _trade(**kw) -> dict:
    defaults = dict(
        schwab_transaction_id="t1",
        account_id="ACC1",
        executed_at="2025-01-01T10:00:00",
        settled_date="2025-01-03",
        asset_type="OPTION",
        symbol="AAPL  250117P00200000",
        underlying="AAPL",
        option_type="PUT",
        strike=200.0,
        expiration="2025-01-17",
        instruction="SELL_TO_OPEN",
        quantity=-1.0,
        price=1.50,
        commission=0.65,
        net_amount=149.35,
    )
    return {**defaults, **kw}


def test_upsert_trade_returns_id():
    from src.wheel_tracker.store import upsert_trade

    conn = _conn()
    tid = upsert_trade(conn, _trade())
    assert isinstance(tid, int) and tid > 0


def test_upsert_trade_idempotent():
    from src.wheel_tracker.store import upsert_trade

    conn = _conn()
    t = _trade(schwab_transaction_id="t_idem")
    id1 = upsert_trade(conn, t)
    id2 = upsert_trade(conn, t)
    assert id1 == id2


def test_get_last_executed_at_none_when_empty():
    from src.wheel_tracker.store import get_last_executed_at

    conn = _conn()
    assert get_last_executed_at(conn, "NO_ACCOUNT") is None


def test_get_last_executed_at_returns_max():
    from src.wheel_tracker.store import upsert_trade, get_last_executed_at

    conn = _conn()
    upsert_trade(conn, _trade(schwab_transaction_id="ta", executed_at="2025-01-01T10:00:00", account_id="ACCA"))
    upsert_trade(conn, _trade(schwab_transaction_id="tb", executed_at="2025-03-15T14:30:00", account_id="ACCA"))
    assert get_last_executed_at(conn, "ACCA") == "2025-03-15T14:30:00"


def test_upsert_position_preserves_alert_columns():
    from src.wheel_tracker.store import upsert_position, update_position_delta

    conn = _conn()
    pos = dict(
        account_id="ACC1",
        symbol="AAPL  250117P00200000",
        underlying="AAPL",
        asset_type="OPTION",
        option_type="PUT",
        strike=200.0,
        expiration="2025-01-17",
        dte=10,
        quantity=-1.0,
        average_price=1.50,
        current_price=0.80,
        market_value=-80.0,
        unrealized_pnl=70.0,
        delta=None,
        refreshed_at="2025-01-07T17:00:00",
    )
    upsert_position(conn, pos)
    # Simulate alert column being set
    conn.execute(
        "UPDATE wt_positions SET last_dte_alerted='2025-01-07' WHERE symbol=?",
        ("AAPL  250117P00200000",),
    )
    conn.commit()
    # Re-upsert (next pipeline run)
    pos["refreshed_at"] = "2025-01-08T17:00:00"
    pos["dte"] = 9
    upsert_position(conn, pos)
    row = conn.execute(
        "SELECT last_dte_alerted FROM wt_positions WHERE symbol=?",
        ("AAPL  250117P00200000",),
    ).fetchone()
    assert row["last_dte_alerted"] == "2025-01-07"  # preserved


def test_create_and_update_cycle():
    from src.wheel_tracker.store import create_cycle, update_cycle

    conn = _conn()
    cid = create_cycle(
        conn,
        dict(
            underlying="AAPL",
            account_id="ACC1",
            status="OPEN",
            opened_at="2025-01-01",
            closed_at=None,
            total_premium=149.35,
            realized_pnl=None,
            auto_detected=1,
        ),
    )
    assert isinstance(cid, int) and cid > 0
    update_cycle(conn, cid, {"status": "CLOSED", "closed_at": "2025-01-17", "realized_pnl": 149.35})
    row = conn.execute("SELECT status, realized_pnl FROM wt_cycles WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "CLOSED"
    assert row["realized_pnl"] == pytest.approx(149.35)


def test_insert_note():
    from src.wheel_tracker.store import insert_note

    conn = _conn()
    insert_note(conn, dict(trade_id=None, cycle_id=1, source="discord", content="Testing"))
    count = conn.execute("SELECT COUNT(*) FROM wt_notes").fetchone()[0]
    assert count >= 1
