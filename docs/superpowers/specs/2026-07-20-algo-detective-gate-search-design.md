# Algo Detective — Automated Gate Search — Design Spec

**Date:** 2026-07-20
**Status:** Approved

---

## Background

35 sessions of manual KS-statistic gate tuning produced the current V42 criteria set. V42 scores
22.2% precision / 30.5% recall against the labeled `detective_features` dataset (334 prime rows,
134,241 control rows across 78 dates and 1,756 tickers). Each gate was added by reading
`analyze.py`'s KS rankings and hand-picking thresholds — a process that works but can't explore
the full threshold space systematically and is blind to interaction effects between features.

This spec adds `src/algo_detective/gate_search.py`, a two-approach automated search that replaces
that manual loop. **Approach A** (greedy stepwise) sweeps all features and thresholds
systematically to build a precision-optimal gate chain. **Approach B** (decision tree) fits a
shallow classifier on the same labeled data to surface interaction effects the greedy search
misses. Both run from one CLI invocation and produce output in the existing criteria-dict format,
compatible with `validate_criteria` and the CSP scanner without any changes to those consumers.

**Optimization target:** maximize precision subject to recall ≥ 30%. The 30% floor matches V42's
current recall and prevents degenerate high-precision/zero-recall solutions. Universal gates only
— sector-scoped rules (e.g. `financials_market_cap_b_min`) are excluded from the search space;
they remain in V42 exactly as hand-tuned and can be layered on top of search results manually.

---

## Goal

Produce a criteria dict with precision materially above V42's 22.2% baseline while maintaining
≥ 30% recall, entirely automatically, in a single CLI run.

---

## Non-Goals

- Sector-scoped gate discovery — excluded from search space, too few prime samples per sector.
- Delta-based gates — delta collection started 2026-07-20; no historical coverage yet. The
  search space includes `delta` but it will be skipped automatically when all rows are null.
  Re-run the search once several weeks of delta history accumulate.
- Portfolio-level optimization or P&L backtesting — `signal_backtest.py` covers that once a
  candidate criteria dict is in hand.
- Hyperparameter sweeping (target_delta, DTE) — out of scope here.

---

## Architecture

```
src/algo_detective/
└── gate_search.py    (NEW — Approach A greedy search, Approach B tree search, CLI)

tests/
└── test_algo_detective_gate_search.py    (NEW)
```

No existing files modified. `validate_criteria` and `_apply_criteria` are imported and called
as-is — the search is built on top of them, not woven into them.

---

## Components

### Data setup (shared by A and B)

`run_gate_search` loads `get_all_features()`, joins `get_options_index()` (adding `best_iv`,
`pcr_vol`, `delta`, `open_interest` per `(date, ticker)`), and computes `iv_rv` inline
(`best_iv / rv20` where both non-null, else `None`). The resulting enriched row list flows
into `_apply_criteria`'s existing handlers without any changes to that function.

### `_build_candidate_gates(rows: list[dict]) -> list[tuple[str, float | int]]`

Produces every `(key, value)` pair to try at a greedy step:

- **Numeric features** (`_NUMERIC_FEATURES` from `analyze.py` plus `adr20_pct`): sweep the
  5th–95th percentile of the *prime* distribution in steps of 5 (19 points per feature,
  calibrated to where prime data sits). For each percentile point, produce both
  `{feature}_min = threshold` and `{feature}_max = threshold`.
- **Boolean features** (`_BOOLEAN_FEATURES` from `analyze.py`): produce `{feature} = 1` only.
- **Options-derived keys**: `options_iv_min`, `iv_rv_min`, `pcr_vol_max` swept at prime
  percentiles the same way as numeric features, using the enriched rows' corresponding fields.

Total candidate pool: ~45 features × 38 (threshold × direction) + 19 booleans + 57 options
candidates ≈ ~1,800 candidates per step. With ~10 steps max, the full greedy run is ~18k
`_apply_criteria` evaluations — completes in a few seconds on the current dataset size.

### `run_greedy_search(rows, recall_floor, max_steps) -> GreedySearchResult`

```
criteria = {}
trace = []
for step in range(max_steps):
    best_gate = None
    best_precision = current_precision
    for (key, val) in _build_candidate_gates(active_rows):
        candidate = {**criteria, key: val}
        result = validate_criteria(candidate, features=rows)
        if result["recall"] >= recall_floor and result["precision"] > best_precision + 0.005:
            best_gate = (key, val)
            best_precision = result["precision"]
    if best_gate is None:
        break  # no gate improves by >0.5pp without violating floor
    criteria[best_gate[0]] = best_gate[1]
    trace.append({"gate": best_gate[0], "value": best_gate[1],
                  "precision": best_precision, "recall": result["recall"]})
```

`active_rows` at each step is the rows that pass `criteria` so far — threshold sweep is over
the *remaining* prime distribution, keeping thresholds relevant to the filtered population.
Returns the final `criteria` dict, the step trace, and final precision/recall.

### `run_tree_search(rows, baseline_precision, recall_floor) -> list[TreeCandidate]`

1. Build feature matrix: all numeric + boolean features from the enriched rows, null-filled
   with column median (numeric) or 0 (boolean). Target: `is_prime`.
2. Fit `DecisionTreeClassifier(max_depth=5, class_weight='balanced', random_state=42)`.
3. Walk all leaf nodes. For each leaf:
   - Extract root-to-leaf path conditions: `(feature_name, direction, threshold)`.
   - Translate: `feature ≤ threshold` → `{feature}_max` when prime mean < threshold;
     `feature > threshold` → `{feature}_min` when prime mean > threshold;
     boolean split at 0.5 → `{feature: 1}` for the `> 0.5` branch.
   - Skip leaves with < 10 samples or sklearn-estimated precision < `baseline_precision`.
4. Run each translated candidate through `validate_criteria` (authoritative ground truth —
   sklearn's leaf precision is approximate due to `class_weight`).
5. Keep candidates with precision ≥ `baseline_precision` and recall ≥ `recall_floor`.
6. Return sorted by precision descending.

### `run_gate_search(recall_floor=0.30, approach="ab") -> GateSearchResult`

Top-level orchestrator. Loads data, runs A, optionally runs B with A's precision as the
baseline, saves output, prints report.

### CLI

```bash
python -m src.algo_detective.gate_search                   # full A+B
python -m src.algo_detective.gate_search --approach a      # greedy only
python -m src.algo_detective.gate_search --approach b      # tree only
python -m src.algo_detective.gate_search --recall-floor 0.40
```

---

## Output

Saved to `data/detective/gate_search_YYYY-MM-DD.json`:

```json
{
  "generated": "2026-07-20",
  "recall_floor": 0.30,
  "v42_baseline": {"precision": 0.222, "recall": 0.305},
  "approach_a": {
    "criteria": {"adx_min": 20, "rv20_max": 0.38, ...},
    "precision": 0.42,
    "recall": 0.31,
    "steps": [
      {"gate": "adx_min", "value": 20, "precision": 0.28, "recall": 0.68},
      {"gate": "rv20_max", "value": 0.38, "precision": 0.35, "recall": 0.45},
      ...
    ]
  },
  "approach_b": [
    {"criteria": {"adx_min": 18, "pct_from_52wk_high_max": 10, ...},
     "precision": 0.45, "recall": 0.30},
    ...
  ]
}
```

Printed report includes V42 baseline, A's step trace, and top-5 B candidates.

---

## Data Flow

```
get_all_features()          → 134,575 rows (334 prime / 134,241 control)
     ↓ join get_options_index()
enriched rows               → adds best_iv, pcr_vol, delta, open_interest, iv_rv
     ↓
Approach A: _build_candidate_gates → greedy loop → criteria dict + trace
     ↓ precision baseline from A
Approach B: feature matrix → DecisionTreeClassifier → leaf extraction → validate_criteria
     ↓
GateSearchResult → stdout report + data/detective/gate_search_YYYY-MM-DD.json
```

---

## Error Handling

- Features with all-null values in the prime population are skipped by `_build_candidate_gates`
  (can't compute percentiles) — this is how `delta` is naturally excluded until coverage builds.
- If Approach A produces no improvement over base rate (degenerate case: every candidate gate
  violates the recall floor at step 1), it returns an empty criteria dict with a warning —
  does not fall back to V42 or raise.
- `class_weight='balanced'` handles the 334:134,241 imbalance for the tree; if sklearn is not
  available in the pipeline image, Approach B logs a warning and skips rather than crashing.

---

## Testing

- **`_build_candidate_gates`**: small fixture of 5 prime rows with known feature values — assert
  numeric features produce both `_min` and `_max` candidates; boolean features produce only `= 1`;
  a feature with all-null prime values is absent from the output.
- **Recall floor enforcement**: synthetic fixture where the single best gate drops recall to 25% —
  assert `run_greedy_search` stops at step 0 and returns an empty criteria dict.
- **Greedy improvement threshold**: fixture where the best available gate improves precision by
  only 0.3pp — assert search stops (does not add a gate that doesn't clear the 0.5pp bar).
- **Tree leaf translator**: manually construct a sklearn decision path (3-node path, known
  features and thresholds) and assert the translated criteria dict matches expectations.
- **Integration**: run `run_greedy_search` on a 10-prime / 100-control / 3-feature synthetic
  fixture — assert output precision > base rate (10/110) and recall ≥ floor.

---

## Dependencies

`sklearn` (`scikit-learn`) for Approach B's `DecisionTreeClassifier`. Already an explicit
dependency in `pyproject.toml` (`scikit-learn>=1.5`, confirmed present in image as 1.9.0).
No new dependencies needed.
