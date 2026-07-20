"""Tests for src/algo_detective/gate_search.py."""

from __future__ import annotations

from src.algo_detective.gate_search import _build_candidate_gates


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
