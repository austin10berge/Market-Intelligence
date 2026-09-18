# Algo Detective — Automated Gate Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an automated precision-focused gate search over the `detective_features` dataset using a greedy stepwise search (Approach A) and a decision tree extraction (Approach B), outputting a criteria dict in the existing format with precision > V42's 22.2% at recall ≥ 30%.

**Architecture:** One new module (`gate_search.py`) built entirely on top of the existing `_apply_criteria` and `validate_criteria` functions — no changes to any existing file. Approach A sweeps feature thresholds greedily; Approach B fits a shallow sklearn decision tree and extracts leaf rules, validating each with `validate_criteria` for authoritative scoring.

**Tech Stack:** Python 3.12, numpy, scikit-learn 1.9.0 (already in `pyproject.toml`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-20-algo-detective-gate-search-design.md`

## Global Constraints

- Python 3.12, no local virtualenv — run tests via `docker compose run --rm test python3 -m pytest tests/...`. Run `docker compose build test` before each test run after any file change.
- `~/.local/bin/ruff` for lint/format; a `PostToolUse` hook auto-runs it but run `ruff check` and `ruff format --check` explicitly before committing.
- `from __future__ import annotations` at the top of every new `.py` file.
- No modifications to `analyze.py`, `validate.py`, `store.py`, or any existing file.
- The greedy loop uses `_apply_criteria` directly (not `validate_criteria`) for the inner eval to avoid redundant options re-joins inside the hot path.
- `validate_criteria` is used only for ground-truth final scoring of a handful of candidates.
- Recall floor default: 0.30. Improvement threshold: >0.005 precision gain required to add a gate.

---

## File Structure

```
src/algo_detective/gate_search.py          (NEW — all search logic + CLI)
tests/test_algo_detective_gate_search.py   (NEW — all tests)
```

---

### Task 1: `_build_candidate_gates` — threshold candidate generation

**Files:**
- Create: `src/algo_detective/gate_search.py`
- Create: `tests/test_algo_detective_gate_search.py`

**Interfaces:**
- Consumes: `_NUMERIC_FEATURES`, `_BOOLEAN_FEATURES` from `analyze.py` (imported at module level).
- Produces: `_build_candidate_gates(prime_rows: list[dict]) -> list[tuple[str, float | int]]` — each tuple is `(criteria_key, threshold)` ready to pass to `_apply_criteria`. `prime_rows` is the subset of enriched rows where `is_prime == 1` that pass the current criteria; used only to compute prime-distribution percentile thresholds.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_gate_search.py`:

```python
"""Tests for src/algo_detective/gate_search.py."""
from __future__ import annotations

import pytest

from src.algo_detective.gate_search import _build_candidate_gates


def _prime(n: int, rsi: float = 40.0, adx: float = 25.0) -> list[dict]:
    """n prime rows with deterministic feature values."""
    return [
        {"is_prime": 1, "rsi": rsi + i * 0.5, "adx": adx + i * 0.5,
         "price_above_ema50": 1, "rv20": 0.30}
        for i in range(n)
    ]


class TestBuildCandidateGates:
    def test_numeric_produces_min_and_max(self):
        candidates = _build_candidate_gates(_prime(5))
        keys = [k for k, _ in candidates]
        assert "rsi_min" in keys
        assert "rsi_max" in keys
        assert "adx_min" in keys
        assert "adx_max" in keys

    def test_boolean_produces_true_only(self):
        candidates = _build_candidate_gates(_prime(5))
        keys = [k for k, _ in candidates]
        assert ("price_above_ema50", 1) in candidates
        assert "price_above_ema50_min" not in keys
        assert "price_above_ema50_max" not in keys

    def test_skips_all_null_feature(self):
        prime = [{"is_prime": 1, "rsi": None} for _ in range(5)]
        candidates = _build_candidate_gates(prime)
        keys = [k for k, _ in candidates]
        assert "rsi_min" not in keys
        assert "rsi_max" not in keys

    def test_returns_empty_for_fewer_than_5_primes(self):
        assert _build_candidate_gates(_prime(4)) == []

    def test_boolean_skipped_when_prevalence_below_10_pct(self):
        # 0/5 primes have this feature → 0% < 10% → skip
        prime = [{"is_prime": 1, "rsi": float(i), "price_above_ema50": 0} for i in range(5)]
        candidates = _build_candidate_gates(prime)
        assert ("price_above_ema50", 1) not in candidates

    def test_adr20_pct_included_in_search(self):
        prime = [{"is_prime": 1, "adr20_pct": float(i)} for i in range(5)]
        candidates = _build_candidate_gates(prime)
        keys = [k for k, _ in candidates]
        assert "adr20_pct_min" in keys
        assert "adr20_pct_max" in keys

    def test_options_iv_min_candidate_generated(self):
        prime = [{"is_prime": 1, "best_iv": 0.20 + i * 0.02} for i in range(5)]
        candidates = _build_candidate_gates(prime)
        keys = [k for k, _ in candidates]
        assert "options_iv_min" in keys

    def test_no_duplicate_thresholds_per_key(self):
        # All primes have the same rsi → only one unique threshold
        prime = [{"is_prime": 1, "rsi": 42.0} for _ in range(5)]
        candidates = _build_candidate_gates(prime)
        rsi_min_vals = [v for k, v in candidates if k == "rsi_min"]
        assert len(rsi_min_vals) == len(set(rsi_min_vals))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose build test
docker compose run --rm test python3 -m pytest tests/test_algo_detective_gate_search.py -v
```

Expected: `ImportError: cannot import name '_build_candidate_gates' from 'src.algo_detective.gate_search'` (module doesn't exist yet).

- [ ] **Step 3: Implement `_build_candidate_gates`**

Create `src/algo_detective/gate_search.py`:

```python
"""Automated gate search for algo detective — greedy stepwise (Approach A)
and decision tree extraction (Approach B).
See docs/superpowers/specs/2026-07-20-algo-detective-gate-search-design.md.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from typing import TypedDict

import numpy as np

from .analyze import _BOOLEAN_FEATURES, _NUMERIC_FEATURES, _apply_criteria
from .store import get_all_features, get_options_index
from .validate import validate_criteria

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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose build test
docker compose run --rm test python3 -m pytest tests/test_algo_detective_gate_search.py -v
```

Expected: `8 passed`.

- [ ] **Step 5: Ruff check**

```bash
~/.local/bin/ruff check src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
~/.local/bin/ruff format --check src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
```

Expected: `All checks passed!` on both commands. If `format --check` reports files to reformat, run `~/.local/bin/ruff format src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py` and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
git commit -m "feat(algo-detective): add _build_candidate_gates for gate search threshold sweep"
```

---

### Task 2: `run_greedy_search` — Approach A greedy stepwise gate search

**Files:**
- Modify: `src/algo_detective/gate_search.py` (append `_eval_candidate` and `run_greedy_search`)
- Modify: `tests/test_algo_detective_gate_search.py` (append new test class)

**Interfaces:**
- Consumes: `_build_candidate_gates` (Task 1); `_apply_criteria` from `analyze.py`.
- Produces:
  - `_eval_candidate(candidate_pool: list[dict], key: str, val: float | int, total_prime: int) -> tuple[float, float]` — returns `(precision, recall)` for adding `{key: val}` to the current filter applied to `candidate_pool`. Private helper.
  - `run_greedy_search(rows: list[dict], recall_floor: float = 0.30, max_steps: int = 15) -> GreedySearchResult` — `rows` is the full enriched dataset (all 134k rows), `recall_floor` is the minimum recall fraction to enforce at every step. Returns the `GreedySearchResult` TypedDict defined in Task 1.

- [ ] **Step 1: Append failing tests**

Append to `tests/test_algo_detective_gate_search.py`:

```python
from src.algo_detective.gate_search import _eval_candidate, run_greedy_search


class TestEvalCandidate:
    def test_returns_correct_precision_and_recall(self):
        pool = [
            {"is_prime": 1, "rsi": 30.0},
            {"is_prime": 1, "rsi": 35.0},
            {"is_prime": 0, "rsi": 25.0},
            {"is_prime": 0, "rsi": 70.0},
            {"is_prime": 0, "rsi": 75.0},
        ]
        # rsi_max=40 keeps rsi<=40: all 2 prime + 1 control (rsi=25) → prec=2/3, rec=2/2
        prec, rec = _eval_candidate(pool, "rsi_max", 40.0, total_prime=2)
        assert abs(prec - 2 / 3) < 1e-9
        assert abs(rec - 1.0) < 1e-9

    def test_returns_zeros_when_nothing_passes(self):
        pool = [{"is_prime": 1, "rsi": 80.0}, {"is_prime": 0, "rsi": 90.0}]
        # rsi_max=10 keeps nothing
        prec, rec = _eval_candidate(pool, "rsi_max", 10.0, total_prime=1)
        assert prec == 0.0
        assert rec == 0.0


class TestRunGreedySearch:
    def _make_rows(self) -> list[dict]:
        """10 prime with rsi 20-29; 100 control with rsi 60-79. rsi_max~40 is a clean separator."""
        prime = [{"is_prime": 1, "rsi": float(20 + i)} for i in range(10)]
        control = [{"is_prime": 0, "rsi": float(60 + i % 20)} for i in range(100)]
        return prime + control

    def test_improves_precision_over_base_rate(self):
        rows = self._make_rows()
        base_rate = 10 / 110
        result = run_greedy_search(rows, recall_floor=0.30, max_steps=5)
        assert result["precision"] > base_rate

    def test_recall_never_drops_below_floor(self):
        rows = self._make_rows()
        result = run_greedy_search(rows, recall_floor=0.30, max_steps=10)
        for step in result["steps"]:
            assert step["recall"] >= 0.30, f"Step violated recall floor: {step}"
        assert result["recall"] >= 0.30

    def test_step_trace_matches_final_result(self):
        rows = self._make_rows()
        result = run_greedy_search(rows, recall_floor=0.30, max_steps=5)
        if result["steps"]:
            last = result["steps"][-1]
            assert abs(last["precision"] - result["precision"]) < 1e-9

    def test_stops_when_no_gate_improves_by_threshold(self):
        # All features uniform between prime and control → no gate improves by >0.5pp
        rows = [{"is_prime": 1, "rsi": 50.0} for _ in range(10)]
        rows += [{"is_prime": 0, "rsi": 50.0} for _ in range(100)]
        result = run_greedy_search(rows, recall_floor=0.30, max_steps=10)
        assert result["criteria"] == {}
        assert result["steps"] == []

    def test_returns_empty_criteria_when_every_gate_violates_recall_floor(self):
        # Gate rsi_min=90 would give 100% prec but only 1/10 = 10% recall < 30% floor.
        # All other possible thresholds also give <30% recall or no precision gain.
        prime = [{"is_prime": 1, "rsi": float(i * 10)} for i in range(10)]  # rsi 0,10,...,90
        # Control: 200 rows with rsi spread uniformly; any low-rsi gate catches most control too
        control = [{"is_prime": 0, "rsi": float(i % 100)} for i in range(200)]
        rows = prime + control
        result = run_greedy_search(rows, recall_floor=0.90, max_steps=5)
        # recall_floor=0.90 means must catch ≥9/10 primes; very restrictive
        if result["criteria"]:
            assert result["recall"] >= 0.90

    def test_empty_input_returns_empty_result(self):
        result = run_greedy_search([], recall_floor=0.30)
        assert result["criteria"] == {}
        assert result["precision"] == 0.0
        assert result["recall"] == 0.0
        assert result["steps"] == []
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_gate_search.py::TestEvalCandidate tests/test_algo_detective_gate_search.py::TestRunGreedySearch -v
```

Expected: `ImportError: cannot import name '_eval_candidate' from 'src.algo_detective.gate_search'`.

- [ ] **Step 3: Implement `_eval_candidate` and `run_greedy_search`**

Append to `src/algo_detective/gate_search.py` (after `_build_candidate_gates`):

```python
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
        steps.append({
            "gate": best_key,
            "value": best_val,
            "precision": round(best_precision, 4),
            "recall": round(best_recall, 4),
        })
        logger.info(
            "Step %d: +%s=%s → precision=%.1f%% recall=%.1f%%",
            len(steps), best_key, best_val,
            best_precision * 100, best_recall * 100,
        )

    final_tp = sum(1 for r in candidate_pool if r.get("is_prime") == 1)
    return {
        "criteria": criteria,
        "precision": round(current_precision, 4),
        "recall": round(final_tp / total_prime, 4),
        "steps": steps,
    }
```

- [ ] **Step 4: Run to verify they pass**

```bash
docker compose build test
docker compose run --rm test python3 -m pytest tests/test_algo_detective_gate_search.py -v
```

Expected: all tests pass (Task 1's 8 + Task 2's 8 = 16 passed).

- [ ] **Step 5: Ruff check**

```bash
~/.local/bin/ruff check src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
~/.local/bin/ruff format --check src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
```

Expected: clean. Apply `ruff format` if needed.

- [ ] **Step 6: Commit**

```bash
git add src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
git commit -m "feat(algo-detective): implement greedy gate search (Approach A)"
```

---

### Task 3: `run_tree_search` — Approach B decision tree extraction

**Files:**
- Modify: `src/algo_detective/gate_search.py` (append `_build_feature_matrix`, `_tree_path_to_criteria`, `run_tree_search`)
- Modify: `tests/test_algo_detective_gate_search.py` (append new test classes)

**Interfaces:**
- Consumes: `_BOOLEAN_FEATURES`, `_NUMERIC_FEATURES` (module constants); `validate_criteria` from `validate.py`; `sklearn.tree.DecisionTreeClassifier`; `_SEARCH_NUMERIC_FEATURES`, `_OPTIONS_GATE_DEFS` (module constants from Task 1).
- Produces:
  - `_build_feature_matrix(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, list[str]]` — returns `(X, y, feature_names)` with nulls filled by column median. Private.
  - `_tree_path_to_criteria(path: list[tuple[str, str, float]]) -> dict` — translates a root-to-leaf path of `(feature_name, direction, threshold)` tuples into a criteria dict. `direction` is `"<="` or `">"`. Private.
  - `run_tree_search(rows: list[dict], baseline_precision: float, recall_floor: float = 0.30) -> list[TreeCandidate]` — fits the tree, extracts leaf rules, validates each with `validate_criteria`, returns candidates sorted by precision descending.

- [ ] **Step 1: Append failing tests**

Append to `tests/test_algo_detective_gate_search.py`:

```python
from src.algo_detective.gate_search import (
    _build_feature_matrix,
    _tree_path_to_criteria,
    run_tree_search,
)


class TestTreePathToCriteria:
    def test_numeric_gt_becomes_min(self):
        assert _tree_path_to_criteria([("rsi", ">", 20.5)]) == {"rsi_min": 20.5}

    def test_numeric_lte_becomes_max(self):
        assert _tree_path_to_criteria([("rv20", "<=", 0.45)]) == {"rv20_max": 0.45}

    def test_boolean_gt_becomes_true(self):
        assert _tree_path_to_criteria([("price_above_ema50", ">", 0.5)]) == {"price_above_ema50": 1}

    def test_boolean_lte_skipped(self):
        assert _tree_path_to_criteria([("price_above_ema50", "<=", 0.5)]) == {}

    def test_best_iv_gt_becomes_options_iv_min(self):
        assert _tree_path_to_criteria([("best_iv", ">", 0.25)]) == {"options_iv_min": 0.25}

    def test_iv_rv_gt_becomes_iv_rv_min(self):
        assert _tree_path_to_criteria([("iv_rv", ">", 1.2)]) == {"iv_rv_min": 1.2}

    def test_pcr_vol_lte_becomes_pcr_vol_max(self):
        assert _tree_path_to_criteria([("pcr_vol", "<=", 1.5)]) == {"pcr_vol_max": 1.5}

    def test_multi_condition_path(self):
        path = [("rsi", "<=", 60.0), ("adx", ">", 20.0)]
        assert _tree_path_to_criteria(path) == {"rsi_max": 60.0, "adx_min": 20.0}

    def test_empty_path_returns_empty_dict(self):
        assert _tree_path_to_criteria([]) == {}


class TestBuildFeatureMatrix:
    def test_shape_matches_rows_and_features(self):
        rows = [
            {"is_prime": 1, "rsi": 40.0, "price_above_ema50": 1},
            {"is_prime": 0, "rsi": 60.0, "price_above_ema50": 0},
        ]
        X, y, names = _build_feature_matrix(rows)
        assert X.shape[0] == 2
        assert X.shape[1] == len(names)
        assert list(y) == [1, 0]

    def test_null_filled_with_column_median(self):
        rows = [
            {"is_prime": 1, "rsi": None},
            {"is_prime": 0, "rsi": 40.0},
            {"is_prime": 0, "rsi": 60.0},
        ]
        X, y, names = _build_feature_matrix(rows)
        rsi_idx = names.index("rsi")
        # median of non-null values [40, 60] = 50
        assert X[0, rsi_idx] == 50.0

    def test_boolean_encoded_as_0_or_1(self):
        rows = [
            {"is_prime": 1, "price_above_ema50": 1},
            {"is_prime": 0, "price_above_ema50": 0},
        ]
        X, y, names = _build_feature_matrix(rows)
        idx = names.index("price_above_ema50")
        assert X[0, idx] == 1.0
        assert X[1, idx] == 0.0


class TestRunTreeSearch:
    def _make_separable_rows(self) -> list[dict]:
        """30 prime (rsi 20-49) + 300 control (rsi 60-89). Tree should find rsi split."""
        prime = [{"is_prime": 1, "rsi": float(20 + i)} for i in range(30)]
        control = [{"is_prime": 0, "rsi": float(60 + i % 30)} for i in range(300)]
        return prime + control

    def test_returns_list(self):
        result = run_tree_search(self._make_separable_rows(), baseline_precision=0.0,
                                 recall_floor=0.30)
        assert isinstance(result, list)

    def test_all_candidates_meet_baseline_and_floor(self):
        rows = self._make_separable_rows()
        result = run_tree_search(rows, baseline_precision=0.50, recall_floor=0.30)
        for cand in result:
            assert cand["precision"] >= 0.50, f"Below baseline: {cand}"
            assert cand["recall"] >= 0.30, f"Below recall floor: {cand}"

    def test_sorted_by_precision_descending(self):
        rows = self._make_separable_rows()
        result = run_tree_search(rows, baseline_precision=0.0, recall_floor=0.30)
        precisions = [c["precision"] for c in result]
        assert precisions == sorted(precisions, reverse=True)

    def test_returns_empty_when_baseline_impossible(self):
        rows = self._make_separable_rows()
        # Baseline of 100% can never be met on a real dataset
        result = run_tree_search(rows, baseline_precision=1.01, recall_floor=0.30)
        assert result == []
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_gate_search.py::TestTreePathToCriteria tests/test_algo_detective_gate_search.py::TestBuildFeatureMatrix tests/test_algo_detective_gate_search.py::TestRunTreeSearch -v
```

Expected: `ImportError: cannot import name '_build_feature_matrix' from 'src.algo_detective.gate_search'`.

- [ ] **Step 3: Implement tree search functions**

Append to `src/algo_detective/gate_search.py` (after `run_greedy_search`):

```python
# Explicit mapping for options-derived feature names that don't follow the
# generic field_name + _min/_max → criteria_key convention.
_TREE_KEY_MAP: dict[tuple[str, str], str] = {
    ("best_iv", ">"): "options_iv_min",
    ("iv_rv", ">"): "iv_rv_min",
    ("pcr_vol", "<="): "pcr_vol_max",
}

_BOOL_SET: frozenset[str] = frozenset(_BOOLEAN_FEATURES)


def _tree_path_to_criteria(path: list[tuple[str, str, float]]) -> dict:
    """Translate a root-to-leaf path of (feature, direction, threshold) to a criteria dict.

    direction is '<=' (left child) or '>' (right child).
    Boolean features: '>' → {feature: 1}; '<=' → skipped (no False gate exists).
    Options-derived features: mapped via _TREE_KEY_MAP.
    All others: '>' → {feature_min: threshold}; '<=' → {feature_max: threshold}.
    """
    criteria: dict = {}
    for feat, direction, threshold in path:
        mapped = _TREE_KEY_MAP.get((feat, direction))
        if mapped is not None:
            criteria[mapped] = round(threshold, 6)
        elif feat in _BOOL_SET:
            if direction == ">":
                criteria[feat] = 1
        else:
            key = f"{feat}_min" if direction == ">" else f"{feat}_max"
            criteria[key] = round(threshold, 6)
    return criteria


def _build_feature_matrix(
    rows: list[dict],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build (X, y, feature_names) for sklearn. Nulls filled with column median."""
    numeric_cols = _SEARCH_NUMERIC_FEATURES + ["best_iv", "pcr_vol", "iv_rv"]
    bool_cols = list(_BOOLEAN_FEATURES)
    feature_names = numeric_cols + bool_cols

    X_list = []
    y_list = []
    for row in rows:
        x_row = [row.get(f) for f in numeric_cols]
        x_row += [1.0 if row.get(f) == 1 else 0.0 for f in bool_cols]
        X_list.append(x_row)
        y_list.append(1 if row.get("is_prime") == 1 else 0)

    X = np.array(X_list, dtype=float)
    y = np.array(y_list, dtype=int)

    for col_idx in range(X.shape[1]):
        col = X[:, col_idx]
        mask = np.isnan(col)
        if mask.any():
            median_val = float(np.nanmedian(col))
            X[mask, col_idx] = median_val if not np.isnan(median_val) else 0.0

    return X, y, feature_names


def run_tree_search(
    rows: list[dict],
    baseline_precision: float,
    recall_floor: float = 0.30,
) -> list[TreeCandidate]:
    """Approach B: fit a shallow decision tree, extract leaf rules as criteria dicts.

    Leaves with < 10 samples are skipped. Each surviving leaf's criteria dict is
    validated by validate_criteria (authoritative ground truth — sklearn leaf stats
    are approximate due to class_weight). Returns candidates sorted by precision.
    """
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.tree import _tree as sklearn_tree

    X, y, feature_names = _build_feature_matrix(rows)
    clf = DecisionTreeClassifier(max_depth=5, class_weight="balanced", random_state=42)
    clf.fit(X, y)

    tree = clf.tree_
    raw_candidates: list[dict] = []

    def _walk(node: int, path: list[tuple[str, str, float]]) -> None:
        if tree.feature[node] == sklearn_tree.TREE_UNDEFINED:
            if int(tree.n_node_samples[node]) < 10:
                return
            crit = _tree_path_to_criteria(path)
            if crit:
                raw_candidates.append(crit)
            return
        feat = feature_names[tree.feature[node]]
        thresh = float(tree.threshold[node])
        _walk(tree.children_left[node], path + [(feat, "<=", thresh)])
        _walk(tree.children_right[node], path + [(feat, ">", thresh)])

    _walk(0, [])

    results: list[TreeCandidate] = []
    for crit in raw_candidates:
        report = validate_criteria(crit, features=rows)
        if report["precision"] >= baseline_precision and report["recall"] >= recall_floor:
            results.append({
                "criteria": crit,
                "precision": report["precision"],
                "recall": report["recall"],
            })

    return sorted(results, key=lambda c: c["precision"], reverse=True)
```

- [ ] **Step 4: Run to verify all tests pass**

```bash
docker compose build test
docker compose run --rm test python3 -m pytest tests/test_algo_detective_gate_search.py -v
```

Expected: all tests pass (16 from Tasks 1–2 + 13 new = 29 passed).

- [ ] **Step 5: Ruff check**

```bash
~/.local/bin/ruff check src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
~/.local/bin/ruff format --check src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
```

Expected: clean. Apply `ruff format` if needed.

- [ ] **Step 6: Commit**

```bash
git add src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
git commit -m "feat(algo-detective): implement tree-extraction gate search (Approach B)"
```

---

### Task 4: `run_gate_search` — orchestrator, report, and CLI

**Files:**
- Modify: `src/algo_detective/gate_search.py` (append `_print_report`, `run_gate_search`, `__main__` block)
- Modify: `tests/test_algo_detective_gate_search.py` (append orchestrator tests)

**Interfaces:**
- Consumes: `run_greedy_search` (Task 2), `run_tree_search` (Task 3), `get_all_features` and `get_options_index` from `store.py`, `validate_criteria` from `validate.py`.
- Produces:
  - `run_gate_search(recall_floor: float = 0.30, approach: str = "ab") -> GateSearchResult` — loads data, enriches rows, runs A and/or B, saves JSON to `data/detective/gate_search_YYYY-MM-DD.json`, prints report, returns result. `approach` is `"a"`, `"b"`, or `"ab"`.
  - CLI: `python -m src.algo_detective.gate_search [--approach {a,b,ab}] [--recall-floor FLOAT]`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_algo_detective_gate_search.py`:

```python
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.algo_detective.gate_search import run_gate_search


class TestRunGateSearch:
    def _tiny_features(self) -> list[dict]:
        """20 prime (rsi 20-39) + 200 control (rsi 60-79)."""
        prime = [{"is_prime": 1, "date": "2026-01-01", "ticker": f"P{i:02d}",
                  "rsi": float(20 + i)} for i in range(20)]
        control = [{"is_prime": 0, "date": "2026-01-01", "ticker": f"C{i:03d}",
                    "rsi": float(60 + i % 20)} for i in range(200)]
        return prime + control

    def test_result_contains_required_keys(self, tmp_path):
        with (
            patch("src.algo_detective.gate_search.get_all_features",
                  return_value=self._tiny_features()),
            patch("src.algo_detective.gate_search.get_options_index", return_value={}),
            patch("src.algo_detective.gate_search.Path",
                  side_effect=lambda *a: tmp_path / Path(*a).name
                  if str(Path(*a)).startswith("data/detective") else Path(*a)),
        ):
            result = run_gate_search(recall_floor=0.30, approach="a")

        assert "generated" in result
        assert "recall_floor" in result
        assert "v42_baseline" in result
        assert "approach_a" in result
        assert "approach_b" in result

    def test_approach_a_only_skips_tree(self, tmp_path):
        with (
            patch("src.algo_detective.gate_search.get_all_features",
                  return_value=self._tiny_features()),
            patch("src.algo_detective.gate_search.get_options_index", return_value={}),
            patch("src.algo_detective.gate_search.run_tree_search") as mock_tree,
            patch("src.algo_detective.gate_search.Path",
                  side_effect=lambda *a: tmp_path / Path(*a).name
                  if str(Path(*a)).startswith("data/detective") else Path(*a)),
        ):
            run_gate_search(recall_floor=0.30, approach="a")

        mock_tree.assert_not_called()

    def test_approach_b_only_skips_greedy(self, tmp_path):
        with (
            patch("src.algo_detective.gate_search.get_all_features",
                  return_value=self._tiny_features()),
            patch("src.algo_detective.gate_search.get_options_index", return_value={}),
            patch("src.algo_detective.gate_search.run_greedy_search") as mock_greedy,
            patch("src.algo_detective.gate_search.Path",
                  side_effect=lambda *a: tmp_path / Path(*a).name
                  if str(Path(*a)).startswith("data/detective") else Path(*a)),
        ):
            run_gate_search(recall_floor=0.30, approach="b")

        mock_greedy.assert_not_called()

    def test_iv_rv_computed_in_enriched_rows(self, tmp_path):
        """iv_rv = best_iv / rv20 is pre-computed in enriched rows."""
        features = [{"is_prime": 1, "date": "2026-01-01", "ticker": "AAA",
                     "rv20": 0.20, "rsi": 30.0}]
        options_idx = {("2026-01-01", "AAA"): {"best_iv": 0.30, "pcr_vol": 1.0}}

        captured: list[dict] = []

        def fake_greedy(rows, **kwargs):
            captured.extend(rows)
            return {"criteria": {}, "precision": 0.0, "recall": 0.0, "steps": []}

        with (
            patch("src.algo_detective.gate_search.get_all_features", return_value=features),
            patch("src.algo_detective.gate_search.get_options_index", return_value=options_idx),
            patch("src.algo_detective.gate_search.run_greedy_search", side_effect=fake_greedy),
            patch("src.algo_detective.gate_search.Path",
                  side_effect=lambda *a: tmp_path / Path(*a).name
                  if str(Path(*a)).startswith("data/detective") else Path(*a)),
        ):
            run_gate_search(recall_floor=0.30, approach="a")

        assert len(captured) == 1
        row = captured[0]
        assert abs(row["iv_rv"] - 0.30 / 0.20) < 1e-9
        assert row["best_iv"] == 0.30
        assert row["pcr_vol"] == 1.0
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_gate_search.py::TestRunGateSearch -v
```

Expected: `ImportError: cannot import name 'run_gate_search' from 'src.algo_detective.gate_search'`.

- [ ] **Step 3: Implement orchestrator, report, and CLI**

Append to `src/algo_detective/gate_search.py` (after `run_tree_search`):

```python
def _print_report(result: GateSearchResult) -> None:
    v42 = result["v42_baseline"]
    print(f"\n{'='*60}")
    print(f"GATE SEARCH RESULTS — {result['generated']}")
    print(f"Recall floor: {result['recall_floor']:.0%}")
    if v42.get("precision") is not None:
        print(f"\nV42 baseline:  precision={v42['precision']:.1%}  recall={v42['recall']:.1%}")

    a = result["approach_a"]
    print(f"\nApproach A (greedy):")
    print(f"  precision={a['precision']:.1%}  recall={a['recall']:.1%}  ({len(a['steps'])} gates)")
    for i, step in enumerate(a["steps"], 1):
        print(f"  {i}. {step['gate']}={step['value']}  →  prec={step['precision']:.1%} rec={step['recall']:.1%}")

    b = result["approach_b"]
    if b:
        print(f"\nApproach B (tree) — top {min(5, len(b))} candidates:")
        for cand in b[:5]:
            print(f"  precision={cand['precision']:.1%}  recall={cand['recall']:.1%}  {cand['criteria']}")
    elif "b" in result.get("_approach", "ab"):
        print("\nApproach B: no candidates met the baseline + recall floor.")

    out = f"data/detective/gate_search_{result['generated']}.json"
    print(f"\nFull results saved to: {out}")
    print("=" * 60)


def run_gate_search(
    recall_floor: float = 0.30,
    approach: str = "ab",
) -> GateSearchResult:
    """Load data, run A+B gate search, save output JSON, print report, return result.

    approach: 'a' (greedy only), 'b' (tree only), or 'ab' (both).
    Options join (best_iv, pcr_vol, delta, open_interest) and iv_rv computation
    are done once upfront so the greedy inner loop never touches the DB.
    """
    logger.info("Loading features and options data...")
    features = get_all_features()
    options_idx = get_options_index()

    enriched: list[dict] = []
    for row in features:
        opt = options_idx.get((row["date"], row["ticker"]), {})
        iv = opt.get("best_iv")
        rv = row.get("rv20")
        enriched.append({
            **row,
            "best_iv": iv,
            "pcr_vol": opt.get("pcr_vol"),
            "delta": opt.get("delta"),
            "open_interest": opt.get("open_interest"),
            "iv_rv": (iv / rv) if (iv is not None and rv is not None and rv > 0) else None,
        })

    total_prime = sum(1 for r in enriched if r.get("is_prime") == 1)
    logger.info(
        "Loaded %d rows (%d prime, %d control)",
        len(enriched), total_prime, len(enriched) - total_prime,
    )

    v42_baseline: dict = {"precision": None, "recall": None}
    v42_path = Path("data/v42_criteria.json")
    if v42_path.exists():
        v42_criteria = json.loads(v42_path.read_text())
        v42_report = validate_criteria(v42_criteria, features=enriched)
        v42_baseline = {"precision": v42_report["precision"], "recall": v42_report["recall"]}
        logger.info(
            "V42 baseline: precision=%.1f%% recall=%.1f%%",
            v42_baseline["precision"] * 100, v42_baseline["recall"] * 100,
        )

    empty_greedy: GreedySearchResult = {"criteria": {}, "precision": 0.0, "recall": 0.0, "steps": []}
    greedy_result = empty_greedy

    if "a" in approach:
        logger.info("Running Approach A (greedy search)...")
        greedy_result = run_greedy_search(enriched, recall_floor=recall_floor)
        logger.info(
            "Approach A: precision=%.1f%% recall=%.1f%% in %d steps",
            greedy_result["precision"] * 100,
            greedy_result["recall"] * 100,
            len(greedy_result["steps"]),
        )

    tree_results: list[TreeCandidate] = []
    if "b" in approach:
        logger.info("Running Approach B (tree search)...")
        baseline_prec = greedy_result["precision"] if "a" in approach else 0.0
        tree_results = run_tree_search(enriched, baseline_precision=baseline_prec,
                                       recall_floor=recall_floor)
        logger.info("Approach B: found %d candidates above baseline", len(tree_results))

    result: GateSearchResult = {
        "generated": date.today().isoformat(),
        "recall_floor": recall_floor,
        "v42_baseline": v42_baseline,
        "approach_a": greedy_result,
        "approach_b": tree_results,
    }

    out_path = Path(f"data/detective/gate_search_{date.today().isoformat()}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    logger.info("Saved to %s", out_path)

    _print_report(result)
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Automated gate search for algo detective")
    parser.add_argument(
        "--approach", choices=["a", "b", "ab"], default="ab",
        help="Which search approach to run (default: ab)",
    )
    parser.add_argument(
        "--recall-floor", type=float, default=0.30,
        help="Minimum recall fraction to enforce (default: 0.30)",
    )
    args = parser.parse_args()
    run_gate_search(recall_floor=args.recall_floor, approach=args.approach)
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
docker compose build test
docker compose run --rm test python3 -m pytest tests/test_algo_detective_gate_search.py -v
```

Expected: all tests pass (29 from Tasks 1–3 + 4 new = 33 passed).

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -v
```

Expected: same 6 pre-existing failures as before this plan (`test_algo_detective_options_chain`, `test_csp_scanner_integration` x2, `test_trading_calendar` x3), no new failures.

- [ ] **Step 6: Ruff check**

```bash
~/.local/bin/ruff check src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
~/.local/bin/ruff format --check src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
```

Expected: clean. Apply `ruff format` if needed.

- [ ] **Step 7: Commit**

```bash
git add src/algo_detective/gate_search.py tests/test_algo_detective_gate_search.py
git commit -m "feat(algo-detective): add run_gate_search orchestrator and CLI"
```

---

### Task 5: Live verification against the real dataset

No automated test covers the full live run. This task runs the gate search against the real `detective.db` and confirms A's output beats V42 in precision.

- [ ] **Step 1: Run the full A+B search**

```bash
docker compose --profile pipeline run --rm pipeline python3 -m src.algo_detective.gate_search --approach ab --recall-floor 0.30
```

Expected output (printed report):
- V42 baseline: precision≈22.2% recall≈30.5%
- Approach A: precision > 22.2%, recall ≥ 30%, with a step trace showing each gate added
- Approach B: list of tree-derived candidates, each with precision ≥ Approach A's result and recall ≥ 30%
- `data/detective/gate_search_YYYY-MM-DD.json` written

If Approach A returns an empty criteria dict (no gate cleared the 0.5pp improvement bar): the base rate of 334/134575 ≈ 0.25% means even a crude threshold should improve significantly; re-check that the enriched rows have populated `rsi`, `adx`, and other features by running `python3 -m src.algo_detective.analyze` and confirming the KS rankings show discriminating features.

- [ ] **Step 2: Validate the Approach A criteria dict with validate_criteria**

```bash
docker compose --profile pipeline run --rm pipeline python3 -c "
import json
from src.algo_detective.gate_search import run_gate_search
from src.algo_detective.validate import validate_criteria, print_report
from src.algo_detective.store import get_all_features
from pathlib import Path
import glob

# Load today's gate search output
files = sorted(glob.glob('data/detective/gate_search_*.json'))
result = json.loads(Path(files[-1]).read_text())
criteria = result['approach_a']['criteria']
print('Approach A criteria:', json.dumps(criteria, indent=2))

report = validate_criteria(criteria, join_options=True)
print_report(report)
"
```

Expected: precision > V42's 22.2% and recall ≥ 30%.

- [ ] **Step 3: Confirm no regressions in the nightly pipeline**

```bash
docker compose --profile pipeline run --rm pipeline
```

Expected: pipeline completes normally, `gate_search.py` is not imported by `main.py` so no regressions possible — this is a one-shot CLI tool, not wired into the pipeline.
