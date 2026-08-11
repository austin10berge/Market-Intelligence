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


from unittest.mock import patch
import asyncio
from src.wheel_tracker.equity_curve import rebuild_equity_curve, DEPOSIT_EVENTS


def test_deposit_events_defined():
    assert len(DEPOSIT_EVENTS) >= 2
    for evt in DEPOSIT_EVENTS:
        assert "date" in evt
        assert "amount" in evt


def _insert_test_trades(conn):
    """Insert a minimal set of trades: one CSP open (premium in) and a stock buy."""
    from src.wheel_tracker.store import ensure_wheel_tables
    ensure_wheel_tables(conn)
    conn.executemany(
        """INSERT INTO wt_trades
           (schwab_transaction_id, account_id, executed_at, asset_type, symbol,
            underlying, option_type, strike, expiration, instruction, quantity,
            price, commission, net_amount)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("t1", "A1", "2026-01-06T10:00:00", "OPTION", "AAPL  260117P00200000",
             "AAPL", "PUT", 200.0, "2026-01-17", "SELL_TO_OPEN", 1, 3.50, 0.65, 349.35),
            ("t2", "A1", "2026-01-12T10:00:00", "EQUITY", "AAPL",
             None, None, None, None, "BUY", 100, 195.0, 0.0, -19500.0),
        ],
    )
    conn.commit()


def _make_spy_df():
    """Build a minimal DataFrame mimicking yfinance output for SPY."""
    import pandas as pd
    dates = pd.bdate_range("2026-01-02", "2026-01-14")
    closes = [480.0 + i * 0.5 for i in range(len(dates))]
    return pd.DataFrame({"Close": closes}, index=dates)


def _make_aapl_df():
    """Build a minimal DataFrame mimicking yfinance output for AAPL."""
    import pandas as pd
    dates = pd.bdate_range("2026-01-02", "2026-01-14")
    closes = [195.0 + i * 0.3 for i in range(len(dates))]
    return pd.DataFrame({"Close": closes}, index=dates)


def test_rebuild_equity_curve_basic(conn):
    _insert_test_trades(conn)

    captured_kwargs = {}

    async def mock_download(*args, **kwargs):
        import pandas as pd
        captured_kwargs.update(kwargs)
        tickers_arg = args[0] if args else kwargs.get("tickers", "")
        if "SPY" in tickers_arg and "AAPL" in tickers_arg:
            spy_df = _make_spy_df()
            aapl_df = _make_aapl_df()
            result = pd.concat({"SPY": spy_df, "AAPL": aapl_df}, axis=1)
            return result
        elif "SPY" in tickers_arg:
            return _make_spy_df()
        return _make_aapl_df()

    with patch("src.wheel_tracker.equity_curve.download_with_retry", side_effect=mock_download):
        with patch("src.wheel_tracker.equity_curve._ytd_start", return_value="2026-01-02"):
            count = asyncio.get_event_loop().run_until_complete(rebuild_equity_curve(conn))

    assert count > 0

    # Regression check: rebuild_equity_curve must request group_by="ticker" so
    # that a real (non-mocked) yf.download() call returns (Ticker, Field)
    # column ordering — the same ordering src/fetchers/sector_etf.py relies on
    # group_by="ticker" for. Without this, real yfinance responses default to
    # (Field, Ticker) ordering (see src/market_data/refresh.py:162-163) and the
    # .loc[dt, (sym, "Close")] lookups below would silently miss, falling back
    # to cost-basis/None forever.
    assert captured_kwargs.get("group_by") == "ticker"

    # The mock's own DataFrame (built via pd.concat({"SPY": ..., "AAPL": ...}))
    # puts Ticker as column level 0 — confirm that matches what group_by="ticker"
    # actually produces, so this test isn't just agreeing with itself.
    from src.wheel_tracker.store import read_equity_curve
    curve = read_equity_curve(conn, "2026-01-01")
    assert len(curve) == count
    assert curve[0]["spy_close"] is not None
    # After the AAPL buy on Jan 12 (next trading day after the Jan 10 weekend),
    # equity should include mark-to-market stock value
    post_buy = [r for r in curve if r["date"] >= "2026-01-10"]
    assert len(post_buy) > 0
    for r in post_buy:
        assert r["equity"] > r["cash"]  # equity includes stock position value
