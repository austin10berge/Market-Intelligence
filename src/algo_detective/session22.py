"""Session 22 — Two-tier regime model + sma50_above_sma150 gate + CC regression fix.

Three experiments:

A) Two-tier regime routing (SECTIONS 1-2):
   Route each date's rows through different criteria based on market breadth.
   bull  (breadth >= threshold): V34 — tight VCP, moderate trend floor
   pullback (breadth < threshold): V35 — relaxed VCP, strong trend floor
   Sweep threshold: 65%, 67%, 68%, 70%, 72%.
   Section 1: Sep-Dec 2025 only (full IV available).
   Section 2: Combined Sep 2025 + 2026 OOS (2026 uses base criteria).

B) sma50_above_sma150 boolean gate (SECTION 3):
   Was #2 KS feature in 2026 (KS=0.317, prime rate 93.5% vs control 61.8%).
   V35_BOOL_BASE: replace price_vs_sma150_pct_min=5 with sma50_above_sma150=1.
   V35_BOTH_BASE: keep both gates.
   Compare on all four splits.

C) Consumer Cyclical regression fix (SECTION 4):
   v35 has 0 CC TPs vs v34's 3. Root cause: price_vs_sma150_pct_min=5 is too strict.
   analyze.py now supports consumer_cyclical_price_vs_sma150_pct_min which overrides
   the global gate for CC rows. V35_CC_FIX = V35 + CC floor = 0.
   Show recovered CC trades and full-split impact.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session22
"""

from __future__ import annotations

from .analyze import _apply_criteria, _score_criteria
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
    "price_vs_ema200_pct_min": 5,
    "price_vs_sma150_pct_min": 5,
    "bb_width_pct_max": 18.0,
    "volume_ratio_max": 1.20,
    "iv_rv_min": 1.0,
    "pcr_vol_max": 2.0,
}

V31A_BASE = {k: v for k, v in V31A.items() if k not in _IV_KEYS}
V34_BASE  = {k: v for k, v in V34.items()  if k not in _IV_KEYS}
V35_BASE  = {k: v for k, v in V35.items()  if k not in _IV_KEYS}

# ── V35_BOOL: boolean sma50>sma150 gate instead of pct floor ─────────────────
_V35_no_sma150 = {k: v for k, v in V35.items() if k != "price_vs_sma150_pct_min"}
V35_BOOL = {**_V35_no_sma150, "sma50_above_sma150": 1}
V35_BOOL_BASE = {k: v for k, v in V35_BOOL.items() if k not in _IV_KEYS}

# V35_BOTH: both the boolean AND the pct floor
V35_BOTH = {**V35, "sma50_above_sma150": 1}
V35_BOTH_BASE = {k: v for k, v in V35_BOTH.items() if k not in _IV_KEYS}

# ── V35_CC_FIX: exempt CC from both new V35 trend floors ────────────────────
# V35 adds ema200_pct_min=5 and sma150_pct_min=5. AMZN's Oct 2025 TPs (the 3
# v34 CC TPs lost in v35) sit at ema200_pct≈1% and sma150_pct≈2% — both below
# 5%. Both gates need CC overrides to recover them.
V35_CC_FIX = {
    **V35,
    "consumer_cyclical_price_vs_ema200_pct_min": 0,
    "consumer_cyclical_price_vs_sma150_pct_min": 0,
}
V35_CC_FIX_BASE = {k: v for k, v in V35_CC_FIX.items() if k not in _IV_KEYS}


# ── Helpers ───────────────────────────────────────────────────────────────────


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


def _compute_breadth() -> dict[str, float]:
    """Return {date: breadth_pct} from detective_features."""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT date,
                   CAST(SUM(sma50_above_sma200) AS REAL) / COUNT(*) * 100 AS pct
            FROM detective_features
            GROUP BY date
        """).fetchall()
    finally:
        conn.close()
    return {r["date"]: r["pct"] for r in rows}


def _score_regime_routing(
    rows: list[dict],
    breadth: dict[str, float],
    threshold: float,
    crit_bull: dict,
    crit_pullback: dict,
) -> dict:
    """Route each row to bull or pullback criteria based on its date's breadth."""
    tp = fp = 0
    for f in rows:
        b = breadth.get(f["date"], 0.0)
        crit = crit_bull if b >= threshold else crit_pullback
        passes = _apply_criteria(f, crit)
        if f["is_prime"] == 1:
            if passes:
                tp += 1
        else:
            if passes:
                fp += 1
    n_prime = sum(1 for f in rows if f["is_prime"] == 1)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / n_prime if n_prime > 0 else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "true_positives": tp,
        "false_positives": fp,
        "n_prime": n_prime,
    }


def _rpt_route(label: str, res: dict) -> None:
    print(
        f"  {label:<46}  P={res['precision']*100:5.1f}%  R={res['recall']*100:5.1f}%  "
        f"TP={res['true_positives']:4d}  FP={res['false_positives']:4d}  "
        f"(prime={res['n_prime']})"
    )


def _rpt_crit(label: str, rows: list[dict], crit: dict) -> dict:
    res = _score_criteria(rows, crit)
    n_prime = sum(1 for f in rows if f["is_prime"] == 1)
    print(
        f"  {label:<46}  P={res['precision']*100:5.1f}%  R={res['recall']*100:5.1f}%  "
        f"TP={res['true_positives']:4d}  FP={res['false_positives']:4d}  "
        f"(prime={n_prime})"
    )
    return res


def main() -> None:
    all_features = get_all_features()
    breadth = _compute_breadth()

    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)

    sep_dec = [f for f in narrow if f["date"] <= "2025-12-31"]
    train   = [f for f in narrow if f["date"] <= TRAIN_CUTOFF]
    test    = [f for f in narrow if f["date"] >= TEST_START and f["date"] <= "2025-12-31"]
    oos     = [f for f in narrow if f["date"] >= OOS_START]

    print(f"Sep-Dec 2025 : {len(sep_dec)} rows, "
          f"{sum(1 for f in sep_dec if f['is_prime']==1)} prime")
    print(f"  Train      : {len(train)} rows, "
          f"{sum(1 for f in train if f['is_prime']==1)} prime")
    print(f"  Test       : {len(test)} rows, "
          f"{sum(1 for f in test if f['is_prime']==1)} prime")
    print(f"2026 OOS     : {len(oos)} rows, "
          f"{sum(1 for f in oos if f['is_prime']==1)} prime")

    hdr = (f"\n  {'Criteria / Threshold':<46}  {'P':>7}  {'R':>7}  "
           f"{'TP':>5}  {'FP':>5}  {'(prime)':>8}")
    sep_line = (f"  {'-'*46}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*8}")

    # ── SECTION 1: Two-tier routing — Sep-Dec 2025 (full IV) ─────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: Two-tier regime routing — Sep-Dec 2025 (full IV criteria)")
    print("  bull (breadth >= threshold): V34   pullback (< threshold): V35")
    print("=" * 80)

    # Date distribution by tier at each threshold
    print("\n  Breadth threshold → dates in each tier (Sep-Dec 2025):")
    sd_dates = sorted(set(f["date"] for f in sep_dec if f["is_prime"] == 1))
    for thr in [65.0, 67.0, 68.0, 70.0, 72.0]:
        bull_d = sum(1 for d in sd_dates if breadth.get(d, 0) >= thr)
        pb_d = len(sd_dates) - bull_d
        print(f"    {thr:.0f}%: bull={bull_d:2d} dates (→V34)  pullback={pb_d:2d} dates (→V35)")

    print(hdr); print(sep_line)
    # Baselines (uniform criteria)
    _rpt_crit("v34 (uniform, baseline)", sep_dec, V34)
    _rpt_crit("v35 (uniform, baseline)", sep_dec, V35)
    print()
    for thr in [65.0, 67.0, 68.0, 70.0, 72.0]:
        res = _score_regime_routing(sep_dec, breadth, thr, V34, V35)
        _rpt_route(f"2-tier  thr={thr:.0f}%  (V34/V35)", res)

    # ── SECTION 2: Two-tier routing — combined Sep 2025 + 2026 ───────────────
    print("\n" + "=" * 80)
    print("SECTION 2: Two-tier regime routing — combined Sep 2025 + 2026 OOS")
    print("  Sep-Dec 2025: full criteria (V34/V35).")
    print("  2026 OOS: base criteria (V34_BASE/V35_BASE) — best_iv=NULL.")
    print("  Format: sd_P/sd_R + oos_P/oos_R + combined_P/combined_R")
    print("=" * 80)
    print(f"\n  {'Threshold':<14}  {'sd_P':>6}  {'sd_R':>6}  "
          f"{'oos_P':>6}  {'oos_R':>6}  {'comb_P':>7}  {'comb_R':>7}  "
          f"{'sd_TP':>6}  {'oos_TP':>6}")
    print(f"  {'-'*14}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*7}  "
          f"{'-'*6}  {'-'*6}")

    # Baseline: uniform criteria on each split
    sd_v34 = _score_criteria(sep_dec, V34)
    oo_v34 = _score_criteria(oos, V34_BASE)
    sd_v35 = _score_criteria(sep_dec, V35)
    oo_v35 = _score_criteria(oos, V35_BASE)

    def _combined(sd_r: dict, oo_r: dict) -> dict:
        tp = sd_r["true_positives"] + oo_r["true_positives"]
        fp = sd_r["false_positives"] + oo_r["false_positives"]
        # _score_criteria has false_negatives; _score_regime_routing has n_prime
        sd_prime = sd_r.get("n_prime", sd_r["true_positives"] + sd_r.get("false_negatives", 0))
        oo_prime = oo_r.get("n_prime", oo_r["true_positives"] + oo_r.get("false_negatives", 0))
        prime = sd_prime + oo_prime
        return {
            "precision": tp / (tp + fp) if (tp + fp) > 0 else 0.0,
            "recall": tp / prime if prime > 0 else 0.0,
            "true_positives": tp,
        }

    def _row(label: str, sd_r: dict, oo_r: dict) -> None:
        c = _combined(sd_r, oo_r)
        print(
            f"  {label:<14}  {sd_r['precision']*100:5.1f}%  {sd_r['recall']*100:5.1f}%  "
            f"{oo_r['precision']*100:5.1f}%  {oo_r['recall']*100:5.1f}%  "
            f"{c['precision']*100:6.1f}%  {c['recall']*100:6.1f}%  "
            f"{sd_r['true_positives']:6d}  {oo_r['true_positives']:6d}"
        )

    _row("v34 uniform", sd_v34, oo_v34)
    _row("v35 uniform", sd_v35, oo_v35)
    print()
    for thr in [65.0, 67.0, 68.0, 70.0, 72.0]:
        sd_r = _score_regime_routing(sep_dec, breadth, thr, V34, V35)
        oo_r = _score_regime_routing(oos, breadth, thr, V34_BASE, V35_BASE)
        _row(f"2-tier  {thr:.0f}%", sd_r, oo_r)

    # Also show the best threshold in detail (train vs test breakdown)
    print("\n  Best threshold detail — train / test breakdown:")
    print(f"  {'Threshold':<14}  {'tr_P':>6}  {'tr_R':>6}  {'te_P':>6}  {'te_R':>6}  "
          f"{'oos_P':>6}  {'oos_R':>6}")
    print(f"  {'-'*14}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}")
    for thr in [65.0, 67.0, 68.0, 70.0, 72.0]:
        tr_r = _score_regime_routing(train, breadth, thr, V34, V35)
        te_r = _score_regime_routing(test, breadth, thr, V34, V35)
        oo_r = _score_regime_routing(oos, breadth, thr, V34_BASE, V35_BASE)
        print(
            f"  {thr:<14.0f}  {tr_r['precision']*100:5.1f}%  {tr_r['recall']*100:5.1f}%  "
            f"{te_r['precision']*100:5.1f}%  {te_r['recall']*100:5.1f}%  "
            f"{oo_r['precision']*100:5.1f}%  {oo_r['recall']*100:5.1f}%"
        )

    # ── SECTION 3: sma50_above_sma150 boolean gate test ──────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: sma50_above_sma150 boolean gate (#2 KS feature in 2026)")
    print("  V35_BOOL: replace price_vs_sma150_pct_min=5 with sma50_above_sma150=1")
    print("  V35_BOTH: keep both gates simultaneously")
    print("=" * 80)

    print(f"\n  {'Criteria':<46}  {'full_P':>6}  {'full_R':>6}  "
          f"{'tr_P':>6}  {'tr_R':>6}  {'te_P':>6}  {'te_R':>6}  "
          f"{'oos_P':>6}  {'oos_R':>6}")
    print(f"  {'-'*46}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  "
          f"{'-'*6}  {'-'*6}")

    def _four_splits(label: str, crit_full: dict, crit_base: dict) -> None:
        f = _score_criteria(sep_dec, crit_full)
        tr = _score_criteria(train,   crit_full)
        te = _score_criteria(test,    crit_full)
        oo = _score_criteria(oos,     crit_base)
        print(
            f"  {label:<46}  "
            f"{f['precision']*100:5.1f}%  {f['recall']*100:5.1f}%  "
            f"{tr['precision']*100:5.1f}%  {tr['recall']*100:5.1f}%  "
            f"{te['precision']*100:5.1f}%  {te['recall']*100:5.1f}%  "
            f"{oo['precision']*100:5.1f}%  {oo['recall']*100:5.1f}%"
        )

    _four_splits("v35      (pct floor=5, no bool)",  V35,       V35_BASE)
    _four_splits("v35_bool (bool gate, no pct floor)", V35_BOOL, V35_BOOL_BASE)
    _four_splits("v35_both (bool + pct floor=5)",    V35_BOTH,  V35_BOTH_BASE)

    # Check: how many 2026 control rows have sma50_above_sma150=0 vs prime rows
    oos_prime = [f for f in oos if f["is_prime"] == 1]
    oos_ctrl  = [f for f in oos if f["is_prime"] == 0]
    p_bool = sum(1 for f in oos_prime if f.get("sma50_above_sma150") == 1)
    c_bool = sum(1 for f in oos_ctrl  if f.get("sma50_above_sma150") == 1)
    print(f"\n  2026 OOS: sma50_above_sma150=1 rate —"
          f" prime={p_bool}/{len(oos_prime)} ({p_bool/len(oos_prime)*100:.1f}%)"
          f"  ctrl={c_bool}/{len(oos_ctrl)} ({c_bool/len(oos_ctrl)*100:.1f}%)")

    # ── SECTION 4: Consumer Cyclical regression fix ───────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: Consumer Cyclical regression fix")
    print("  v35 has 0 CC TPs vs v34's 3 (all AMZN, Oct 2025).")
    print("  AMZN TPs sit at ema200_pct≈1% and sma150_pct≈2% — both below v35 floors of 5%.")
    print("  V35_CC_FIX exempts CC from BOTH new V35 trend floors (ema200 and sma150).")
    print("  analyze.py updated: price_vs_ema200_pct_min and price_vs_sma150_pct_min")
    print("  both support CC-specific overrides via consumer_cyclical_* keys.")
    print("=" * 80)

    # Show the failing CC prime rows
    cc_prime_sd = [f for f in sep_dec if f["is_prime"] == 1 and f.get("sector") == "Consumer Cyclical"]
    print(f"\n  CC prime rows in Sep-Dec 2025 ({len(cc_prime_sd)} total):")
    print(f"  {'date':<12}  {'ticker':<8}  {'sma150_pct':>11}  {'ema200_pct':>11}  "
          f"{'pass_v34':>9}  {'pass_v35':>9}")
    print(f"  {'-'*12}  {'-'*8}  {'-'*11}  {'-'*11}  {'-'*9}  {'-'*9}")
    for f in sorted(cc_prime_sd, key=lambda x: x["date"]):
        sma150_pct = f.get("price_vs_sma150_pct")
        ema200_pct = f.get("price_vs_ema200_pct")
        pv34 = _apply_criteria(f, V34)
        pv35 = _apply_criteria(f, V35)
        sma150_str = f"{sma150_pct:9.1f}%" if sma150_pct is not None else "       NULL"
        ema200_str = f"{ema200_pct:9.1f}%" if ema200_pct is not None else "       NULL"
        print(
            f"  {f['date']:<12}  {f['ticker']:<8}  {sma150_str:>11}  {ema200_str:>11}  "
            f"  {'✓' if pv34 else '✗':>8}    {'✓' if pv35 else '✗':>8}"
        )

    print("\n  Full-split comparison: v35 vs V35_CC_FIX:")
    print(f"\n  {'Criteria':<46}  {'full_P':>6}  {'full_R':>6}  "
          f"{'tr_P':>6}  {'tr_R':>6}  {'te_P':>6}  {'te_R':>6}  "
          f"{'oos_P':>6}  {'oos_R':>6}")
    print(f"  {'-'*46}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  "
          f"{'-'*6}  {'-'*6}")
    _four_splits("v35           (CC fails sma150 floor)", V35,         V35_BASE)
    _four_splits("v35_cc_fix    (CC sma150 floor = 0%)", V35_CC_FIX,  V35_CC_FIX_BASE)

    # Also show 2026 CC prime rows
    cc_prime_oos = [f for f in oos if f["is_prime"] == 1 and f.get("sector") == "Consumer Cyclical"]
    if cc_prime_oos:
        print(f"\n  CC prime rows in 2026 OOS ({len(cc_prime_oos)} total):")
        print(f"  {'date':<12}  {'ticker':<8}  {'sma150_pct':>11}  {'ema200_pct':>11}  "
              f"{'pass_v35b':>10}  {'pass_fix_b':>11}")
        print(f"  {'-'*12}  {'-'*8}  {'-'*11}  {'-'*11}  {'-'*10}  {'-'*11}")
        for f in sorted(cc_prime_oos, key=lambda x: x["date"]):
            sma150_pct = f.get("price_vs_sma150_pct")
            ema200_pct = f.get("price_vs_ema200_pct")
            pv35b = _apply_criteria(f, V35_BASE)
            pfixb = _apply_criteria(f, V35_CC_FIX_BASE)
            sma150_str = f"{sma150_pct:9.1f}%" if sma150_pct is not None else "       NULL"
            ema200_str = f"{ema200_pct:9.1f}%" if ema200_pct is not None else "       NULL"
            print(
                f"  {f['date']:<12}  {f['ticker']:<8}  {sma150_str:>11}  {ema200_str:>11}  "
                f"  {'✓' if pv35b else '✗':>9}    {'✓' if pfixb else '✗':>10}"
            )

    # CC sector breakdown for v35 vs v35_cc_fix
    print("\n  CC sector detail — Sep-Dec 2025 full:")
    print(f"  {'Criteria':<26}  {'CC_TP':>6}  {'CC_FP':>6}  {'CC_P':>7}")
    print(f"  {'-'*26}  {'-'*6}  {'-'*6}  {'-'*7}")
    cc_rows_sd = [f for f in sep_dec if f.get("sector") == "Consumer Cyclical"]
    for label, crit in [("v34", V34), ("v35", V35), ("v35_cc_fix", V35_CC_FIX)]:
        cc_tp = sum(1 for f in cc_rows_sd if f["is_prime"] == 1 and _apply_criteria(f, crit))
        cc_fp = sum(1 for f in cc_rows_sd if f["is_prime"] == 0 and _apply_criteria(f, crit))
        cc_p  = cc_tp / (cc_tp + cc_fp) if (cc_tp + cc_fp) > 0 else 0.0
        print(f"  {label:<26}  {cc_tp:6d}  {cc_fp:6d}  {cc_p*100:6.1f}%")

    print("\n--- Session 22 complete ---")


if __name__ == "__main__":
    main()
