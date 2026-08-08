"""Tests for Schwab sync — uses mocked MCP responses."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.wheel_tracker.store import ensure_wheel_tables


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


def _mock_session(accounts_json: str, transactions_json: str, positions_json: str):
    """Return a mock MCP ClientSession that returns preset responses."""
    session = AsyncMock()

    def _call_tool(name, args=None):
        result = MagicMock()
        if name == "get_accounts":
            result.content = [MagicMock(text=accounts_json)]
        elif name == "get_transactions":
            result.content = [MagicMock(text=transactions_json)]
        elif name == "get_account":
            result.content = [MagicMock(text=positions_json)]
        elif name == "get_option_chain":
            result.content = [MagicMock(text="[]")]
        else:
            result.content = [MagicMock(text="[]")]
        return result

    session.call_tool = AsyncMock(side_effect=_call_tool)
    session.initialize = AsyncMock()
    return session


SAMPLE_ACCOUNTS = json.dumps([{"accountNumber": "ACC1", "hashValue": "ACC1"}])

SAMPLE_TRANSACTIONS = json.dumps([
    {
        "activityId": "TXN001",
        "time": "2025-01-01T10:00:00+0000",
        "type": "TRADE",
        "description": "SELL TO OPEN 1 AAPL 01/17/2025 200.00 P",
        "netAmount": 149.35,
        "transactionItem": {
            "accountNumber": "ACC1",
            "amount": 1.0,
            "price": 1.50,
            "cost": 150.0,
            "instruction": "SELL_TO_OPEN",
            "instrument": {
                "symbol": "AAPL  250117P00200000",
                "assetType": "OPTION",
                "putCall": "PUT",
                "underlyingSymbol": "AAPL",
                "optionExpirationDate": "2025-01-17",
                "strikePrice": 200.0,
            },
        },
    }
])

SAMPLE_POSITIONS = json.dumps({
    "securitiesAccount": {
        "positions": [
            {
                "instrument": {
                    "symbol": "AAPL  250117P00200000",
                    "assetType": "OPTION",
                    "putCall": "PUT",
                    "underlyingSymbol": "AAPL",
                    "optionExpirationDate": "2025-01-17",
                    "strikePrice": 200.0,
                },
                "shortQuantity": 1.0,
                "longQuantity": 0.0,
                "averagePrice": 1.50,
                "marketValue": -80.0,
                "currentDayProfitLoss": 70.0,
                "currentDayCost": 0,
            }
        ]
    }
})


@pytest.mark.asyncio
async def test_sync_imports_transactions(conn):
    from src.wheel_tracker.sync import _sync_account
    session = _mock_session(SAMPLE_ACCOUNTS, SAMPLE_TRANSACTIONS, SAMPLE_POSITIONS)
    count = await _sync_account(conn, session, "ACC1", "2020-01-01", "2025-12-31")
    assert count == 1
    row = conn.execute("SELECT instruction, net_amount FROM wt_trades WHERE account_id='ACC1'").fetchone()
    assert row["instruction"] == "SELL_TO_OPEN"
    assert row["net_amount"] == pytest.approx(149.35)


@pytest.mark.asyncio
async def test_sync_is_idempotent(conn):
    from src.wheel_tracker.sync import _sync_account
    session = _mock_session(SAMPLE_ACCOUNTS, SAMPLE_TRANSACTIONS, SAMPLE_POSITIONS)
    await _sync_account(conn, session, "ACC1", "2020-01-01", "2025-12-31")
    await _sync_account(conn, session, "ACC1", "2020-01-01", "2025-12-31")
    count = conn.execute("SELECT COUNT(*) FROM wt_trades").fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_sync_upserts_positions(conn):
    from src.wheel_tracker.sync import _sync_positions
    session = _mock_session(SAMPLE_ACCOUNTS, SAMPLE_TRANSACTIONS, SAMPLE_POSITIONS)
    await _sync_positions(conn, session, "ACC1")
    count = conn.execute("SELECT COUNT(*) FROM wt_positions WHERE account_id='ACC1'").fetchone()[0]
    assert count == 1
