from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import ks_2samp

from .store import get_all_features

logger = logging.getLogger(__name__)

_NUMERIC_FEATURES = [
    "rsi", "adx", "rv20", "bb_pct_b", "bb_width_pct", "atr_pct",
    "volume_ratio", "roc20", "macd_histogram", "pct_from_52wk_high",
    "price_vs_ema20_pct", "price_vs_ema50_pct", "price_vs_ema150_pct", "price_vs_ema200_pct",
    "price_vs_sma20_pct", "price_vs_sma50_pct", "price_vs_sma150_pct", "price_vs_sma200_pct",
    # Fundamentals
    "market_cap_b", "beta", "forward_pe", "peg_ratio",
    "revenue_growth", "earnings_growth", "debt_to_equity", "dividend_yield", "fcf",
]

_BOOLEAN_FEATURES = [
    "price_above_ema20", "price_above_ema50", "price_above_ema150", "price_above_ema200",
    "price_above_sma20", "price_above_sma50", "price_above_sma150", "price_above_sma200",
    "ema20_above_ema50", "ema50_above_ema150", "ema50_above_ema200", "ema150_above_ema200",
    "sma20_above_sma50", "sma50_above_sma150", "sma50_above_sma200", "sma150_above_sma200",
    "price_above_bb_middle", "price_above_bb_upper", "price_below_bb_lower",
]


def rank_features(features: list[dict]) -> list[dict]:
    """Rank all features by KS statistic (prime vs control distribution separation)."""
    prime = [f for f in features if f["is_prime"] == 1]
    control = [f for f in features if f["is_prime"] == 0]
    if not prime or not control:
        return []

    rankings = []

    for feat in _NUMERIC_FEATURES:
        p_vals = [f[feat] for f in prime if f.get(feat) is not None]
        c_vals = [f[feat] for f in control if f.get(feat) is not None]
        if len(p_vals) < 5 or len(c_vals) < 5:
            continue
        stat, pval = ks_2samp(p_vals, c_vals)
        rankings.append({
            "feature": feat,
            "ks_stat": round(float(stat), 4),
            "p_value": float(pval),
            "prime_mean": round(float(np.mean(p_vals)), 4),
            "control_mean": round(float(np.mean(c_vals)), 4),
            "type": "numeric",
        })

    for feat in _BOOLEAN_FEATURES:
        p_vals = [f[feat] for f in prime if f.get(feat) is not None]
        c_vals = [f[feat] for f in control if f.get(feat) is not None]
        if len(p_vals) < 5 or len(c_vals) < 5:
            continue
        p_rate = float(np.mean(p_vals))
        c_rate = float(np.mean(c_vals))
        # Use absolute difference as the KS proxy for booleans
        stat = abs(p_rate - c_rate)
        rankings.append({
            "feature": feat,
            "ks_stat": round(stat, 4),
            "p_value": None,
            "prime_mean": round(p_rate, 4),
            "control_mean": round(c_rate, 4),
            "type": "boolean",
        })

    return sorted(rankings, key=lambda r: r["ks_stat"], reverse=True)


def _apply_criteria(row: dict, criteria: dict) -> bool:
    """Return True if row satisfies all criteria.

    Standard keys: KEY_min / KEY_max apply floor/ceiling to feature KEY.
      _min: NULL fails; _max: NULL passes.
    Integer value keys: exact match required.

    Special sector-scoped keys:
    - options_iv_min: global IV floor (NULL best_iv fails)
    - financials_market_cap_b_min: market_cap_b floor for Financial Services only
    - technology_fcf_min: fcf floor for Technology only
    - {sector}_iv_min: IV floor for that sector (NULL fails); sectors: industrials,
      consumer_cyclical, technology, healthcare, energy, basic_materials, utilities
    - consumer_defensive_iv_max: IV ceiling for Consumer Defensive (NULL passes)
    - real_estate_block: if truthy, exclude all Real Estate rows
    - communication_services_market_cap_b_min: mcap floor for Communication Services only
    - iv_rv_min: minimum IV/RV20 ratio (NULL best_iv or rv20 fails)
    """
    for key, val in criteria.items():
        if key == "options_iv_min":
            iv = row.get("best_iv")
            if iv is None or iv < val:
                return False
        elif key == "financials_market_cap_b_min":
            if row.get("sector") == "Financial Services":
                if row.get("market_cap_b") is None or row["market_cap_b"] < val:
                    return False
        elif key == "technology_fcf_min":
            if row.get("sector") == "Technology":
                if row.get("fcf") is None or row["fcf"] < val:
                    return False
        elif key == "industrials_iv_min":
            if row.get("sector") == "Industrials":
                iv = row.get("best_iv")
                if iv is None or iv < val:
                    return False
        elif key == "consumer_cyclical_iv_min":
            if row.get("sector") == "Consumer Cyclical":
                iv = row.get("best_iv")
                if iv is None or iv < val:
                    return False
        elif key == "technology_iv_min":
            if row.get("sector") == "Technology":
                iv = row.get("best_iv")
                if iv is None or iv < val:
                    return False
        elif key == "healthcare_iv_min":
            if row.get("sector") == "Healthcare":
                iv = row.get("best_iv")
                if iv is None or iv < val:
                    return False
        elif key == "energy_iv_min":
            if row.get("sector") == "Energy":
                iv = row.get("best_iv")
                if iv is None or iv < val:
                    return False
        elif key == "consumer_defensive_iv_max":
            if row.get("sector") == "Consumer Defensive":
                iv = row.get("best_iv")
                if iv is not None and iv > val:
                    return False
        elif key == "real_estate_block":
            if val and row.get("sector") == "Real Estate":
                return False
        elif key == "basic_materials_iv_min":
            if row.get("sector") == "Basic Materials":
                iv = row.get("best_iv")
                if iv is None or iv < val:
                    return False
        elif key == "utilities_iv_min":
            if row.get("sector") == "Utilities":
                iv = row.get("best_iv")
                if iv is None or iv < val:
                    return False
        elif key == "communication_services_market_cap_b_min":
            if row.get("sector") == "Communication Services":
                if row.get("market_cap_b") is None or row["market_cap_b"] < val:
                    return False
        elif key == "iv_rv_min":
            iv = row.get("best_iv")
            rv = row.get("rv20")
            if iv is None or rv is None or rv == 0 or iv / rv < val:
                return False
        elif key == "financials_volume_ratio_max":
            if row.get("sector") == "Financial Services":
                vr = row.get("volume_ratio")
                if vr is not None and vr > val:
                    return False
        elif key == "financials_adx_min":
            if row.get("sector") == "Financial Services":
                adx = row.get("adx")
                if adx is None or adx < val:
                    return False
        elif key == "technology_volume_ratio_max":
            if row.get("sector") == "Technology":
                vr = row.get("volume_ratio")
                if vr is not None and vr > val:
                    return False
        elif key == "technology_market_cap_b_min":
            if row.get("sector") == "Technology":
                if row.get("market_cap_b") is None or row["market_cap_b"] < val:
                    return False
        elif key == "financials_rsi_max":
            if row.get("sector") == "Financial Services":
                rsi = row.get("rsi")
                if rsi is not None and rsi > val:
                    return False
        elif key == "consumer_cyclical_rsi_max":
            if row.get("sector") == "Consumer Cyclical":
                rsi = row.get("rsi")
                if rsi is not None and rsi > val:
                    return False
        elif key == "technology_rsi_max":
            if row.get("sector") == "Technology":
                rsi = row.get("rsi")
                if rsi is not None and rsi > val:
                    return False
        elif key.endswith("_min"):
            feat = key[:-4]
            if row.get(feat) is None or row[feat] < val:
                return False
        elif key.endswith("_max"):
            feat = key[:-4]
            feat_val = row.get(feat)
            if feat_val is not None and feat_val > val:
                return False
        elif isinstance(val, int):
            expected = int(val)
            if row.get(key) != expected:
                return False
    return True


def _score_criteria(features: list[dict], criteria: dict) -> dict:
    prime = [f for f in features if f["is_prime"] == 1]
    control = [f for f in features if f["is_prime"] == 0]
    tp = sum(1 for f in prime if _apply_criteria(f, criteria))
    fp = sum(1 for f in control if _apply_criteria(f, criteria))
    fn = len(prime) - tp
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / len(prime) if prime else 0.0
    return {
        "criteria": criteria,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }


def find_thresholds(features: list[dict], top_n: int = 10) -> list[dict]:
    """Grid-search thresholds for top-ranked features. Returns criteria candidates sorted by precision."""
    rankings = rank_features(features)
    top = rankings[:top_n]

    # Build a candidate pool — start with each top feature individually, then combine
    candidates = []

    # Boolean features: just require True for those with higher prime_mean than control_mean
    bool_criteria: dict = {}
    for r in top:
        if r["type"] == "boolean" and r["prime_mean"] > r["control_mean"] + 0.15:
            bool_criteria[r["feature"]] = True

    if bool_criteria:
        candidates.append(_score_criteria(features, bool_criteria))

    # Numeric features: grid-search percentile-based min/max thresholds
    prime = [f for f in features if f["is_prime"] == 1]
    for r in [x for x in top if x["type"] == "numeric"]:
        feat = r["feature"]
        p_vals = sorted(f[feat] for f in prime if f.get(feat) is not None)
        if len(p_vals) < 10:
            continue
        p10 = float(np.percentile(p_vals, 10))
        p90 = float(np.percentile(p_vals, 90))
        # Try with just this numeric constraint plus the bool constraints
        crit = {**bool_criteria, f"{feat}_min": round(p10, 2), f"{feat}_max": round(p90, 2)}
        candidates.append(_score_criteria(features, crit))

    # Combined: add top-2 numeric constraints together
    num_top = [r for r in top if r["type"] == "numeric"][:2]
    if len(num_top) == 2:
        crit = dict(bool_criteria)
        for r in num_top:
            feat = r["feature"]
            p_vals = sorted(f[feat] for f in prime if f.get(feat) is not None)
            if len(p_vals) >= 10:
                crit[f"{feat}_min"] = round(float(np.percentile(p_vals, 10)), 2)
                crit[f"{feat}_max"] = round(float(np.percentile(p_vals, 90)), 2)
        candidates.append(_score_criteria(features, crit))

    passing = [c for c in candidates if c["recall"] >= 0.7]
    if not passing:
        logger.warning("find_thresholds: no criteria candidate achieved recall >= 0.7")
    return sorted(passing, key=lambda c: (c["precision"], c["recall"]), reverse=True)


def run_analyze(output_dir: Path | None = None) -> None:
    if output_dir is None:
        output_dir = Path(__file__).parent.parent.parent / "data" / "detective"
    output_dir.mkdir(parents=True, exist_ok=True)

    features = get_all_features()
    prime_count = sum(1 for f in features if f["is_prime"] == 1)
    control_count = len(features) - prime_count
    logger.info("Analyzing %d prime + %d control rows", prime_count, control_count)

    rankings = rank_features(features)
    candidates = find_thresholds(features, top_n=10)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_prime": prime_count,
        "total_control": control_count,
        "feature_rankings": rankings,
        "criteria_candidates": [{"rank": i + 1, **c} for i, c in enumerate(candidates)],
    }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = output_dir / f"analysis_{today}.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info("Analysis written to %s", out_path)

    print("\n=== Top 10 discriminating features ===")
    for i, r in enumerate(rankings[:10], 1):
        print(f"  {i:2}. {r['feature']:<30} KS={r['ks_stat']:.3f}  prime_mean={r['prime_mean']:.3f}  control_mean={r['control_mean']:.3f}")

    print("\n=== Top 3 criteria candidates ===")
    for i, c in enumerate(candidates[:3], 1):
        print(f"  {i}. precision={c['precision']:.3f}  recall={c['recall']:.3f}  TP={c['true_positives']}  FP={c['false_positives']}")
        print(f"     {c['criteria']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_analyze()
