"""Session 07 — Time-series-aware cross-validation for the ML classifier.

Addresses the time-leakage problem in Session 06's stratified 5-fold CV (which
let the model see future dates during training). Uses forward-chain
(expanding-window) CV ordered by scan date so each test window is always in the
future relative to the training window.

Two approaches:
  1. sklearn TimeSeriesSplit(n_splits=5) on date-ordered rows
  2. Manual expanding-window fold with ~6-date test windows and a minimum of
     12 training dates before the first test fold

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session07_ts_cv
"""

from __future__ import annotations

import logging
import warnings

import numpy as np

from .store import _get_connection, get_all_features, get_options_index

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Feature lists (identical to session06) ────────────────────────────────────

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

HIGH_NULL_COLS = ["best_iv", "dividend_yield", "fcf", "forward_pe", "peg_ratio", "beta"]

# ── GBM hyperparameters (identical to session06) ──────────────────────────────

GBM_PARAMS = dict(
    n_estimators=200,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    min_samples_leaf=20,
    random_state=42,
)


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

def _build_X_y(
    rows: list[dict],
    train_medians: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str], dict]:
    """Build X and y with null imputation.

    If train_medians is provided, use those values for imputation (prevents
    future-data leakage when building the test matrix).  Otherwise compute
    medians from the supplied rows (training case).
    """
    if train_medians is None:
        medians: dict = {}
        for feat in NUMERIC_FEATS:
            vals = [r[feat] for r in rows if r.get(feat) is not None]
            medians[feat] = float(np.median(vals)) if vals else 0.0
    else:
        medians = train_medians

    feat_names = (
        [f"{c}_is_null" for c in HIGH_NULL_COLS]
        + NUMERIC_FEATS
        + BOOL_FEATS
    )

    X_rows = []
    for r in rows:
        row_vals = []
        for col in HIGH_NULL_COLS:
            row_vals.append(0.0 if r.get(col) is not None else 1.0)
        for feat in NUMERIC_FEATS:
            val = r.get(feat)
            row_vals.append(float(val) if val is not None else medians[feat])
        for feat in BOOL_FEATS:
            val = r.get(feat)
            row_vals.append(float(val) if val is not None else 0.0)
        X_rows.append(row_vals)

    return (
        np.array(X_rows, dtype=np.float32),
        np.array([r["is_prime"] for r in rows], dtype=np.int32),
        feat_names,
        medians,
    )


def _sample_weight(y: np.ndarray) -> np.ndarray:
    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    w = np.where(y == 1, neg / pos if pos else 1.0, 1.0)
    return w.astype(np.float64)


# ── Evaluation helpers ────────────────────────────────────────────────────────

def _precision_at_recall(
    y_true: np.ndarray,
    y_score: np.ndarray,
    target_recall: float,
) -> tuple[float, float, float]:
    from sklearn.metrics import precision_recall_curve

    precision, recall, thresholds = precision_recall_curve(y_true, y_score)
    candidates = [
        (p, r, t)
        for p, r, t in zip(precision, recall, thresholds)
        if r >= target_recall
    ]
    if not candidates:
        return 0.0, 0.0, 0.0
    return max(candidates, key=lambda x: x[0])


def _score_at_threshold(
    y_true: np.ndarray,
    y_score: np.ndarray,
    threshold: float,
) -> dict:
    y_pred = (y_score >= threshold).astype(int)
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    prec = tp / (tp + fp) if tp + fp > 0 else 0.0
    rec = tp / (tp + fn) if tp + fn > 0 else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4)}


# ── Forward-chain CV ──────────────────────────────────────────────────────────

def _run_ts_cv(rows: list[dict], n_splits: int = 5) -> dict:
    """Forward-chain (expanding-window) cross-validation sorted by date.

    Strategy:
      - Sort all unique dates.
      - Require at least 12 dates of training data before the first test fold.
      - Divide remaining dates into n_splits roughly equal test windows.
      - Each fold trains on all dates before the test window (expanding window).

    Returns accumulated y_true, y_score, and per-fold stats.
    """
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import average_precision_score, roc_auc_score

    dates = sorted(set(r["date"] for r in rows))
    n_dates = len(dates)
    min_train_dates = 12

    if n_dates <= min_train_dates:
        raise ValueError(
            f"Need more than {min_train_dates} unique dates, got {n_dates}"
        )

    # Dates available for splitting into test folds
    available_for_test = dates[min_train_dates:]
    n_available = len(available_for_test)

    if n_available < n_splits:
        n_splits = n_available
        logger.warning("Reducing n_splits to %d (only %d testable dates)", n_splits, n_available)

    # Divide available dates into n_splits windows
    window_size = n_available // n_splits
    fold_boundaries: list[tuple[int, int]] = []
    for k in range(n_splits):
        start = min_train_dates + k * window_size
        # Last fold absorbs any remainder
        end = min_train_dates + (k + 1) * window_size if k < n_splits - 1 else n_dates
        fold_boundaries.append((start, end))

    print(f"\n  Unique dates: {n_dates}  |  min_train_dates: {min_train_dates}  |  n_splits: {n_splits}")
    print(f"  Approx test window: {window_size} dates per fold\n")

    # Build a date→rows lookup for efficiency
    date_to_rows: dict[str, list[dict]] = {}
    for r in rows:
        date_to_rows.setdefault(r["date"], []).append(r)

    all_y_true: list[np.ndarray] = []
    all_y_score: list[np.ndarray] = []
    fold_stats: list[dict] = []

    for fold_idx, (test_start_i, test_end_i) in enumerate(fold_boundaries):
        train_date_set = set(dates[:test_start_i])
        test_date_set = set(dates[test_start_i:test_end_i])

        train_rows = [r for d in dates[:test_start_i] for r in date_to_rows[d]]
        test_rows = [r for d in dates[test_start_i:test_end_i] for r in date_to_rows[d]]

        if not train_rows or not test_rows:
            logger.warning("Fold %d: empty train or test — skipping", fold_idx + 1)
            continue

        X_train, y_train, feat_names, train_medians = _build_X_y(train_rows)
        X_test, y_test, _, _ = _build_X_y(test_rows, train_medians=train_medians)

        sw = _sample_weight(y_train)
        gbm = GradientBoostingClassifier(**GBM_PARAMS)
        gbm.fit(X_train, y_train, sample_weight=sw)
        y_score = gbm.predict_proba(X_test)[:, 1]

        n_prime = int((y_test == 1).sum())
        n_ctrl = int((y_test == 0).sum())

        try:
            fold_auc = roc_auc_score(y_test, y_score)
            fold_ap = average_precision_score(y_test, y_score)
        except Exception:
            fold_auc = float("nan")
            fold_ap = float("nan")

        all_y_true.append(y_test)
        all_y_score.append(y_score)

        test_date_range = f"{dates[test_start_i]} → {dates[test_end_i - 1]}"
        fold_stats.append({
            "fold": fold_idx + 1,
            "train_dates": len(train_date_set),
            "test_dates": len(test_date_set),
            "test_range": test_date_range,
            "n_prime": n_prime,
            "n_ctrl": n_ctrl,
            "auc_roc": fold_auc,
            "ap": fold_ap,
        })

        print(
            f"  Fold {fold_idx + 1}/{n_splits}: "
            f"train={len(train_date_set)} dates, "
            f"test={len(test_date_set)} dates [{test_date_range}]  "
            f"prime={n_prime} ctrl={n_ctrl}  "
            f"AUC={fold_auc:.3f}  AP={fold_ap:.3f}"
        )

    y_true_all = np.concatenate(all_y_true)
    y_score_all = np.concatenate(all_y_score)

    return {
        "y_true": y_true_all,
        "y_score": y_score_all,
        "fold_stats": fold_stats,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    from sklearn.metrics import average_precision_score, roc_auc_score

    print("\n=== Session 07 — Time-Series CV ===\n")

    rows = _load_sp500_with_options()
    prime_all = [r for r in rows if r["is_prime"] == 1]
    ctrl_all  = [r for r in rows if r["is_prime"] == 0]
    print(f"Dataset: {len(prime_all)} prime, {len(ctrl_all)} control  ({len(rows)} total)")
    print(f"Class balance: {len(prime_all)/len(rows)*100:.1f}% positive")
    dates_all = sorted(set(r["date"] for r in rows))
    print(f"Unique scan dates: {len(dates_all)}  ({dates_all[0]} → {dates_all[-1]})")

    # ── Forward-chain CV ──────────────────────────────────────────────────────
    print("\n--- Forward-chain (expanding-window) CV ---")
    cv_result = _run_ts_cv(rows, n_splits=5)

    y_true = cv_result["y_true"]
    y_score = cv_result["y_score"]
    fold_stats = cv_result["fold_stats"]

    auc_roc_cv = roc_auc_score(y_true, y_score)
    ap_cv = average_precision_score(y_true, y_score)

    print(f"\n  Accumulated CV results: {int((y_true==1).sum())} prime, {int((y_true==0).sum())} control")
    print(f"  AUC-ROC (accumulated): {auc_roc_cv:.3f}")
    print(f"  Avg Precision (accumulated): {ap_cv:.3f}")

    # ── Precision/recall table ────────────────────────────────────────────────
    print("\n--- Precision at recall thresholds (TS-CV accumulated) ---")
    recall_targets = [0.80, 0.75, 0.726, 0.70, 0.65, 0.60, 0.50]
    print(f"  {'Recall target':>14}  {'Precision':>10}  {'Actual recall':>14}  {'TP':>5}  {'FP':>6}")
    pr_at_target: dict[float, dict] = {}
    for target_r in recall_targets:
        p, r, t = _precision_at_recall(y_true, y_score, target_r)
        s = _score_at_threshold(y_true, y_score, t)
        pr_at_target[target_r] = s
        print(
            f"  recall≥{target_r:.0%}:   "
            f"{s['precision']:.1%}  "
            f"            {s['recall']:.1%}          "
            f"{s['tp']:>5}  {s['fp']:>6}"
        )

    # ── Per-fold summary table ────────────────────────────────────────────────
    print("\n--- Per-fold breakdown ---")
    print(f"  {'Fold':>4}  {'Train dates':>11}  {'Test dates':>10}  {'Test range':>27}  {'Prime':>6}  {'Ctrl':>6}  {'AUC-ROC':>8}  {'AP':>6}")
    for fs in fold_stats:
        print(
            f"  {fs['fold']:>4}  "
            f"{fs['train_dates']:>11}  "
            f"{fs['test_dates']:>10}  "
            f"{fs['test_range']:>27}  "
            f"{fs['n_prime']:>6}  "
            f"{fs['n_ctrl']:>6}  "
            f"{fs['auc_roc']:>8.3f}  "
            f"{fs['ap']:>6.3f}"
        )

    # Average per-fold AUC and AP
    valid_folds = [fs for fs in fold_stats if not np.isnan(fs["auc_roc"])]
    mean_auc = float(np.mean([fs["auc_roc"] for fs in valid_folds]))
    mean_ap  = float(np.mean([fs["ap"] for fs in valid_folds]))
    print(f"\n  Mean per-fold AUC-ROC: {mean_auc:.3f}  (accumulated AUC: {auc_roc_cv:.3f})")
    print(f"  Mean per-fold AP:      {mean_ap:.3f}  (accumulated AP:  {ap_cv:.3f})")

    # ── Precision at ~72.6% recall for the comparison line ───────────────────
    p_72, r_72, t_72 = _precision_at_recall(y_true, y_score, 0.726)
    s_72 = _score_at_threshold(y_true, y_score, t_72)

    # ── Comparison ────────────────────────────────────────────────────────────
    print("\n=== Comparison ===")
    print("  Session 06 leaky CV:      AUC=0.959  P=25.6%  R=73.0%")
    print("  Session 06 holdout:       AUC=0.910  P=7.9%   R=71.1%  (train=24 dates, test=12)")
    print(
        f"  Session 07 TS-CV (accum): "
        f"AUC={auc_roc_cv:.3f}  "
        f"P={s_72['precision']:.1%}  "
        f"R={s_72['recall']:.1%}  "
        f"TP={s_72['tp']}  FP={s_72['fp']}"
        f"  [recall≥72.6%]"
    )
    print("  Rule-based v23 (full):    AUC=n/a    P=6.6%   R=53.0%  (test-set dates only)")

    print("\n--- Interpretation ---")
    if auc_roc_cv < 0.93:
        print("  TS-CV AUC is well below the leaky 0.959 — confirms leakage inflated session 06 CV.")
    else:
        print("  TS-CV AUC is close to leaky CV — leakage may be less severe than expected.")

    if s_72["precision"] < 0.15:
        print(f"  At ≥72.6% recall, precision is {s_72['precision']:.1%} — GBM is honest, modest gain over v23 rules (~6.6%).")
    else:
        print(f"  At ≥72.6% recall, precision is {s_72['precision']:.1%} — notable gain vs v23 rules.")

    print("\n=== Session 07 complete ===\n")


if __name__ == "__main__":
    main()
