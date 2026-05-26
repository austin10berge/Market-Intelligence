"""Parameter sweep — re-run a backtest N times with one strategy field varied.

Used to study sensitivity: "how does total return change if I move my stop loss
between 5% and 25%?" Returns one summary point per value (full equity curves
would be too heavy for the UI).
"""

from __future__ import annotations

import copy
import logging
from typing import Any

import pandas as pd

from .engine import run_backtest
from .models import (
    BacktestRequest,
    ParameterSweepRequest,
    ParameterSweepResult,
    SweepPoint,
)

logger = logging.getLogger(__name__)


SUPPORTED_PARAMS = {
    "exit.stop_loss_pct",
    "exit.take_profit_pct",
    "exit.trailing_stop_pct",
    "exit.max_hold_days",
    "position_sizing.value",
    "pyramiding.max_positions",
    "pyramiding.pullback_pct",
    "options.target_dte",
    "options.target_delta",
}


def run_parameter_sweep(request: ParameterSweepRequest, df: pd.DataFrame) -> ParameterSweepResult:
    """Run the same backtest N times, varying one strategy parameter each pass.

    The same DataFrame is reused across runs so we don't refetch yfinance per
    sweep value.
    """
    base = request.base
    spec = request.sweep

    if spec.param not in SUPPORTED_PARAMS:
        raise ValueError(f"Unsupported sweep parameter: {spec.param!r}. "
                         f"Allowed: {sorted(SUPPORTED_PARAMS)}")

    points: list[SweepPoint] = []
    for value in spec.values:
        scoped_request = _request_with_override(base, spec.param, value)
        # Each run mutates df.attrs (indicator cache); strip it so the cache
        # doesn't carry over and mask param effects on indicator-driven exits.
        df.attrs.pop("_bt_indicator_cache", None)
        try:
            result = run_backtest(scoped_request, df.copy())
        except Exception as exc:
            logger.exception("Sweep run failed for %s=%s", spec.param, value)
            points.append(SweepPoint(
                param_value=float(value),
                stats={"error": str(exc)},
                trade_count=0,
                final_equity=0.0,
                total_return_pct=None,
                sharpe_ratio=None,
                max_drawdown_pct=None,
            ))
            continue

        stats = result.stats
        points.append(SweepPoint(
            param_value=float(value),
            stats=stats,
            trade_count=len(result.trades),
            final_equity=float(stats.get("final_equity", 0.0)),
            total_return_pct=_safe_float(stats.get("total_return_pct")),
            sharpe_ratio=_safe_float(stats.get("sharpe_ratio")),
            max_drawdown_pct=_safe_float(stats.get("max_drawdown_pct")),
        ))

    start = df.index[0].strftime("%Y-%m-%d") if not df.empty else ""
    end = df.index[-1].strftime("%Y-%m-%d") if not df.empty else ""

    return ParameterSweepResult(
        param=spec.param,
        points=points,
        ticker=base.ticker,
        start_date=start,
        end_date=end,
    )


def _request_with_override(base: BacktestRequest, dotted_path: str, value: Any) -> BacktestRequest:
    """Clone a request and set `dotted_path` on its strategy to `value`."""
    raw = base.model_dump(mode="json")
    cursor: Any = raw["strategy"]
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        if part not in cursor or cursor[part] is None:
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value
    return BacktestRequest.model_validate(raw)


def _safe_float(v: Any) -> float | None:
    if v is None or v == "N/A":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
