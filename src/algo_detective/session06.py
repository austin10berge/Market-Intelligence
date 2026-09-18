"""Session 06 — ML classifier on the full feature matrix.

Compares gradient-boosted classifier against the v23 rule-based ceiling (8.5% precision,
72.6% recall) using the same SP500 universe and options-joined features.

Two evaluation modes:
  1. Stratified 5-fold CV on the full dataset (standard, potential time-leakage)
  2. Date-based holdout: train on first 24 scan dates, test on last 12 (honest)

Feature strategy:
  - All numeric + boolean features from detective_features
  - best_iv from detective_options (joined)
  - Null imputation: median for numerics, 0 for booleans, + null-indicator columns
    for high-null features (best_iv, dividend_yield, fcf)

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session06
"""

from __future__ import annotations

import logging
import warnings

import numpy as np

from .analyze import _apply_criteria
from .store import _get_connection, get_all_features, get_options_index

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Feature lists ─────────────────────────────────────────────────────────────

NUMERIC_FEATS = [
    "rsi", "adx", "rv20", "bb_pct_b", "bb_width_pct", "atr_pct",
    "volume_ratio", "roc20", "macd_histogram", "pct_from_52wk_high",
    "price_vs_ema20_pct", "price_vs_ema50_pct", "price_vs_ema150_pct", "price_vs_ema200_pct",
    "price_vs_sma20_pct", "price_vs_sma50_pct", "price_vs_sma150_pct", "price_vs_sma200_pct",
    "market_cap_b", "beta", "forward_pe", "peg_ratio",
    "revenue_growth", "earnings_growth", "debt_to_equity", "dividend_yield", "fcf",
    "best_iv",
]

BOOL_FEATS = [
    "price_above_ema20", "price_above_ema50", "price_above_ema150", "price_above_ema200",
    "price_above_sma20", "price_above_sma50", "price_above_sma150", "price_above_sma200",
    "ema20_above_ema50", "ema50_above_ema150", "ema50_above_ema200", "ema150_above_ema200",
    "sma20_above_sma50", "sma50_above_sma150", "sma50_above_sma200", "sma150_above_sma200",
    "price_above_bb_middle", "price_above_bb_upper", "price_below_bb_lower",
]

# Columns with non-trivial null rates — add binary null-indicator features
HIGH_NULL_COLS = ["best_iv", "dividend_yield", "fcf", "forward_pe", "peg_ratio", "beta"]

V23 = {
    "sma50_above_sma200": 1, "market_cap_b_min": 25,
    "price_vs_ema200_pct_min": 0, "price_vs_ema200_pct_max": 42,
    "pct_from_52wk_high_max": 18, "rv20_max": 0.45,
    "dividend_yield_max": 2.5, "options_iv_min": 0.20,
    "financials_market_cap_b_min": 100, "technology_fcf_min": 0.01,
    "industrials_iv_min": 0.30, "consumer_cyclical_iv_min": 0.30,
    "healthcare_iv_min": 0.25, "real_estate_block": 1,
    "consumer_defensive_iv_max": 0.32, "energy_iv_min": 0.38,
    "basic_materials_iv_min": 0.38, "utilities_iv_min": 0.50,
}


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_sp500_with_options() -> list[dict]:
    features = get_all_features()
    conn = _get_connection()
    try:
        sp500 = {r["symbol"] for r in conn.execute(
            "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%sp500%'"
        ).fetchall()}
    finally:
        conn.close()
    filtered = [f for f in features if f["is_prime"] == 1 or f["ticker"] in sp500]
    options_idx = get_options_index()
    return [
        {**f, "best_iv": options_idx.get((f["date"], f["ticker"]), {}).get("best_iv")}
        for f in filtered
    ]


# ── Feature matrix construction ───────────────────────────────────────────────

def _build_X_y(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build X (feature matrix) and y (labels) with null imputation."""
    # Compute medians for numeric imputation (from training data — passed in as rows)
    medians = {}
    for feat in NUMERIC_FEATS:
        vals = [r[feat] for r in rows if r.get(feat) is not None]
        medians[feat] = float(np.median(vals)) if vals else 0.0

    feat_names = []
    X_rows = []

    for r in rows:
        row_vals = []

        # Null indicators for high-null columns (before imputation)
        for col in HIGH_NULL_COLS:
            row_vals.append(0.0 if r.get(col) is not None else 1.0)

        # Numeric features (imputed with median)
        for feat in NUMERIC_FEATS:
            val = r.get(feat)
            row_vals.append(float(val) if val is not None else medians[feat])

        # Boolean features (null → 0)
        for feat in BOOL_FEATS:
            val = r.get(feat)
            row_vals.append(float(val) if val is not None else 0.0)

        X_rows.append(row_vals)

    if not feat_names:
        feat_names = (
            [f"{c}_is_null" for c in HIGH_NULL_COLS]
            + NUMERIC_FEATS
            + BOOL_FEATS
        )

    return np.array(X_rows, dtype=np.float32), np.array([r["is_prime"] for r in rows], dtype=np.int32), feat_names


# ── Evaluation helpers ────────────────────────────────────────────────────────

def _precision_at_recall(y_true, y_score, target_recall: float) -> tuple[float, float, float]:
    """Find threshold where recall >= target_recall and report precision there."""
    from sklearn.metrics import precision_recall_curve
    precision, recall, thresholds = precision_recall_curve(y_true, y_score)
    # precision_recall_curve returns recall in descending order
    # Find the highest threshold where recall >= target_recall
    candidates = [(p, r, t) for p, r, t in zip(precision, recall, thresholds) if r >= target_recall]
    if not candidates:
        return 0.0, 0.0, 0.0
    # Among candidates, pick the one with the highest precision
    best = max(candidates, key=lambda x: x[0])
    return best[0], best[1], best[2]


def _score_at_threshold(y_true, y_score, threshold: float) -> dict:
    y_pred = (y_score >= threshold).astype(int)
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    prec = tp / (tp + fp) if tp + fp > 0 else 0.0
    rec = tp / (tp + fn) if tp + fn > 0 else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4)}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.preprocessing import StandardScaler

    print("\n=== Session 06 — ML Classifier ===\n")

    rows = _load_sp500_with_options()
    prime = [r for r in rows if r["is_prime"] == 1]
    control = [r for r in rows if r["is_prime"] == 0]
    print(f"Dataset: {len(prime)} prime, {len(control)} control  ({len(rows)} total)")
    print(f"Class balance: {len(prime)/len(rows)*100:.1f}% positive")

    # Rule-based v23 baseline
    v23_tp = sum(1 for r in prime if _apply_criteria(r, V23))
    v23_fp = sum(1 for r in control if _apply_criteria(r, V23))
    v23_prec = v23_tp / (v23_tp + v23_fp) if v23_tp + v23_fp else 0
    v23_rec = v23_tp / len(prime) if prime else 0
    print(f"\nRule-based v23 baseline: P={v23_prec:.1%} R={v23_rec:.1%} TP={v23_tp} FP={v23_fp}")

    # --- Exp 1: Stratified 5-fold CV ---
    print("\n--- Exp 1: Stratified 5-fold CV ---")
    X, y, feat_names = _build_X_y(rows)
    print(f"Feature matrix: {X.shape[0]} rows × {X.shape[1]} features")

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    # Class weight to handle imbalance
    neg, pos = int((y == 0).sum()), int((y == 1).sum())
    scale_pos_weight = neg / pos

    models = {
        "GradientBoosting": GradientBoostingClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=20,
            random_state=42,
        ),
        "RandomForest": RandomForestClassifier(
            n_estimators=200, max_depth=8, min_samples_leaf=10,
            class_weight="balanced", random_state=42, n_jobs=-1,
        ),
        "LogisticRegression": LogisticRegression(
            class_weight="balanced", max_iter=1000, C=0.1, random_state=42,
        ),
    }

    results_cv = {}
    for name, model in models.items():
        if name == "GradientBoosting":
            # Manual CV loop: GBM needs sample_weight passed to fit()
            sample_weight = np.where(y == 1, scale_pos_weight, 1.0)
            y_score = np.zeros(len(y))
            for train_idx, val_idx in cv.split(X, y):
                m = GradientBoostingClassifier(
                    n_estimators=200, max_depth=4, learning_rate=0.05,
                    subsample=0.8, min_samples_leaf=20, random_state=42,
                )
                m.fit(X[train_idx], y[train_idx], sample_weight=sample_weight[train_idx])
                y_score[val_idx] = m.predict_proba(X[val_idx])[:, 1]
        elif name == "LogisticRegression":
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            y_score = cross_val_predict(model, X_scaled, y, cv=cv, method="predict_proba")[:, 1]
        else:
            y_score = cross_val_predict(model, X, y, cv=cv, method="predict_proba", n_jobs=-1)[:, 1]

        auc_roc = roc_auc_score(y, y_score)
        ap = average_precision_score(y, y_score)
        p_at_72r, r_at_72r, thresh_72r = _precision_at_recall(y, y_score, 0.726)
        results_cv[name] = {"auc_roc": auc_roc, "ap": ap, "p_72r": p_at_72r, "r_72r": r_at_72r, "thresh": thresh_72r, "y_score": y_score}

        s = _score_at_threshold(y, y_score, thresh_72r)
        print(f"  {name:<22} AUC-ROC={auc_roc:.3f}  AP={ap:.3f}  @recall≥72.6%: P={s['precision']:.1%} R={s['recall']:.1%} TP={s['tp']} FP={s['fp']}")

    # --- Exp 2: Date-based holdout ---
    print("\n--- Exp 2: Date-based holdout (train=first 24 dates, test=last 12) ---")
    all_dates = sorted(set(r["date"] for r in rows))
    split_idx = len(all_dates) * 2 // 3
    train_dates = set(all_dates[:split_idx])
    test_dates = set(all_dates[split_idx:])
    print(f"  Train: {len(train_dates)} dates  |  Test: {len(test_dates)} dates")

    train_rows = [r for r in rows if r["date"] in train_dates]
    test_rows  = [r for r in rows if r["date"] in test_dates]
    print(f"  Train: {sum(r['is_prime']==1 for r in train_rows)} prime / {sum(r['is_prime']==0 for r in train_rows)} control")
    print(f"  Test:  {sum(r['is_prime']==1 for r in test_rows)} prime / {sum(r['is_prime']==0 for r in test_rows)} control")

    X_train, y_train, _ = _build_X_y(train_rows)
    X_test, y_test, _   = _build_X_y(test_rows)

    # Best CV model: GBM
    gbm = GradientBoostingClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, min_samples_leaf=20, random_state=42,
    )
    sw_train = np.where(y_train == 1, (y_train == 0).sum() / (y_train == 1).sum(), 1.0)
    gbm.fit(X_train, y_train, sample_weight=sw_train)
    y_score_test = gbm.predict_proba(X_test)[:, 1]

    auc_roc_ho = roc_auc_score(y_test, y_score_test)
    ap_ho = average_precision_score(y_test, y_score_test)
    p_ho, r_ho, thresh_ho = _precision_at_recall(y_test, y_score_test, 0.70)
    s_ho = _score_at_threshold(y_test, y_score_test, thresh_ho)
    print(f"  GBM holdout: AUC-ROC={auc_roc_ho:.3f}  AP={ap_ho:.3f}")
    print(f"  @recall≥70%: P={s_ho['precision']:.1%} R={s_ho['recall']:.1%} TP={s_ho['tp']} FP={s_ho['fp']}")

    # Rule-based on test set only
    test_prime = [r for r in test_rows if r["is_prime"] == 1]
    test_ctrl  = [r for r in test_rows if r["is_prime"] == 0]
    v23_tp_te = sum(1 for r in test_prime if _apply_criteria(r, V23))
    v23_fp_te = sum(1 for r in test_ctrl  if _apply_criteria(r, V23))
    v23_p_te  = v23_tp_te / (v23_tp_te + v23_fp_te) if v23_tp_te + v23_fp_te else 0
    v23_r_te  = v23_tp_te / len(test_prime) if test_prime else 0
    print(f"  v23 rules on test set: P={v23_p_te:.1%} R={v23_r_te:.1%} TP={v23_tp_te} FP={v23_fp_te}")

    # --- Exp 3: Feature importances ---
    print("\n--- Exp 3: Feature importances (GBM, CV-trained on full set) ---")
    gbm_full = GradientBoostingClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, min_samples_leaf=20, random_state=42,
    )
    sw_full = np.where(y == 1, scale_pos_weight, 1.0)
    gbm_full.fit(X, y, sample_weight=sw_full)
    importances = gbm_full.feature_importances_
    ranked = sorted(zip(feat_names, importances), key=lambda x: x[1], reverse=True)
    print("  Top 20 features:")
    for feat, imp in ranked[:20]:
        bar = "█" * int(imp * 200)
        print(f"  {feat:<35} {imp:.4f}  {bar}")

    # --- Exp 4: Precision-recall sweep at multiple recall targets ---
    print("\n--- Exp 4: Precision at recall thresholds (GBM full-CV scores) ---")
    gbm_name = "GradientBoosting"
    if gbm_name in results_cv:
        # Use the scores already computed in Exp 1 (GBM manual CV loop)
        y_score_cv = results_cv["GradientBoosting"]["y_score"]

        print(f"  {'Recall target':>15}  {'Precision':>10}  {'Actual recall':>14}  {'TP':>5}  {'FP':>6}")
        for target_r in [0.80, 0.75, 0.726, 0.70, 0.65, 0.60, 0.55, 0.50]:
            p, r, t = _precision_at_recall(y, y_score_cv, target_r)
            s = _score_at_threshold(y, y_score_cv, t)
            vs23 = f"+{s['precision']-v23_prec:.1%}" if s['precision'] > v23_prec else f"{s['precision']-v23_prec:.1%}"
            print(f"  recall≥{target_r:.0%}:   {s['precision']:.1%}  ({vs23} vs v23)    {s['recall']:.1%}          {s['tp']:>5}  {s['fp']:>6}")

    print("\n=== Summary ===")
    print(f"  Rule-based v23:  P={v23_prec:.1%}  R={v23_rec:.1%}  TP={v23_tp}  FP={v23_fp}")
    best_cv = max(results_cv.items(), key=lambda x: x[1]["p_72r"])
    best_name, best_r = best_cv
    print(f"  ML best (CV):    P={best_r['p_72r']:.1%}  R={best_r['r_72r']:.1%}  model={best_name}  [at same recall target]")
    print(f"  ML holdout GBM:  P={s_ho['precision']:.1%}  R={s_ho['recall']:.1%}  TP={s_ho['tp']}  FP={s_ho['fp']}  [on unseen dates]")


if __name__ == "__main__":
    main()
