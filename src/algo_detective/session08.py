"""Session 08 — Narrow universe: gate combinations + ML.

Universe is restricted to only the 74 tickers that ever appear in prime_tickers.csv.
This eliminates structural FPs (tickers the scanner never picks) and sharpens the
discrimination problem to: "which days is each prime ticker actually selected?"

v26 baseline on narrow universe: P=24.3%, R=69.8%, TP=196, FP=610

Key finding from single-gate tests:
  Prime days have LOWER volatility than non-prime days for these same tickers
  (bb_width_pct KS=0.248, rv20 KS=0.236, atr_pct KS=0.204, adr20_pct KS=0.190)

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session08
"""

from __future__ import annotations

import logging
import warnings
from itertools import product

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _apply_criteria
from .store import get_all_features, get_options_index

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

V26 = {
    "sma50_above_sma200": 1,
    "market_cap_b_min": 25,
    "price_vs_ema200_pct_min": 0,
    "price_vs_ema200_pct_max": 42,
    "pct_from_52wk_high_max": 18,
    "rv20_max": 0.45,
    "dividend_yield_max": 2.5,
    "options_iv_min": 0.20,
    "financials_market_cap_b_min": 100,
    "technology_fcf_min": 0.01,
    "industrials_iv_min": 0.30,
    "consumer_cyclical_iv_min": 0.30,
    "healthcare_iv_min": 0.25,
    "real_estate_block": 1,
    "consumer_defensive_iv_max": 0.32,
    "energy_iv_min": 0.38,
    "basic_materials_iv_min": 0.38,
    "utilities_iv_min": 0.50,
    "adx_min": 15,
    "bb_width_pct_min": 4.0,
    "forward_pe_max": 50,
    "communication_services_market_cap_b_min": 50,
    "iv_rv_min": 0.9,
}


def _load_narrow_universe() -> list[dict]:
    """Load only rows where ticker is in the prime set (74 unique tickers)."""
    features = get_all_features()
    prime_tickers = {f["ticker"] for f in features if f["is_prime"] == 1}
    narrow = [f for f in features if f["ticker"] in prime_tickers]
    options_idx = get_options_index()
    return [
        {**f, "best_iv": options_idx.get((f["date"], f["ticker"]), {}).get("best_iv")}
        for f in narrow
    ]


def _score(rows: list[dict], criteria: dict) -> dict:
    prime = [r for r in rows if r["is_prime"] == 1]
    ctrl  = [r for r in rows if r["is_prime"] == 0]
    tp = sum(1 for r in prime if _apply_criteria(r, criteria))
    fp = sum(1 for r in ctrl  if _apply_criteria(r, criteria))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec  = tp / len(prime) if prime else 0.0
    return {"tp": tp, "fp": fp, "precision": round(prec, 4), "recall": round(rec, 4)}


def _ks_within_passed(rows: list[dict], criteria: dict, feat: str) -> tuple[float, float, float]:
    """KS stat for feat among rows that PASS criteria (prime vs ctrl)."""
    passed = [r for r in rows if _apply_criteria(r, criteria)]
    p_vals = [r[feat] for r in passed if r["is_prime"] == 1 and r.get(feat) is not None]
    c_vals = [r[feat] for r in passed if r["is_prime"] == 0 and r.get(feat) is not None]
    if len(p_vals) < 5 or len(c_vals) < 5:
        return 0.0, 0.0, 0.0
    stat, _ = ks_2samp(p_vals, c_vals)
    return stat, float(np.median(p_vals)), float(np.median(c_vals))


def main():
    print("\n=== Session 08 — Narrow Universe Gate Combinations ===\n")

    rows = _load_narrow_universe()
    prime = [r for r in rows if r["is_prime"] == 1]
    ctrl  = [r for r in rows if r["is_prime"] == 0]
    print(f"Narrow universe: {len(prime)} prime, {len(ctrl)} control  ({len(rows)} total)")
    print(f"Unique tickers: {len({r['ticker'] for r in prime})} prime tickers")
    print(f"Unique dates: {len({r['date'] for r in rows})} dates")
    print(f"Class balance: {len(prime)/len(rows)*100:.1f}% positive\n")

    # --- Baseline ---
    base = _score(rows, V26)
    print(f"v26 baseline: P={base['precision']:.1%}  R={base['recall']:.1%}  TP={base['tp']}  FP={base['fp']}\n")

    # --- KS analysis on rows that pass v26 ---
    print("--- Feature discrimination (KS) among v26 survivors ---")
    feat_stats = []
    volatility_feats = [
        "bb_width_pct", "rv20", "atr_pct", "adr20_pct",
        "pct_from_52wk_high", "macd_histogram", "rsi", "adx",
        "volume_ratio", "roc20", "bb_pct_b",
        "price_vs_sma50_pct", "price_vs_ema50_pct",
    ]
    for feat in volatility_feats:
        ks, p_med, c_med = _ks_within_passed(rows, V26, feat)
        feat_stats.append((feat, ks, p_med, c_med))
    feat_stats.sort(key=lambda x: x[1], reverse=True)
    print(f"  {'Feature':<28} {'KS':>6}  {'Prime median':>13}  {'Ctrl median':>12}  Direction")
    for feat, ks, p_med, c_med in feat_stats:
        direction = "prime<ctrl" if p_med < c_med else "prime>ctrl"
        bar = "█" * int(ks * 40)
        print(f"  {feat:<28} {ks:>6.3f}  {p_med:>13.3f}  {c_med:>12.3f}  {direction}  {bar}")

    # --- Systematic gate sweep (added on top of v26) ---
    print("\n--- Gate sweep on top of v26 (narrow universe) ---")

    # Single additional gates
    single_gates = [
        ("bb_width_pct_max",      [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]),
        ("rv20_max",              [0.25, 0.28, 0.30, 0.33, 0.35, 0.38, 0.40]),
        ("atr_pct_max",           [1.5, 2.0, 2.5, 3.0, 3.5]),
        ("adr20_pct_max",         [2.5, 3.0, 3.5, 4.0, 4.5]),
        ("pct_from_52wk_high_max",[8, 10, 12, 14, 15]),
        ("macd_histogram_max",    [0.0, 0.5, 1.0, -0.1]),
        ("rsi_max",               [50, 55, 60, 65]),
    ]

    best_single = []
    print(f"  {'Gate':<35} {'Value':>7}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}")
    for gate_name, values in single_gates:
        gate_best = None
        for val in values:
            criteria = {**V26, gate_name: val}
            s = _score(rows, criteria)
            delta_p = s["precision"] - base["precision"]
            if gate_best is None or s["precision"] > gate_best["precision"]:
                gate_best = {**s, "val": val, "gate": gate_name}
        # Print best value for this gate
        s = gate_best
        delta_p = s["precision"] - base["precision"]
        sign = "+" if delta_p >= 0 else ""
        print(f"  {s['gate']:<35} {s['val']:>7}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}  {sign}{delta_p:.1%}")
        best_single.append(s)

    # Also print detailed sweep for the two most promising dimensions
    print("\n  Detail: bb_width_pct_max sweep")
    print(f"  {'Value':>7}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    for val in [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 18.0]:
        criteria = {**V26, "bb_width_pct_max": val}
        s = _score(rows, criteria)
        print(f"  {val:>7.1f}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}")

    print("\n  Detail: rv20_max sweep")
    print(f"  {'Value':>7}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    for val in [0.22, 0.25, 0.28, 0.30, 0.33, 0.35, 0.38, 0.40]:
        criteria = {**V26, "rv20_max": val}
        s = _score(rows, criteria)
        print(f"  {val:>7.2f}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}")

    # --- Two-gate combinations ---
    print("\n--- Two-gate combinations on top of v26 ---")
    bb_vals   = [12.0, 13.0, 14.0, 15.0]
    rv_vals   = [0.28, 0.30, 0.33, 0.35]
    atr_vals  = [2.0, 2.5, 3.0]
    h52_vals  = [10, 12, 14]
    macd_vals = [0.0, 0.5]

    combos_2gate: list[tuple[str, float, str, float]] = []
    for bb, rv in product(bb_vals, rv_vals):
        combos_2gate.append(("bb_width_pct_max", bb, "rv20_max", rv))
    for bb, atr in product([12.0, 14.0], atr_vals):
        combos_2gate.append(("bb_width_pct_max", bb, "atr_pct_max", atr))
    for bb, h52 in product([12.0, 14.0], h52_vals):
        combos_2gate.append(("bb_width_pct_max", bb, "pct_from_52wk_high_max", h52))
    for rv, macd in product([0.30, 0.33, 0.35], macd_vals):
        combos_2gate.append(("rv20_max", rv, "macd_histogram_max", macd))
    for bb, macd in product([12.0, 14.0], macd_vals):
        combos_2gate.append(("bb_width_pct_max", bb, "macd_histogram_max", macd))

    results_2gate = []
    for g1, v1, g2, v2 in combos_2gate:
        criteria = {**V26, g1: v1, g2: v2}
        s = _score(rows, criteria)
        results_2gate.append({"g1": g1, "v1": v1, "g2": g2, "v2": v2, **s})

    # Show top 15 by precision (min recall 50%)
    filtered = [r for r in results_2gate if r["recall"] >= 0.50]
    filtered.sort(key=lambda x: x["precision"], reverse=True)
    print("  (showing top 20 by precision, recall≥50%)")
    print(f"  {'Gate 1':<28} {'v1':>7}  {'Gate 2':<28} {'v2':>7}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    for r in filtered[:20]:
        print(f"  {r['g1']:<28} {r['v1']:>7}  {r['g2']:<28} {r['v2']:>7}  {r['precision']:>6.1%}  {r['recall']:>6.1%}  {r['tp']:>5}  {r['fp']:>5}")

    # --- Three-gate combinations (best 2-gate expanded) ---
    print("\n--- Three-gate combinations (best 2-gate + one more) ---")
    top2 = filtered[:5]
    three_gate_results = []
    for base2 in top2:
        g1, v1, g2, v2 = base2["g1"], base2["v1"], base2["g2"], base2["v2"]
        # Try adding a third gate
        for g3, v3 in [
            ("adr20_pct_max", 3.0), ("adr20_pct_max", 3.5),
            ("atr_pct_max", 2.0), ("atr_pct_max", 2.5),
            ("pct_from_52wk_high_max", 12), ("pct_from_52wk_high_max", 14),
            ("macd_histogram_max", 0.0), ("macd_histogram_max", 0.5),
            ("rsi_max", 55), ("rsi_max", 60),
        ]:
            if g3 in (g1, g2):
                continue
            criteria = {**V26, g1: v1, g2: v2, g3: v3}
            s = _score(rows, criteria)
            three_gate_results.append({"g1": g1, "v1": v1, "g2": g2, "v2": v2, "g3": g3, "v3": v3, **s})

    filtered3 = [r for r in three_gate_results if r["recall"] >= 0.50]
    filtered3.sort(key=lambda x: x["precision"], reverse=True)
    print("  (showing top 15 by precision, recall≥50%)")
    print(f"  {'G1':<26} {'v1':>6}  {'G2':<26} {'v2':>6}  {'G3':<24} {'v3':>6}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    for r in filtered3[:15]:
        print(f"  {r['g1']:<26} {r['v1']:>6}  {r['g2']:<26} {r['v2']:>6}  {r['g3']:<24} {r['v3']:>6}  {r['precision']:>6.1%}  {r['recall']:>6.1%}  {r['tp']:>5}  {r['fp']:>5}")

    # --- ML on narrow universe ---
    print("\n--- ML on narrow universe (GBM) ---")
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.metrics import average_precision_score, roc_auc_score
        from sklearn.model_selection import StratifiedKFold

        from .session06 import BOOL_FEATS, HIGH_NULL_COLS, NUMERIC_FEATS

        # Build feature matrix
        NARROW_FEATS = NUMERIC_FEATS + ["adr20_pct"]
        all_feat_names = (
            [f"{c}_is_null" for c in HIGH_NULL_COLS]
            + NARROW_FEATS
            + BOOL_FEATS
        )

        medians = {}
        for feat in NARROW_FEATS:
            vals = [r[feat] for r in rows if r.get(feat) is not None]
            medians[feat] = float(np.median(vals)) if vals else 0.0

        X_rows = []
        y_list = []
        for r in rows:
            row_vals = []
            for col in HIGH_NULL_COLS:
                row_vals.append(0.0 if r.get(col) is not None else 1.0)
            for feat in NARROW_FEATS:
                val = r.get(feat)
                row_vals.append(float(val) if val is not None else medians[feat])
            for feat in BOOL_FEATS:
                val = r.get(feat)
                row_vals.append(float(val) if val is not None else 0.0)
            X_rows.append(row_vals)
            y_list.append(r["is_prime"])

        X = np.array(X_rows, dtype=np.float32)
        y = np.array(y_list, dtype=np.int32)
        print(f"  Feature matrix: {X.shape[0]} rows × {X.shape[1]} features")

        neg, pos = int((y == 0).sum()), int((y == 1).sum())
        scale_pos_weight = neg / pos
        print(f"  scale_pos_weight = {scale_pos_weight:.1f}  (neg={neg}, pos={pos})")

        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        y_score_cv = np.zeros(len(y))
        for fold_i, (train_idx, val_idx) in enumerate(cv.split(X, y)):
            m = GradientBoostingClassifier(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                subsample=0.8, min_samples_leaf=5,
                random_state=42,
            )
            sw = np.where(y[train_idx] == 1, scale_pos_weight, 1.0)
            m.fit(X[train_idx], y[train_idx], sample_weight=sw)
            y_score_cv[val_idx] = m.predict_proba(X[val_idx])[:, 1]
            logger.info("Fold %d done", fold_i + 1)

        auc_roc = roc_auc_score(y, y_score_cv)
        ap = average_precision_score(y, y_score_cv)
        print(f"  GBM 5-fold CV: AUC-ROC={auc_roc:.3f}  AP={ap:.3f}")

        from .session06 import _precision_at_recall, _score_at_threshold

        print("\n  Precision at recall thresholds (CV scores):")
        print(f"  {'Recall target':>14}  {'Precision':>10}  {'Actual R':>9}  {'TP':>5}  {'FP':>6}")
        for target_r in [0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50]:
            p, r, t = _precision_at_recall(y, y_score_cv, target_r)
            s = _score_at_threshold(y, y_score_cv, t)
            vs26 = s["precision"] - base["precision"]
            sign = "+" if vs26 >= 0 else ""
            print(f"  recall≥{target_r:.0%}:       {s['precision']:>8.1%}  ({sign}{vs26:.1%} vs v26)    {s['recall']:>8.1%}  {s['tp']:>5}  {s['fp']:>6}")

        # Date-based holdout
        all_dates = sorted(set(r["date"] for r in rows))
        split_idx = len(all_dates) * 2 // 3
        train_dates = set(all_dates[:split_idx])
        test_dates  = set(all_dates[split_idx:])
        train_rows  = [r for r in rows if r["date"] in train_dates]
        test_rows   = [r for r in rows if r["date"] in test_dates]

        print(f"\n  Date-based holdout: train={len(train_dates)} dates, test={len(test_dates)} dates")
        print(f"  Train: {sum(r['is_prime']==1 for r in train_rows)} prime / {sum(r['is_prime']==0 for r in train_rows)} control")
        print(f"  Test:  {sum(r['is_prime']==1 for r in test_rows)} prime / {sum(r['is_prime']==0 for r in test_rows)} control")

        X_tr, X_te = [], []
        y_tr, y_te = [], []
        for rows_split, X_split, y_split in [(train_rows, X_tr, y_tr), (test_rows, X_te, y_te)]:
            m_vals = {}
            for feat in NARROW_FEATS:
                vals = [r[feat] for r in rows_split if r.get(feat) is not None]
                m_vals[feat] = float(np.median(vals)) if vals else 0.0
            for r in rows_split:
                rv = []
                for col in HIGH_NULL_COLS:
                    rv.append(0.0 if r.get(col) is not None else 1.0)
                for feat in NARROW_FEATS:
                    val = r.get(feat)
                    rv.append(float(val) if val is not None else m_vals[feat])
                for feat in BOOL_FEATS:
                    val = r.get(feat)
                    rv.append(float(val) if val is not None else 0.0)
                X_split.append(rv)
                y_split.append(r["is_prime"])

        X_train = np.array(X_tr, dtype=np.float32)
        X_test  = np.array(X_te, dtype=np.float32)
        y_train = np.array(y_tr, dtype=np.int32)
        y_test  = np.array(y_te, dtype=np.int32)

        gbm_ho = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=5, random_state=42,
        )
        sw_tr = np.where(y_train == 1, (y_train == 0).sum() / (y_train == 1).sum(), 1.0)
        gbm_ho.fit(X_train, y_train, sample_weight=sw_tr)
        y_score_ho = gbm_ho.predict_proba(X_test)[:, 1]

        auc_ho = roc_auc_score(y_test, y_score_ho)
        ap_ho  = average_precision_score(y_test, y_score_ho)
        p_ho, r_ho, t_ho = _precision_at_recall(y_test, y_score_ho, 0.60)
        s_ho = _score_at_threshold(y_test, y_score_ho, t_ho)
        print(f"  GBM holdout: AUC-ROC={auc_ho:.3f}  AP={ap_ho:.3f}")
        print(f"  @recall≥60%: P={s_ho['precision']:.1%}  R={s_ho['recall']:.1%}  TP={s_ho['tp']}  FP={s_ho['fp']}")

        # v26 on test set only
        te_prime = [r for r in test_rows if r["is_prime"] == 1]
        te_ctrl  = [r for r in test_rows if r["is_prime"] == 0]
        v26_tp_te = sum(1 for r in te_prime if _apply_criteria(r, V26))
        v26_fp_te = sum(1 for r in te_ctrl  if _apply_criteria(r, V26))
        v26_p_te  = v26_tp_te / (v26_tp_te + v26_fp_te) if v26_tp_te + v26_fp_te else 0
        v26_r_te  = v26_tp_te / len(te_prime) if te_prime else 0
        print(f"  v26 on test set: P={v26_p_te:.1%}  R={v26_r_te:.1%}  TP={v26_tp_te}  FP={v26_fp_te}")

        # Feature importances (full dataset)
        gbm_full = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=5, random_state=42,
        )
        sw_full = np.where(y == 1, scale_pos_weight, 1.0)
        gbm_full.fit(X, y, sample_weight=sw_full)
        ranked_feats = sorted(zip(all_feat_names, gbm_full.feature_importances_),
                              key=lambda x: x[1], reverse=True)
        print("\n  Top 20 feature importances (GBM, full narrow dataset):")
        for feat, imp in ranked_feats[:20]:
            bar = "█" * int(imp * 300)
            print(f"  {feat:<35} {imp:.4f}  {bar}")

    except ImportError as e:
        print(f"  sklearn not available: {e}")

    # --- Summary of best narrow-universe criteria ---
    print("\n--- Best narrow-universe criteria candidates ---")
    all_results = results_2gate + three_gate_results
    all_results_filtered = [r for r in all_results if r.get("recall", 0) >= 0.55]
    all_results_filtered.sort(key=lambda x: x["precision"], reverse=True)

    print("\n  Top combos (recall≥55%):")
    seen = set()
    printed = 0
    for r in all_results_filtered:
        key = (r.get("g1"), r.get("v1"), r.get("g2"), r.get("v2"), r.get("g3"), r.get("v3"))
        if key in seen:
            continue
        seen.add(key)
        parts = [f"{r['g1']}={r['v1']}", f"{r['g2']}={r['v2']}"]
        if r.get("g3"):
            parts.append(f"{r['g3']}={r['v3']}")
        print(f"  P={r['precision']:.1%}  R={r['recall']:.1%}  TP={r['tp']}  FP={r['fp']}  |  {' + '.join(parts)}")
        printed += 1
        if printed >= 10:
            break


if __name__ == "__main__":
    main()
