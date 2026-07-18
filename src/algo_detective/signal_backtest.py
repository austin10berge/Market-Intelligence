"""Simulate real CSP wheel trades on every historical gate hit for a
candidate algo_detective criteria dict, using the existing backtester
engine (src/backtester/) — validates trade-level P&L, not just
precision/recall. See
docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

import logging
from collections import defaultdict

import pandas as pd

from ..backtester.data_provider import get_historical_data
from ..backtester.engine import run_backtest
from ..backtester.models import (
    BacktestRequest,
    ExitStrategy,
    OptionsConfig,
    ProfitLadderTier,
    StrategyDefinition,
    WalkForwardMode,
)
from ..backtester.walk_forward import _generate_folds
from .signal_events import get_signal_events
from .store import get_options_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_GTPRO_LADDER = [
    ProfitLadderTier(max_days_held=2, take_profit_pct=30.0),
    ProfitLadderTier(max_days_held=4, take_profit_pct=50.0),
    ProfitLadderTier(max_days_held=5, take_profit_pct=75.0),
]


def compute_pooled_trade_stats(trades: list) -> dict:
    """Aggregate P&L stats across trades pooled from many independent
    per-ticker backtests — no shared equity curve or capital allocation.

    Accepts backtester Trade objects (attribute access) or plain dicts
    with the same keys (pnl/pnl_pct), so tests can use either.
    """
    def _get(t, key):
        return getattr(t, key) if hasattr(t, key) else t[key]

    total = len(trades)
    if total == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "profit_factor": None, "avg_pnl": 0.0, "avg_pnl_pct": 0.0,
            "total_pnl": 0.0,
        }

    pnls = [_get(t, "pnl") for t in trades]
    pnl_pcts = [_get(t, "pnl_pct") for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / total * 100.0, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "avg_pnl": round(sum(pnls) / total, 2),
        "avg_pnl_pct": round(sum(pnl_pcts) / total, 2),
        "total_pnl": round(sum(pnls), 2),
    }


def _build_ticker_df(ticker: str, options_idx: dict) -> pd.DataFrame | None:
    """Fetch full OHLCV history for a ticker and join real IV where
    available, leaving other bars for the engine's own RV20 proxy."""
    df = get_historical_data(symbol=ticker, start_date=None, end_date=None)
    if df.empty:
        return None
    df["iv_override"] = [
        options_idx.get((d.strftime("%Y-%m-%d"), ticker), {}).get("best_iv")
        for d in df.index
    ]
    return df


def _build_strategy(
    dates: list[str], target_delta: float, target_dte: int, ladder: list[ProfitLadderTier]
) -> StrategyDefinition:
    return StrategyDefinition(
        entry={"type": "signal_dates", "dates": dates},
        direction="short",
        options=OptionsConfig(
            enabled=True, type="put", target_delta=target_delta, target_dte=target_dte
        ),
        exit=ExitStrategy(profit_ladder=ladder),
    )


def run_signal_backtest(
    criteria: dict,
    target_delta: float = 0.25,
    target_dte: int = 5,
    ladder: list[ProfitLadderTier] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """Simulate a CSP wheel trade on every historical (date, ticker) gate
    hit. Returns pooled trade-level stats — see compute_pooled_trade_stats.
    """
    ladder = ladder if ladder is not None else DEFAULT_GTPRO_LADDER
    events = events if events is not None else get_signal_events(criteria)

    by_ticker: dict[str, list[str]] = defaultdict(list)
    for e in events:
        by_ticker[e["ticker"]].append(e["date"])

    options_idx = get_options_index()
    all_trades = []
    tickers_skipped = []
    for ticker, dates in by_ticker.items():
        df = _build_ticker_df(ticker, options_idx)
        if df is None:
            logger.warning("No OHLCV data for %s, skipping %d signal event(s)", ticker, len(dates))
            tickers_skipped.append(ticker)
            continue
        strategy = _build_strategy(sorted(set(dates)), target_delta, target_dte, ladder)
        request = BacktestRequest(strategy=strategy, ticker=ticker)
        result = run_backtest(request, df)
        all_trades.extend(result.trades)

    return {
        "criteria": criteria,
        "stats": compute_pooled_trade_stats(all_trades),
        "trades": all_trades,
        "tickers_skipped": tickers_skipped,
    }


def _compute_pooled_degradation(is_stats: dict, oos_stats: dict) -> dict:
    """OOS/IS ratios for the pooled trade metrics — values near 1.0 mean
    the gate's P&L held up out of sample; values << 1.0 suggest overfit."""
    metrics = ["win_rate_pct", "profit_factor", "avg_pnl", "avg_pnl_pct"]
    ratios = {}
    for m in metrics:
        is_val, oos_val = is_stats.get(m), oos_stats.get(m)
        if is_val in (None, 0) or oos_val is None:
            ratios[m] = None
        else:
            ratios[m] = round(oos_val / is_val, 3)
    return ratios


def run_signal_walk_forward(
    criteria: dict,
    mode: WalkForwardMode = WalkForwardMode.ROLLING,
    in_sample_days: int = 756,
    out_of_sample_days: int = 252,
    target_delta: float = 0.25,
    target_dte: int = 5,
    ladder: list[ProfitLadderTier] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """Split the signal event set into IS/OOS folds by calendar date
    (reusing the backtester's existing fold-generation logic) and report
    IS-vs-OOS degradation for the pooled trade stats in each fold."""
    ladder = ladder if ladder is not None else DEFAULT_GTPRO_LADDER
    events = events if events is not None else get_signal_events(criteria)

    all_dates = sorted({e["date"] for e in events})
    if not all_dates:
        return {"criteria": criteria, "folds": []}

    folds_idx = _generate_folds(
        total_bars=len(all_dates),
        is_bars=in_sample_days,
        oos_bars=out_of_sample_days,
        mode=mode,
    )

    folds = []
    for fold_num, (is_start, is_end, oos_start, oos_end) in enumerate(folds_idx, 1):
        is_dates = set(all_dates[is_start:is_end])
        oos_dates = set(all_dates[oos_start:oos_end])

        is_events = [e for e in events if e["date"] in is_dates]
        oos_events = [e for e in events if e["date"] in oos_dates]

        is_result = run_signal_backtest(
            criteria, target_delta, target_dte, ladder, events=is_events
        )
        oos_result = run_signal_backtest(
            criteria, target_delta, target_dte, ladder, events=oos_events
        )

        folds.append({
            "fold_number": fold_num,
            "is_start": all_dates[is_start], "is_end": all_dates[is_end - 1],
            "oos_start": all_dates[oos_start], "oos_end": all_dates[oos_end - 1],
            "is_stats": is_result["stats"],
            "oos_stats": oos_result["stats"],
            "degradation": _compute_pooled_degradation(is_result["stats"], oos_result["stats"]),
        })

    return {"criteria": criteria, "folds": folds}
