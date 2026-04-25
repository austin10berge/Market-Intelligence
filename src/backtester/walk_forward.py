"""Walk-forward analysis — rolling and anchored validation.

Splits historical data into in-sample (IS) and out-of-sample (OOS) folds,
runs the strategy on each, and aggregates OOS results to produce a
realistic performance estimate that reveals regime sensitivity.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from .engine import run_backtest
from .models import (
    BacktestRequest,
    WalkForwardFold,
    WalkForwardMode,
    WalkForwardRequest,
    WalkForwardResult,
)
from .stats import compute_stats

logger = logging.getLogger(__name__)


def run_walk_forward(
    request: WalkForwardRequest,
    df: pd.DataFrame,
) -> WalkForwardResult:
    """Execute walk-forward analysis across multiple folds.

    Args:
        request: Walk-forward configuration (strategy, IS/OOS windows, mode).
        df: Full OHLCV DataFrame with DatetimeIndex, sorted ascending.

    Returns:
        WalkForwardResult with per-fold stats, aggregated OOS curve,
        and degradation ratios.
    """
    folds_spec = _generate_folds(
        total_bars=len(df),
        is_bars=request.in_sample_days,
        oos_bars=request.out_of_sample_days,
        mode=request.mode,
    )

    if not folds_spec:
        logger.warning("Not enough data for walk-forward analysis")
        return _empty_result(request, df)

    folds: list[WalkForwardFold] = []
    all_oos_curves: list[list[dict]] = []
    all_oos_trades = []

    for fold_num, (is_start, is_end, oos_start, oos_end) in enumerate(folds_spec, 1):
        is_df = df.iloc[is_start:is_end]
        oos_df = df.iloc[oos_start:oos_end]

        if is_df.empty or oos_df.empty:
            continue

        # Run IS backtest
        is_request = BacktestRequest(
            strategy=request.strategy,
            ticker=request.ticker,
            initial_capital=request.initial_capital,
            commission=request.commission,
            slippage_pct=request.slippage_pct,
            timeframe=request.timeframe,
        )
        is_result = run_backtest(is_request, is_df)

        # Run OOS backtest
        oos_request = BacktestRequest(
            strategy=request.strategy,
            ticker=request.ticker,
            initial_capital=request.initial_capital,
            commission=request.commission,
            slippage_pct=request.slippage_pct,
            timeframe=request.timeframe,
        )
        oos_result = run_backtest(oos_request, oos_df)

        # Determine market regime from benchmark
        regime = _classify_regime(oos_df)

        fold = WalkForwardFold(
            fold_number=fold_num,
            is_start=is_df.index[0].strftime("%Y-%m-%d"),
            is_end=is_df.index[-1].strftime("%Y-%m-%d"),
            oos_start=oos_df.index[0].strftime("%Y-%m-%d"),
            oos_end=oos_df.index[-1].strftime("%Y-%m-%d"),
            is_stats=is_result.stats,
            oos_stats=oos_result.stats,
            regime=regime,
        )
        folds.append(fold)
        all_oos_curves.append(oos_result.equity_curve)
        all_oos_trades.extend(oos_result.trades)

    # Build aggregated OOS equity curve (chain folds together)
    aggregated_oos_curve = _chain_equity_curves(
        all_oos_curves, request.initial_capital
    )

    # Compute aggregated OOS stats
    aggregated_oos_stats = compute_stats(
        trades=all_oos_trades,
        equity_curve=aggregated_oos_curve,
        initial_capital=request.initial_capital,
    )

    # Compute degradation ratios
    degradation_ratios = _compute_degradation(folds)

    start_date_str = df.index[0].strftime("%Y-%m-%d") if not df.empty else ""
    end_date_str = df.index[-1].strftime("%Y-%m-%d") if not df.empty else ""

    return WalkForwardResult(
        folds=folds,
        aggregated_oos_curve=aggregated_oos_curve,
        aggregated_oos_stats=aggregated_oos_stats,
        degradation_ratios=degradation_ratios,
        mode=request.mode.value,
        ticker=request.ticker,
        start_date=start_date_str,
        end_date=end_date_str,
    )


def _generate_folds(
    total_bars: int,
    is_bars: int,
    oos_bars: int,
    mode: WalkForwardMode,
) -> list[tuple[int, int, int, int]]:
    """Generate (is_start, is_end, oos_start, oos_end) index tuples.

    Rolling: fixed IS window slides forward by OOS window size.
    Anchored: IS always starts at 0, grows by OOS window size each fold.
    """
    folds = []
    fold_size = is_bars + oos_bars

    if mode == WalkForwardMode.ROLLING:
        start = 0
        while start + fold_size <= total_bars:
            is_start = start
            is_end = start + is_bars
            oos_start = is_end
            oos_end = min(is_end + oos_bars, total_bars)
            folds.append((is_start, is_end, oos_start, oos_end))
            start += oos_bars  # Slide by OOS window size
    else:
        # Anchored: IS always starts from 0
        is_end = is_bars
        while is_end + oos_bars <= total_bars:
            oos_start = is_end
            oos_end = min(is_end + oos_bars, total_bars)
            folds.append((0, is_end, oos_start, oos_end))
            is_end += oos_bars  # IS grows, OOS slides

    return folds


def _chain_equity_curves(
    curves: list[list[dict]],
    initial_capital: float,
) -> list[dict]:
    """Chain multiple OOS equity curves into one continuous curve.

    Each fold's OOS curve starts at initial_capital. We chain them so that
    the end equity of fold N becomes the start equity of fold N+1.
    """
    if not curves:
        return []

    chained: list[dict] = []
    running_capital = initial_capital

    for curve in curves:
        if not curve:
            continue

        fold_start = curve[0]["equity"]
        if fold_start <= 0:
            fold_start = initial_capital

        scale = running_capital / fold_start

        for pt in curve:
            chained.append({
                "date": pt["date"],
                "equity": round(pt["equity"] * scale, 2),
            })

        if chained:
            running_capital = chained[-1]["equity"]

    return chained


def _classify_regime(df: pd.DataFrame) -> str:
    """Classify the market regime for an OOS period based on price change."""
    if df.empty or len(df) < 2:
        return "unknown"

    start_price = float(df["Close"].iloc[0])
    end_price = float(df["Close"].iloc[-1])

    if start_price <= 0:
        return "unknown"

    change_pct = ((end_price - start_price) / start_price) * 100.0

    if change_pct > 10:
        return "bull"
    elif change_pct < -10:
        return "bear"
    else:
        return "sideways"


def _compute_degradation(folds: list[WalkForwardFold]) -> dict[str, float | None]:
    """Compute OOS/IS degradation ratios for key metrics.

    Values close to 1.0 = strategy is robust (OOS matches IS).
    Values << 1.0 = strategy degrades out of sample (potential overfit).
    """
    metrics_to_compare = [
        "total_return_pct", "sharpe_ratio", "win_rate_pct",
        "profit_factor", "cagr",
    ]

    ratios: dict[str, float | None] = {}

    for metric in metrics_to_compare:
        is_values = []
        oos_values = []

        for fold in folds:
            is_val = fold.is_stats.get(metric)
            oos_val = fold.oos_stats.get(metric)

            if is_val not in (None, "N/A") and oos_val not in (None, "N/A"):
                is_values.append(float(is_val))
                oos_values.append(float(oos_val))

        if is_values and oos_values:
            avg_is = sum(is_values) / len(is_values)
            avg_oos = sum(oos_values) / len(oos_values)

            if abs(avg_is) > 1e-9:
                ratios[metric] = round(avg_oos / avg_is, 3)
            else:
                ratios[metric] = None
        else:
            ratios[metric] = None

    return ratios


def _empty_result(
    request: WalkForwardRequest,
    df: pd.DataFrame,
) -> WalkForwardResult:
    """Return an empty result when walk-forward can't run."""
    start = df.index[0].strftime("%Y-%m-%d") if not df.empty else ""
    end = df.index[-1].strftime("%Y-%m-%d") if not df.empty else ""
    return WalkForwardResult(
        folds=[],
        aggregated_oos_curve=[],
        aggregated_oos_stats={},
        degradation_ratios={},
        mode=request.mode.value,
        ticker=request.ticker,
        start_date=start,
        end_date=end,
    )
