"""Nightly Schwab sync: transactions, open positions, and delta refresh."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ..config import settings
from .store import (
    delete_stale_positions,
    get_last_executed_at,
    update_position_delta,
    upsert_position,
    upsert_trade,
)

logger = logging.getLogger(__name__)

def _schwab_url() -> str:
    from ..config import settings

    return settings.schwab_mcp_url


def _parse_schwab_text(raw: str):
    """Parse schwab-mcp's text responses — JSON or YAML with optional [N]: wrapper.

    schwab-mcp serialises Schwab API objects as YAML rather than raw JSON.
    The outer document is often a mapping like '[1]:\n  - ...' where the key
    is a YAML flow-sequence (unhashable in Python, so yaml.safe_load raises).
    Strip that wrapper first, then parse the indented body as plain YAML.
    """
    import re

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strip '[N]:' outer wrapper — e.g. '[1]:\n  -\n    key: val' → '-\n  key: val'
    lines = raw.splitlines()
    if lines and re.match(r"^\[\d+\]:\s*$", lines[0]):
        if len(lines) == 1:
            return []  # e.g. '[0]:' — an explicitly empty list, not a parse failure
        # Dedent the body by 2 spaces (standard indent after the wrapper key)
        body = "\n".join(line[2:] if line.startswith("  ") else line for line in lines[1:])
    else:
        body = raw

    # schwab-mcp also annotates every NESTED list-valued key with its item count,
    # e.g. 'transferItems[5]:' or 'positions[10]:'. Left alone, yaml.safe_load turns
    # that into a literal dict key "transferItems[5]" instead of "transferItems", so
    # lookups like txn.get("transferItems") silently return nothing. Strip the '[N]'
    # suffix from any key line so the key name matches what callers expect.
    body = re.sub(r"^(\s*\S+)\[\d+\](:\s*)$", r"\1\2", body, flags=re.MULTILINE)

    try:
        return yaml.safe_load(body)
    except yaml.YAMLError:
        return None


def _parse_accounts(raw: str) -> list[str]:
    """Return list of account hash values (used as account_hash in subsequent calls)."""
    data = _parse_schwab_text(raw)
    if data is None:
        logger.warning("Could not parse accounts response: %r", raw[:200])
        return []

    # schwab-mcp wraps the list: [{securitiesAccount: {accountHash: ...}}]
    if not isinstance(data, list):
        logger.warning("Unexpected accounts format: %r", raw[:200])
        return []

    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        acct = item.get("securitiesAccount", item)
        acct_id = acct.get("accountHash") or acct.get("hashValue") or acct.get("accountNumber")
        if acct_id:
            result.append(str(acct_id))
    return result


def _parse_transactions(raw: str, account_id: str) -> list[dict]:
    """
    Parse Schwab get_transactions response into a list of trade dicts ready for upsert_trade().

    Actual shape (Schwab Individual Trader API, verified against live schwab-mcp):
    [{"activityId": ..., "time": "ISO8601", "type": "TRADE", "netAmount": float,
      "settlementDate": str|None,
      "transferItems": [
          {"instrument": {"assetType": "CURRENCY", ...}, "amount": float, "feeType": str},
          ...,
          {"instrument": {"symbol": str, "assetType": str, "putCall": str|None,
                          "underlyingSymbol": str|None, "expirationDate": str|None,
                          "strikePrice": float|None},
           "amount": float, "price": float, "positionEffect": "OPENING"|"CLOSING"}]}]

    There is no top-level "transactionItem"/"instruction" — transferItems is a list mixing
    fee entries (assetType CURRENCY) with the actual traded instrument, and the buy/sell
    direction must be derived from positionEffect + the sign of "amount" (negative = sold).
    """
    data = _parse_schwab_text(raw)
    if data is None:
        logger.warning("Could not parse transactions response for %s: %r", account_id, raw[:200])
        return []

    if not isinstance(data, list):
        logger.warning("Unexpected transactions format for %s: %r", account_id, raw[:200])
        return []

    trades = []
    for txn in data:
        if txn.get("type") != "TRADE":
            continue
        transfer_items = txn.get("transferItems", [])
        item = next(
            (
                ti
                for ti in transfer_items
                if ti.get("instrument", {}).get("assetType") != "CURRENCY"
            ),
            None,
        )
        if item is None:
            logger.warning("No traded instrument found in transaction %s", txn.get("activityId"))
            continue
        instrument = item.get("instrument", {})
        commission_item = next(
            (ti for ti in transfer_items if ti.get("feeType") == "COMMISSION"), None
        )
        commission = float(commission_item.get("amount", 0) or 0) if commission_item else 0.0

        amount = float(item.get("amount", 0) or 0)
        position_effect = item.get("positionEffect")
        side = "BUY" if amount > 0 else "SELL" if amount < 0 else None
        effect = {"OPENING": "OPEN", "CLOSING": "CLOSE"}.get(position_effect)
        instruction = f"{side}_TO_{effect}" if side and effect else "UNKNOWN"

        expiration = instrument.get("expirationDate")
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
                    "expiration": expiration[:10] if expiration else None,
                    "instruction": instruction,
                    "quantity": abs(amount),
                    "price": float(item.get("price", 0) or 0),
                    "commission": commission,
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


def _parse_occ_symbol(symbol: str) -> tuple[float | None, str | None]:
    """Extract (strike, expiration ISO date) from an OCC-format option symbol,
    e.g. 'CRM   270617C00190000' -> (190.0, '2027-06-17').

    schwab-mcp's get_account positions payload does not include strikePrice/
    optionExpirationDate on the instrument (unlike the transactions payload) —
    those details are only encoded in the OCC symbol's last 15 characters
    (YYMMDD + C/P + 8-digit strike*1000).
    """
    if len(symbol) < 15:
        return None, None
    suffix = symbol[-15:]
    date_part, strike_part = suffix[:6], suffix[7:15]
    try:
        expiration = f"20{date_part[0:2]}-{date_part[2:4]}-{date_part[4:6]}"
        date.fromisoformat(expiration)
        strike = int(strike_part) / 1000.0
    except (ValueError, IndexError):
        return None, None
    return strike, expiration


def _parse_positions(raw: str, account_id: str, refreshed_at: str) -> list[dict]:
    """
    Parse Schwab get_account positions response.

    Expected shape: {"securitiesAccount": {"positions": [...]}}
    Each position has "instrument" (assetType/symbol/putCall/underlyingSymbol only —
    no strikePrice/optionExpirationDate, unlike transactions; see _parse_occ_symbol),
    "shortQuantity", "longQuantity", "averagePrice", "marketValue", and the total
    open return since cost basis in "longOpenProfitLoss"/"shortOpenProfitLoss"
    (NOT "currentDayProfitLoss", which is only today's session move and understates
    or overstates the wheel's actual return for anything held longer than a day).
    """
    data = _parse_schwab_text(raw)
    if data is None:
        logger.warning("Could not parse positions response for %s: %r", account_id, raw[:200])
        return []

    # Single account response: {securitiesAccount: {positions: [...]}}
    acct = data.get("securitiesAccount", data) if isinstance(data, dict) else {}
    raw_positions = acct.get("positions", [])

    positions = []
    for pos in raw_positions:
        instrument = pos.get("instrument", {})
        short_qty = float(pos.get("shortQuantity", 0) or 0)
        long_qty = float(pos.get("longQuantity", 0) or 0)
        quantity = long_qty - short_qty  # negative = short

        symbol = instrument.get("symbol", "")
        strike = instrument.get("strikePrice")
        expiration = instrument.get("optionExpirationDate")
        if instrument.get("assetType") == "OPTION" and (strike is None or expiration is None):
            occ_strike, occ_expiration = _parse_occ_symbol(symbol)
            strike = strike if strike is not None else occ_strike
            expiration = expiration if expiration is not None else occ_expiration

        positions.append(
            {
                "account_id": account_id,
                "symbol": symbol,
                "underlying": instrument.get("underlyingSymbol"),
                "asset_type": instrument.get("assetType", "EQUITY"),
                "option_type": instrument.get("putCall"),
                "strike": strike,
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
                "unrealized_pnl": (
                    float(pos.get("longOpenProfitLoss", 0) or 0)
                    if quantity > 0
                    else float(pos.get("shortOpenProfitLoss", 0) or 0)
                    if quantity < 0
                    else 0.0
                ),
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
    """Pull transactions for one account. Returns count of newly imported rows.

    Schwab's transactions API rejects date ranges over a year, so a backfill
    spanning more than that (e.g. a first-ever sync) is chunked into <=365-day
    windows. Count only rows that were actually new (ON CONFLICT DO NOTHING in
    upsert_trade means overlapping chunks won't double-count).
    """
    count = 0
    window_start = date.fromisoformat(start_date)
    final_end = date.fromisoformat(end_date)
    while window_start <= final_end:
        window_end = min(window_start + timedelta(days=365), final_end)
        result = await session.call_tool(
            "get_transactions",
            {
                "account_hash": account_id,
                "transaction_type": "TRADE",
                "start_date": window_start.isoformat(),
                "end_date": window_end.isoformat(),
            },
        )
        raw = result.content[0].text if result.content else "[]"
        trades = _parse_transactions(raw, account_id)
        for t in trades:
            before = conn.total_changes
            upsert_trade(conn, t)
            if conn.total_changes > before:
                count += 1
        window_start = window_end + timedelta(days=1)
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
        # verbose=True is required — the default compact response reduces positions
        # to a symbol/quantity/marketValue/averagePrice/unrealizedPL table with no
        # "instrument" sub-object, which _parse_positions cannot read.
        {"account_hash": account_id, "include_positions": True, "verbose": True},
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
            if result.is_error:
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
            async with streamable_http_client(url) as (read, write):
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
            from .alerts import check_alerts

            alerts_sent = await check_alerts(conn)
            logger.info("wheel_tracker: sent %d alert(s)", len(alerts_sent))

            from .equity_curve import rebuild_equity_curve

            try:
                curve_rows = await rebuild_equity_curve(conn)
                logger.info("wheel_tracker: equity curve rebuilt (%d rows)", curve_rows)
            except Exception as exc:
                logger.warning("wheel_tracker: equity curve rebuild failed (non-fatal): %s", exc)

        except Exception as exc:
            logger.error("wheel_tracker sync failed: %s", exc, exc_info=True)

    finally:
        if _owns_conn and conn:
            conn.close()

    logger.info("wheel_tracker sync complete: %s", summary)
    return summary
