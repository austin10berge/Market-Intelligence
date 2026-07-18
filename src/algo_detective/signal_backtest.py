"""Simulate real CSP wheel trades on every historical gate hit for a
candidate algo_detective criteria dict, using the existing backtester
engine (src/backtester/) — validates trade-level P&L, not just
precision/recall. See
docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


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
