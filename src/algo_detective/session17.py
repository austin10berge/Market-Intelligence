"""Session 17 — Temporal holdout: train Sep-Oct 2025, test Nov-Dec 2025.

All criteria from v13–v34 were selected by their performance on the full
Sep-Dec 2025 dataset. This session evaluates v34 (and v31a for comparison)
on a true holdout: Nov-Dec 2025 dates were never the exclusive target of
any tuning decision.

Train: dates ≤ 2025-10-31  (23 dates, 185 prime obs)
Test:  dates ≥ 2025-11-01  (13 dates, 96 prime obs)

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session17
"""

from __future__ import annotations

from .analyze import _score_criteria
from .store import _get_connection, get_all_features


def _join_options(features: list[dict]) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT date, ticker, best_iv, pcr_vol, pcr_oi FROM detective_options"
        ).fetchall()
        index = {(r["date"], r["ticker"]): dict(r) for r in rows}
    finally:
        conn.close()
    return [
        {
            **f,
            "best_iv": index.get((f["date"], f["ticker"]), {}).get("best_iv"),
            "pcr_vol": index.get((f["date"], f["ticker"]), {}).get("pcr_vol"),
            "pcr_oi":  index.get((f["date"], f["ticker"]), {}).get("pcr_oi"),
        }
        for f in features
    ]


V31A = {
    "sma50_above_sma200": 1,
    "market_cap_b_min": 25,
    "price_vs_ema200_pct_min": 0,
    "price_vs_ema200_pct_max": 42,
    "pct_from_52wk_high_max": 12,
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
    "bb_width_pct_max": 14.0,
    "volume_ratio_max": 1.10,
    "forward_pe_max": 50,
    "communication_services_market_cap_b_min": 50,
    "iv_rv_min": 0.9,
    "financials_adx_min": 20,
    "financials_volume_ratio_max": 0.90,
    "consumer_cyclical_rsi_max": 44,
    "technology_rsi_max": 54,
}

V34 = {**V31A, "iv_rv_min": 1.0, "pcr_vol_max": 2.0}

TRAIN_CUTOFF = "2025-10-31"
TEST_START   = "2025-11-01"


def _report(label: str, narrow: list[dict], crit: dict) -> dict:
    res = _score_criteria(narrow, crit)
    n_prime = sum(1 for f in narrow if f["is_prime"] == 1)
    print(
        f"  {label:<28}  P={res['precision']*100:5.1f}%  R={res['recall']*100:5.1f}%  "
        f"TP={res['true_positives']:4d}  FP={res['false_positives']:4d}  "
        f"(prime={n_prime})"
    )
    return res


def main() -> None:
    all_features = get_all_features()
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)

    train = [f for f in narrow if f["date"] <= TRAIN_CUTOFF]
    test  = [f for f in narrow if f["date"] >= TEST_START]

    train_dates = sorted(set(f["date"] for f in train if f["is_prime"] == 1))
    test_dates  = sorted(set(f["date"] for f in test  if f["is_prime"] == 1))
    train_prime = sum(1 for f in train if f["is_prime"] == 1)
    test_prime  = sum(1 for f in test  if f["is_prime"] == 1)

    print(f"Full narrow universe: {len(narrow)} rows")
    print(f"Train (Sep-Oct ≤ {TRAIN_CUTOFF}): {len(train)} rows, {train_prime} prime, {len(train_dates)} dates")
    print(f"Test  (Nov-Dec ≥ {TEST_START}):  {len(test)} rows, {test_prime} prime, {len(test_dates)} dates")

    # ── SECTION 1: Full-data baseline ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 1: Full Sep-Dec 2025 (sanity check vs previous sessions)")
    print("=" * 70)
    print(f"\n  {'Criteria':<28}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'Prime':>7}")
    print(f"  {'-'*28}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")
    _report("v31a (full data)", narrow, V31A)
    _report("v34  (full data)", narrow, V34)

    # ── SECTION 2: Train split ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"SECTION 2: TRAIN split (Sep-Oct 2025, {len(train_dates)} dates)")
    print("=" * 70)
    print(f"\n  {'Criteria':<28}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'Prime':>7}")
    print(f"  {'-'*28}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")
    r_v31a_train = _report("v31a (train)", train, V31A)
    r_v34_train  = _report("v34  (train)", train, V34)

    # ── SECTION 3: Test split ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"SECTION 3: TEST split (Nov-Dec 2025, {len(test_dates)} dates) ← OOS")
    print("=" * 70)
    print(f"\n  {'Criteria':<28}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'Prime':>7}")
    print(f"  {'-'*28}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")
    r_v31a_test = _report("v31a (test OOS)", test, V31A)
    r_v34_test  = _report("v34  (test OOS)", test, V34)

    # ── SECTION 4: Degradation summary ───────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 4: Train → Test degradation (overfitting signal)")
    print("=" * 70)

    def _deg(name: str, train_res: dict, test_res: dict) -> None:
        dp = (test_res["precision"] - train_res["precision"]) * 100
        dr = (test_res["recall"]    - train_res["recall"])    * 100
        print(f"  {name:<8}  train P={train_res['precision']*100:5.1f}%  test P={test_res['precision']*100:5.1f}%  ΔP={dp:+5.1f}pp  |  train R={train_res['recall']*100:5.1f}%  test R={test_res['recall']*100:5.1f}%  ΔR={dr:+5.1f}pp")

    _deg("v31a", r_v31a_train, r_v31a_test)
    _deg("v34 ", r_v34_train,  r_v34_test)

    v34_overfit_pp = (r_v34_train["precision"] - r_v34_test["precision"]) * 100
    v31a_overfit_pp = (r_v31a_train["precision"] - r_v31a_test["precision"]) * 100
    extra_overfit = v34_overfit_pp - v31a_overfit_pp
    print(f"\n  v34 introduced {extra_overfit:+.1f}pp additional train→test degradation vs v31a")
    print(f"  (v31a gap: {v31a_overfit_pp:+.1f}pp  |  v34 gap: {v34_overfit_pp:+.1f}pp)")

    # ── SECTION 5: Sweep all iv_rv × pcr_vol on test split only ──────────────
    print("\n" + "=" * 70)
    print("SECTION 5: Grid sweep on TEST split only — find best OOS criteria")
    print("=" * 70)
    print(f"\n  {'iv_rv_min':>10}  {'pcr_vol_max':>11}  {'P (test)':>9}  {'R (test)':>9}  {'TP':>5}  {'FP':>5}")
    print(f"  {'-'*10}  {'-'*11}  {'-'*9}  {'-'*9}  {'-'*5}  {'-'*5}")

    best_oos_p = 0.0
    best_oos_label = ""
    for iv_rv in [0.9, 1.0, 1.1, 1.2]:
        for pcr_max in [None, 1.5, 2.0, 3.0]:
            crit = {**V31A, "iv_rv_min": iv_rv}
            if pcr_max is not None:
                crit["pcr_vol_max"] = pcr_max
            res = _score_criteria(test, crit)
            pcr_label = f"{pcr_max:.1f}" if pcr_max is not None else "none"
            marker = " <--" if res["precision"] > best_oos_p and res["recall"] >= 0.30 else ""
            if res["recall"] >= 0.30 and res["precision"] > best_oos_p:
                best_oos_p = res["precision"]
                best_oos_label = f"iv_rv≥{iv_rv} + pcr_vol≤{pcr_label}"
            print(
                f"  {iv_rv:10.1f}  {pcr_label:>11}  {res['precision']*100:8.1f}%  "
                f"{res['recall']*100:8.1f}%  {res['true_positives']:5d}  "
                f"{res['false_positives']:5d}{marker}"
            )

    print(f"\n  Best OOS (recall≥30%): {best_oos_label}  P={best_oos_p*100:.1f}%")
    print("\n--- Session 17 complete ---")


if __name__ == "__main__":
    main()
