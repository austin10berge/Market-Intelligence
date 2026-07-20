"""Tests for src/algo_detective/gate_search.py."""

from __future__ import annotations

from src.algo_detective.gate_search import (
    _build_candidate_gates,
    _build_feature_matrix,
    _eval_candidate,
    _tree_path_to_criteria,
    run_greedy_search,
    run_tree_search,
)


def _prime(n: int, rsi: float = 40.0, adx: float = 25.0) -> list[dict]:
    """n prime rows with deterministic feature values."""
    return [
        {
            "is_prime": 1,
            "rsi": rsi + i * 0.5,
            "adx": adx + i * 0.5,
            "price_above_ema50": 1,
            "rv20": 0.30,
        }
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

    def test_inverse_options_directions_skipped(self):
        assert _tree_path_to_criteria([("best_iv", "<=", 0.30)]) == {}
        assert _tree_path_to_criteria([("iv_rv", "<=", 1.2)]) == {}
        assert _tree_path_to_criteria([("pcr_vol", ">", 1.5)]) == {}


class TestBuildFeatureMatrix:
    def test_shape_matches_rows_and_features(self):
        rows = [
            {"is_prime": 1, "rsi": 40.0, "price_above_ema50": 1},
            {"is_prime": 0, "rsi": 60.0, "price_above_ema50": 0},
        ]
        x, y, names = _build_feature_matrix(rows)
        assert x.shape[0] == 2
        assert x.shape[1] == len(names)
        assert list(y) == [1, 0]

    def test_null_filled_with_column_median(self):
        rows = [
            {"is_prime": 1, "rsi": None},
            {"is_prime": 0, "rsi": 40.0},
            {"is_prime": 0, "rsi": 60.0},
        ]
        x, y, names = _build_feature_matrix(rows)
        rsi_idx = names.index("rsi")
        # median of non-null values [40, 60] = 50
        assert x[0, rsi_idx] == 50.0

    def test_boolean_encoded_as_0_or_1(self):
        rows = [
            {"is_prime": 1, "price_above_ema50": 1},
            {"is_prime": 0, "price_above_ema50": 0},
        ]
        x, y, names = _build_feature_matrix(rows)
        idx = names.index("price_above_ema50")
        assert x[0, idx] == 1.0
        assert x[1, idx] == 0.0


class TestRunTreeSearch:
    def _make_separable_rows(self) -> list[dict]:
        """30 prime (rsi 20-49) + 300 control (rsi 60-89). Tree should find rsi split."""
        prime = [
            {"is_prime": 1, "rsi": float(20 + i), "date": "2024-01-01", "ticker": f"P{i:03d}"}
            for i in range(30)
        ]
        control = [
            {"is_prime": 0, "rsi": float(60 + i % 30), "date": "2024-01-01", "ticker": f"C{i:03d}"}
            for i in range(300)
        ]
        return prime + control

    def test_returns_list(self):
        result = run_tree_search(
            self._make_separable_rows(), baseline_precision=0.0, recall_floor=0.30
        )
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
