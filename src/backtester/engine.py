"""Bar-by-bar backtesting engine (long-only on the underlying).

Iterates through each bar of historical data, evaluates entry/exit conditions,
manages positions, and records the full equity curve and trade log. No lookahead
bias — each decision uses only data available at that bar.

Key differences vs. the previous version:
  * Long-only on the underlying. The broken `direction=short` path for stocks
    has been removed — short exposure now lives in `OptionsConfig.position`.
  * Short options supported: a sold put/call credits premium at entry and we
    repurchase at close. Stop-loss / take-profit semantics are inverted.
  * Option DTE is tracked in calendar days, not trading bars. A 30-DTE option
    opened on day N expires 30 calendar days later (not 30 trading bars).
  * IV at entry is sourced from `stock_iv_history` when available, falling back
    to scaled 20-day realized vol. See `iv_provider.py`.
  * Strikes are snapped to a realistic OCC-style grid.
  * Pyramiding (scale-in) has three trigger modes (pullback only / signal only /
    require both) and three reference modes (last fill / original entry /
    high watermark). Tranches that belong together share a `campaign_id`.
  * The equity-curve points carry `avg_cost` and `n_positions` so the dashboard
    can plot weighted-average cost basis without recomputing.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .conditions import evaluate_condition_tree
from .iv_provider import build_iv_lookup, lookup_iv
from .models import (
    BacktestRequest,
    BacktestResult,
    OptionPosition,
    PositionSizingMethod,
    PyramidingExitMode,
    PyramidingReference,
    PyramidingTriggerMode,
    Trade,
)
from .stats import compute_stats
from .options import black_scholes_price, get_strike_for_delta

logger = logging.getLogger(__name__)

# Risk-free rate used for Black-Scholes pricing. Could be made configurable.
RISK_FREE_RATE = 0.045


@dataclass
class OpenPosition:
    """Tracks an active position during the backtest."""
    entry_date: str
    entry_timestamp: pd.Timestamp
    entry_price: float                # Per-share fill (option premium or stock price)
    entry_underlying_price: float     # Underlying close on the entry bar
    shares: float                     # Stocks: shares. Options: contracts.
    entry_bar_idx: int
    campaign_id: int
    highest_underlying_since_entry: float = 0.0
    is_option: bool = False
    option_type: str | None = None
    option_position: str | None = None  # "long" or "short"
    option_strike: float | None = None
    option_dte_entry: float | None = None  # Calendar-day DTE at entry
    option_iv_entry: float | None = None


@dataclass
class EngineState:
    cash: float = 0.0
    positions: list[OpenPosition] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    next_campaign_id: int = 1
    current_campaign_id: int = 0


def run_backtest(request: BacktestRequest, df: pd.DataFrame) -> BacktestResult:
    """Execute a full backtest on the provided data."""
    strategy = request.strategy
    state = EngineState(cash=request.initial_capital)

    entry_tree = strategy.entry
    exit_config = strategy.exit
    exit_tree = exit_config.conditions if exit_config.conditions else None
    pyr = strategy.pyramiding

    # Pre-compute 20-day realized volatility once (used by IV fallback).
    if strategy.options.enabled and "rv20" not in df.columns:
        df["returns"] = df["Close"].pct_change()
        df["rv20"] = df["returns"].rolling(20).std() * math.sqrt(252)

    iv_map = build_iv_lookup(request.ticker) if strategy.options.enabled else {}

    for i in range(len(df)):
        bar_timestamp: pd.Timestamp = df.index[i]
        bar_date = bar_timestamp.strftime("%Y-%m-%d")
        close = float(df["Close"].iloc[i])
        high = float(df["High"].iloc[i])
        low = float(df["Low"].iloc[i])

        if pd.isna(close) or close <= 0:
            _record_equity(state, bar_date, close, df, i, request)
            continue

        _check_and_close_exits(state, df, i, bar_timestamp, close, high, low, exit_config, exit_tree, request)

        _maybe_open_position(
            state=state,
            df=df,
            i=i,
            bar_timestamp=bar_timestamp,
            close=close,
            request=request,
            entry_tree=entry_tree,
            pyr=pyr,
            iv_map=iv_map,
        )

        _record_equity(state, bar_date, close, df, i, request)

    # Close any remaining positions at end of data
    if state.positions:
        final_close = float(df["Close"].iloc[-1])
        for pos in list(state.positions):
            _close_position(state, pos, df, len(df) - 1, final_close, "end_of_data", request)
        if state.equity_curve:
            state.equity_curve[-1]["equity"] = _calc_equity(state, final_close, df, len(df) - 1, request)

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


# ── Exit handling ─────────────────────────────────────────────────────────────


def _check_and_close_exits(
    state: EngineState,
    df: pd.DataFrame,
    i: int,
    bar_timestamp: pd.Timestamp,
    close: float,
    high: float,
    low: float,
    exit_config,
    exit_tree,
    request: BacktestRequest,
) -> None:
    """Identify which positions triggered an exit on this bar and close them."""
    positions_to_close: list[OpenPosition] = []
    exit_reasons: list[str] = []

    for pos in state.positions:
        if pos.is_option:
            rem_dte = _calendar_dte_remaining(pos, bar_timestamp)
            # The high/low of the option mark depends on direction of underlying move
            if pos.option_type == "call":
                opt_high = _bs_price(high, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
                opt_low = _bs_price(low, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
            else:
                opt_high = _bs_price(low, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
                opt_low = _bs_price(high, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
            pos_high, pos_low = opt_high, opt_low
        else:
            pos_high, pos_low = high, low

        # Track high watermark of the underlying for trailing-stop / scale-in
        pos.highest_underlying_since_entry = max(pos.highest_underlying_since_entry, high)

        reason = _check_exit(pos, exit_config, exit_tree, df, i, bar_timestamp, pos_high, pos_low)

        if reason is None and pos.is_option and pos.option_dte_entry is not None:
            rem_dte = _calendar_dte_remaining(pos, bar_timestamp)
            if rem_dte <= 0:
                reason = "expiration"

        if reason:
            positions_to_close.append(pos)
            exit_reasons.append(reason)

    if not positions_to_close:
        return

    if exit_config.pyramiding_exit_mode == PyramidingExitMode.SELL_ALL:
        # Close every open tranche in the same campaign as any triggered one
        triggered_campaigns = {p.campaign_id for p in positions_to_close}
        for pos in list(state.positions):
            if pos.campaign_id not in triggered_campaigns:
                continue
            if pos in positions_to_close:
                actual_reason = exit_reasons[positions_to_close.index(pos)]
            else:
                actual_reason = f"{exit_reasons[0]}_forced"
            _close_position(state, pos, df, i, close, actual_reason, request)
    else:
        # SELL_PROFITABLE — close triggered tranches AND any other open tranche
        # in the campaign whose mark-to-market P&L is positive. Losers stay open
        # to (potentially) be averaged down further.
        triggered_campaigns = {p.campaign_id for p in positions_to_close}
        # Close triggered tranches first
        for pos, reason in zip(positions_to_close, exit_reasons):
            if pos in state.positions:
                _close_position(state, pos, df, i, close, reason, request)
        # Then sweep profitable siblings
        for pos in list(state.positions):
            if pos.campaign_id not in triggered_campaigns:
                continue
            if _position_pnl(pos, close, bar_timestamp) > 0:
                _close_position(state, pos, df, i, close, "sibling_profit_take", request)


# ── Entry handling ────────────────────────────────────────────────────────────


def _maybe_open_position(
    state: EngineState,
    df: pd.DataFrame,
    i: int,
    bar_timestamp: pd.Timestamp,
    close: float,
    request: BacktestRequest,
    entry_tree: dict,
    pyr,
    iv_map: dict,
) -> None:
    strategy = request.strategy

    if state.positions:
        if not pyr.enabled or len(state.positions) >= pyr.max_positions:
            return

        # We're considering a scale-in. Compute the pullback test first.
        anchor = _pullback_anchor(state, pyr.pullback_reference)
        drop_pct = ((anchor - close) / anchor) * 100.0 if anchor > 0 else 0.0
        pullback_ok = pyr.pullback_pct is not None and drop_pct >= pyr.pullback_pct
        signal_ok = _safe_evaluate(entry_tree, df, i)

        if pyr.trigger_mode == PyramidingTriggerMode.PULLBACK_ONLY:
            if not pullback_ok:
                return
        elif pyr.trigger_mode == PyramidingTriggerMode.SIGNAL_ONLY:
            if not signal_ok:
                return
        elif pyr.trigger_mode == PyramidingTriggerMode.BOTH:
            if not (pullback_ok and signal_ok):
                return
        # New tranche joins the existing campaign
        campaign_id = state.current_campaign_id
    else:
        if not _safe_evaluate(entry_tree, df, i):
            return
        campaign_id = state.next_campaign_id
        state.next_campaign_id += 1
        state.current_campaign_id = campaign_id

    _open_position(state, i, bar_timestamp, close, df, request, iv_map, campaign_id)


def _open_position(
    state: EngineState,
    bar_idx: int,
    bar_timestamp: pd.Timestamp,
    price: float,
    df: pd.DataFrame,
    request: BacktestRequest,
    iv_map: dict,
    campaign_id: int,
) -> None:
    sizing = request.strategy.position_sizing
    opt_conf = request.strategy.options
    bar_date = bar_timestamp.strftime("%Y-%m-%d")

    equity = _calc_equity(state, price, df, bar_idx, request)

    if opt_conf.enabled:
        rv20 = float(df["rv20"].iloc[bar_idx]) if "rv20" in df.columns else None
        iv = lookup_iv(iv_map, bar_timestamp.date(), rv20)

        # Compute strike for the target delta from the BUYER'S perspective,
        # then we'll mark/credit appropriately depending on long vs short.
        target_delta = opt_conf.target_delta
        if opt_conf.type == "put":
            target_delta = -target_delta

        strike = get_strike_for_delta(
            price,
            target_delta,
            max(opt_conf.target_dte / 365.0, 0.001),
            RISK_FREE_RATE,
            iv,
            opt_conf.type,
        )
        premium_per_share = _bs_price(price, strike, opt_conf.target_dte, iv, opt_conf.type)
        if premium_per_share <= 0:
            return
        cost_per_contract = premium_per_share * 100
    else:
        # Stock: include slippage on the buy
        premium_per_share = price * (1 + request.slippage_pct / 100.0)
        cost_per_contract = premium_per_share

    shares = _calculate_shares(
        sizing_method=sizing.method,
        sizing_value=sizing.value,
        risk_pct=sizing.risk_pct,
        equity=equity,
        fill_price=cost_per_contract,
        stop_loss_pct=request.strategy.exit.stop_loss_pct,
    )
    if shares <= 0:
        return

    is_short_option = opt_conf.enabled and opt_conf.position == OptionPosition.SHORT
    notional = shares * cost_per_contract

    if is_short_option:
        # Collect premium minus commission. No buying-power model — assumes margin.
        state.cash += notional - request.commission
    else:
        if notional + request.commission > state.cash:
            affordable = (state.cash - request.commission) / cost_per_contract
            shares = math.floor(affordable)
            if shares <= 0:
                return
            notional = shares * cost_per_contract
        state.cash -= notional + request.commission

    pos = OpenPosition(
        entry_date=bar_date,
        entry_timestamp=bar_timestamp,
        entry_price=premium_per_share,
        entry_underlying_price=price,
        shares=shares,
        entry_bar_idx=bar_idx,
        campaign_id=campaign_id,
        highest_underlying_since_entry=float(df["High"].iloc[bar_idx]),
        is_option=opt_conf.enabled,
        option_type=opt_conf.type if opt_conf.enabled else None,
        option_position=opt_conf.position.value if opt_conf.enabled else None,
        option_strike=strike if opt_conf.enabled else None,
        option_dte_entry=float(opt_conf.target_dte) if opt_conf.enabled else None,
        option_iv_entry=iv if opt_conf.enabled else None,
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
    bar_timestamp = df.index[bar_idx]

    if pos.is_option:
        rem_dte = _calendar_dte_remaining(pos, bar_timestamp)
        mark = _bs_price(price, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
        fill_price = _apply_option_exit_limits(pos, reason, mark, request)
    else:
        fill_price = _apply_stock_exit_limits(pos, reason, price, request)

    if pos.is_option:
        notional = pos.shares * fill_price * 100
        if pos.option_position == OptionPosition.SHORT.value:
            # We sold premium at entry; buy it back to close.
            pnl = (pos.entry_price - fill_price) * pos.shares * 100 - request.commission
            state.cash -= notional + request.commission
        else:
            pnl = (fill_price - pos.entry_price) * pos.shares * 100 - request.commission
            state.cash += notional - request.commission
        basis = pos.entry_price * pos.shares * 100
        pnl_pct = (pnl / basis) * 100.0 if basis > 0 else 0.0
    else:
        pnl = (fill_price - pos.entry_price) * pos.shares - request.commission
        state.cash += pos.shares * fill_price - request.commission
        pnl_pct = (pnl / (pos.entry_price * pos.shares)) * 100.0 if pos.entry_price > 0 else 0.0

    exit_date = bar_timestamp.strftime("%Y-%m-%d")
    bars_held = bar_idx - pos.entry_bar_idx
    dte_exit = _calendar_dte_remaining(pos, bar_timestamp) if pos.is_option else None

    direction = "short" if (pos.is_option and pos.option_position == OptionPosition.SHORT.value) else "long"

    trade = Trade(
        entry_date=pos.entry_date,
        exit_date=exit_date,
        direction=direction,
        entry_price=round(pos.entry_price, 4),
        exit_price=round(fill_price, 4),
        shares=pos.shares,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        exit_reason=reason,
        bars_held=bars_held,
        campaign_id=pos.campaign_id,
        is_option=pos.is_option,
        option_type=pos.option_type,
        option_position=pos.option_position,
        option_strike=pos.option_strike,
        option_dte_entry=pos.option_dte_entry,
        option_dte_exit=dte_exit,
        option_iv_entry=pos.option_iv_entry,
    )
    state.trades.append(trade)
    state.positions.remove(pos)
    if not state.positions:
        state.current_campaign_id = 0


def _apply_stock_exit_limits(pos: OpenPosition, reason: str, market_price: float, request: BacktestRequest) -> float:
    """Limit-price approximation for stock exits."""
    ex = request.strategy.exit
    if reason == "take_profit" and ex.take_profit_pct is not None:
        return pos.entry_price * (1 + ex.take_profit_pct / 100.0)
    if reason == "stop_loss" and ex.stop_loss_pct is not None:
        return pos.entry_price * (1 - ex.stop_loss_pct / 100.0)
    if reason == "trailing_stop" and ex.trailing_stop_pct is not None:
        return pos.highest_underlying_since_entry * (1 - ex.trailing_stop_pct / 100.0)
    return market_price * (1 - request.slippage_pct / 100.0)


def _apply_option_exit_limits(pos: OpenPosition, reason: str, mark: float, request: BacktestRequest) -> float:
    """For option exits we fill at the BS mark unless an explicit limit was hit.

    For SHORT options, take_profit / stop_loss semantics are inverted:
        * profit when premium DROPS (you bought back cheaper)
        * loss when premium RISES (you bought back more expensive)
    """
    ex = request.strategy.exit
    is_short = pos.option_position == OptionPosition.SHORT.value

    if reason == "take_profit" and ex.take_profit_pct is not None:
        if is_short:
            return pos.entry_price * (1 - ex.take_profit_pct / 100.0)
        return pos.entry_price * (1 + ex.take_profit_pct / 100.0)
    if reason == "stop_loss" and ex.stop_loss_pct is not None:
        if is_short:
            return pos.entry_price * (1 + ex.stop_loss_pct / 100.0)
        return pos.entry_price * (1 - ex.stop_loss_pct / 100.0)
    return mark


def _check_exit(
    pos: OpenPosition,
    exit_config,
    exit_tree: dict | None,
    df: pd.DataFrame,
    bar_idx: int,
    bar_timestamp: pd.Timestamp,
    high: float,
    low: float,
) -> str | None:
    """Stop-loss / take-profit / trailing / max-hold / signal exit checks.

    For SHORT options the per-position high/low are inverted relative to a long:
        - A SHORT option profits when premium FALLS — TP triggers on a low premium.
        - A SHORT option loses when premium RISES — stop triggers on a high premium.
    """
    is_short_opt = pos.is_option and pos.option_position == OptionPosition.SHORT.value

    if exit_config.stop_loss_pct is not None:
        if is_short_opt:
            stop_price = pos.entry_price * (1 + exit_config.stop_loss_pct / 100.0)
            if high >= stop_price:
                return "stop_loss"
        else:
            stop_price = pos.entry_price * (1 - exit_config.stop_loss_pct / 100.0)
            if low <= stop_price:
                return "stop_loss"

    if exit_config.take_profit_pct is not None:
        if is_short_opt:
            target = pos.entry_price * (1 - exit_config.take_profit_pct / 100.0)
            if low <= target:
                return "take_profit"
        else:
            target = pos.entry_price * (1 + exit_config.take_profit_pct / 100.0)
            if high >= target:
                return "take_profit"

    if exit_config.trailing_stop_pct is not None and not pos.is_option:
        trail_price = pos.highest_underlying_since_entry * (1 - exit_config.trailing_stop_pct / 100.0)
        if low <= trail_price:
            return "trailing_stop"

    if exit_config.max_hold_days is not None:
        bars_held = bar_idx - pos.entry_bar_idx
        if bars_held >= exit_config.max_hold_days:
            return "time"

    if exit_tree is not None and _safe_evaluate(exit_tree, df, bar_idx):
        return "signal"

    return None


# ── Helpers ────────────────────────────────────────────────────────────────────


def _safe_evaluate(tree: dict, df: pd.DataFrame, bar_idx: int) -> bool:
    if not tree:
        return False
    try:
        return evaluate_condition_tree(tree, df, bar_idx)
    except Exception as exc:
        logger.debug("Condition eval error at bar %d: %s", bar_idx, exc)
        return False


def _bs_price(spot: float, strike: float, dte_calendar_days: float, iv: float, opt_type: str) -> float:
    return black_scholes_price(spot, strike, max(dte_calendar_days / 365.0, 0.001), RISK_FREE_RATE, iv, opt_type)


def _calendar_dte_remaining(pos: OpenPosition, bar_timestamp: pd.Timestamp) -> float:
    """Days remaining until the option expires, in CALENDAR days.

    The previous engine subtracted trading bars, which over-estimated lifetime
    by ~40% (5 trading days = 7 calendar days).
    """
    if pos.option_dte_entry is None:
        return 0
    elapsed = (bar_timestamp - pos.entry_timestamp).days
    return max(pos.option_dte_entry - elapsed, 0)


def _position_pnl(pos: OpenPosition, current_underlying: float, bar_timestamp: pd.Timestamp) -> float:
    """Mark-to-market P&L of a single position in dollars."""
    if pos.is_option:
        rem_dte = _calendar_dte_remaining(pos, bar_timestamp)
        mark = _bs_price(current_underlying, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
        if pos.option_position == OptionPosition.SHORT.value:
            return (pos.entry_price - mark) * pos.shares * 100
        return (mark - pos.entry_price) * pos.shares * 100
    return (current_underlying - pos.entry_price) * pos.shares


def _pullback_anchor(state: EngineState, ref: PyramidingReference) -> float:
    """Reference price for the pullback test."""
    if not state.positions:
        return 0.0
    campaign_positions = [p for p in state.positions if p.campaign_id == state.current_campaign_id]
    if not campaign_positions:
        return 0.0
    if ref == PyramidingReference.LAST_FILL:
        return campaign_positions[-1].entry_underlying_price
    if ref == PyramidingReference.ORIGINAL_ENTRY:
        return campaign_positions[0].entry_underlying_price
    # HIGH_WATERMARK
    return max(p.highest_underlying_since_entry for p in campaign_positions)


def _calc_equity(state: EngineState, current_price: float, df: pd.DataFrame, bar_idx: int, req: BacktestRequest) -> float:
    """Long-only mark-to-market: cash + value of open positions.

    Short options are special: the premium was already credited to cash at entry,
    so the open-position mark is `-(current_mark * contracts * 100)` (we still
    owe that close-out cost).
    """
    bar_timestamp = df.index[bar_idx]
    equity = state.cash
    for pos in state.positions:
        if pos.is_option:
            rem_dte = _calendar_dte_remaining(pos, bar_timestamp)
            mark = _bs_price(current_price, pos.option_strike, rem_dte, pos.option_iv_entry, pos.option_type)
            if pos.option_position == OptionPosition.SHORT.value:
                equity -= pos.shares * mark * 100
            else:
                equity += pos.shares * mark * 100
        else:
            equity += pos.shares * current_price
    return equity


def _record_equity(state: EngineState, bar_date: str, close: float, df: pd.DataFrame, bar_idx: int, req: BacktestRequest) -> None:
    equity = _calc_equity(state, close, df, bar_idx, req) if close > 0 else state.cash
    if state.positions:
        total_shares = sum(p.shares for p in state.positions)
        avg_cost = (
            sum(p.shares * p.entry_underlying_price for p in state.positions) / total_shares
            if total_shares > 0
            else None
        )
    else:
        avg_cost = None
    state.equity_curve.append({
        "date": bar_date,
        "equity": round(equity, 2),
        "avg_cost": round(avg_cost, 4) if avg_cost is not None else None,
        "n_positions": len(state.positions),
    })


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

    if sizing_method == PositionSizingMethod.FIXED_DOLLAR:
        return math.floor(sizing_value / fill_price)

    if sizing_method == PositionSizingMethod.PERCENT_EQUITY:
        dollar_amount = equity * (sizing_value / 100.0)
        return math.floor(dollar_amount / fill_price)

    if sizing_method == PositionSizingMethod.RISK_BASED:
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
