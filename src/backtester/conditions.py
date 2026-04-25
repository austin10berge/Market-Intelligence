"""Compound condition tree evaluator.

Evaluates entry/exit conditions bar-by-bar with no lookahead. Supports
AND/OR nesting, threshold comparisons, crossovers, pullbacks, and
consecutive candle patterns.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .indicators import compute_indicator

logger = logging.getLogger(__name__)


def evaluate_condition_tree(
    tree: dict[str, Any],
    df: pd.DataFrame,
    bar_idx: int,
) -> bool:
    """Evaluate a compound condition tree at a specific bar index.

    Args:
        tree: Condition definition dict — either a leaf condition or a group
              with an "operator" key and nested "conditions" list.
        df: Full OHLCV DataFrame (indicators are computed lazily).
        bar_idx: Current bar index (integer position). Only data at or before
                 this index is used — no lookahead.

    Returns:
        True if the condition tree evaluates to True at bar_idx.
    """
    if "operator" in tree:
        return _evaluate_group(tree, df, bar_idx)
    return _evaluate_leaf(tree, df, bar_idx)


def _evaluate_group(
    group: dict[str, Any],
    df: pd.DataFrame,
    bar_idx: int,
) -> bool:
    """Evaluate an AND/OR group of conditions."""
    operator = group.get("operator", "AND").upper()
    children = group.get("conditions", [])

    if not children:
        return False

    if operator == "AND":
        return all(evaluate_condition_tree(c, df, bar_idx) for c in children)
    elif operator == "OR":
        return any(evaluate_condition_tree(c, df, bar_idx) for c in children)
    else:
        logger.warning("Unknown operator '%s', treating as AND", operator)
        return all(evaluate_condition_tree(c, df, bar_idx) for c in children)


def _evaluate_leaf(
    cond: dict[str, Any],
    df: pd.DataFrame,
    bar_idx: int,
) -> bool:
    """Evaluate a single leaf condition."""
    cond_type = cond.get("type", "threshold")

    try:
        if cond_type == "threshold":
            return _eval_threshold(cond, df, bar_idx)
        elif cond_type == "reference":
            return _eval_reference(cond, df, bar_idx)
        elif cond_type == "crossover":
            return _eval_crossover(cond, df, bar_idx)
        elif cond_type == "pullback":
            return _eval_pullback(cond, df, bar_idx)
        elif cond_type == "consecutive":
            return _eval_consecutive(cond, df, bar_idx)
        else:
            logger.warning("Unknown condition type '%s'", cond_type)
            return False
    except Exception as exc:
        logger.debug("Condition evaluation error at bar %d: %s", bar_idx, exc)
        return False


def _get_indicator_value(
    indicator_def: dict[str, Any],
    df: pd.DataFrame,
    bar_idx: int,
) -> float | None:
    """Compute an indicator and return its value at bar_idx."""
    name = indicator_def.get("name", "CLOSE")
    params = indicator_def.get("params", {})
    series = compute_indicator(name, df, params)
    val = series.iloc[bar_idx]
    if pd.isna(val):
        return None
    return float(val)


def _compare(val_a: float, comparator: str, val_b: float) -> bool:
    """Apply a comparison operator."""
    if comparator == "lt":
        return val_a < val_b
    elif comparator == "lte":
        return val_a <= val_b
    elif comparator == "gt":
        return val_a > val_b
    elif comparator == "gte":
        return val_a >= val_b
    elif comparator == "eq":
        return abs(val_a - val_b) < 1e-9
    else:
        return False


# ── Leaf evaluators ───────────────────────────────────────────────────────────


def _eval_threshold(cond: dict, df: pd.DataFrame, bar_idx: int) -> bool:
    """Indicator vs. static value (e.g. RSI(14) < 30)."""
    indicator_def = cond.get("indicator", {"name": "CLOSE"})
    val = _get_indicator_value(indicator_def, df, bar_idx)
    if val is None:
        return False
    threshold = float(cond.get("value", 0))
    return _compare(val, cond.get("comparator", "lt"), threshold)


def _eval_reference(cond: dict, df: pd.DataFrame, bar_idx: int) -> bool:
    """Indicator vs. indicator (e.g. Price > SMA(200))."""
    ind_def = cond.get("indicator", {"name": "CLOSE"})
    ref_def = cond.get("ref_indicator", {"name": "SMA", "params": {"period": 200}})

    val = _get_indicator_value(ind_def, df, bar_idx)
    ref_val = _get_indicator_value(ref_def, df, bar_idx)

    if val is None or ref_val is None:
        return False
    return _compare(val, cond.get("comparator", "gt"), ref_val)


def _eval_crossover(cond: dict, df: pd.DataFrame, bar_idx: int) -> bool:
    """Indicator crosses above/below another indicator.

    A crossover requires:
    - Current bar: indicator is above (or below) reference
    - Previous bar: indicator was below (or above) reference
    """
    if bar_idx < 1:
        return False

    ind_def = cond.get("indicator", {"name": "CLOSE"})
    ref_def = cond.get("ref_indicator", {"name": "SMA", "params": {"period": 20}})
    direction = cond.get("direction", "above")

    curr_val = _get_indicator_value(ind_def, df, bar_idx)
    prev_val = _get_indicator_value(ind_def, df, bar_idx - 1)
    curr_ref = _get_indicator_value(ref_def, df, bar_idx)
    prev_ref = _get_indicator_value(ref_def, df, bar_idx - 1)

    if any(v is None for v in [curr_val, prev_val, curr_ref, prev_ref]):
        return False

    if direction == "above":
        return prev_val <= prev_ref and curr_val > curr_ref
    elif direction == "below":
        return prev_val >= prev_ref and curr_val < curr_ref
    return False


def _eval_pullback(cond: dict, df: pd.DataFrame, bar_idx: int) -> bool:
    """Price has dropped X% from N-day high."""
    lookback = cond.get("lookback", 20)
    pct = cond.get("pct", 5.0)

    start_idx = max(0, bar_idx - lookback + 1)
    if start_idx >= bar_idx:
        return False

    high_window = df["High"].iloc[start_idx: bar_idx + 1]
    if high_window.empty:
        return False

    recent_high = high_window.max()
    current_close = df["Close"].iloc[bar_idx]

    if pd.isna(recent_high) or pd.isna(current_close) or recent_high <= 0:
        return False

    drop_pct = ((recent_high - current_close) / recent_high) * 100.0
    return drop_pct >= pct


def _eval_consecutive(cond: dict, df: pd.DataFrame, bar_idx: int) -> bool:
    """N consecutive up or down candles (close > open = up, close < open = down)."""
    direction = cond.get("direction", "down")
    count = cond.get("count", 3)

    if bar_idx < count - 1:
        return False

    for i in range(count):
        idx = bar_idx - i
        close = df["Close"].iloc[idx]
        open_price = df["Open"].iloc[idx]

        if pd.isna(close) or pd.isna(open_price):
            return False

        if direction == "up" and close <= open_price:
            return False
        if direction == "down" and close >= open_price:
            return False

    return True
