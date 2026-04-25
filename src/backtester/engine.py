"""Bar-by-bar backtesting engine.

Iterates through each bar of historical data, evaluates entry/exit conditions,
manages positions, and records the full equity curve and trade log. No lookahead
bias — each decision uses only data available at that bar.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .conditions import evaluate_condition_tree
from .models import (
    BacktestRequest,
    BacktestResult,
    Direction,
    PositionSizingMethod,
    Trade,
)
from .stats import compute_stats

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    """Tracks an active position during the backtest."""
    direction: str  # "long" or "short"
    entry_date: str
    entry_price: float
    shares: float
    entry_bar_idx: int
    highest_since_entry: float = 0.0  # For trailing stop (long)
    lowest_since_entry: float = float("inf")  # For trailing stop (short)


@dataclass
class EngineState:
    """Mutable state tracked during the backtest loop."""
    cash: float = 0.0
    position: OpenPosition | None = None
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)


def run_backtest(request: BacktestRequest, df: pd.DataFrame) -> BacktestResult:
    """Execute a full backtest on the provided data.

    Args:
        request: Backtest configuration (strategy, sizing, commission, etc.).
        df: OHLCV DataFrame with DatetimeIndex, sorted ascending.

    Returns:
        BacktestResult with equity curve, trades, and statistics.
    """
    strategy = request.strategy
    state = EngineState(cash=request.initial_capital)

    entry_tree = strategy.entry
    exit_config = strategy.exit
    exit_tree = exit_config.conditions if exit_config.conditions else None

    for i in range(len(df)):
        bar_date = df.index[i].strftime("%Y-%m-%d")
        close = float(df["Close"].iloc[i])
        high = float(df["High"].iloc[i])
        low = float(df["Low"].iloc[i])

        if pd.isna(close) or close <= 0:
            _record_equity(state, bar_date, close)
            continue

        if state.position is not None:
            # Update trailing stop tracking
            if state.position.direction == "long":
                state.position.highest_since_entry = max(
                    state.position.highest_since_entry, high
                )
            else:
                state.position.lowest_since_entry = min(
                    state.position.lowest_since_entry, low
                )

            # Check exit conditions
            exit_reason = _check_exit(
                state, exit_config, exit_tree, df, i, close, high, low
            )
            if exit_reason:
                _close_position(state, df, i, close, exit_reason, request)

        # If no position, check entry
        if state.position is None:
            should_enter_long = (
                strategy.direction in (Direction.LONG, Direction.BOTH)
                and _safe_evaluate(entry_tree, df, i)
            )
            should_enter_short = (
                strategy.direction in (Direction.SHORT, Direction.BOTH)
                and not should_enter_long
                # For short entries, we'd need separate short entry conditions
                # For now, v1 focuses on long entries
            )

            if should_enter_long:
                _open_position(state, "long", i, close, df, request)
            elif should_enter_short and strategy.direction == Direction.SHORT:
                _open_position(state, "short", i, close, df, request)

        _record_equity(state, bar_date, close)

    # Close any remaining position at end of data
    if state.position is not None:
        final_close = float(df["Close"].iloc[-1])
        _close_position(state, df, len(df) - 1, final_close, "end_of_data", request)
        # Re-record final equity
        if state.equity_curve:
            state.equity_curve[-1]["equity"] = _calc_equity(state, final_close)

    # Build benchmark (buy-and-hold) curve
    benchmark_curve = _build_benchmark(df, request.initial_capital)

    # Compute statistics
    stats = compute_stats(
        trades=state.trades,
        equity_curve=state.equity_curve,
        initial_capital=request.initial_capital,
        benchmark_curve=benchmark_curve,
    )

    start_date_str = df.index[0].strftime("%Y-%m-%d")
    end_date_str = df.index[-1].strftime("%Y-%m-%d")

    return BacktestResult(
        equity_curve=state.equity_curve,
        trades=state.trades,
        stats=stats,
        benchmark_curve=benchmark_curve,
        ticker=request.ticker,
        start_date=start_date_str,
        end_date=end_date_str,
        initial_capital=request.initial_capital,
    )


def _safe_evaluate(tree: dict, df: pd.DataFrame, bar_idx: int) -> bool:
    """Evaluate condition tree with exception safety."""
    try:
        return evaluate_condition_tree(tree, df, bar_idx)
    except Exception as exc:
        logger.debug("Condition eval error at bar %d: %s", bar_idx, exc)
        return False


def _calc_equity(state: EngineState, current_price: float) -> float:
    """Calculate total equity (cash + position value)."""
    equity = state.cash
    if state.position is not None:
        if state.position.direction == "long":
            equity += state.position.shares * current_price
        else:
            # Short: profit = (entry - current) * shares
            equity += state.position.shares * (
                2 * state.position.entry_price - current_price
            )
    return equity


def _record_equity(state: EngineState, bar_date: str, close: float) -> None:
    """Record an equity curve data point."""
    equity = _calc_equity(state, close) if close > 0 else state.cash
    state.equity_curve.append({"date": bar_date, "equity": round(equity, 2)})


def _open_position(
    state: EngineState,
    direction: str,
    bar_idx: int,
    price: float,
    df: pd.DataFrame,
    request: BacktestRequest,
) -> None:
    """Open a new position."""
    sizing = request.strategy.position_sizing
    fill_price = price * (1 + request.slippage_pct / 100.0) if direction == "long" else \
                 price * (1 - request.slippage_pct / 100.0)

    shares = _calculate_shares(
        sizing_method=sizing.method,
        sizing_value=sizing.value,
        risk_pct=sizing.risk_pct,
        equity=_calc_equity(state, price),
        fill_price=fill_price,
        stop_loss_pct=request.strategy.exit.stop_loss_pct,
    )

    if shares <= 0:
        return

    cost = shares * fill_price + request.commission
    if cost > state.cash:
        # Reduce to what we can afford
        affordable = (state.cash - request.commission) / fill_price
        shares = math.floor(affordable)
        if shares <= 0:
            return
        cost = shares * fill_price + request.commission

    state.cash -= cost
    bar_date = df.index[bar_idx].strftime("%Y-%m-%d")

    state.position = OpenPosition(
        direction=direction,
        entry_date=bar_date,
        entry_price=fill_price,
        shares=shares,
        entry_bar_idx=bar_idx,
        highest_since_entry=float(df["High"].iloc[bar_idx]),
        lowest_since_entry=float(df["Low"].iloc[bar_idx]),
    )


def _close_position(
    state: EngineState,
    df: pd.DataFrame,
    bar_idx: int,
    price: float,
    reason: str,
    request: BacktestRequest,
) -> None:
    """Close the current position and record the trade."""
    pos = state.position
    if pos is None:
        return

    if pos.direction == "long":
        fill_price = price * (1 - request.slippage_pct / 100.0)
        pnl = (fill_price - pos.entry_price) * pos.shares
    else:
        fill_price = price * (1 + request.slippage_pct / 100.0)
        pnl = (pos.entry_price - fill_price) * pos.shares

    pnl -= request.commission  # Exit commission

    pnl_pct = (pnl / (pos.entry_price * pos.shares)) * 100.0 if pos.entry_price > 0 else 0.0

    proceeds = pos.shares * fill_price - request.commission if pos.direction == "long" else \
               pos.shares * (2 * pos.entry_price - fill_price) - request.commission
    state.cash += proceeds

    exit_date = df.index[bar_idx].strftime("%Y-%m-%d")
    bars_held = bar_idx - pos.entry_bar_idx

    trade = Trade(
        entry_date=pos.entry_date,
        exit_date=exit_date,
        direction=pos.direction,
        entry_price=round(pos.entry_price, 4),
        exit_price=round(fill_price, 4),
        shares=pos.shares,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        exit_reason=reason,
        bars_held=bars_held,
    )

    state.trades.append(trade)
    state.position = None


def _check_exit(
    state: EngineState,
    exit_config,
    exit_tree: dict | None,
    df: pd.DataFrame,
    bar_idx: int,
    close: float,
    high: float,
    low: float,
) -> str | None:
    """Check all exit conditions. Returns exit reason or None."""
    pos = state.position
    if pos is None:
        return None

    # Use the bar date for trade recording
    bar_date = df.index[bar_idx].strftime("%Y-%m-%d")

    # 1. Stop loss
    if exit_config.stop_loss_pct is not None:
        if pos.direction == "long":
            stop_price = pos.entry_price * (1 - exit_config.stop_loss_pct / 100.0)
            if low <= stop_price:
                return "stop_loss"
        else:
            stop_price = pos.entry_price * (1 + exit_config.stop_loss_pct / 100.0)
            if high >= stop_price:
                return "stop_loss"

    # 2. Take profit
    if exit_config.take_profit_pct is not None:
        if pos.direction == "long":
            target = pos.entry_price * (1 + exit_config.take_profit_pct / 100.0)
            if high >= target:
                return "take_profit"
        else:
            target = pos.entry_price * (1 - exit_config.take_profit_pct / 100.0)
            if low <= target:
                return "take_profit"

    # 3. Trailing stop
    if exit_config.trailing_stop_pct is not None:
        if pos.direction == "long":
            trail_price = pos.highest_since_entry * (1 - exit_config.trailing_stop_pct / 100.0)
            if low <= trail_price:
                return "trailing_stop"
        else:
            trail_price = pos.lowest_since_entry * (1 + exit_config.trailing_stop_pct / 100.0)
            if high >= trail_price:
                return "trailing_stop"

    # 4. Time-based exit
    if exit_config.max_hold_days is not None:
        bars_held = bar_idx - pos.entry_bar_idx
        if bars_held >= exit_config.max_hold_days:
            return "time"

    # 5. Indicator-based exit
    if exit_tree is not None:
        if _safe_evaluate(exit_tree, df, bar_idx):
            return "signal"

    return None


def _calculate_shares(
    sizing_method: PositionSizingMethod,
    sizing_value: float,
    risk_pct: float | None,
    equity: float,
    fill_price: float,
    stop_loss_pct: float | None,
) -> float:
    """Determine the number of shares to buy based on position sizing method."""
    if fill_price <= 0:
        return 0

    if sizing_method == PositionSizingMethod.FIXED_SHARES:
        return math.floor(sizing_value)

    elif sizing_method == PositionSizingMethod.FIXED_DOLLAR:
        return math.floor(sizing_value / fill_price)

    elif sizing_method == PositionSizingMethod.PERCENT_EQUITY:
        dollar_amount = equity * (sizing_value / 100.0)
        return math.floor(dollar_amount / fill_price)

    elif sizing_method == PositionSizingMethod.RISK_BASED:
        # Risk X% of equity per trade based on stop distance
        if risk_pct is None or stop_loss_pct is None or stop_loss_pct <= 0:
            # Fall back to 10% of equity if stop loss not defined
            dollar_amount = equity * 0.10
            return math.floor(dollar_amount / fill_price)

        risk_dollars = equity * (risk_pct / 100.0)
        risk_per_share = fill_price * (stop_loss_pct / 100.0)
        if risk_per_share <= 0:
            return 0
        return math.floor(risk_dollars / risk_per_share)

    return 0


def _build_benchmark(df: pd.DataFrame, initial_capital: float) -> list[dict]:
    """Build a buy-and-hold equity curve for comparison."""
    if df.empty:
        return []

    first_close = float(df["Close"].iloc[0])
    if first_close <= 0:
        return []

    shares = initial_capital / first_close
    curve = []
    for i in range(len(df)):
        bar_date = df.index[i].strftime("%Y-%m-%d")
        close = float(df["Close"].iloc[i])
        equity = shares * close if not pd.isna(close) else initial_capital
        curve.append({"date": bar_date, "equity": round(equity, 2)})

    return curve
