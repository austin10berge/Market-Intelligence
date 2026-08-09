"""Wheel-cycle auto-detection. Walks unlinked trades per account and groups them."""
from __future__ import annotations

import logging
import sqlite3

from .store import (
    create_cycle,
    get_distinct_accounts,
    get_unlinked_trades,
    set_trade_cycle,
)

logger = logging.getLogger(__name__)


def link_cycles(conn: sqlite3.Connection) -> int:
    """Detect wheel cycles from unlinked trades across all accounts. Returns new cycle count."""
    total = 0
    for account_id in get_distinct_accounts(conn):
        trades = get_unlinked_trades(conn, account_id)
        if trades:
            total += _link_account(conn, account_id, trades)
    return total


def _link_account(conn: sqlite3.Connection, account_id: str, trades: list[dict]) -> int:
    processed: set[int] = set()
    new_cycles = 0

    for trade in trades:
        if trade["id"] in processed:
            continue
        # Only option SELL_TO_OPEN PUTs start a wheel cycle
        if (
            trade["asset_type"] == "OPTION"
            and trade["option_type"] == "PUT"
            and trade["instruction"] == "SELL_TO_OPEN"
        ):
            cycle_trades, status = _collect(trade, trades, processed)
            if not cycle_trades:
                continue

            total_premium = sum(
                t["net_amount"] for t in cycle_trades
                if t["asset_type"] == "OPTION" and (t["net_amount"] or 0) > 0
            )
            realized_pnl: float | None = None
            if status == "CLOSED":
                realized_pnl = sum(t["net_amount"] or 0 for t in cycle_trades)

            cycle_id = create_cycle(conn, {
                "underlying": trade["underlying"] or trade["symbol"],
                "account_id": account_id,
                "status": status,
                "opened_at": cycle_trades[0]["executed_at"][:10],
                "closed_at": cycle_trades[-1]["executed_at"][:10] if status == "CLOSED" else None,
                "total_premium": round(total_premium, 2),
                "realized_pnl": round(realized_pnl, 2) if realized_pnl is not None else None,
                "auto_detected": 1,
            })
            for t in cycle_trades:
                set_trade_cycle(conn, t["id"], cycle_id)
                processed.add(t["id"])
            new_cycles += 1
            logger.debug(
                "Created %s cycle %d for %s (%d trades)",
                status, cycle_id, trade["underlying"], len(cycle_trades),
            )

    return new_cycles


def _collect(
    csp_open: dict,
    all_trades: list[dict],
    already: set[int],
) -> tuple[list[dict], str]:
    """
    Walk forward from a CSP SELL_TO_OPEN trade and collect all related trades.
    Returns (collected_trades, "OPEN"|"CLOSED").
    """
    cycle: list[dict] = [csp_open]
    underlying = csp_open.get("underlying") or ""
    csp_symbol = csp_open["symbol"]

    # All later trades on the same underlying, not yet in another cycle
    later = [
        t for t in all_trades
        if t["executed_at"] > csp_open["executed_at"]
        and t["id"] not in already
        and (
            t.get("underlying") == underlying
            or (t["asset_type"] == "EQUITY" and t["symbol"] == underlying)
        )
    ]

    phase = "csp"  # csp → assigned → shares → cc → cc_close (loops) → closed

    for t in later:
        if phase == "csp":
            if t["symbol"] == csp_symbol:
                if t["instruction"] in ("BUY_TO_CLOSE", "EXPIRED"):
                    cycle.append(t)
                    return cycle, "CLOSED"
                if t["instruction"] == "ASSIGNED":
                    cycle.append(t)
                    phase = "shares"
            # Some brokers record assignment as a direct equity BUY without an ASSIGNED record
            elif (
                t["asset_type"] == "EQUITY"
                and t["instruction"] == "BUY"
                and t["symbol"] == underlying
                and abs(t["quantity"]) == abs(csp_open["quantity"]) * 100
            ):
                cycle.append(t)
                phase = "cc"

        elif phase == "shares":
            # Look for the equity delivery following assignment
            if (
                t["asset_type"] == "EQUITY"
                and t["instruction"] == "BUY"
                and t["symbol"] == underlying
            ):
                cycle.append(t)
                phase = "cc"

        elif phase == "cc":
            if (
                t["asset_type"] == "OPTION"
                and t["option_type"] == "CALL"
                and t["instruction"] == "SELL_TO_OPEN"
            ):
                cycle.append(t)
                phase = "cc_close"
            elif (
                t["asset_type"] == "EQUITY"
                and t["instruction"] == "SELL"
                and t["symbol"] == underlying
            ):
                # Sold shares without writing a CC — closes the cycle
                cycle.append(t)
                return cycle, "CLOSED"

        elif phase == "cc_close":
            cc_symbol = cycle[-1]["symbol"]
            if t["symbol"] == cc_symbol:
                if t["instruction"] in ("BUY_TO_CLOSE", "EXPIRED"):
                    cycle.append(t)
                    phase = "cc"  # loop — may write another CC
                elif t["instruction"] == "ASSIGNED":
                    cycle.append(t)
                    return cycle, "CLOSED"

    return cycle, "OPEN"
