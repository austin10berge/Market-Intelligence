"""Automated gate search for algo detective — greedy stepwise (Approach A)
and decision tree extraction (Approach B).
See docs/superpowers/specs/2026-07-20-algo-detective-gate-search-design.md.
"""

from __future__ import annotations

import logging
from typing import TypedDict

import numpy as np

from .analyze import _BOOLEAN_FEATURES, _NUMERIC_FEATURES

logger = logging.getLogger(__name__)

# adr20_pct is in the store schema but not in analyze._NUMERIC_FEATURES
_SEARCH_NUMERIC_FEATURES: list[str] = list(_NUMERIC_FEATURES) + ["adr20_pct"]

# Options-derived gate keys and the source field in the enriched row used to
# compute prime-distribution percentile thresholds for that gate.
_OPTIONS_GATE_DEFS: list[tuple[str, str]] = [
    ("options_iv_min", "best_iv"),
    ("pcr_vol_max", "pcr_vol"),
    ("iv_rv_min", "iv_rv"),
]

_PERCENTILE_STEPS: list[int] = list(range(5, 100, 5))  # 5, 10, ..., 95


class StepTrace(TypedDict):
    gate: str
    value: float | int
    precision: float
    recall: float


class GreedySearchResult(TypedDict):
    criteria: dict
    precision: float
    recall: float
    steps: list[StepTrace]


class TreeCandidate(TypedDict):
    criteria: dict
    precision: float
    recall: float


class GateSearchResult(TypedDict):
    generated: str
    recall_floor: float
    v42_baseline: dict
    approach_a: GreedySearchResult
    approach_b: list[TreeCandidate]


def _build_candidate_gates(prime_rows: list[dict]) -> list[tuple[str, float | int]]:
    """Return (criteria_key, value) pairs swept from the prime_rows distribution.

    prime_rows must be the is_prime==1 rows that pass the current criteria —
    thresholds are calibrated to this filtered prime population so each step's
    candidates stay relevant to what's left.
    """
    if len(prime_rows) < 5:
        return []

    candidates: list[tuple[str, float | int]] = []

    # Numeric features: both _min and _max at each percentile point
    for feat in _SEARCH_NUMERIC_FEATURES:
        vals = [r[feat] for r in prime_rows if r.get(feat) is not None]
        if len(vals) < 5:
            continue
        seen: set[float] = set()
        for pct in _PERCENTILE_STEPS:
            t = round(float(np.percentile(vals, pct)), 6)
            if t in seen:
                continue
            seen.add(t)
            candidates.append((f"{feat}_min", t))
            candidates.append((f"{feat}_max", t))

    # Boolean features: only the True (= 1) direction
    for feat in _BOOLEAN_FEATURES:
        prime_rate = sum(1 for r in prime_rows if r.get(feat) == 1) / len(prime_rows)
        if prime_rate >= 0.10:
            candidates.append((feat, 1))

    # Options-derived gates: same percentile sweep over their source fields
    for crit_key, field in _OPTIONS_GATE_DEFS:
        vals = [r[field] for r in prime_rows if r.get(field) is not None]
        if len(vals) < 5:
            continue
        seen = set()
        for pct in _PERCENTILE_STEPS:
            t = round(float(np.percentile(vals, pct)), 6)
            if t in seen:
                continue
            seen.add(t)
            candidates.append((crit_key, t))

    return candidates
