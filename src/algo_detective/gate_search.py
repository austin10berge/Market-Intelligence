"""Automated gate search for algo detective — greedy stepwise (Approach A)
and decision tree extraction (Approach B).
See docs/superpowers/specs/2026-07-20-algo-detective-gate-search-design.md.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import TypedDict

import numpy as np

from .analyze import (
    _BOOLEAN_FEATURES,
    _NUMERIC_FEATURES,
    _SECTOR_NAME_MAP,
    _apply_criteria,
    _match_sector_prefix,
)
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


# Inverse of _SECTOR_NAME_MAP: sector name → criteria key prefix
_SECTOR_PREFIX_MAP: dict[str, str] = {v: k for k, v in _SECTOR_NAME_MAP.items()}


def _build_sector_candidate_gates(prime_rows: list[dict]) -> list[tuple[str, float | int]]:
    """Return sector-scoped (criteria_key, value) pairs for sectors with ≥5 prime rows.

    Generates {sector_prefix}_{feature}_{min|max} candidates by sweeping
    _SEARCH_NUMERIC_FEATURES at prime-distribution percentiles within each sector.
    These are handled by _apply_criteria's generic sector fallback.
    """
    if len(prime_rows) < 5:
        return []

    by_sector: dict[str, list[dict]] = defaultdict(list)
    for row in prime_rows:
        sector = row.get("sector")
        if sector and sector in _SECTOR_PREFIX_MAP:
            by_sector[sector].append(row)

    candidates: list[tuple[str, float | int]] = []
    for sector, sector_rows in by_sector.items():
        if len(sector_rows) < 5:
            continue
        prefix = _SECTOR_PREFIX_MAP[sector]
        for feat in _SEARCH_NUMERIC_FEATURES:
            vals = [r[feat] for r in sector_rows if r.get(feat) is not None]
            if len(vals) < 5:
                continue
            seen: set[float] = set()
            for pct in _PERCENTILE_STEPS:
                t = round(float(np.percentile(vals, pct)), 6)
                if t in seen:
                    continue
                seen.add(t)
                candidates.append((f"{prefix}_{feat}_min", t))
                candidates.append((f"{prefix}_{feat}_max", t))

    return candidates


# Integer code for each sector — used in fast numpy sector comparisons.
_SECTOR_CODES: dict[str, int] = {
    sector: i for i, sector in enumerate(sorted(_SECTOR_NAME_MAP.values()))
}

# Options-derived gate keys whose source field differs from the key name.
# Value is (source_field, null_behavior): "min" means null fails, "max" means null passes.
_OPTIONS_KEY_FIELD: dict[str, tuple[str, str]] = {
    "options_iv_min": ("best_iv", "min"),
    "iv_rv_min": ("iv_rv", "min"),
    "pcr_vol_max": ("pcr_vol", "max"),
}

# All feature columns needed in the evaluation matrix.
_EVAL_FEATURES: list[str] = (
    _SEARCH_NUMERIC_FEATURES + ["best_iv", "pcr_vol", "iv_rv"] + list(_BOOLEAN_FEATURES)
)


def _build_row_arrays(
    rows: list[dict],
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Pre-build numpy arrays for vectorized gate evaluation in the greedy inner loop.

    Returns feat_arrs (feature→float array, NaN for None), sector_arr (int, -1 for unknown),
    and is_prime_arr (bool).
    """
    feat_arrs: dict[str, np.ndarray] = {
        feat: np.array(
            [r.get(feat) if r.get(feat) is not None else float("nan") for r in rows],
            dtype=float,
        )
        for feat in _EVAL_FEATURES
    }
    sector_arr = np.array(
        [_SECTOR_CODES.get(r.get("sector", ""), -1) for r in rows],
        dtype=np.int8,
    )
    is_prime_arr = np.array([r.get("is_prime") == 1 for r in rows], dtype=bool)
    return feat_arrs, sector_arr, is_prime_arr


def _gate_mask(
    key: str,
    val: float | int,
    feat_arrs: dict[str, np.ndarray],
    sector_arr: np.ndarray,
) -> np.ndarray:
    """Return a boolean array of rows that pass the single gate {key: val}.

    Replicates _apply_criteria semantics for the gate types the greedy search generates:
    - _min gates: null fails  (NaN row does not meet the floor)
    - _max gates: null passes (NaN row is not filtered out by the ceiling)
    - sector gates: non-sector rows always pass; sector rows get the _min/_max rule
    - options keys: mapped to their source field via _OPTIONS_KEY_FIELD
    """
    n = sector_arr.shape[0]
    _empty = np.full(n, np.nan)

    # Options-derived keys with non-standard names
    if key in _OPTIONS_KEY_FIELD:
        src, null_behavior = _OPTIONS_KEY_FIELD[key]
        arr = feat_arrs.get(src, _empty)
        if null_behavior == "min":
            return ~np.isnan(arr) & (arr >= val)
        return np.where(np.isnan(arr), True, arr <= val)

    # Sector-scoped gate: {prefix}_{feature}_{min|max}
    _sm = _match_sector_prefix(key)
    if _sm is not None:
        _, sector_name, remainder = _sm
        s_code = _SECTOR_CODES.get(sector_name, -1)
        non_sector: np.ndarray = sector_arr != s_code
        if remainder.endswith("_min"):
            arr = feat_arrs.get(remainder[:-4], _empty)
            return non_sector | (~np.isnan(arr) & (arr >= val))
        if remainder.endswith("_max"):
            arr = feat_arrs.get(remainder[:-4], _empty)
            return non_sector | np.where(np.isnan(arr), True, arr <= val)
        arr = feat_arrs.get(remainder, _empty)
        return non_sector | (arr == val)

    # Standard generic gates
    if key.endswith("_min"):
        arr = feat_arrs.get(key[:-4], _empty)
        return ~np.isnan(arr) & (arr >= val)
    if key.endswith("_max"):
        arr = feat_arrs.get(key[:-4], _empty)
        return np.where(np.isnan(arr), True, arr <= val)
    # Boolean exact match (e.g. sma50_above_sma150 = 1)
    arr = feat_arrs.get(key, _empty)
    return arr == val


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

    Uses numpy vectorized gate evaluation — each candidate is applied as a single
    boolean array operation over all rows rather than a Python loop per row.
    """
    total_prime = sum(1 for r in rows if r.get("is_prime") == 1)
    if total_prime == 0:
        return {"criteria": {}, "precision": 0.0, "recall": 0.0, "steps": []}

    feat_arrs, sector_arr, is_prime_arr = _build_row_arrays(rows)
    active_mask = np.ones(len(rows), dtype=bool)

    criteria: dict = {}
    steps: list[StepTrace] = []
    current_precision = total_prime / len(rows)

    for _ in range(max_steps):
        active_idx = np.where(active_mask)[0]
        prime_in_pool = [rows[i] for i in active_idx if is_prime_arr[i]]
        gate_candidates = _build_candidate_gates(prime_in_pool) + _build_sector_candidate_gates(
            prime_in_pool
        )

        best_key: str | None = None
        best_val: float | int | None = None
        best_precision = current_precision + 0.005  # must beat by >0.5pp
        best_recall = 0.0

        for key, val in gate_candidates:
            if key in criteria:
                continue
            gate_mask = _gate_mask(key, val, feat_arrs, sector_arr)
            filtered = active_mask & gate_mask
            filtered_count = int(filtered.sum())
            if filtered_count == 0:
                continue
            tp = int((filtered & is_prime_arr).sum())
            prec = tp / filtered_count
            rec = tp / total_prime
            if rec < recall_floor:
                continue
            if prec > best_precision:
                best_key, best_val = key, val
                best_precision = prec
                best_recall = rec

        if best_key is None:
            break

        active_mask = active_mask & _gate_mask(best_key, best_val, feat_arrs, sector_arr)
        current_precision = best_precision
        criteria[best_key] = best_val
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

    active_prime = int((active_mask & is_prime_arr).sum())
    return {
        "criteria": criteria,
        "precision": round(current_precision, 4),
        "recall": round(active_prime / total_prime, 4),
        "steps": steps,
    }


# Explicit mapping for options-derived feature names that don't follow the
# generic field_name + _min/_max → criteria_key convention.
_TREE_KEY_MAP: dict[tuple[str, str], str] = {
    ("best_iv", ">"): "options_iv_min",
    ("iv_rv", ">"): "iv_rv_min",
    ("pcr_vol", "<="): "pcr_vol_max",
}

_OPTIONS_INVERSE_SKIP: frozenset[tuple[str, str]] = frozenset(
    {
        ("best_iv", "<="),
        ("iv_rv", "<="),
        ("pcr_vol", ">"),
    }
)

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
        elif (feat, direction) in _OPTIONS_INVERSE_SKIP:
            pass
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
    """Build (x, y, feature_names) for sklearn. Nulls filled with column median."""
    numeric_cols = _SEARCH_NUMERIC_FEATURES + ["best_iv", "pcr_vol", "iv_rv"]
    bool_cols = list(_BOOLEAN_FEATURES)
    feature_names = numeric_cols + bool_cols

    x_list = []
    y_list = []
    for row in rows:
        x_row = [row.get(f) for f in numeric_cols]
        x_row += [1.0 if row.get(f) == 1 else 0.0 for f in bool_cols]
        x_list.append(x_row)
        y_list.append(1 if row.get("is_prime") == 1 else 0)

    x = np.array(x_list, dtype=float)
    y = np.array(y_list, dtype=int)

    for col_idx in range(x.shape[1]):
        col = x[:, col_idx]
        mask = np.isnan(col)
        if mask.any():
            median_val = float(np.nanmedian(col))
            x[mask, col_idx] = median_val if not np.isnan(median_val) else 0.0

    return x, y, feature_names


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

    x, y, feature_names = _build_feature_matrix(rows)
    clf = DecisionTreeClassifier(max_depth=5, class_weight="balanced", random_state=42)
    clf.fit(x, y)

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
            results.append(
                {
                    "criteria": crit,
                    "precision": report["precision"],
                    "recall": report["recall"],
                }
            )

    return sorted(results, key=lambda c: c["precision"], reverse=True)


def _print_report(result: GateSearchResult, approach: str = "ab") -> None:
    v42 = result["v42_baseline"]
    print(f"\n{'=' * 60}")
    print(f"GATE SEARCH RESULTS — {result['generated']}")
    print(f"Recall floor: {result['recall_floor']:.0%}")
    if v42.get("precision") is not None:
        print(f"\nV42 baseline:  precision={v42['precision']:.1%}  recall={v42['recall']:.1%}")

    if "a" in approach:
        a = result["approach_a"]
        print("\nApproach A (greedy):")
        print(
            f"  precision={a['precision']:.1%}  recall={a['recall']:.1%}  ({len(a['steps'])} gates)"
        )
        for i, step in enumerate(a["steps"], 1):
            prec = step["precision"]
            rec = step["recall"]
            print(f"  {i}. {step['gate']}={step['value']}  →  prec={prec:.1%} rec={rec:.1%}")

    if "b" in approach:
        b = result["approach_b"]
        if b:
            top_n = min(5, len(b))
            print(f"\nApproach B (tree) — top {top_n} candidates:")
            for cand in b[:5]:
                prec = cand["precision"]
                rec = cand["recall"]
                print(f"  precision={prec:.1%}  recall={rec:.1%}  {cand['criteria']}")
        else:
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
        enriched.append(
            {
                **row,
                "best_iv": iv,
                "pcr_vol": opt.get("pcr_vol"),
                "delta": opt.get("delta"),
                "open_interest": opt.get("open_interest"),
                "iv_rv": (iv / rv) if (iv is not None and rv is not None and rv > 0) else None,
            }
        )

    total_prime = sum(1 for r in enriched if r.get("is_prime") == 1)
    logger.info(
        "Loaded %d rows (%d prime, %d control)",
        len(enriched),
        total_prime,
        len(enriched) - total_prime,
    )

    v42_baseline: dict = {"precision": None, "recall": None}
    v42_path = Path("data/v42_criteria.json")
    if v42_path.exists():
        v42_criteria = json.loads(v42_path.read_text())
        v42_report = validate_criteria(v42_criteria, features=enriched)
        v42_baseline = {"precision": v42_report["precision"], "recall": v42_report["recall"]}
        logger.info(
            "V42 baseline: precision=%.1f%% recall=%.1f%%",
            v42_baseline["precision"] * 100,
            v42_baseline["recall"] * 100,
        )

    empty_greedy: GreedySearchResult = {
        "criteria": {},
        "precision": 0.0,
        "recall": 0.0,
        "steps": [],
    }
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
        tree_results = run_tree_search(
            enriched, baseline_precision=baseline_prec, recall_floor=recall_floor
        )
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

    _print_report(result, approach)
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Automated gate search for algo detective")
    parser.add_argument(
        "--approach",
        choices=["a", "b", "ab"],
        default="ab",
        help="Which search approach to run (default: ab)",
    )
    parser.add_argument(
        "--recall-floor",
        type=float,
        default=0.30,
        help="Minimum recall fraction to enforce (default: 0.30)",
    )
    args = parser.parse_args()
    run_gate_search(recall_floor=args.recall_floor, approach=args.approach)
