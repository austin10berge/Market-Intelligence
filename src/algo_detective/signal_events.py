"""Extract every historical (date, ticker) event where a candidate GTPro
gate criteria fires — both prime- and control-labeled tickers, since a
live scanner can't distinguish a true positive from a false positive at
fire time.

Unlike validate.py's validate_criteria() (which scores precision/recall
using only the prime subset as ground truth), this returns the full set
of firings for downstream trade simulation in signal_backtest.py. See
docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

from .analyze import _apply_criteria
from .store import get_all_features, get_options_index


def get_signal_events(
    criteria: dict,
    features: list[dict] | None = None,
    join_options: bool = False,
) -> list[dict]:
    """Return every {date, ticker, is_prime} row where criteria fires.

    Args:
        join_options: If True (or criteria contains 'options_iv_min'),
            merge detective_options data (best_iv) into each row before
            evaluation, mirroring validate_criteria's join behavior.
    """
    if features is None:
        features = get_all_features()

    needs_options = join_options or "options_iv_min" in criteria
    if needs_options:
        options_idx = get_options_index()
        enriched = []
        for f in features:
            opt = options_idx.get((f["date"], f["ticker"]), {})
            enriched.append({**f, "best_iv": opt.get("best_iv")})
        features = enriched

    return [
        {"date": f["date"], "ticker": f["ticker"], "is_prime": f["is_prime"]}
        for f in features
        if _apply_criteria(f, criteria)
    ]
