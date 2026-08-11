"""Build precomputed YTD equity curve from wt_trades + yfinance prices."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date

import pandas as pd

from ..fetchers._yf_lock import download_with_retry
from .store import write_equity_curve

logger = logging.getLogger(__name__)

# Asset types with real daily price history in yfinance, so they get marked
# to market. MUTUAL_FUND (e.g. SWVXX, a $1 NAV cash sweep) and OPTION stay at
# trade cost since neither has usable historical pricing here.
MARKABLE_ASSET_TYPES = ("EQUITY", "COLLECTIVE_INVESTMENT")

DEPOSIT_EVENTS = [
    {"date": "2021-04-20", "amount": 700.0},
    {"date": "2021-04-27", "amount": 450.0},
    {"date": "2021-05-04", "amount": 600.0},
    {"date": "2025-11-13", "amount": 20000.0},
    {"date": "2026-07-17", "amount": 25000.0},
]


def _ytd_start() -> str:
    return f"{date.today().year}-01-01"


def _cumulative_deposits_at(as_of: str) -> float:
    return sum(d["amount"] for d in DEPOSIT_EVENTS if d["date"] <= as_of)


def _deposit_on_date(dt: str) -> float:
    return sum(d["amount"] for d in DEPOSIT_EVENTS if d["date"] == dt)


async def rebuild_equity_curve(conn: sqlite3.Connection) -> int:
    ytd = _ytd_start()

    trades = conn.execute(
        """
        SELECT executed_at, asset_type, symbol, underlying, instruction,
               quantity, net_amount
        FROM wt_trades
        ORDER BY executed_at
        """,
    ).fetchall()
    cols = ["executed_at", "asset_type", "symbol", "underlying", "instruction",
            "quantity", "net_amount"]
    trades = [dict(zip(cols, r)) for r in trades]

    initial_cash = _cumulative_deposits_at(ytd)

    cash = initial_cash
    # All tradeable assets must remain in the replay ledger.  Cash-equivalent
    # funds (for example SWVXX) and options still affect cash, so excluding
    # them from positions makes a purchase look like a withdrawal.
    positions: dict[str, dict] = {}

    # Replay all trades before YTD to establish starting state
    pre_ytd = [t for t in trades if t["executed_at"][:10] < ytd]
    for t in pre_ytd:
        cash, positions = _apply_trade(cash, positions, t)

    ytd_trades = [t for t in trades if t["executed_at"][:10] >= ytd]
    trade_by_date: dict[str, list[dict]] = {}
    for t in ytd_trades:
        d = t["executed_at"][:10]
        trade_by_date.setdefault(d, []).append(t)

    held_tickers = set()
    for t in trades:
        sym = t["underlying"] or t["symbol"]
        if t["asset_type"] in MARKABLE_ASSET_TYPES:
            held_tickers.add(sym)
    held_tickers.add("SPY")

    tickers_str = " ".join(sorted(held_tickers))
    prices_df = await download_with_retry(
        tickers_str,
        start=ytd,
        progress=False,
        auto_adjust=True,
        group_by="ticker",
    )

    if prices_df is None or prices_df.empty:
        logger.warning("equity_curve: no price data returned from yfinance")
        return 0

    if isinstance(prices_df.columns, pd.MultiIndex):
        trading_dates = sorted(prices_df.index)
    else:
        trading_dates = sorted(prices_df.index)

    rows = []
    for dt in trading_dates:
        dt_str = dt.strftime("%Y-%m-%d")

        day_trades = trade_by_date.get(dt_str, [])
        for t in day_trades:
            cash, positions = _apply_trade(cash, positions, t)

        deposit_today = _deposit_on_date(dt_str)
        if deposit_today > 0 and dt_str >= ytd:
            cash += deposit_today

        stock_value = 0.0
        for sym, pos in positions.items():
            if pos["qty"] == 0:
                continue
            # Equities and ETFs are marked to market. Historical prices are not
            # available for cash-equivalent funds or options, so keep those at
            # their trade cost rather than dropping their value from equity
            # altogether.
            close = pos["avg_cost"]
            if pos["asset_type"] in MARKABLE_ASSET_TYPES:
                try:
                    if isinstance(prices_df.columns, pd.MultiIndex):
                        close = float(prices_df.loc[dt, (sym, "Close")])
                    else:
                        close = float(prices_df.loc[dt, "Close"])
                except (KeyError, TypeError):
                    pass
            if pd.isna(close):
                close = pos["avg_cost"]
            stock_value += pos["qty"] * close

        try:
            if isinstance(prices_df.columns, pd.MultiIndex):
                spy_close = float(prices_df.loc[dt, ("SPY", "Close")])
            else:
                spy_close = float(prices_df.loc[dt, "Close"]) if "SPY" in tickers_str else None
        except (KeyError, TypeError):
            spy_close = None
        if spy_close is not None and pd.isna(spy_close):
            spy_close = None

        equity = cash + stock_value
        cumulative_deps = _cumulative_deposits_at(dt_str)

        rows.append({
            "date": dt_str,
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "deposits": cumulative_deps,
            "spy_close": round(spy_close, 4) if spy_close is not None else None,
        })

    write_equity_curve(conn, rows)
    logger.info("equity_curve: wrote %d rows", len(rows))
    return len(rows)


def _apply_trade(
    cash: float,
    positions: dict[str, dict],
    trade: dict,
) -> tuple[float, dict[str, dict]]:
    net = trade["net_amount"] or 0
    cash += net

    sym = trade["symbol"]
    qty = abs(trade["quantity"] or 0)
    if not sym or qty == 0:
        return cash, positions

    price = abs(net) / qty
    instruction = trade["instruction"] or ""
    direction = 1 if instruction in ("BUY", "BUY_TO_OPEN", "BUY_TO_CLOSE") else -1
    pos = positions.setdefault(sym, {
        "qty": 0.0,
        "avg_cost": 0.0,
        "asset_type": trade["asset_type"],
    })

    old_qty = pos["qty"]
    new_qty = old_qty + direction * qty
    # Weighted cost is meaningful only while adding to a same-side position.
    # When a trade closes or reverses a position, the remaining shares/contracts
    # carry the execution price of the opening leg on the new side.
    if old_qty == 0 or old_qty * direction > 0:
        total_cost = abs(old_qty) * pos["avg_cost"] + qty * price
        pos["avg_cost"] = total_cost / abs(new_qty) if new_qty else 0.0
    elif new_qty == 0:
        pos["avg_cost"] = 0.0
    elif old_qty * new_qty < 0:
        pos["avg_cost"] = price
    pos["qty"] = new_qty

    return cash, positions
