"""Tests for Schwab sync — uses mocked MCP responses."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
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


SAMPLE_ACCOUNTS = json.dumps(
    [{"securitiesAccount": {"accountNumber": "13879857", "accountHash": "ACC1"}}]
)

# Real schwab-mcp shape: transferItems is a list mixing CURRENCY fee entries with the
# actual traded instrument; there is no "transactionItem"/"instruction" field — the
# direction is derived from positionEffect + the sign of "amount".
SAMPLE_TRANSACTIONS = json.dumps(
    [
        {
            "activityId": "TXN001",
            "time": "2025-01-01T10:00:00+0000",
            "type": "TRADE",
            "netAmount": 149.35,
            "settlementDate": "2025-01-02T00:00:00+0000",
            "transferItems": [
                {
                    "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
                    "amount": 0.65,
                    "cost": -0.65,
                    "feeType": "COMMISSION",
                },
                {
                    "instrument": {
                        "symbol": "AAPL  250117P00200000",
                        "assetType": "OPTION",
                        "putCall": "PUT",
                        "underlyingSymbol": "AAPL",
                        "expirationDate": "2025-01-17T00:00:00+0000",
                        "strikePrice": 200.0,
                    },
                    "amount": -1.0,
                    "price": 1.50,
                    "positionEffect": "OPENING",
                },
            ],
        }
    ]
)

# Real schwab-mcp shape: get_account's positions never include strikePrice/
# optionExpirationDate on the instrument — only symbol/putCall/underlyingSymbol.
# strike/expiration must be derived from the OCC-format symbol.
SAMPLE_POSITIONS = json.dumps(
    {
        "securitiesAccount": {
            "positions": [
                {
                    "instrument": {
                        "symbol": "AAPL  250117P00200000",
                        "assetType": "OPTION",
                        "putCall": "PUT",
                        "underlyingSymbol": "AAPL",
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
    }
)


@pytest.mark.asyncio
async def test_sync_imports_transactions(conn):
    from src.wheel_tracker.sync import _sync_account

    session = _mock_session(SAMPLE_ACCOUNTS, SAMPLE_TRANSACTIONS, SAMPLE_POSITIONS)
    count = await _sync_account(conn, session, "ACC1", "2020-01-01", "2025-12-31")
    assert count == 1
    row = conn.execute(
        "SELECT instruction, net_amount FROM wt_trades WHERE account_id='ACC1'"
    ).fetchone()
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
    row = conn.execute(
        "SELECT strike, expiration FROM wt_positions WHERE account_id='ACC1'"
    ).fetchone()
    # instrument has no strikePrice/optionExpirationDate — must be derived from the
    # OCC symbol 'AAPL  250117P00200000' (see _parse_occ_symbol).
    assert row["strike"] == pytest.approx(200.0)
    assert row["expiration"] == "2025-01-17"


def test_parse_accounts_reads_account_hash():
    from src.wheel_tracker.sync import _parse_accounts

    account_ids = _parse_accounts(SAMPLE_ACCOUNTS)
    assert account_ids == ["ACC1"]


def test_parse_occ_symbol_extracts_strike_and_expiration():
    from src.wheel_tracker.sync import _parse_occ_symbol

    strike, expiration = _parse_occ_symbol("CRM   270617C00190000")
    assert strike == pytest.approx(190.0)
    assert expiration == "2027-06-17"


@pytest.mark.asyncio
async def test_sync_account_call_uses_account_hash_param(conn):
    """_sync_account must call get_transactions with account_hash/start_date/end_date/
    transaction_type — not the raw Schwab REST field names (accountNumber/startDate/
    endDate/types) that the actual schwab-mcp tool schema rejects."""
    from src.wheel_tracker.sync import _sync_account

    call_args_list = []

    async def _recording_call_tool(name, args=None):
        call_args_list.append((name, args))
        result = MagicMock()
        result.content = [MagicMock(text="[]")]
        return result

    session = AsyncMock()
    session.call_tool = AsyncMock(side_effect=_recording_call_tool)

    await _sync_account(conn, session, "ACC1", "2025-01-01", "2025-06-01")

    _, params = call_args_list[0]
    assert params["account_hash"] == "ACC1"
    assert "start_date" in params and "end_date" in params
    assert "transaction_type" in params
    assert "accountNumber" not in params
    assert "startDate" not in params
    assert "types" not in params


@pytest.mark.asyncio
async def test_sync_account_chunks_ranges_over_a_year(conn):
    """Schwab's transactions API rejects date ranges over a year, so a backfill
    spanning more must be split into <=365-day windows."""
    from src.wheel_tracker.sync import _sync_account

    call_args_list = []

    async def _recording_call_tool(name, args=None):
        call_args_list.append((name, args))
        result = MagicMock()
        result.content = [MagicMock(text="[]")]
        return result

    session = AsyncMock()
    session.call_tool = AsyncMock(side_effect=_recording_call_tool)

    await _sync_account(conn, session, "ACC1", "2020-01-01", "2025-12-31")

    assert len(call_args_list) > 1
    from datetime import date as _date

    for _, params in call_args_list:
        span = (
            _date.fromisoformat(params["end_date"]) - _date.fromisoformat(params["start_date"])
        ).days
        assert span <= 365


def _mock_async_cm(value):
    """Return a MagicMock usable as `async with mock as value:`."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _patch_mcp_transport(session):
    """Patch run_sync's MCP transport (streamablehttp_client + ClientSession) so that
    `async with streamablehttp_client(url) as (read, write, _)` and
    `async with ClientSession(read, write) as session` resolve without any network I/O."""
    stream_cm = _mock_async_cm((MagicMock(), MagicMock(), MagicMock()))
    session_cm = _mock_async_cm(session)
    return (
        patch("src.wheel_tracker.sync.streamablehttp_client", MagicMock(return_value=stream_cm)),
        patch("src.wheel_tracker.sync.ClientSession", MagicMock(return_value=session_cm)),
    )


@pytest.mark.asyncio
async def test_run_sync_calls_link_cycles_and_check_alerts(conn):
    """Task 5 wiring: after a successful MCP sync, run_sync must invoke
    cycles.link_cycles and alerts.check_alerts on the populated tables."""
    from src.wheel_tracker.sync import run_sync

    session = _mock_session("[]", "[]", "{}")
    patch_stream, patch_client_session = _patch_mcp_transport(session)

    with (
        patch_stream,
        patch_client_session,
        patch("src.wheel_tracker.sync._schwab_url", return_value="http://fake-schwab-mcp"),
        patch("src.wheel_tracker.cycles.link_cycles", return_value=3) as mock_link,
        patch(
            "src.wheel_tracker.alerts.check_alerts",
            new=AsyncMock(return_value=["alert1", "alert2"]),
        ) as mock_alerts,
    ):
        await run_sync(conn)

        mock_link.assert_called_once_with(conn)
        mock_alerts.assert_called_once_with(conn)


@pytest.mark.asyncio
async def test_run_sync_link_cycles_failure_is_caught_non_fatally(conn):
    """A raised exception from link_cycles must be caught by run_sync's existing
    exception handling (logged, not propagated) — same pattern as MCP failures."""
    from src.wheel_tracker.sync import run_sync

    session = _mock_session("[]", "[]", "{}")
    patch_stream, patch_client_session = _patch_mcp_transport(session)

    with (
        patch_stream,
        patch_client_session,
        patch("src.wheel_tracker.sync._schwab_url", return_value="http://fake-schwab-mcp"),
        patch("src.wheel_tracker.cycles.link_cycles", side_effect=RuntimeError("boom")),
        patch(
            "src.wheel_tracker.alerts.check_alerts", new=AsyncMock(return_value=[])
        ) as mock_alerts,
    ):
        summary = await run_sync(conn)  # must not raise

        assert summary == {"accounts_synced": 0, "trades_imported": 0, "positions_refreshed": 0}
        mock_alerts.assert_not_called()  # never reached — link_cycles raised first


@pytest.mark.asyncio
async def test_run_sync_check_alerts_failure_is_caught_non_fatally(conn):
    """A raised exception from check_alerts must be caught by run_sync's existing
    exception handling (logged, not propagated)."""
    from src.wheel_tracker.sync import run_sync

    session = _mock_session("[]", "[]", "{}")
    patch_stream, patch_client_session = _patch_mcp_transport(session)

    with (
        patch_stream,
        patch_client_session,
        patch("src.wheel_tracker.sync._schwab_url", return_value="http://fake-schwab-mcp"),
        patch("src.wheel_tracker.cycles.link_cycles", return_value=0) as mock_link,
        patch(
            "src.wheel_tracker.alerts.check_alerts", new=AsyncMock(side_effect=RuntimeError("boom"))
        ),
    ):
        summary = await run_sync(conn)  # must not raise

        assert summary == {"accounts_synced": 0, "trades_imported": 0, "positions_refreshed": 0}
        mock_link.assert_called_once_with(conn)


# --- compact chain format fixture ---
_COMPACT_CHAIN = """\
"450.0"[1,]{strike,bid,ask,delta,openInterest}:
450.0,3.50,3.60,-0.25,100
"460.0"[1,]{strike,bid,ask,delta,openInterest}:
460.0,5.00,5.10,-0.35,200
"""


def test_extract_delta_returns_matching_strike():
    from src.wheel_tracker.sync import _extract_delta

    delta = _extract_delta(_COMPACT_CHAIN, "PUT", 450.0, "2025-01-17")
    assert delta == pytest.approx(-0.25)


def test_extract_delta_returns_none_when_not_found():
    from src.wheel_tracker.sync import _extract_delta

    delta = _extract_delta(_COMPACT_CHAIN, "PUT", 999.0, "2025-01-17")
    assert delta is None


@pytest.mark.asyncio
async def test_fetch_deltas_uses_correct_param_names(conn):
    """_fetch_deltas must call get_option_chain with snake_case param names
    (contract_type, from_date, to_date) and must NOT use contractType or expirationDate."""
    from datetime import datetime, timezone

    from src.wheel_tracker.store import upsert_position
    from src.wheel_tracker.sync import _fetch_deltas

    # Insert one open short option position so _fetch_deltas has something to process
    refreshed_at = datetime.now(timezone.utc).isoformat()
    upsert_position(
        conn,
        {
            "account_id": "ACC1",
            "symbol": "AAPL  250117P00450000",
            "underlying": "AAPL",
            "asset_type": "OPTION",
            "option_type": "PUT",
            "strike": 450.0,
            "expiration": "2025-01-17",
            "dte": 10,
            "quantity": -1.0,
            "average_price": 3.50,
            "current_price": 3.55,
            "market_value": -355.0,
            "unrealized_pnl": -5.0,
            "delta": None,
            "refreshed_at": refreshed_at,
        },
    )

    call_args_list = []

    async def _recording_call_tool(name, args=None):
        call_args_list.append((name, args))
        result = MagicMock()
        result.isError = False
        result.content = [MagicMock(text=_COMPACT_CHAIN)]
        return result

    session = AsyncMock()
    session.call_tool = AsyncMock(side_effect=_recording_call_tool)

    await _fetch_deltas(conn, session, "ACC1")

    # Verify get_option_chain was called at least once
    chain_calls = [(name, args) for name, args in call_args_list if name == "get_option_chain"]
    assert len(chain_calls) == 1, f"Expected 1 get_option_chain call, got {len(chain_calls)}"

    _, params = chain_calls[0]
    assert "contract_type" in params, "Expected snake_case 'contract_type' param"
    assert "from_date" in params, "Expected 'from_date' param"
    assert "to_date" in params, "Expected 'to_date' param"
    assert "contractType" not in params, "Must NOT use camelCase 'contractType'"
    assert "expirationDate" not in params, "Must NOT use 'expirationDate'"
