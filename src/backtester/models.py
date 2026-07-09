"""Pydantic models for strategy definitions, backtest requests, and results."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

# ── Enums ─────────────────────────────────────────────────────────────────────


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class PyramidingExitMode(str, Enum):
    SELL_ALL = "sell_all"
    SELL_PROFITABLE = "sell_profitable"


class PositionSizingMethod(str, Enum):
    FIXED_SHARES = "fixed_shares"
    FIXED_DOLLAR = "fixed_dollar"
    PERCENT_EQUITY = "percent_equity"
    RISK_BASED = "risk_based"


class Comparator(str, Enum):
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    EQ = "eq"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"


class ConditionOperator(str, Enum):
    AND = "AND"
    OR = "OR"


class WalkForwardMode(str, Enum):
    ROLLING = "rolling"
    ANCHORED = "anchored"


# ── Condition Tree ────────────────────────────────────────────────────────────


class IndicatorRef(BaseModel):
    """Reference to a technical indicator with its parameters."""
    name: str  # e.g. "RSI", "SMA", "EMA", "MACD_LINE", "MACD_SIGNAL", "BB_UPPER", etc.
    params: dict[str, Any] = Field(default_factory=dict)  # e.g. {"period": 14}


class ThresholdCondition(BaseModel):
    """Indicator vs. static value (e.g. RSI(14) < 30)."""
    type: str = "threshold"
    indicator: IndicatorRef
    comparator: Comparator
    value: float


class ReferenceCondition(BaseModel):
    """Indicator vs. indicator (e.g. Price > SMA(200))."""
    type: str = "reference"
    indicator: IndicatorRef
    comparator: Comparator
    ref_indicator: IndicatorRef


class CrossoverCondition(BaseModel):
    """Indicator crosses above/below another indicator."""
    type: str = "crossover"
    indicator: IndicatorRef
    direction: str  # "above" or "below"
    ref_indicator: IndicatorRef


class PullbackCondition(BaseModel):
    """Price dropped X% from N-day high."""
    type: str = "pullback"
    lookback: int = 20
    pct: float = 5.0


class ConsecutiveCondition(BaseModel):
    """N consecutive up or down candles."""
    type: str = "consecutive"
    direction: str  # "up" or "down"
    count: int = 3


class ConditionGroup(BaseModel):
    """Compound condition with AND/OR nesting. Children can be conditions or nested groups."""
    operator: ConditionOperator
    conditions: list[dict[str, Any]]  # Raw dicts that get parsed by the evaluator


# ── Exit Strategy ─────────────────────────────────────────────────────────────


class ExitStrategy(BaseModel):
    """All exit conditions for a strategy."""
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    max_hold_days: int | None = None
    # Indicator-based exit conditions (same tree structure as entry)
    conditions: dict[str, Any] | None = None
    pyramiding_exit_mode: PyramidingExitMode = PyramidingExitMode.SELL_ALL


# ── Position Sizing ───────────────────────────────────────────────────────────


class PositionSizing(BaseModel):
    method: PositionSizingMethod = PositionSizingMethod.FIXED_DOLLAR
    value: float = 10000.0  # Shares, dollars, percentage, or risk percentage
    # For risk-based: what % of equity to risk per trade
    risk_pct: float | None = None


# ── Options & Pyramiding ──────────────────────────────────────────────────────


class OptionsConfig(BaseModel):
    enabled: bool = False
    type: OptionType = OptionType.CALL
    target_dte: int = 30
    target_delta: float = 0.50
    roll_dte: int | None = None  # Roll losing contracts when DTE falls to this value


class PyramidingConfig(BaseModel):
    enabled: bool = False
    max_positions: int = 1
    # "pullback_only" — scale in when underlying drops pullback_pct% from reference
    # "entry_signal"  — scale in when entry conditions fire again
    # "both"          — both pullback AND entry signal must be true
    trigger_mode: str = "entry_signal"
    pullback_pct: float | None = None       # e.g. 20.0 for 20% pullback
    pullback_reference: str = "entry"       # "entry" or "rolling"

    @model_validator(mode="before")
    @classmethod
    def _map_legacy_scale_in_fields(cls, data: Any) -> Any:
        """Map pre-rename field names (scale_in_trigger/scale_in_value) onto
        trigger_mode/pullback_pct so strategies saved before this rename don't
        silently validate with defaults instead of their saved values.

        Mirrors the mapping already applied client-side in
        src/web/v2/backtester.js's applyStrategyToForm().
        """
        if not isinstance(data, dict):
            return data
        if "trigger_mode" not in data and "scale_in_trigger" in data:
            data = {
                **data,
                "trigger_mode": (
                    "pullback_only" if data["scale_in_trigger"] == "pullback" else "entry_signal"
                ),
            }
        if "pullback_pct" not in data and "scale_in_value" in data:
            data = {**data, "pullback_pct": data["scale_in_value"]}
        return data


# ── Strategy Definition ──────────────────────────────────────────────────────


class StrategyDefinition(BaseModel):
    """Complete strategy specification — entry, exit, sizing, direction."""
    name: str = "Untitled Strategy"
    entry: dict[str, Any]  # ConditionGroup as dict (parsed at evaluation time)
    exit: ExitStrategy = Field(default_factory=ExitStrategy)
    position_sizing: PositionSizing = Field(default_factory=PositionSizing)
    options: OptionsConfig = Field(default_factory=OptionsConfig)
    pyramiding: PyramidingConfig = Field(default_factory=PyramidingConfig)
    direction: Direction = Direction.LONG


# ── Backtest Request ──────────────────────────────────────────────────────────


class BacktestRequest(BaseModel):
    """Full backtest execution request."""
    strategy: StrategyDefinition
    ticker: str
    start_date: date | None = None  # None = go back as far as possible
    end_date: date | None = None  # None = today
    initial_capital: float = 100000.0
    commission: float = 0.0  # Per-trade flat fee
    slippage_pct: float = 0.0  # Slippage as % of fill price
    timeframe: str = "daily"  # "daily" or "weekly"


class WalkForwardRequest(BaseModel):
    """Walk-forward analysis request."""
    strategy: StrategyDefinition
    ticker: str
    start_date: date | None = None
    end_date: date | None = None
    initial_capital: float = 100000.0
    commission: float = 0.0
    slippage_pct: float = 0.0
    timeframe: str = "daily"
    mode: WalkForwardMode = WalkForwardMode.ROLLING
    in_sample_days: int = 756  # ~3 years of trading days
    out_of_sample_days: int = 252  # ~1 year of trading days


# ── Results ───────────────────────────────────────────────────────────────────


class Trade(BaseModel):
    """Record of a single completed trade."""
    entry_date: str
    exit_date: str
    direction: str
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    pnl_pct: float
    exit_reason: str  # "stop_loss", "take_profit", "trailing_stop", "signal", "time", "end_of_data"
    bars_held: int
    is_option: bool = False
    option_type: str | None = None
    option_strike: float | None = None
    option_dte_entry: float | None = None
    option_dte_exit: float | None = None
    option_iv_entry: float | None = None


class BacktestResult(BaseModel):
    """Complete backtest output."""
    # Equity curve: list of {"date": "YYYY-MM-DD", "equity": float}
    equity_curve: list[dict[str, Any]]
    # Completed trades
    trades: list[Trade]
    # Summary statistics
    stats: dict[str, Any]
    # Benchmark (buy-and-hold) equity curve
    benchmark_curve: list[dict[str, Any]]
    # Strategy metadata
    ticker: str
    start_date: str
    end_date: str
    initial_capital: float


class WalkForwardFold(BaseModel):
    """Results for a single walk-forward fold."""
    fold_number: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_stats: dict[str, Any]
    oos_stats: dict[str, Any]
    regime: str  # "bull", "bear", "sideways"


class WalkForwardResult(BaseModel):
    """Complete walk-forward analysis output."""
    folds: list[WalkForwardFold]
    # Aggregated OOS-only equity curve
    aggregated_oos_curve: list[dict[str, Any]]
    # Aggregated OOS statistics
    aggregated_oos_stats: dict[str, Any]
    # Per-metric degradation ratios (OOS / IS)
    degradation_ratios: dict[str, float | None]
    mode: str
    ticker: str
    start_date: str
    end_date: str
