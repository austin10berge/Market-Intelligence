"""Session 21 — v35 definition and full validation.

v35 design (from session 19-20 findings):
  - Keep all v31a base gates
  - Raise price_vs_ema200_pct_min: 0 → 5  (trend-strength floor; improves train/test precision)
  - Add price_vs_sma150_pct_min: 5         (top KS feature in 2026; further trend filter)
  - Relax bb_width_pct_max: 14 → 18        (recovers 2026 selloff-regime trades: DAL, DVN, NVDA...)
  - Relax volume_ratio_max: 1.10 → 1.20   (recovers GOOG, C, JPM, AAL-adjacent names)
  - Keep iv_rv_min=1.0 + pcr_vol_max=2.0  (v34 IV gates for Sep-Dec 2025 full eval)

Evaluated on four datasets:
  1. Sep-Dec 2025 full (sanity vs sessions 16-17)
  2. Train  Sep-Oct 2025 (temporal holdout train)
  3. Test   Nov-Dec 2025 (temporal holdout OOS)
  4. 2026   Jan-Jun 2026 (true OOS, base criteria — no IV keys)

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session21
"""

from __future__ import annotations

from .analyze import _score_criteria
from .store import _get_connection, get_all_features

TRAIN_CUTOFF = "2025-10-31"
TEST_START   = "2025-11-01"
OOS_START    = "2026-01-01"

_IV_KEYS = {
    "options_iv_min", "iv_rv_min", "pcr_vol_max",
    "industrials_iv_min", "consumer_cyclical_iv_min", "healthcare_iv_min",
    "consumer_defensive_iv_max", "energy_iv_min", "basic_materials_iv_min",
    "utilities_iv_min",
}

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

V35 = {
    **V31A,
    # Trend-strength floors (new / raised)
    "price_vs_ema200_pct_min": 5,
    "price_vs_sma150_pct_min": 5,
    # Relaxed VCP ceilings for regime robustness
    "bb_width_pct_max": 18.0,
    "volume_ratio_max": 1.20,
    # IV gates from v34 (apply when best_iv is available)
    "iv_rv_min": 1.0,
    "pcr_vol_max": 2.0,
}

# Base variants (IV gates stripped — for 2026 evaluation where best_iv=NULL)
V31A_BASE = {k: v for k, v in V31A.items() if k not in _IV_KEYS}
V34_BASE  = {k: v for k, v in V34.items()  if k not in _IV_KEYS}
V35_BASE  = {k: v for k, v in V35.items()  if k not in _IV_KEYS}


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


def _report(label: str, rows: list[dict], crit: dict) -> dict:
    res = _score_criteria(rows, crit)
    n_p = sum(1 for f in rows if f["is_prime"] == 1)
    print(
        f"  {label:<38}  P={res['precision']*100:5.1f}%  R={res['recall']*100:5.1f}%  "
        f"TP={res['true_positives']:4d}  FP={res['false_positives']:4d}  (prime={n_p})"
    )
    return res


def main() -> None:
    all_features = get_all_features()
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)

    sep_dec = [f for f in narrow if f["date"] <= "2025-12-31"]
    train   = [f for f in narrow if f["date"] <= TRAIN_CUTOFF]
    test    = [f for f in narrow if f["date"] >= TEST_START and f["date"] <= "2025-12-31"]
    oos     = [f for f in narrow if f["date"] >= OOS_START]

    hdr = f"\n  {'Criteria':<38}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'prime':>7}"
    sep = f"  {'-'*38}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}"

    # ── SECTION 1: Full Sep-Dec 2025 (sanity) ────────────────────────────────
    print("=" * 72)
    print("SECTION 1: Full Sep-Dec 2025 (sanity vs sessions 16-17)")
    print("=" * 72)
    print(hdr); print(sep)
    _report("v31a", sep_dec, V31A)
    _report("v34",  sep_dec, V34)
    _report("v35",  sep_dec, V35)

    # ── SECTION 2: Temporal holdout ───────────────────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 2: Temporal holdout — train Sep-Oct / test Nov-Dec 2025")
    print("=" * 72)
    print("\n  (full criteria for both splits — best_iv available for Sep-Dec 2025)")
    print(hdr); print(sep)
    r_v31a_tr = _report("v31a  (train)", train, V31A)
    r_v34_tr  = _report("v34   (train)", train, V34)
    r_v35_tr  = _report("v35   (train)", train, V35)
    print()
    r_v31a_te = _report("v31a  (test OOS)", test, V31A)
    r_v34_te  = _report("v34   (test OOS)", test, V34)
    r_v35_te  = _report("v35   (test OOS)", test, V35)

    print("\n  Train → Test degradation:")
    for name, r_tr, r_te in [("v31a", r_v31a_tr, r_v31a_te),
                               ("v34 ", r_v34_tr,  r_v34_te),
                               ("v35 ", r_v35_tr,  r_v35_te)]:
        dp = (r_te["precision"] - r_tr["precision"]) * 100
        dr = (r_te["recall"]    - r_tr["recall"])    * 100
        print(
            f"  {name}  train P={r_tr['precision']*100:5.1f}%  test P={r_te['precision']*100:5.1f}%  "
            f"ΔP={dp:+5.1f}pp  |  train R={r_tr['recall']*100:5.1f}%  test R={r_te['recall']*100:5.1f}%  "
            f"ΔR={dr:+5.1f}pp"
        )

    # ── SECTION 3: 2026 true OOS (base criteria) ─────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 3: 2026 true OOS — Jan-Jun 2026 (base criteria, no IV keys)")
    print("  best_iv=NULL for all 2026 dates — IV gates stripped for fair comparison")
    print("=" * 72)
    print(hdr); print(sep)
    _report("v31a_base (2026)", oos, V31A_BASE)
    _report("v34_base  (2026)", oos, V34_BASE)
    _report("v35_base  (2026)", oos, V35_BASE)

    # ── SECTION 4: Summary table ──────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 4: Summary — all splits, all criteria")
    print("=" * 72)
    print(f"\n  {'Criteria':<10}  {'full P/R':>12}  {'train P/R':>12}  {'test P/R':>12}  {'OOS P/R':>12}")
    print(f"  {'-'*10}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*12}")
    for name, crit_full, crit_base in [
        ("v31a", V31A, V31A_BASE),
        ("v34 ", V34,  V34_BASE),
        ("v35 ", V35,  V35_BASE),
    ]:
        f = _score_criteria(sep_dec, crit_full)
        tr = _score_criteria(train,   crit_full)
        te = _score_criteria(test,    crit_full)
        oo = _score_criteria(oos,     crit_base)
        print(
            f"  {name:<10}  "
            f"{f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
            f"{tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
            f"{te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
            f"{oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%"
        )

    # ── SECTION 5: v35 sector breakdown ──────────────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 5: v35 sector breakdown — Sep-Dec 2025 full")
    print("=" * 72)
    sectors: dict[str, list] = {}
    for f in sep_dec:
        s = f.get("sector") or "Unknown"
        sectors.setdefault(s, []).append(f)

    print(f"\n  {'Sector':<26}  {'TP':>4}  {'FP':>4}  {'P':>7}  {'prime':>6}")
    print(f"  {'-'*26}  {'-'*4}  {'-'*4}  {'-'*7}  {'-'*6}")
    from .analyze import _apply_criteria
    total_tp = total_fp = 0
    for sector in sorted(sectors):
        rows = sectors[sector]
        tp = sum(1 for f in rows if f["is_prime"] == 1 and _apply_criteria(f, V35))
        fp = sum(1 for f in rows if f["is_prime"] == 0 and _apply_criteria(f, V35))
        n_prime = sum(1 for f in rows if f["is_prime"] == 1)
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        total_tp += tp; total_fp += fp
        print(f"  {sector:<26}  {tp:4d}  {fp:4d}  {p*100:6.1f}%  {n_prime:6d}")
    tot_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    print(f"  {'TOTAL':<26}  {total_tp:4d}  {total_fp:4d}  {tot_p*100:6.1f}%")

    print("\n--- Session 21 complete ---")
    print("\nv35 definition:")
    for k, v in V35.items():
        changed = V34.get(k) != v or k not in V34
        marker = "  ← new/changed" if changed else ""
        print(f"  {k:<42} = {v}{marker}")


if __name__ == "__main__":
    main()
