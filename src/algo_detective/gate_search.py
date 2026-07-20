"""Automated gate search for algo detective — greedy stepwise (Approach A)
and decision tree extraction (Approach B).
See docs/superpowers/specs/2026-07-20-algo-detective-gate-search-design.md.
"""

from __future__ import annotations

import logging
from typing import TypedDict

import numpy as np

from .analyze import _BOOLEAN_FEATURES, _NUMERIC_FEATURES, _apply_criteria

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


def _eval_candidate(
    candidate_pool: list[dict],
    key: str,
    val: float | int,
    total_prime: int,
) -> tuple[float, float]:
    """Return (precision, recall) for adding {key: val} to the current filter.

    candidate_pool is the rows already passing the current criteria — only the
    new gate is applied here, keeping the inner loop O(len(candidate_pool)).
    recall is computed against the original total_prime count, not just the pool.
    """
    gate = {key: val}
    filtered = [r for r in candidate_pool if _apply_criteria(r, gate)]
    if not filtered:
        return 0.0, 0.0
    tp = sum(1 for r in filtered if r.get("is_prime") == 1)
    return tp / len(filtered), tp / total_prime


def run_greedy_search(
    rows: list[dict],
    recall_floor: float = 0.30,
    max_steps: int = 15,
) -> GreedySearchResult:
    """Approach A: add one gate per step, maximising precision while recall >= recall_floor.

    rows: full enriched dataset (prime + control). The greedy loop pre-filters
    to candidate_pool at each step so _eval_candidate only touches the shrinking
    subset rather than all 134k rows.
    """
    total_prime = sum(1 for r in rows if r.get("is_prime") == 1)
    if total_prime == 0:
        return {"criteria": {}, "precision": 0.0, "recall": 0.0, "steps": []}

    criteria: dict = {}
    steps: list[StepTrace] = []
    candidate_pool = list(rows)
    current_precision = total_prime / len(rows)

    for _ in range(max_steps):
        prime_in_pool = [r for r in candidate_pool if r.get("is_prime") == 1]
        gate_candidates = _build_candidate_gates(prime_in_pool)

        best_key: str | None = None
        best_val: float | int | None = None
        best_precision = current_precision + 0.005  # must beat by >0.5pp
        best_recall = 0.0

        for key, val in gate_candidates:
            if key in criteria:
                continue
            prec, rec = _eval_candidate(candidate_pool, key, val, total_prime)
            if rec < recall_floor:
                continue
            if prec > best_precision:
                best_key, best_val = key, val
                best_precision = prec
                best_recall = rec

        if best_key is None:
            break

        criteria[best_key] = best_val
        candidate_pool = [r for r in candidate_pool if _apply_criteria(r, {best_key: best_val})]
        current_precision = best_precision
        steps.append(
            {
                "gate": best_key,
                "value": best_val,
                "precision": round(best_precision, 4),
                "recall": round(best_recall, 4),
            }
        )
        logger.info(
            "Step %d: +%s=%s → precision=%.1f%% recall=%.1f%%",
            len(steps),
            best_key,
            best_val,
            best_precision * 100,
            best_recall * 100,
        )

    final_tp = sum(1 for r in candidate_pool if r.get("is_prime") == 1)
    return {
        "criteria": criteria,
        "precision": round(current_precision, 4),
        "recall": round(final_tp / total_prime, 4),
        "steps": steps,
    }
