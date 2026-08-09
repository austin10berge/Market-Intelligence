"""Nightly Schwab sync: transactions, open positions, and delta refresh."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ..config import settings
from .store import (
    delete_stale_positions,
    get_last_executed_at,
    update_position_delta,
    upsert_position,
    upsert_trade,
)

logger = logging.getLogger(__name__)

_SCHWAB_CONFIG = Path(__file__).parent.parent.parent / "discord_bot" / "schwab-mcp.json"


def _schwab_url() -> str:
    config = json.loads(_SCHWAB_CONFIG.read_text())
    return config["mcpServers"]["schwab"]["url"]


def _parse_accounts(raw: str) -> list[str]:
    """Return list of account hash values (used as accountNumber in subsequent calls)."""
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [a.get("hashValue") or a.get("accountNumber") for a in data if a]
        logger.warning("Unexpected accounts format: %r", raw[:200])
        return []
    except json.JSONDecodeError:
        logger.warning("Could not parse accounts JSON: %r", raw[:200])
        return []


def _parse_transactions(raw: str, account_id: str) -> list[dict]:
    """
    Parse Schwab get_transactions response into a list of trade dicts ready for upsert_trade().

    Expected shape (Schwab Individual Trader API):
    [{"activityId": "...", "time": "ISO8601", "type": "TRADE", "netAmount": float,
      "transactionItem": {"amount": float, "price": float, "instruction": str,
                          "instrument": {"symbol": str, "assetType": str, "putCall": str|None,
                                         "underlyingSymbol": str|None, "optionExpirationDate": str|None,
                                         "strikePrice": float|None}}}]

    Adjust this parser if the schwab-mcp wrapper reformats the response.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse transactions JSON for %s: %r", account_id, raw[:200])
        return []

    if not isinstance(data, list):
        logger.warning("Unexpected transactions format for %s: %r", account_id, raw[:200])
        return []

    trades = []
    for txn in data:
        if txn.get("type") != "TRADE":
            continue
        item = txn.get("transactionItem", {})
        instrument = item.get("instrument", {})
        try:
            trades.append(
                {
                    "schwab_transaction_id": str(txn["activityId"]),
                    "account_id": account_id,
                    "executed_at": txn["time"].replace("+0000", "+00:00"),
                    "settled_date": txn.get("settlementDate"),
                    "asset_type": instrument.get("assetType", "EQUITY"),
                    "symbol": instrument["symbol"],
                    "underlying": instrument.get("underlyingSymbol"),
                    "option_type": instrument.get("putCall"),
                    "strike": instrument.get("strikePrice"),
                    "expiration": instrument.get("optionExpirationDate"),
                    "instruction": item.get("instruction", "UNKNOWN"),
                    "quantity": float(item.get("amount", 0)),
                    "price": float(item.get("price", 0) or 0),
                    "commission": float(txn.get("fees", {}).get("commission", 0) or 0),
                    "net_amount": float(txn.get("netAmount", 0)),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping unparseable transaction %s: %s", txn.get("activityId"), exc)
    return trades


def _dte(expiration: str | None) -> int | None:
    if not expiration:
        return None
    try:
        exp = date.fromisoformat(expiration[:10])
        return max(0, (exp - date.today()).days)
    except ValueError:
        return None


def _parse_positions(raw: str, account_id: str, refreshed_at: str) -> list[dict]:
    """
    Parse Schwab get_account positions response.

    Expected shape: {"securitiesAccount": {"positions": [...]}}
    Each position has "instrument" (same shape as transaction), "shortQuantity",
    "longQuantity", "averagePrice", "marketValue", "currentDayProfitLoss".
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse positions JSON for %s: %r", account_id, raw[:200])
        return []

    acct = data.get("securitiesAccount", data)
    raw_positions = acct.get("positions", [])

    positions = []
    for pos in raw_positions:
        instrument = pos.get("instrument", {})
        short_qty = float(pos.get("shortQuantity", 0) or 0)
        long_qty = float(pos.get("longQuantity", 0) or 0)
        quantity = long_qty - short_qty  # negative = short

        symbol = instrument.get("symbol", "")
        expiration = instrument.get("optionExpirationDate")

        positions.append(
            {
                "account_id": account_id,
                "symbol": symbol,
                "underlying": instrument.get("underlyingSymbol"),
                "asset_type": instrument.get("assetType", "EQUITY"),
                "option_type": instrument.get("putCall"),
                "strike": instrument.get("strikePrice"),
                "expiration": expiration,
                "dte": _dte(expiration),
                "quantity": quantity,
                "average_price": float(pos.get("averagePrice", 0) or 0),
                "current_price": (
                    abs(float(pos.get("marketValue", 0) or 0) / quantity / 100)
                    if quantity and instrument.get("assetType") == "OPTION"
                    else abs(float(pos.get("marketValue", 0) or 0) / quantity)
                    if quantity
                    else None
                ),
                "market_value": float(pos.get("marketValue", 0) or 0),
                "unrealized_pnl": float(pos.get("currentDayProfitLoss", 0) or 0),
                "delta": None,  # populated separately by _fetch_deltas
                "refreshed_at": refreshed_at,
            }
        )
    return positions


def _extract_delta(
    chain_raw: str, option_type: str, strike: float, expiration: str
) -> float | None:
    """
    Extract delta for a specific contract from get_option_chain response.
    The chain response from schwab-mcp uses the same compact format as schwab_options.py.
    Returns None if not found.
    """
    from ..algo_detective.schwab_options import _parse_put_chain

    contracts = _parse_put_chain(chain_raw)

    for c in contracts:
        if abs(c.get("strike", 0) - strike) < 0.01:
            return c.get("delta")
    return None


async def _sync_account(
    conn: sqlite3.Connection,
    session: ClientSession,
    account_id: str,
    start_date: str,
    end_date: str,
) -> int:
    """Pull transactions for one account. Returns count of newly imported rows."""
    result = await session.call_tool(
        "get_transactions",
        {"accountNumber": account_id, "types": "TRADE", "startDate": start_date, "endDate": end_date},
    )
    raw = result.content[0].text if result.content else "[]"
    trades = _parse_transactions(raw, account_id)
    count = 0
    for t in trades:
        upsert_trade(conn, t)
        count += 1
    return count


async def _sync_positions(
    conn: sqlite3.Connection,
    session: ClientSession,
    account_id: str,
) -> set[str]:
    """Refresh open positions for one account. Returns set of active symbols."""
    from datetime import datetime, timezone

    refreshed_at = datetime.now(timezone.utc).isoformat()

    result = await session.call_tool(
        "get_account",
        {"accountNumber": account_id, "fields": "positions"},
    )
    raw = result.content[0].text if result.content else "{}"
    positions = _parse_positions(raw, account_id, refreshed_at)
    active_symbols: set[str] = set()
    for pos in positions:
        upsert_position(conn, pos)
        active_symbols.add(pos["symbol"])
    delete_stale_positions(conn, account_id, active_symbols)
    return active_symbols


async def _fetch_deltas(
    conn: sqlite3.Connection,
    session: ClientSession,
    account_id: str,
) -> None:
    """Fetch current delta for each open short option position in this account."""
    rows = conn.execute(
        """
        SELECT symbol, underlying, option_type, strike, expiration
        FROM wt_positions
        WHERE account_id = ? AND asset_type = 'OPTION' AND quantity < 0
        """,
        (account_id,),
    ).fetchall()

    fetched_chains: dict[tuple[str, str], str] = {}  # (underlying, expiration) → raw text

    for symbol, underlying, option_type, strike, expiration in rows:
        if not underlying:
            continue
        exp_key = expiration[:10] if expiration else ""
        cache_key = (underlying, exp_key)
        if cache_key not in fetched_chains:
            result = await session.call_tool(
                "get_option_chain",
                {
                    "symbol": underlying,
                    "contract_type": option_type or "ALL",
                    "from_date": exp_key,
                    "to_date": exp_key,
                },
            )
            if result.isError:
                logger.warning(
                    "wheel_tracker: get_option_chain error for %s: %s", underlying, result.content
                )
                fetched_chains[cache_key] = ""
            else:
                fetched_chains[cache_key] = result.content[0].text if result.content else ""

        chain_raw = fetched_chains[cache_key]
        delta = _extract_delta(chain_raw, option_type or "", strike or 0.0, expiration or "")
        if delta is not None:
            update_position_delta(conn, account_id, symbol, delta)


async def run_sync(conn: sqlite3.Connection | None = None) -> dict:
    """
    Full sync: pull all Schwab transactions + positions, refresh deltas.
    Safe to call if Schwab MCP is unreachable (logs error, returns empty summary).
    If conn is None, opens a new connection via settings.db_path.
    Returns {"accounts_synced": N, "trades_imported": N, "positions_refreshed": N}.
    """
    _owns_conn = conn is None
    if _owns_conn:
        conn = sqlite3.connect(settings.db_path)
        conn.row_factory = sqlite3.Row

    summary = {"accounts_synced": 0, "trades_imported": 0, "positions_refreshed": 0}

    try:
        try:
            url = _schwab_url()
        except Exception as exc:
            logger.error("wheel_tracker: cannot read Schwab MCP config: %s", exc)
            return summary

        today = date.today().isoformat()

        try:
            async with streamablehttp_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    accts_result = await session.call_tool("get_accounts", {})
                    raw_accounts = accts_result.content[0].text if accts_result.content else "[]"
                    account_ids = _parse_accounts(raw_accounts)
                    logger.info("wheel_tracker: found %d account(s)", len(account_ids))

                    for account_id in account_ids:
                        if not account_id:
                            continue
                        last = get_last_executed_at(conn, account_id)
                        start = (
                            (date.fromisoformat(last[:10]) + timedelta(days=1)).isoformat()
                            if last
                            else "2020-01-01"
                        )
                        imported = await _sync_account(conn, session, account_id, start, today)
                        summary["trades_imported"] += imported

                        active = await _sync_positions(conn, session, account_id)
                        summary["positions_refreshed"] += len(active)

                        await _fetch_deltas(conn, session, account_id)
                        summary["accounts_synced"] += 1

            # MCP session closed — now do CPU-only work on the populated tables
            from .cycles import link_cycles
            from .alerts import check_alerts

            new_cycles = link_cycles(conn)
            logger.info("wheel_tracker: linked %d new cycle(s)", new_cycles)
            alerts_sent = await check_alerts(conn)
            logger.info("wheel_tracker: sent %d alert(s)", len(alerts_sent))

        except Exception as exc:
            logger.error("wheel_tracker sync failed: %s", exc, exc_info=True)

    finally:
        if _owns_conn and conn:
            conn.close()

    logger.info("wheel_tracker sync complete: %s", summary)
    return summary
