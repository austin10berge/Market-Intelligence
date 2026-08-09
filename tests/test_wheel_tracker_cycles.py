"""Tests for wheel-cycle linking algorithm."""
from __future__ import annotations

import sqlite3
import tempfile
import os
import pytest

from src.wheel_tracker.store import ensure_wheel_tables, upsert_trade


@pytest.fixture
def conn():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    c = sqlite3.connect(f.name)
    c.row_factory = sqlite3.Row
    ensure_wheel_tables(c)
    yield c
    c.close()
    os.unlink(f.name)


def _t(**kw) -> dict:
    """Minimal trade dict with sensible defaults."""
    base = dict(
        schwab_transaction_id="tx",
        account_id="ACC1",
        executed_at="2025-01-01T10:00:00",
        settled_date=None,
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
    return {**base, **kw}


def test_csp_expired_worthless(conn):
    """SELL_TO_OPEN + EXPIRED on same symbol → 1 closed cycle, full premium captured."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
                          executed_at="2025-01-01T10:00:00", net_amount=149.35))
    upsert_trade(conn, _t(schwab_transaction_id="t2", instruction="EXPIRED",
                          executed_at="2025-01-17T16:00:00", net_amount=0.0, quantity=1.0))

    from src.wheel_tracker.cycles import link_cycles
    n = link_cycles(conn)

    assert n == 1
    cycle = conn.execute("SELECT * FROM wt_cycles").fetchone()
    assert cycle["status"] == "CLOSED"
    assert cycle["total_premium"] == pytest.approx(149.35)
    assert cycle["realized_pnl"] == pytest.approx(149.35)
    linked = conn.execute("SELECT COUNT(*) FROM wt_trades WHERE cycle_id = ?", (cycle["id"],)).fetchone()[0]
    assert linked == 2


def test_csp_bought_back(conn):
    """SELL_TO_OPEN + BUY_TO_CLOSE → 1 closed cycle."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
                          executed_at="2025-01-01T10:00:00", net_amount=149.35))
    upsert_trade(conn, _t(schwab_transaction_id="t2", instruction="BUY_TO_CLOSE",
                          executed_at="2025-01-10T10:00:00", net_amount=-30.0, quantity=1.0))

    from src.wheel_tracker.cycles import link_cycles
    n = link_cycles(conn)

    assert n == 1
    cycle = conn.execute("SELECT * FROM wt_cycles").fetchone()
    assert cycle["status"] == "CLOSED"
    assert cycle["realized_pnl"] == pytest.approx(119.35)


def test_csp_open_no_close(conn):
    """SELL_TO_OPEN with no matching close → 1 OPEN cycle."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
                          executed_at="2025-01-01T10:00:00", net_amount=149.35))

    from src.wheel_tracker.cycles import link_cycles
    n = link_cycles(conn)

    assert n == 1
    cycle = conn.execute("SELECT * FROM wt_cycles").fetchone()
    assert cycle["status"] == "OPEN"


def test_full_wheel_cycle(conn):
    """CSP assigned → shares bought → CC expired worthless → cycle still OPEN (shares held), all 5 trades linked."""
    trades = [
        _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
           executed_at="2025-01-01T10:00:00", net_amount=149.35,
           asset_type="OPTION", option_type="PUT", symbol="AAPL  250117P00200000"),
        _t(schwab_transaction_id="t2", instruction="ASSIGNED",
           executed_at="2025-01-17T16:00:00", net_amount=0.0, quantity=1.0,
           asset_type="OPTION", option_type="PUT", symbol="AAPL  250117P00200000"),
        _t(schwab_transaction_id="t3", instruction="BUY",
           executed_at="2025-01-17T16:01:00", net_amount=-20000.0, quantity=100.0,
           asset_type="EQUITY", symbol="AAPL", underlying=None, option_type=None,
           strike=None, expiration=None),
        _t(schwab_transaction_id="t4", instruction="SELL_TO_OPEN",
           executed_at="2025-01-20T10:00:00", net_amount=120.0, quantity=-1.0,
           asset_type="OPTION", option_type="CALL", symbol="AAPL  250221C00210000",
           underlying="AAPL", strike=210.0, expiration="2025-02-21"),
        _t(schwab_transaction_id="t5", instruction="EXPIRED",
           executed_at="2025-02-21T16:00:00", net_amount=0.0, quantity=1.0,
           asset_type="OPTION", option_type="CALL", symbol="AAPL  250221C00210000",
           underlying="AAPL", strike=210.0, expiration="2025-02-21"),
    ]
    for t in trades:
        upsert_trade(conn, t)

    from src.wheel_tracker.cycles import link_cycles
    n = link_cycles(conn)

    assert n == 1
    cycle = conn.execute("SELECT * FROM wt_cycles").fetchone()
    assert cycle["status"] == "OPEN"  # still open: shares held, CC expired, no new CC or exit yet
    linked = conn.execute("SELECT COUNT(*) FROM wt_trades WHERE cycle_id IS NOT NULL").fetchone()[0]
    assert linked == 5


def test_standalone_equity_trade_unlinked(conn):
    """A plain stock BUY with no options context stays unlinked (cycle_id NULL)."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="BUY",
                          asset_type="EQUITY", symbol="MSFT", underlying=None,
                          option_type=None, strike=None, expiration=None,
                          net_amount=-40000.0, quantity=100.0))

    from src.wheel_tracker.cycles import link_cycles
    link_cycles(conn)

    row = conn.execute("SELECT cycle_id FROM wt_trades WHERE schwab_transaction_id='t1'").fetchone()
    assert row["cycle_id"] is None


def test_link_cycles_idempotent(conn):
    """Calling link_cycles twice on same data creates no duplicate cycles."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
                          executed_at="2025-01-01T10:00:00", net_amount=149.35))

    from src.wheel_tracker.cycles import link_cycles
    link_cycles(conn)
    n2 = link_cycles(conn)

    assert n2 == 0
    assert conn.execute("SELECT COUNT(*) FROM wt_cycles").fetchone()[0] == 1
