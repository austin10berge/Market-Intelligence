from __future__ import annotations

from src.algo_detective.analyze import find_thresholds, rank_features


def _make_features(n_prime=50, n_control=500):
    """Synthetic feature rows where prime tickers cluster at RSI 50-65, control at 30-80."""
    import random
    random.seed(99)
    rows = []
    for i in range(n_prime):
        rows.append({
            "is_prime": 1,
            "rsi": random.uniform(50, 65),
            "adx": random.uniform(22, 38),
            "price_above_ema50": 1,
            "ema20_above_ema50": 1,
            "rv20": random.uniform(0.28, 0.45),
            "bb_pct_b": random.uniform(0.45, 0.75),
            "price_above_ema200": 1,
            "volume_ratio": random.uniform(0.9, 1.8),
            "pct_from_52wk_high": random.uniform(1, 10),
        })
    for i in range(n_control):
        rows.append({
            "is_prime": 0,
            "rsi": random.uniform(25, 75),
            "adx": random.uniform(10, 50),
            "price_above_ema50": random.randint(0, 1),
            "ema20_above_ema50": random.randint(0, 1),
            "rv20": random.uniform(0.15, 0.80),
            "bb_pct_b": random.uniform(0.1, 0.9),
            "price_above_ema200": random.randint(0, 1),
            "volume_ratio": random.uniform(0.3, 3.0),
            "pct_from_52wk_high": random.uniform(0, 40),
        })
    return rows


def test_rank_features_returns_sorted_by_ks():
    rows = _make_features()
    rankings = rank_features(rows)
    assert len(rankings) > 0
    ks_values = [r["ks_stat"] for r in rankings]
    assert ks_values == sorted(ks_values, reverse=True)


def test_rank_features_includes_required_fields():
    rows = _make_features()
    rankings = rank_features(rows)
    for r in rankings:
        assert "feature" in r
        assert "ks_stat" in r
        assert "prime_mean" in r
        assert "control_mean" in r


def test_discriminating_features_rank_high():
    rows = _make_features()
    rankings = rank_features(rows)
    top_features = [r["feature"] for r in rankings[:5]]
    # RSI, bb_pct_b, or adx should appear in top — they were given tighter distributions
    assert any(f in top_features for f in ["rsi", "bb_pct_b", "adx"])


def test_find_thresholds_returns_criteria_with_scores():
    rows = _make_features()
    candidates = find_thresholds(rows, top_n=5)
    assert len(candidates) > 0
    for c in candidates:
        assert "criteria" in c
        assert "precision" in c
        assert "recall" in c
        assert 0.0 <= c["precision"] <= 1.0
        assert 0.0 <= c["recall"] <= 1.0


def test_find_thresholds_precision_focus():
    rows = _make_features()
    candidates = find_thresholds(rows, top_n=5)
    # Best candidate should have reasonable precision and recall
    best = max(candidates, key=lambda c: c["precision"])
    assert best["precision"] >= 0.5
    assert best["recall"] >= 0.7
