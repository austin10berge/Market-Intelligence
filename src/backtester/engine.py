"""Bar-by-bar backtesting engine.

Iterates through each bar of historical data, evaluates entry/exit conditions,
manages positions, and records the full equity curve and trade log. No lookahead
bias — each decision uses only data available at that bar.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import pandas as pd

from .conditions import evaluate_condition_tree
from .models import (
    BacktestRequest,
    BacktestResult,
    Direction,
    PositionSizingMethod,
    ProfitLadderTier,
    PyramidingExitMode,
    Trade,
)
from .options import black_scholes_price, get_strike_for_delta
from .stats import compute_stats

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    """Tracks an active position during the backtest."""
    direction: str  # "long" or "short"
    entry_date: str
    entry_price: float
    entry_underlying_price: float
    shares: float
    entry_bar_idx: int
    highest_since_entry: float = 0.0  # For trailing stop (long) — tracks option price for options
    lowest_since_entry: float = float("inf")  # For trailing stop (short)
    # Underlying price highs/lows — used for pyramiding pullback reference
    highest_underlying_since_entry: float = 0.0
    lowest_underlying_since_entry: float = float("inf")
    # Options data
    is_option: bool = False
    option_type: str | None = None
    option_strike: float | None = None
    option_dte_entry: float | None = None
    option_iv_entry: float | None = None


@dataclass
class EngineState:
    """Mutable state tracked during the backtest loop."""
    cash: float = 0.0
    positions: list[OpenPosition] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)


def run_backtest(request: BacktestRequest, df: pd.DataFrame) -> BacktestResult:
    """Execute a full backtest on the provided data."""
    strategy = request.strategy
    state = EngineState(cash=request.initial_capital)

    entry_tree = strategy.entry
    exit_config = strategy.exit
    exit_tree = exit_config.conditions if exit_config.conditions else None
    pyr = strategy.pyramiding

    # Pre-calculate 20-day Realized Volatility for options pricing
    if strategy.options.enabled:
        df["returns"] = df["Close"].pct_change()
        df["rv20"] = df["returns"].rolling(20).std() * math.sqrt(252)

    for i in range(len(df)):
        bar_date = df.index[i].strftime("%Y-%m-%d")
        close = float(df["Close"].iloc[i])
        high = float(df["High"].iloc[i])
        low = float(df["Low"].iloc[i])

        if pd.isna(close) or close <= 0:
            _record_equity(state, bar_date, close, df, i, request)
            continue

        # Check exits for all open positions
        positions_to_close = []
        exit_reasons = []

        for pos in state.positions:
            if pos.is_option:
                days_held = i - pos.entry_bar_idx
                rem_dte = max(pos.option_dte_entry - days_held, 0)
                if pos.option_type == "call":
                    opt_high = _get_option_price(high, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
                    opt_low = _get_option_price(low, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
                else:
                    opt_high = _get_option_price(low, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
                    opt_low = _get_option_price(high, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
                pos_high, pos_low = opt_high, opt_low
            else:
                pos_high, pos_low = high, low

            if pos.direction == "long":
                pos.highest_since_entry = max(pos.highest_since_entry, pos_high)
                pos.highest_underlying_since_entry = max(pos.highest_underlying_since_entry, high)
            else:
                pos.lowest_since_entry = min(pos.lowest_since_entry, pos_low)
                pos.lowest_underlying_since_entry = min(pos.lowest_underlying_since_entry, low)

            reason = _check_exit(pos, exit_config, exit_tree, df, i, close, pos_high, pos_low)

            # Additional option expiration check
            if reason is None and pos.is_option and pos.option_dte_entry is not None:
                if days_held >= pos.option_dte_entry:
                    reason = "expiration"

            if reason:
                positions_to_close.append(pos)
                exit_reasons.append(reason)

        if positions_to_close:
            if exit_config.pyramiding_exit_mode == PyramidingExitMode.SELL_ALL:
                # If any exit triggers, sell everything
                sell_all_reason = exit_reasons[0]
                for pos in list(state.positions):
                    # Only the one that actually triggered gets the precise limit fill, others get market price
                    if pos in positions_to_close:
                        actual_reason = exit_reasons[positions_to_close.index(pos)]
                    else:
                        actual_reason = f"{sell_all_reason}_forced"
                    _close_position(state, pos, df, i, close, actual_reason, request)
            else:
                # Sell only the ones that triggered
                for pos, reason in zip(positions_to_close, exit_reasons):
                    if pos in state.positions:
                        _close_position(state, pos, df, i, close, reason, request)

        # Roll losing option positions that have reached the roll DTE threshold
        opt_conf = strategy.options
        if opt_conf.enabled and opt_conf.roll_dte is not None:
            closing_ids = {id(p) for p in positions_to_close}
            for pos in list(state.positions):
                if not pos.is_option or id(pos) in closing_ids:
                    continue
                days_held = i - pos.entry_bar_idx
                rem_dte = max(pos.option_dte_entry - days_held, 0)
                if rem_dte <= opt_conf.roll_dte:
                    current_opt_price = _get_option_price(close, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
                    if current_opt_price < pos.entry_price:
                        roll_shares = pos.shares
                        _close_position(state, pos, df, i, close, "roll", request)
                        _open_position(state, pos.direction, i, close, df, request, override_shares=roll_shares)

        # Check entry
        can_enter = True
        scale_in_active = False  # True when we're adding to an existing position
        if state.positions:
            if not pyr.enabled or len(state.positions) >= pyr.max_positions:
                can_enter = False
            else:
                scale_in_active = True
                last_pos = state.positions[-1]
                trigger = pyr.trigger_mode  # "pullback_only", "entry_signal", "both"

                if trigger in ("pullback_only", "both"):
                    if pyr.pullback_pct is None:
                        can_enter = False
                    else:
                        # Determine reference price from the underlying (not the option price)
                        if pyr.pullback_reference == "rolling":
                            ref_long = last_pos.highest_underlying_since_entry
                            ref_short = last_pos.lowest_underlying_since_entry
                        else:  # "entry"
                            ref_long = last_pos.entry_underlying_price
                            ref_short = last_pos.entry_underlying_price

                        if strategy.direction in (Direction.LONG, Direction.BOTH):
                            drop = ((ref_long - close) / ref_long * 100) if ref_long > 0 else 0.0
                            if drop < pyr.pullback_pct:
                                can_enter = False
                        else:
                            rise = ((close - ref_short) / ref_short * 100) if ref_short > 0 else 0.0
                            if rise < pyr.pullback_pct:
                                can_enter = False

        if can_enter:
            # For pure pullback-only scale-ins, skip re-evaluating the entry signal
            is_pullback_scalein = (
                scale_in_active
                and pyr.trigger_mode == "pullback_only"
            )
            if is_pullback_scalein:
                should_enter_long = strategy.direction in (Direction.LONG, Direction.BOTH)
                should_enter_short = strategy.direction in (Direction.SHORT, Direction.BOTH)
            else:
                should_enter_long = (
                    strategy.direction in (Direction.LONG, Direction.BOTH)
                    and _safe_evaluate(entry_tree, df, i)
                )
                should_enter_short = (
                    strategy.direction == Direction.SHORT
                    and _safe_evaluate(entry_tree, df, i)
                ) or (
                    strategy.direction == Direction.BOTH
                    and not should_enter_long
                )

            if should_enter_long:
                _open_position(state, "long", i, close, df, request)
            elif should_enter_short and strategy.direction == Direction.SHORT:
                _open_position(state, "short", i, close, df, request)

        _record_equity(state, bar_date, close, df, i, request)

    # Close any remaining positions at end of data
    if state.positions:
        final_close = float(df["Close"].iloc[-1])
        for pos in list(state.positions):
            _close_position(state, pos, df, len(df) - 1, final_close, "end_of_data", request)
        if state.equity_curve:
            state.equity_curve[-1]["equity"] = _calc_equity(state, final_close, df, len(df)-1, request)

    benchmark_curve = _build_benchmark(df, request.initial_capital)

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
    try:
        return evaluate_condition_tree(tree, df, bar_idx)
    except Exception as exc:
        logger.debug("Condition eval error at bar %d: %s", bar_idx, exc)
        return False


def _get_option_price(
    spot: float, strike: float, dte_days: float, iv: float, opt_type: str
) -> float:
    # 100 multiplier is applied to the final premium, this returns per-share price
    return black_scholes_price(spot, strike, max(dte_days/365.0, 0.001), 0.045, iv, opt_type)


def _calc_equity(state: EngineState, current_price: float, df: pd.DataFrame, bar_idx: int, req: BacktestRequest) -> float:
    equity = state.cash
    for pos in state.positions:
        if pos.is_option:
            days_held = bar_idx - pos.entry_bar_idx
            remaining_dte = max(pos.option_dte_entry - days_held, 0)
            # Use entry IV as a simplistic proxy for remaining IV if not available
            iv = pos.option_iv_entry
            opt_price = _get_option_price(current_price, pos.option_strike, remaining_dte, iv, pos.option_type)
            if pos.direction == "long":
                equity += pos.shares * opt_price * 100  # Options are 100 shares
            else:
                # Sold option: mark as a liability (cost to buy back)
                equity -= pos.shares * opt_price * 100
        else:
            if pos.direction == "long":
                equity += pos.shares * current_price
            else:
                equity += pos.shares * (2 * pos.entry_price - current_price)
    return equity


def _record_equity(state: EngineState, bar_date: str, close: float, df: pd.DataFrame, bar_idx: int, req: BacktestRequest) -> None:
    equity = _calc_equity(state, close, df, bar_idx, req) if close > 0 else state.cash
    state.equity_curve.append({"date": bar_date, "equity": round(equity, 2)})


def _open_position(
    state: EngineState,
    direction: str,
    bar_idx: int,
    price: float,
    df: pd.DataFrame,
    request: BacktestRequest,
    override_shares: float | None = None,
) -> None:
    sizing = request.strategy.position_sizing
    opt_conf = request.strategy.options

    equity = _calc_equity(state, price, df, bar_idx, request)

    if opt_conf.enabled:
        # Approximate option pricing
        iv = float(df["rv20"].iloc[bar_idx]) if "rv20" in df and not pd.isna(df["rv20"].iloc[bar_idx]) else 0.30
        iv = max(iv, 0.10) # Floor IV at 10%

        target_delta = opt_conf.target_delta
        if direction == "short" and opt_conf.type == "call":
            target_delta = -target_delta # Selling call is negative delta
        elif direction == "long" and opt_conf.type == "put":
            target_delta = -target_delta # Buying put is negative delta

        strike = get_strike_for_delta(price, target_delta, max(opt_conf.target_dte/365.0, 0.001), 0.045, iv, opt_conf.type)
        fill_price = _get_option_price(price, strike, opt_conf.target_dte, iv, opt_conf.type)
        fill_price_cost = fill_price * 100 # Per contract
    else:
        fill_price = price * (1 + request.slippage_pct / 100.0) if direction == "long" else \
                     price * (1 - request.slippage_pct / 100.0)
        fill_price_cost = fill_price

    if override_shares is not None:
        shares = override_shares
    else:
        shares = _calculate_shares(
            sizing_method=sizing.method,
            sizing_value=sizing.value,
            risk_pct=sizing.risk_pct,
            equity=equity,
            fill_price=fill_price_cost,
            stop_loss_pct=request.strategy.exit.stop_loss_pct,
        )

    if shares <= 0:
        return

    if opt_conf.enabled and direction == "short":
        # Sell to open: premium is received, not paid. Collateral is not
        # modeled — pair short-option strategies with fixed-contract sizing.
        state.cash += shares * fill_price_cost - request.commission
    else:
        cost = shares * fill_price_cost + request.commission
        if cost > state.cash:
            affordable = (state.cash - request.commission) / fill_price_cost
            shares = math.floor(affordable)
            if shares <= 0:
                return
            cost = shares * fill_price_cost + request.commission

        state.cash -= cost
    bar_date = df.index[bar_idx].strftime("%Y-%m-%d")

    if opt_conf.enabled:
        init_high_stock = float(df["High"].iloc[bar_idx])
        init_low_stock = float(df["Low"].iloc[bar_idx])
        if opt_conf.type == "call":
            init_high = _get_option_price(init_high_stock, strike, opt_conf.target_dte, iv, opt_conf.type)
            init_low = _get_option_price(init_low_stock, strike, opt_conf.target_dte, iv, opt_conf.type)
        else:
            init_high = _get_option_price(init_low_stock, strike, opt_conf.target_dte, iv, opt_conf.type)
            init_low = _get_option_price(init_high_stock, strike, opt_conf.target_dte, iv, opt_conf.type)

        pos = OpenPosition(
            direction=direction,
            entry_date=bar_date,
            entry_price=fill_price,
            entry_underlying_price=price,
            shares=shares,
            entry_bar_idx=bar_idx,
            highest_since_entry=init_high,
            lowest_since_entry=init_low,
            highest_underlying_since_entry=float(df["High"].iloc[bar_idx]),
            lowest_underlying_since_entry=float(df["Low"].iloc[bar_idx]),
            is_option=True,
            option_type=opt_conf.type,
            option_strike=strike,
            option_dte_entry=opt_conf.target_dte,
            option_iv_entry=iv
        )
    else:
        pos = OpenPosition(
            direction=direction,
            entry_date=bar_date,
            entry_price=fill_price,
            entry_underlying_price=price,
            shares=shares,
            entry_bar_idx=bar_idx,
            highest_since_entry=float(df["High"].iloc[bar_idx]),
            lowest_since_entry=float(df["Low"].iloc[bar_idx]),
            highest_underlying_since_entry=float(df["High"].iloc[bar_idx]),
            lowest_underlying_since_entry=float(df["Low"].iloc[bar_idx]),
        )

    state.positions.append(pos)


def _close_position(
    state: EngineState,
    pos: OpenPosition,
    df: pd.DataFrame,
    bar_idx: int,
    price: float,
    reason: str,
    request: BacktestRequest,
) -> None:
    fill_price = price

    if pos.is_option:
        days_held = bar_idx - pos.entry_bar_idx
        remaining_dte = max(pos.option_dte_entry - days_held, 0)
        fill_price = _get_option_price(price, pos.option_strike, remaining_dte, pos.option_iv_entry, pos.option_type)

    # Adjust for limit orders
    if reason == "take_profit":
        days_held = bar_idx - pos.entry_bar_idx
        tier = _resolve_ladder_tier(request.strategy.exit, days_held)
        effective_pct = tier.take_profit_pct if tier is not None else request.strategy.exit.take_profit_pct
        if effective_pct is not None:
            if pos.direction == "long":
                fill_price = pos.entry_price * (1 + effective_pct / 100.0)
            else:
                fill_price = pos.entry_price * (1 - effective_pct / 100.0)
    elif reason == "stop_loss" and request.strategy.exit.stop_loss_pct is not None:
        if pos.direction == "long":
            fill_price = pos.entry_price * (1 - request.strategy.exit.stop_loss_pct / 100.0)
        else:
            fill_price = pos.entry_price * (1 + request.strategy.exit.stop_loss_pct / 100.0)
    elif reason == "trailing_stop" and request.strategy.exit.trailing_stop_pct is not None:
        if pos.direction == "long":
            fill_price = pos.highest_since_entry * (1 - request.strategy.exit.trailing_stop_pct / 100.0)
        else:
            fill_price = pos.lowest_since_entry * (1 + request.strategy.exit.trailing_stop_pct / 100.0)
    else:
        # Normal market order slippage
        if pos.direction == "long":
            fill_price = fill_price * (1 - request.slippage_pct / 100.0)
        else:
            fill_price = fill_price * (1 + request.slippage_pct / 100.0)

    if pos.is_option:
        if pos.direction == "long":
            pnl = (fill_price - pos.entry_price) * pos.shares * 100
            proceeds = (pos.shares * fill_price * 100) - request.commission
        else:
            # Sold option: profit is premium received minus buy-back cost
            pnl = (pos.entry_price - fill_price) * pos.shares * 100
            proceeds = -(pos.shares * fill_price * 100) - request.commission
        pnl -= request.commission
        pnl_pct = (pnl / (pos.entry_price * pos.shares * 100)) * 100.0 if pos.entry_price > 0 else 0.0
    else:
        if pos.direction == "long":
            pnl = (fill_price - pos.entry_price) * pos.shares
        else:
            pnl = (pos.entry_price - fill_price) * pos.shares

        pnl -= request.commission
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
        is_option=pos.is_option,
        option_type=pos.option_type,
        option_strike=pos.option_strike,
        option_dte_entry=pos.option_dte_entry,
        option_dte_exit=max(pos.option_dte_entry - bars_held, 0) if pos.is_option else None,
        option_iv_entry=pos.option_iv_entry,
    )

    state.trades.append(trade)
    state.positions.remove(pos)


def _resolve_ladder_tier(exit_config, days_held: int) -> ProfitLadderTier | None:
    """Return the first profit_ladder tier covering days_held, or None
    if no ladder is configured or none apply yet. Shared by _check_exit
    (decides whether to exit) and _close_position (picks the fill price)
    so both always agree on which tier fired."""
    if not exit_config.profit_ladder:
        return None
    return next(
        (t for t in exit_config.profit_ladder if t.max_days_held >= days_held),
        None,
    )


def _check_exit(
    pos: OpenPosition,
    exit_config,
    exit_tree: dict | None,
    df: pd.DataFrame,
    bar_idx: int,
    close: float,
    high: float,
    low: float,
) -> str | None:
    if exit_config.stop_loss_pct is not None:
        if pos.direction == "long":
            stop_price = pos.entry_price * (1 - exit_config.stop_loss_pct / 100.0)
            if low <= stop_price:
                return "stop_loss"
        else:
            stop_price = pos.entry_price * (1 + exit_config.stop_loss_pct / 100.0)
            if high >= stop_price:
                return "stop_loss"

    if exit_config.profit_ladder:
        tier = _resolve_ladder_tier(exit_config, bar_idx - pos.entry_bar_idx)
        if tier is not None:
            if pos.direction == "long":
                target = pos.entry_price * (1 + tier.take_profit_pct / 100.0)
                if high >= target:
                    return "take_profit"
            else:
                target = pos.entry_price * (1 - tier.take_profit_pct / 100.0)
                if low <= target:
                    return "take_profit"
    elif exit_config.take_profit_pct is not None:
        if pos.direction == "long":
            target = pos.entry_price * (1 + exit_config.take_profit_pct / 100.0)
            if high >= target:
                return "take_profit"
        else:
            target = pos.entry_price * (1 - exit_config.take_profit_pct / 100.0)
            if low <= target:
                return "take_profit"

    if exit_config.trailing_stop_pct is not None:
        if pos.direction == "long":
            trail_price = pos.highest_since_entry * (1 - exit_config.trailing_stop_pct / 100.0)
            if low <= trail_price:
                return "trailing_stop"
        else:
            trail_price = pos.lowest_since_entry * (1 + exit_config.trailing_stop_pct / 100.0)
            if high >= trail_price:
                return "trailing_stop"

    if exit_config.max_hold_days is not None:
        bars_held = bar_idx - pos.entry_bar_idx
        if bars_held >= exit_config.max_hold_days:
            return "time"

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
        if risk_pct is None or stop_loss_pct is None or stop_loss_pct <= 0:
            dollar_amount = equity * 0.10
            return math.floor(dollar_amount / fill_price)

        risk_dollars = equity * (risk_pct / 100.0)
        risk_per_share = fill_price * (stop_loss_pct / 100.0)
        if risk_per_share <= 0:
            return 0
        return math.floor(risk_dollars / risk_per_share)

    return 0


def _build_benchmark(df: pd.DataFrame, initial_capital: float) -> list[dict]:
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
