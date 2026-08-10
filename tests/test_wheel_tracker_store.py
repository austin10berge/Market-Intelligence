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


def test_strategy_label_covers_every_wheel_leg():
    from src.wheel_tracker.store import _strategy_label

    assert _strategy_label("OPTION", "PUT", "SELL_TO_OPEN") == "Cash-Secured Put"
    assert _strategy_label("OPTION", "PUT", "BUY_TO_CLOSE") == "CSP Closed"
    assert _strategy_label("OPTION", "PUT", "EXPIRED") == "CSP Closed"
    assert _strategy_label("OPTION", "PUT", "BUY_TO_OPEN") == "Long Put"
    assert _strategy_label("OPTION", "PUT", "SELL_TO_CLOSE") == "Long Put Closed"
    assert _strategy_label("OPTION", "PUT", "ASSIGNED") == "Put Assigned"
    assert _strategy_label("OPTION", "CALL", "SELL_TO_OPEN") == "Covered Call"
    assert _strategy_label("OPTION", "CALL", "BUY_TO_CLOSE") == "Covered Call Closed"
    assert _strategy_label("OPTION", "CALL", "EXPIRED") == "Covered Call Closed"
    assert _strategy_label("OPTION", "CALL", "BUY_TO_OPEN") == "Long Call"
    assert _strategy_label("OPTION", "CALL", "SELL_TO_CLOSE") == "Long Call Closed"
    assert _strategy_label("OPTION", "CALL", "ASSIGNED") == "Call Assigned"
    assert _strategy_label("EQUITY", None, "BUY") == "Shares Bought"
    assert _strategy_label("EQUITY", None, "BUY_TO_OPEN") == "Shares Bought"
    assert _strategy_label("EQUITY", None, "SELL") == "Shares Sold"
    assert _strategy_label("EQUITY", None, "SELL_TO_CLOSE") == "Shares Sold"


def test_strategy_label_falls_back_to_instruction_for_unknown_combo():
    from src.wheel_tracker.store import _strategy_label

    assert _strategy_label("EQUITY", None, "SPLIT") == "SPLIT"
    assert _strategy_label("OPTION", "CALL", "UNKNOWN") == "UNKNOWN"


def test_get_ticker_ledger_groups_multiple_rounds_into_one_entry():
    """Historically the cycle-linker made a new wt_cycles row per CSP round —
    two CSP rounds on the same ticker must collapse into a single ledger entry."""
    from src.wheel_tracker.store import upsert_trade, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="r1a", symbol="SOFI  250117P00015000",
        underlying="SOFI", instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00",
        net_amount=50.0,
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="r1b", symbol="SOFI  250117P00015000",
        underlying="SOFI", instruction="EXPIRED", executed_at="2025-01-17T21:00:00",
        net_amount=0.0,
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="r2a", symbol="SOFI  250321P00016000",
        underlying="SOFI", instruction="SELL_TO_OPEN", executed_at="2025-02-01T10:00:00",
        net_amount=60.0,
    ))

    tickers = get_ticker_ledger(conn)
    sofi_entries = [t for t in tickers if t["underlying"] == "SOFI"]
    assert len(sofi_entries) == 1
    assert len(sofi_entries[0]["trades"]) == 3
    assert sofi_entries[0]["total_premium"] == pytest.approx(110.0)


def test_get_ticker_ledger_includes_covered_call_with_no_prior_csp():
    """The old linker required a CSP to start a cycle; a CC written on shares
    that were never wheeled through a CSP must still appear."""
    from src.wheel_tracker.store import upsert_trade, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="cc1", asset_type="OPTION", symbol="MSFT  250620C00450000",
        underlying="MSFT", option_type="CALL", instruction="SELL_TO_OPEN",
        executed_at="2025-01-05T10:00:00", net_amount=120.0,
    ))

    tickers = get_ticker_ledger(conn)
    msft = next(t for t in tickers if t["underlying"] == "MSFT")
    assert len(msft["trades"]) == 1
    assert msft["trades"][0]["strategy"] == "Covered Call"


def test_get_ticker_ledger_includes_plain_equity_trade():
    from src.wheel_tracker.store import upsert_trade, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="eq1", asset_type="EQUITY", symbol="NVDA",
        underlying=None, option_type=None, strike=None, expiration=None,
        instruction="BUY", executed_at="2025-01-02T10:00:00", net_amount=-500.0,
    ))

    tickers = get_ticker_ledger(conn)
    nvda = next(t for t in tickers if t["underlying"] == "NVDA")
    assert nvda["trades"][0]["strategy"] == "Shares Bought"
    assert nvda["realized_pnl"] == pytest.approx(-500.0)


def test_get_ticker_ledger_excludes_mutual_fund_rows():
    from src.wheel_tracker.store import upsert_trade, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="mf1", asset_type="MUTUAL_FUND", symbol="SWVXX",
        underlying=None, option_type=None, strike=None, expiration=None,
        instruction="BUY_TO_OPEN", executed_at="2025-01-02T10:00:00", net_amount=-1000.0,
    ))

    tickers = get_ticker_ledger(conn)
    assert not any(t["underlying"] == "SWVXX" for t in tickers)


def test_get_ticker_ledger_status_reflects_open_position():
    from src.wheel_tracker.store import upsert_trade, upsert_position, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="hood1", symbol="HOOD  250117P00030000",
        underlying="HOOD", instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00",
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="crwv1", symbol="CRWV  250117P00050000",
        underlying="CRWV", instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00",
    ))
    upsert_position(conn, dict(
        account_id="ACC1", symbol="CRWV  250117P00050000", underlying="CRWV",
        asset_type="OPTION", option_type="PUT", strike=50.0, expiration="2025-01-17",
        dte=10, quantity=-1.0, average_price=1.0, current_price=0.5, market_value=-50.0,
        unrealized_pnl=50.0, delta=-0.2, refreshed_at="2025-01-07T17:00:00",
    ))

    tickers = {t["underlying"]: t for t in get_ticker_ledger(conn)}
    assert tickers["CRWV"]["status"] == "ACTIVE"
    assert tickers["HOOD"]["status"] == "CLOSED"


def test_get_ticker_ledger_orders_active_first_then_recency():
    from src.wheel_tracker.store import upsert_trade, upsert_position, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="old1", symbol="IOT   250117P00020000",
        underlying="IOT", instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00",
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="new1", symbol="DRAM  250321P00040000",
        underlying="DRAM", instruction="SELL_TO_OPEN", executed_at="2025-03-01T10:00:00",
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="active1", symbol="WMT   250321P00100000",
        underlying="WMT", instruction="SELL_TO_OPEN", executed_at="2025-01-15T10:00:00",
    ))
    upsert_position(conn, dict(
        account_id="ACC1", symbol="WMT   250321P00100000", underlying="WMT",
        asset_type="OPTION", option_type="PUT", strike=100.0, expiration="2025-03-21",
        dte=10, quantity=-1.0, average_price=1.0, current_price=0.5, market_value=-50.0,
        unrealized_pnl=50.0, delta=-0.2, refreshed_at="2025-01-07T17:00:00",
    ))

    relevant = {"WMT", "DRAM", "IOT"}
    order = [t["underlying"] for t in get_ticker_ledger(conn) if t["underlying"] in relevant]
    assert order == ["WMT", "DRAM", "IOT"]


def test_wheel_stats_win_rate_counts_closed_legs_only():
    """A symbol with only an opening trade (still open) must not count toward
    win_rate at all — win_rate is closed-leg wins / closed-leg total."""
    from src.wheel_tracker.store import upsert_trade, get_wheel_stats

    conn = _conn()
    conn.execute("DELETE FROM wt_trades")
    conn.execute("DELETE FROM wt_positions")
    conn.commit()
    # Closed, net positive -> win
    upsert_trade(conn, _trade(
        schwab_transaction_id="w1", symbol="AAA   250117P00010000",
        instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00", net_amount=100.0,
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="w2", symbol="AAA   250117P00010000",
        instruction="EXPIRED", executed_at="2025-01-17T21:00:00", net_amount=0.0,
    ))
    # Closed, net negative -> loss
    upsert_trade(conn, _trade(
        schwab_transaction_id="l1", symbol="BBB   250117P00010000",
        instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00", net_amount=50.0,
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="l2", symbol="BBB   250117P00010000",
        instruction="BUY_TO_CLOSE", executed_at="2025-01-10T10:00:00", net_amount=-200.0,
    ))
    # Still open -> excluded entirely
    upsert_trade(conn, _trade(
        schwab_transaction_id="o1", symbol="CCC   250117P00010000",
        instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00", net_amount=75.0,
    ))

    stats = get_wheel_stats(conn)
    assert stats["win_rate"] == pytest.approx(0.5)


def test_wheel_stats_win_rate_none_when_no_closed_legs():
    from src.wheel_tracker.store import upsert_trade, get_wheel_stats

    conn = _conn()
    conn.execute("DELETE FROM wt_trades")
    conn.execute("DELETE FROM wt_positions")
    conn.commit()
    upsert_trade(conn, _trade(schwab_transaction_id="o1", instruction="SELL_TO_OPEN"))

    stats = get_wheel_stats(conn)
    assert stats["win_rate"] is None


def test_wheel_stats_ticker_counts():
    from src.wheel_tracker.store import upsert_trade, upsert_position, get_wheel_stats

    conn = _conn()
    conn.execute("DELETE FROM wt_trades")
    conn.execute("DELETE FROM wt_positions")
    conn.commit()
    upsert_trade(conn, _trade(
        schwab_transaction_id="t1", symbol="AAA   250117P00010000",
        underlying="AAA", instruction="SELL_TO_OPEN",
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="t2", symbol="BBB   250117P00010000",
        underlying="BBB", instruction="SELL_TO_OPEN",
    ))
    upsert_position(conn, dict(
        account_id="ACC1", symbol="AAA   250117P00010000", underlying="AAA",
        asset_type="OPTION", option_type="PUT", strike=10.0, expiration="2025-01-17",
        dte=10, quantity=-1.0, average_price=1.0, current_price=0.5, market_value=-50.0,
        unrealized_pnl=50.0, delta=-0.2, refreshed_at="2025-01-07T17:00:00",
    ))

    stats = get_wheel_stats(conn)
    assert stats["total_tickers"] == 2
    assert stats["active_tickers"] == 1


def test_insert_note():
    from src.wheel_tracker.store import insert_note

    conn = _conn()
    insert_note(conn, dict(trade_id=None, cycle_id=1, source="discord", content="Testing"))
    count = conn.execute("SELECT COUNT(*) FROM wt_notes").fetchone()[0]
    assert count >= 1
