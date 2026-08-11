"""Stats and TWR computation for the wheel equity curve."""
from __future__ import annotations

import math


def compute_spy_curve(curve: list[dict]) -> list[dict]:
    if not curve or curve[0]["spy_close"] is None:
        return []
    first = curve[0]["spy_close"]
    if first <= 0:
        return []
    return [
        {"date": r["date"], "pct": round(((r["spy_close"] / first) - 1) * 100, 4) if r["spy_close"] else 0.0}
        for r in curve
    ]


def compute_twr_curve(curve: list[dict]) -> list[dict]:
    if not curve:
        return []

    result = [{"date": curve[0]["date"], "pct": 0.0}]
    cumulative = 1.0
    prev_deposits = curve[0]["deposits"]

    for i in range(1, len(curve)):
        prev_equity = curve[i - 1]["equity"]
        curr_equity = curve[i]["equity"]
        curr_deposits = curve[i]["deposits"]
        deposit_delta = curr_deposits - prev_deposits

        if prev_equity > 0:
            adjusted_prev = prev_equity + deposit_delta
            period_return = (curr_equity / adjusted_prev) if adjusted_prev > 0 else 1.0
        else:
            period_return = 1.0

        cumulative *= period_return
        prev_deposits = curr_deposits
        result.append({"date": curve[i]["date"], "pct": round((cumulative - 1) * 100, 4)})

    return result


def compute_curve_stats(curve: list[dict]) -> dict:
    empty = {
        "net_pnl": 0, "net_pnl_pct": 0, "max_drawdown_pct": 0,
        "sharpe_ratio": None, "sortino_ratio": None,
        "annualized_yield_pct": None, "avg_weekly_roc_pct": None,
    }
    if len(curve) < 2:
        return empty

    twr = compute_twr_curve(curve)
    total_return_pct = twr[-1]["pct"]

    final_equity = curve[-1]["equity"]
    final_deposits = curve[-1]["deposits"]
    net_pnl = final_equity - final_deposits

    # Daily returns from TWR curve
    daily_returns = []
    for i in range(1, len(twr)):
        prev_factor = 1 + twr[i - 1]["pct"] / 100
        curr_factor = 1 + twr[i]["pct"] / 100
        if prev_factor > 0:
            daily_returns.append(curr_factor / prev_factor - 1)

    # Sharpe
    sharpe = None
    if len(daily_returns) > 1:
        avg = sum(daily_returns) / len(daily_returns)
        std = _std(daily_returns)
        if std > 0:
            sharpe = round((avg / std) * math.sqrt(252), 3)

    # Sortino
    sortino = None
    if len(daily_returns) > 1:
        avg = sum(daily_returns) / len(daily_returns)
        down = [r for r in daily_returns if r < 0]
        if down:
            down_std = _std(down)
            if down_std > 0:
                sortino = round((avg / down_std) * math.sqrt(252), 3)

    # Max drawdown (on TWR cumulative factors)
    factors = [1 + t["pct"] / 100 for t in twr]
    max_dd = 0.0
    peak = factors[0]
    for f in factors:
        if f > peak:
            peak = f
        dd = (peak - f) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Annualized yield
    n_days = len(curve)
    annualized = None
    total_factor = 1 + total_return_pct / 100
    if n_days > 1 and total_factor > 0:
        annualized = round((math.pow(total_factor, 252 / n_days) - 1) * 100, 2)

    # Avg weekly ROC
    weekly_roc = None
    if len(twr) >= 5:
        weekly_returns = []
        i = 0
        while i + 5 <= len(twr):
            prev_f = 1 + twr[i]["pct"] / 100
            curr_f = 1 + twr[i + 5]["pct"] / 100 if i + 5 < len(twr) else 1 + twr[-1]["pct"] / 100
            if prev_f > 0:
                weekly_returns.append((curr_f / prev_f - 1) * 100)
            i += 5
        if weekly_returns:
            weekly_roc = round(sum(weekly_returns) / len(weekly_returns), 3)

    return {
        "net_pnl": round(net_pnl, 2),
        "net_pnl_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(-max_dd * 100, 2),
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "annualized_yield_pct": annualized,
        "avg_weekly_roc_pct": weekly_roc,
    }


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)
