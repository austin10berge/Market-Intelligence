"""Pydantic models for strategy definitions, backtest requests, and results.

Long-only by design (the engine no longer supports `direction=short` for shares —
short positions are expressed via `options.position = "short"` on options strategies).
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ── Enums ─────────────────────────────────────────────────────────────────────


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class OptionPosition(str, Enum):
    """Whether we are paying premium (long) or collecting premium (short)."""
    LONG = "long"   # Buying calls/puts
    SHORT = "short"  # Selling calls (covered) or puts (CSP)


class PyramidingExitMode(str, Enum):
    SELL_ALL = "sell_all"
    SELL_PROFITABLE = "sell_profitable"


class PyramidingTriggerMode(str, Enum):
    """How to decide when to add another tranche to an open campaign."""
    PULLBACK_ONLY = "pullback_only"   # Price has moved X% from the chosen reference
    SIGNAL_ONLY = "signal_only"        # Entry condition tree re-evaluates True
    BOTH = "both"                      # Require pullback AND a fresh entry signal


class PyramidingReference(str, Enum):
    """Anchor used by the pullback measurement."""
    LAST_FILL = "last_fill"           # % drop from the most recent tranche
    ORIGINAL_ENTRY = "original_entry"  # % drop from the first tranche
    HIGH_WATERMARK = "high_watermark"  # % drop from the highest close since first entry


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
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ThresholdCondition(BaseModel):
    type: str = "threshold"
    indicator: IndicatorRef
    comparator: Comparator
    value: float


class ReferenceCondition(BaseModel):
    type: str = "reference"
    indicator: IndicatorRef
    comparator: Comparator
    ref_indicator: IndicatorRef


class CrossoverCondition(BaseModel):
    type: str = "crossover"
    indicator: IndicatorRef
    direction: str
    ref_indicator: IndicatorRef


class PullbackCondition(BaseModel):
    type: str = "pullback"
    lookback: int = 20
    pct: float = 5.0


class ConsecutiveCondition(BaseModel):
    type: str = "consecutive"
    direction: str
    count: int = 3


class ConditionGroup(BaseModel):
    operator: ConditionOperator
    conditions: list[dict[str, Any]]


# ── Exit Strategy ─────────────────────────────────────────────────────────────


class ExitStrategy(BaseModel):
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    max_hold_days: int | None = None
    conditions: dict[str, Any] | None = None
    pyramiding_exit_mode: PyramidingExitMode = PyramidingExitMode.SELL_ALL


# ── Position Sizing ───────────────────────────────────────────────────────────


class PositionSizing(BaseModel):
    method: PositionSizingMethod = PositionSizingMethod.FIXED_DOLLAR
    value: float = 10000.0
    risk_pct: float | None = None


# ── Options & Pyramiding ──────────────────────────────────────────────────────


class OptionsConfig(BaseModel):
    enabled: bool = False
    type: OptionType = OptionType.CALL
    position: OptionPosition = OptionPosition.LONG
    target_dte: int = 30
    target_delta: float = 0.50
    # Multi-leg: when True alongside (type=call, position=short), the engine
    # opens a paired long-stock position (100 shares per contract) as the
    # "covered" side of a covered call. Both legs share a campaign_id.
    paired_stock: bool = False


class PyramidingConfig(BaseModel):
    enabled: bool = False
    max_positions: int = 1
    trigger_mode: PyramidingTriggerMode = PyramidingTriggerMode.SIGNAL_ONLY
    pullback_pct: float | None = None
    pullback_reference: PyramidingReference = PyramidingReference.LAST_FILL

    # Legacy fields kept so already-saved strategies still deserialize.
    # `model_validator` below migrates them into the new fields if present.
    scale_in_trigger: str | None = None
    scale_in_value: float | None = None

    @model_validator(mode="after")
    def _migrate_legacy(self) -> "PyramidingConfig":
        if self.scale_in_trigger and self.trigger_mode == PyramidingTriggerMode.SIGNAL_ONLY:
            if self.scale_in_trigger == "pullback":
                self.trigger_mode = PyramidingTriggerMode.PULLBACK_ONLY
            elif self.scale_in_trigger == "entry_signal":
                self.trigger_mode = PyramidingTriggerMode.SIGNAL_ONLY
        if self.pullback_pct is None and self.scale_in_value is not None:
            self.pullback_pct = self.scale_in_value
        return self


# ── Strategy Definition ──────────────────────────────────────────────────────


class StrategyDefinition(BaseModel):
    """Long-only strategy specification.

    Shorting is only available via `options.position = "short"` (CSP, covered call).
    Underlying-stock short selling was removed because the previous implementation
    was broken (entered on every bar regardless of signals).
    """
    name: str = "Untitled Strategy"
    entry: dict[str, Any]
    exit: ExitStrategy = Field(default_factory=ExitStrategy)
    position_sizing: PositionSizing = Field(default_factory=PositionSizing)
    options: OptionsConfig = Field(default_factory=OptionsConfig)
    pyramiding: PyramidingConfig = Field(default_factory=PyramidingConfig)


# ── Backtest Request ──────────────────────────────────────────────────────────


class BacktestRequest(BaseModel):
    strategy: StrategyDefinition
    ticker: str
    start_date: date | None = None
    end_date: date | None = None
    initial_capital: float = 100000.0
    commission: float = 0.0
    slippage_pct: float = 0.0
    timeframe: str = "daily"


class WalkForwardRequest(BaseModel):
    strategy: StrategyDefinition
    ticker: str
    start_date: date | None = None
    end_date: date | None = None
    initial_capital: float = 100000.0
    commission: float = 0.0
    slippage_pct: float = 0.0
    timeframe: str = "daily"
    mode: WalkForwardMode = WalkForwardMode.ROLLING
    in_sample_days: int = 756
    out_of_sample_days: int = 252


# ── Results ───────────────────────────────────────────────────────────────────


class Trade(BaseModel):
    """One closed tranche. A pyramided campaign produces multiple Trade rows
    that share the same `campaign_id`."""
    entry_date: str
    exit_date: str
    direction: str  # "long" (stock/long-option) or "short" (short-option)
    entry_price: float
    exit_price: float
    # Underlying price at entry/exit. For stock trades these equal entry_price /
    # exit_price; for option trades they're the spot, used by the Position chart.
    entry_underlying_price: float = 0.0
    exit_underlying_price: float = 0.0
    shares: float   # Stocks: shares. Options: contracts.
    pnl: float
    pnl_pct: float
    exit_reason: str
    bars_held: int
    campaign_id: int = 0
    is_option: bool = False
    option_type: str | None = None
    option_position: str | None = None
    option_strike: float | None = None
    option_dte_entry: float | None = None
    option_dte_exit: float | None = None
    option_iv_entry: float | None = None


class BacktestResult(BaseModel):
    """Equity-curve dicts carry extra optional keys: `avg_cost` (weighted-average
    underlying entry price while at least one position is open, else None) and
    `n_positions` (open tranches at the close of the bar)."""
    equity_curve: list[dict[str, Any]]
    trades: list[Trade]
    stats: dict[str, Any]
    benchmark_curve: list[dict[str, Any]]
    ticker: str
    start_date: str
    end_date: str
    initial_capital: float


class WalkForwardFold(BaseModel):
    fold_number: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_stats: dict[str, Any]
    oos_stats: dict[str, Any]
    regime: str


class SweepSpec(BaseModel):
    """One axis of a parameter sweep.

    `param` is a dotted path into the strategy dict. Examples:
      * "exit.stop_loss_pct"
      * "exit.take_profit_pct"
      * "pyramiding.pullback_pct"
      * "pyramiding.max_positions"
      * "options.target_dte"
      * "options.target_delta"
      * "position_sizing.value"
    """
    param: str
    values: list[float]


class ParameterSweepRequest(BaseModel):
    """Run the same backtest N times with one strategy parameter varied."""
    base: BacktestRequest
    sweep: SweepSpec


class SweepPoint(BaseModel):
    param_value: float
    stats: dict[str, Any]
    trade_count: int
    final_equity: float
    total_return_pct: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None


class ParameterSweepResult(BaseModel):
    param: str
    points: list[SweepPoint]
    ticker: str
    start_date: str
    end_date: str


class WalkForwardResult(BaseModel):
    folds: list[WalkForwardFold]
    aggregated_oos_curve: list[dict[str, Any]]
    aggregated_oos_stats: dict[str, Any]
    degradation_ratios: dict[str, float | None]
    mode: str
    ticker: str
    start_date: str
    end_date: str
