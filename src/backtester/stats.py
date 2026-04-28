"""Performance statistics calculator for backtest results.

Computes a comprehensive set of risk-adjusted metrics from the equity curve
and trade log, including benchmark comparison.
"""

from __future__ import annotations

import math
from typing import Any

from .models import Trade


def compute_stats(
    trades: list[Trade],
    equity_curve: list[dict],
    initial_capital: float,
    benchmark_curve: list[dict] | None = None,
    risk_free_rate: float = 0.0,
    trading_days_per_year: int = 252,
) -> dict[str, Any]:
    """Compute full performance statistics from a backtest result.

    Returns a dict of all metrics — values are floats or "N/A" strings.
    """
    stats: dict[str, Any] = {}

    # ── Basic metrics ─────────────────────────────────────────────────────────

    equities = [pt["equity"] for pt in equity_curve]
    if not equities:
        return _empty_stats()

    final_equity = equities[-1]
    total_return_pct = ((final_equity - initial_capital) / initial_capital) * 100.0

    stats["total_return_pct"] = round(total_return_pct, 2)
    stats["final_equity"] = round(final_equity, 2)
    stats["initial_capital"] = round(initial_capital, 2)

    # ── CAGR ──────────────────────────────────────────────────────────────────

    n_bars = len(equities)
    years = n_bars / trading_days_per_year
    if years > 0 and final_equity > 0 and initial_capital > 0:
        cagr = (math.pow(final_equity / initial_capital, 1.0 / years) - 1) * 100.0
        stats["cagr"] = round(cagr, 2)
    else:
        stats["cagr"] = "N/A"

    # ── Daily returns for risk metrics ────────────────────────────────────────

    daily_returns = []
    for i in range(1, len(equities)):
        if equities[i - 1] > 0:
            daily_returns.append((equities[i] - equities[i - 1]) / equities[i - 1])

    # ── Sharpe Ratio ──────────────────────────────────────────────────────────

    if len(daily_returns) > 1:
        avg_return = sum(daily_returns) / len(daily_returns)
        std_return = _std(daily_returns)
        if std_return > 0:
            daily_rf = risk_free_rate / trading_days_per_year
            sharpe = ((avg_return - daily_rf) / std_return) * math.sqrt(trading_days_per_year)
            stats["sharpe_ratio"] = round(sharpe, 3)
        else:
            stats["sharpe_ratio"] = "N/A"
    else:
        stats["sharpe_ratio"] = "N/A"

    # ── Sortino Ratio ─────────────────────────────────────────────────────────

    if len(daily_returns) > 1:
        downside_returns = [r for r in daily_returns if r < 0]
        if downside_returns:
            downside_std = _std(downside_returns)
            if downside_std > 0:
                avg_return = sum(daily_returns) / len(daily_returns)
                daily_rf = risk_free_rate / trading_days_per_year
                sortino = ((avg_return - daily_rf) / downside_std) * math.sqrt(trading_days_per_year)
                stats["sortino_ratio"] = round(sortino, 3)
            else:
                stats["sortino_ratio"] = "N/A"
        else:
            stats["sortino_ratio"] = "N/A"  # No losing days
    else:
        stats["sortino_ratio"] = "N/A"

    # ── Max Drawdown ──────────────────────────────────────────────────────────

    max_dd, max_dd_duration = _max_drawdown(equities)
    stats["max_drawdown_pct"] = round(max_dd * 100, 2)
    stats["max_drawdown_duration_bars"] = max_dd_duration

    # ── Calmar Ratio ──────────────────────────────────────────────────────────

    if stats["cagr"] != "N/A" and max_dd > 0:
        stats["calmar_ratio"] = round(stats["cagr"] / (max_dd * 100), 3)
    else:
        stats["calmar_ratio"] = "N/A"

    # ── Trade statistics ──────────────────────────────────────────────────────

    stats["total_trades"] = len(trades)

    if trades:
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl < 0]
        breakeven = [t for t in trades if t.pnl == 0]

        stats["winning_trades"] = len(winners)
        stats["losing_trades"] = len(losers)
        stats["breakeven_trades"] = len(breakeven)
        stats["win_rate_pct"] = round(len(winners) / len(trades) * 100, 1)

        gross_profit = sum(t.pnl for t in winners) if winners else 0
        gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0

        stats["gross_profit"] = round(gross_profit, 2)
        stats["gross_loss"] = round(gross_loss, 2)
        stats["net_profit"] = round(gross_profit - gross_loss, 2)

        # Profit factor
        if gross_loss > 0:
            stats["profit_factor"] = round(gross_profit / gross_loss, 2)
        else:
            stats["profit_factor"] = "N/A"

        # Average win / loss
        stats["avg_win"] = round(gross_profit / len(winners), 2) if winners else 0
        stats["avg_loss"] = round(gross_loss / len(losers), 2) if losers else 0

        # Expectancy (avg P&L per trade)
        stats["expectancy"] = round(sum(t.pnl for t in trades) / len(trades), 2)

        # Average bars held
        stats["avg_bars_held"] = round(sum(t.bars_held for t in trades) / len(trades), 1)

        # Max consecutive wins/losses
        stats["max_consecutive_wins"] = _max_consecutive(trades, winning=True)
        stats["max_consecutive_losses"] = _max_consecutive(trades, winning=False)

        # Largest single trade
        stats["largest_win"] = round(max(t.pnl for t in trades), 2)
        stats["largest_loss"] = round(min(t.pnl for t in trades), 2)

        # Exit reason breakdown
        exit_reasons: dict[str, int] = {}
        for t in trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
        stats["exit_reasons"] = exit_reasons
    else:
        stats.update(_empty_trade_stats())

    # ── Exposure ──────────────────────────────────────────────────────────────

    if trades and n_bars > 0:
        total_bars_in_market = sum(t.bars_held for t in trades)
        stats["exposure_pct"] = min(round(total_bars_in_market / n_bars * 100, 1), 100.0)
    else:
        stats["exposure_pct"] = 0.0

    # ── Benchmark comparison ──────────────────────────────────────────────────

    if benchmark_curve:
        bench_equities = [pt["equity"] for pt in benchmark_curve]
        if bench_equities and bench_equities[0] > 0:
            bench_return = ((bench_equities[-1] - bench_equities[0]) / bench_equities[0]) * 100
            stats["benchmark_return_pct"] = round(bench_return, 2)
            stats["alpha"] = round(total_return_pct - bench_return, 2)
        else:
            stats["benchmark_return_pct"] = "N/A"
            stats["alpha"] = "N/A"
    else:
        stats["benchmark_return_pct"] = "N/A"
        stats["alpha"] = "N/A"

    return stats


def _max_drawdown(equities: list[float]) -> tuple[float, int]:
    """Compute maximum drawdown and its duration in bars.

    Returns (max_drawdown_fraction, max_duration_bars).
    """
    if not equities:
        return 0.0, 0

    peak = equities[0]
    max_dd = 0.0
    max_duration = 0
    current_dd_start = 0

    for i, eq in enumerate(equities):
        if eq >= peak:
            peak = eq
            current_dd_start = i
        else:
            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_duration = i - current_dd_start

    return max_dd, max_duration


def _std(values: list[float]) -> float:
    """Sample standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _max_consecutive(trades: list[Trade], winning: bool) -> int:
    """Count max consecutive winning or losing trades."""
    max_streak = 0
    current = 0
    for t in trades:
        is_win = t.pnl > 0
        if is_win == winning:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _empty_trade_stats() -> dict:
    """Default trade stats when no trades occurred."""
    return {
        "winning_trades": 0,
        "losing_trades": 0,
        "breakeven_trades": 0,
        "win_rate_pct": 0,
        "gross_profit": 0,
        "gross_loss": 0,
        "net_profit": 0,
        "profit_factor": "N/A",
        "avg_win": 0,
        "avg_loss": 0,
        "expectancy": 0,
        "avg_bars_held": 0,
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
        "largest_win": 0,
        "largest_loss": 0,
        "exit_reasons": {},
    }


def _empty_stats() -> dict:
    """Default stats when there's no data at all."""
    return {
        "total_return_pct": 0,
        "final_equity": 0,
        "initial_capital": 0,
        "cagr": "N/A",
        "sharpe_ratio": "N/A",
        "sortino_ratio": "N/A",
        "max_drawdown_pct": 0,
        "max_drawdown_duration_bars": 0,
        "calmar_ratio": "N/A",
        "total_trades": 0,
        "exposure_pct": 0,
        "benchmark_return_pct": "N/A",
        "alpha": "N/A",
        **_empty_trade_stats(),
    }
