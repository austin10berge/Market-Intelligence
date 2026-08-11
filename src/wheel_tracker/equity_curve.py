"""Build precomputed YTD equity curve from wt_trades + yfinance prices."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date

import pandas as pd

from ..fetchers._yf_lock import download_with_retry
from .store import write_equity_curve

logger = logging.getLogger(__name__)

DEPOSIT_EVENTS = [
    {"date": "2025-12-01", "amount": 20000.0},
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
    positions: dict[str, dict] = {}  # symbol -> {"qty": float, "avg_cost": float}

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
        if t["asset_type"] == "EQUITY":
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
            try:
                if isinstance(prices_df.columns, pd.MultiIndex):
                    close = float(prices_df.loc[dt, (sym, "Close")])
                else:
                    close = float(prices_df.loc[dt, "Close"])
            except (KeyError, TypeError):
                close = pos["avg_cost"]
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

    if trade["asset_type"] == "EQUITY":
        sym = trade["symbol"]
        qty = abs(trade["quantity"] or 0)
        price = abs(net) / qty if qty > 0 else 0

        if trade["instruction"] in ("BUY", "BUY_TO_OPEN"):
            pos = positions.setdefault(sym, {"qty": 0, "avg_cost": 0})
            total_cost = pos["qty"] * pos["avg_cost"] + qty * price
            pos["qty"] += qty
            pos["avg_cost"] = total_cost / pos["qty"] if pos["qty"] > 0 else 0
        elif trade["instruction"] in ("SELL", "SELL_TO_CLOSE"):
            pos = positions.get(sym)
            if pos:
                pos["qty"] = max(0, pos["qty"] - qty)
                if pos["qty"] == 0:
                    pos["avg_cost"] = 0

    return cash, positions
