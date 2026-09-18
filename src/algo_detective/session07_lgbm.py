"""Session 07 — LightGBM classifier vs sklearn GBM (session06).

Key differences from session06:
  - LightGBM handles NaN natively: numeric features are passed raw (no median imputation).
  - Null-indicator columns (best_iv_is_null, etc.) are still included as features.
  - scale_pos_weight handles class imbalance.
  - Same stratified 5-fold CV and date-based holdout splits as session06 for apples-to-apples.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session07_lgbm
"""

from __future__ import annotations

import logging
import warnings

import numpy as np

from .analyze import _apply_criteria
from .session06 import (
    BOOL_FEATS,
    HIGH_NULL_COLS,
    NUMERIC_FEATS,
    V23,
    _load_sp500_with_options,
    _precision_at_recall,
    _score_at_threshold,
)

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Feature matrix for LightGBM (NaN-native) ─────────────────────────────────

def _build_X_y_lgb(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build X/y for LightGBM.

    Numeric features keep raw NaN (LightGBM handles them natively).
    Null-indicator columns are still included as informative features.
    Boolean features: null → 0.0.
    """
    feat_names = (
        [f"{c}_is_null" for c in HIGH_NULL_COLS]
        + NUMERIC_FEATS
        + BOOL_FEATS
    )

    X_rows = []
    for r in rows:
        row_vals = []

        # Null indicators for high-null columns
        for col in HIGH_NULL_COLS:
            row_vals.append(0.0 if r.get(col) is not None else 1.0)

        # Numeric features — keep NaN as float('nan'), LightGBM handles it
        for feat in NUMERIC_FEATS:
            val = r.get(feat)
            row_vals.append(float(val) if val is not None else float("nan"))

        # Boolean features — null → 0
        for feat in BOOL_FEATS:
            val = r.get(feat)
            row_vals.append(float(val) if val is not None else 0.0)

        X_rows.append(row_vals)

    return (
        np.array(X_rows, dtype=np.float32),
        np.array([r["is_prime"] for r in rows], dtype=np.int32),
        feat_names,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import lightgbm as lgb
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    print("\n=== Session 07 — LightGBM Classifier ===\n")

    rows = _load_sp500_with_options()
    prime = [r for r in rows if r["is_prime"] == 1]
    control = [r for r in rows if r["is_prime"] == 0]
    print(f"Dataset: {len(prime)} prime, {len(control)} control  ({len(rows)} total)")
    print(f"Class balance: {len(prime)/len(rows)*100:.1f}% positive")

    # Rule-based v23 baseline (same as session06)
    v23_tp = sum(1 for r in prime if _apply_criteria(r, V23))
    v23_fp = sum(1 for r in control if _apply_criteria(r, V23))
    v23_prec = v23_tp / (v23_tp + v23_fp) if v23_tp + v23_fp else 0
    v23_rec = v23_tp / len(prime) if prime else 0
    print(f"\nRule-based v23 baseline: P={v23_prec:.1%} R={v23_rec:.1%} TP={v23_tp} FP={v23_fp}")

    # ── Exp 1: Stratified 5-fold CV ──────────────────────────────────────────
    print("\n--- Exp 1: Stratified 5-fold CV (LightGBM) ---")
    X, y, feat_names = _build_X_y_lgb(rows)
    print(f"Feature matrix: {X.shape[0]} rows × {X.shape[1]} features")

    neg_count = int((y == 0).sum())
    pos_count = int((y == 1).sum())
    scale_pos_weight = neg_count / pos_count
    print(f"scale_pos_weight = {scale_pos_weight:.2f}  (neg={neg_count}, pos={pos_count})")

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    y_score_cv = np.zeros(len(y))
    for fold, (train_idx, val_idx) in enumerate(cv.split(X, y), 1):
        m = lgb.LGBMClassifier(
            n_estimators=300,
            num_leaves=31,
            max_depth=-1,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_samples=20,
            scale_pos_weight=scale_pos_weight,
            random_state=42,
            verbose=-1,
        )
        m.fit(X[train_idx], y[train_idx])
        y_score_cv[val_idx] = m.predict_proba(X[val_idx])[:, 1]
        logger.info("Fold %d done", fold)

    auc_roc_cv = roc_auc_score(y, y_score_cv)
    ap_cv = average_precision_score(y, y_score_cv)
    p_cv, r_cv, thresh_cv = _precision_at_recall(y, y_score_cv, 0.726)
    s_cv = _score_at_threshold(y, y_score_cv, thresh_cv)
    print(f"  LightGBM CV: AUC-ROC={auc_roc_cv:.3f}  AP={ap_cv:.3f}")
    print(f"  @recall≥72.6%: P={s_cv['precision']:.1%} R={s_cv['recall']:.1%} TP={s_cv['tp']} FP={s_cv['fp']}")

    # ── Exp 2: Precision at recall sweep ────────────────────────────────────
    print("\n--- Exp 2: Precision at recall thresholds (LightGBM CV scores) ---")
    print(f"  {'Recall target':>15}  {'Precision':>10}  {'Actual recall':>14}  {'TP':>5}  {'FP':>6}")
    for target_r in [0.80, 0.75, 0.726, 0.70, 0.65, 0.60, 0.55, 0.50]:
        p, r, t = _precision_at_recall(y, y_score_cv, target_r)
        s = _score_at_threshold(y, y_score_cv, t)
        vs23 = (
            f"+{s['precision'] - v23_prec:.1%}"
            if s["precision"] > v23_prec
            else f"{s['precision'] - v23_prec:.1%}"
        )
        print(
            f"  recall≥{target_r:.0%}:   {s['precision']:.1%}  ({vs23} vs v23)"
            f"    {s['recall']:.1%}          {s['tp']:>5}  {s['fp']:>6}"
        )

    # ── Exp 3: Date-based holdout ─────────────────────────────────────────────
    print("\n--- Exp 3: Date-based holdout (train=first 24 dates, test=last 12) ---")
    all_dates = sorted(set(r["date"] for r in rows))
    split_idx = len(all_dates) * 2 // 3
    train_dates = set(all_dates[:split_idx])
    test_dates = set(all_dates[split_idx:])
    print(f"  Train: {len(train_dates)} dates  |  Test: {len(test_dates)} dates")

    train_rows = [r for r in rows if r["date"] in train_dates]
    test_rows = [r for r in rows if r["date"] in test_dates]
    print(
        f"  Train: {sum(r['is_prime']==1 for r in train_rows)} prime"
        f" / {sum(r['is_prime']==0 for r in train_rows)} control"
    )
    print(
        f"  Test:  {sum(r['is_prime']==1 for r in test_rows)} prime"
        f" / {sum(r['is_prime']==0 for r in test_rows)} control"
    )

    X_train, y_train, _ = _build_X_y_lgb(train_rows)
    X_test, y_test, _ = _build_X_y_lgb(test_rows)

    neg_tr = int((y_train == 0).sum())
    pos_tr = int((y_train == 1).sum())
    spw_tr = neg_tr / pos_tr

    lgbm_ho = lgb.LGBMClassifier(
        n_estimators=300,
        num_leaves=31,
        max_depth=-1,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=20,
        scale_pos_weight=spw_tr,
        random_state=42,
        verbose=-1,
    )
    lgbm_ho.fit(X_train, y_train)
    y_score_ho = lgbm_ho.predict_proba(X_test)[:, 1]

    auc_roc_ho = roc_auc_score(y_test, y_score_ho)
    ap_ho = average_precision_score(y_test, y_score_ho)
    p_ho, r_ho, thresh_ho = _precision_at_recall(y_test, y_score_ho, 0.70)
    s_ho = _score_at_threshold(y_test, y_score_ho, thresh_ho)
    print(f"  LightGBM holdout: AUC-ROC={auc_roc_ho:.3f}  AP={ap_ho:.3f}")
    print(f"  @recall≥70%: P={s_ho['precision']:.1%} R={s_ho['recall']:.1%} TP={s_ho['tp']} FP={s_ho['fp']}")

    # v23 on test set
    test_prime = [r for r in test_rows if r["is_prime"] == 1]
    test_ctrl = [r for r in test_rows if r["is_prime"] == 0]
    v23_tp_te = sum(1 for r in test_prime if _apply_criteria(r, V23))
    v23_fp_te = sum(1 for r in test_ctrl if _apply_criteria(r, V23))
    v23_p_te = v23_tp_te / (v23_tp_te + v23_fp_te) if v23_tp_te + v23_fp_te else 0
    v23_r_te = v23_tp_te / len(test_prime) if test_prime else 0
    print(f"  v23 rules on test set: P={v23_p_te:.1%} R={v23_r_te:.1%} TP={v23_tp_te} FP={v23_fp_te}")

    # ── Exp 4: Feature importances ────────────────────────────────────────────
    print("\n--- Exp 4: Feature importances (LightGBM, trained on full dataset) ---")
    lgbm_full = lgb.LGBMClassifier(
        n_estimators=300,
        num_leaves=31,
        max_depth=-1,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=20,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        verbose=-1,
    )
    lgbm_full.fit(X, y)

    importances = lgbm_full.feature_importances_
    # Normalize to [0, 1] for display
    imp_norm = importances / importances.sum() if importances.sum() > 0 else importances
    ranked = sorted(
        zip(feat_names, imp_norm, importances),
        key=lambda x: x[2],
        reverse=True,
    )
    print("  Top 20 features (by split count):")
    for feat, imp_n, imp_raw in ranked[:20]:
        bar = "█" * int(imp_n * 500)
        print(f"  {feat:<35} {imp_n:.4f}  {bar}")

    # ── Comparison table ──────────────────────────────────────────────────────
    print("\n=== Session 06 GBM vs Session 07 LGB ===")
    print(f"  {'Metric':<28} {'GBM (s06)':>12}  {'LGB (s07)':>12}")
    print(f"  {'CV AUC-ROC:':<28} {'0.959':>12}  {auc_roc_cv:.3f}")
    print(f"  {'CV AP:':<28} {'0.392':>12}  {ap_cv:.3f}")
    print(f"  {'CV P@R≥72.6%:':<28} {'25.6%':>12}  {s_cv['precision']:.1%}")
    print(f"  {'Holdout AUC-ROC:':<28} {'0.910':>12}  {auc_roc_ho:.3f}")
    print(f"  {'Holdout P@R≥70%:':<28} {'7.9%':>12}  {s_ho['precision']:.1%}")
    print(f"  {'Holdout TP:':<28} {'59':>12}  {s_ho['tp']}")
    print(f"  {'Holdout FP:':<28} {'686':>12}  {s_ho['fp']}")


if __name__ == "__main__":
    main()
