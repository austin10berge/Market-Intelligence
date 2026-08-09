"""Tests for wheel tracker alert generation."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.wheel_tracker.store import ensure_wheel_tables, upsert_position


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


def _pos(**kw) -> dict:
    exp = (date.today() + timedelta(days=10)).isoformat()
    base = dict(
        account_id="ACC1",
        symbol="AAPL  250117P00200000",
        underlying="AAPL",
        asset_type="OPTION",
        option_type="PUT",
        strike=200.0,
        expiration=exp,
        dte=10,
        quantity=-1.0,
        average_price=1.50,
        current_price=0.80,
        market_value=-80.0,
        unrealized_pnl=70.0,
        delta=0.18,
        refreshed_at="2025-01-07T17:00:00",
    )
    return {**base, **kw}


@pytest.mark.asyncio
async def test_no_alert_when_dte_above_threshold(conn):
    upsert_position(conn, _pos(dte=15))
    with patch("src.wheel_tracker.alerts._send_alert", new_callable=AsyncMock) as mock_alert:
        from src.wheel_tracker.alerts import check_alerts

        alerts = await check_alerts(conn)
    assert alerts == []
    mock_alert.assert_not_called()


@pytest.mark.asyncio
async def test_dte_alert_fires_when_dte_le_7(conn):
    upsert_position(conn, _pos(dte=5, symbol="AAPL  250107P00200000"))
    with patch("src.wheel_tracker.alerts._send_alert", new_callable=AsyncMock) as mock_alert:
        mock_alert.return_value = None
        from src.wheel_tracker.alerts import check_alerts

        alerts = await check_alerts(conn)
    assert len(alerts) == 1
    assert "DTE" in alerts[0] or "Expiring" in alerts[0]


@pytest.mark.asyncio
async def test_dte_alert_dedup(conn):
    today = date.today().isoformat()
    upsert_position(conn, _pos(dte=5, symbol="AAPL  250107P00200000"))
    conn.execute(
        "UPDATE wt_positions SET last_dte_alerted=? WHERE symbol=?",
        (today, "AAPL  250107P00200000"),
    )
    conn.commit()
    with patch("src.wheel_tracker.alerts._send_alert", new_callable=AsyncMock) as mock_alert:
        from src.wheel_tracker.alerts import check_alerts

        alerts = await check_alerts(conn)
    assert alerts == []
    mock_alert.assert_not_called()


@pytest.mark.asyncio
async def test_delta_alert_fires_when_above_threshold(conn):
    upsert_position(conn, _pos(delta=0.35, dte=20))
    with patch("src.wheel_tracker.alerts._send_alert", new_callable=AsyncMock) as mock_alert:
        mock_alert.return_value = None
        from src.wheel_tracker.alerts import check_alerts

        alerts = await check_alerts(conn)
    assert any("delta" in a.lower() or "assignment" in a.lower() for a in alerts)


@pytest.mark.asyncio
async def test_delta_alert_dedup(conn):
    today = date.today().isoformat()
    upsert_position(conn, _pos(delta=0.35, dte=20))
    conn.execute(
        "UPDATE wt_positions SET last_delta_alerted=? WHERE symbol=?",
        (today, "AAPL  250117P00200000"),
    )
    conn.commit()
    with patch("src.wheel_tracker.alerts._send_alert", new_callable=AsyncMock) as mock_alert:
        from src.wheel_tracker.alerts import check_alerts

        alerts = await check_alerts(conn)
    assert alerts == []
    mock_alert.assert_not_called()


@pytest.mark.asyncio
async def test_both_alerts_fire_independently(conn):
    """A position at DTE<=7 AND delta>=0.30 fires two separate alerts."""
    upsert_position(conn, _pos(dte=3, delta=0.40, symbol="AAPL  250103P00200000"))
    with patch("src.wheel_tracker.alerts._send_alert", new_callable=AsyncMock) as mock_alert:
        mock_alert.return_value = None
        from src.wheel_tracker.alerts import check_alerts

        alerts = await check_alerts(conn)
    assert len(alerts) == 2
    assert mock_alert.call_count == 2


@pytest.mark.asyncio
async def test_non_option_positions_ignored(conn):
    """Stock (non-OPTION) positions are not evaluated for alerts."""
    upsert_position(
        conn,
        _pos(
            asset_type="EQUITY",
            option_type=None,
            dte=None,
            delta=None,
            symbol="AAPL",
        ),
    )
    with patch("src.wheel_tracker.alerts._send_alert", new_callable=AsyncMock) as mock_alert:
        from src.wheel_tracker.alerts import check_alerts

        alerts = await check_alerts(conn)
    assert alerts == []
    mock_alert.assert_not_called()
