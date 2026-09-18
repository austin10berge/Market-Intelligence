"""Session 26 — consumer_cyclical_rsi_max audit on V38.

Session 13 set cc_rsi_max=44 based on V29. Since then, the criteria have changed
significantly (V34→V38). The CC sector now has the ema200 override added in V36/V37
(consumer_cyclical_price_vs_ema200_pct_min=0), which recovered AMZN. Tech RSI was
loosened from 54→60 in session 25 (+3.8pp test P). The CC gate may be similarly
miscalibrated.

Experiments:
  1. CC sector profiling on V38 — which tickers/dates survive V38 filtering?
     RSI distributions prime vs ctrl within CC sector, per split.
  2. consumer_cyclical_rsi_max sweep 40→65 on V38, full 4-split scorecard.
  3. V39 definition (if a better value exists).
  4. Date-level breakdown: which CC primes are blocked by cc_rsi_max=44?

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session26
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

V38 = {
    **V31A,
    "price_vs_ema200_pct_min": 5,
    "sma50_above_sma150": 1,
    "bb_width_pct_max": 20.0,
    "volume_ratio_max": 1.15,
    "iv_rv_min": 1.0,
    "pcr_vol_max": 2.0,
    "consumer_cyclical_price_vs_ema200_pct_min": 0,
    "technology_rsi_max": 60,  # relaxed from 54 in session 25
}

V38_BASE = {k: v for k, v in V38.items() if k not in _IV_KEYS}


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


def _ks(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    all_vals = sorted(set(a + b))
    def ecdf(vals: list[float], x: float) -> float:
        return sum(1 for v in vals if v <= x) / len(vals)
    return max(abs(ecdf(a, x) - ecdf(b, x)) for x in all_vals)


def _median(vals: list[float]) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    n = len(s)
    return (s[n // 2] + s[(n - 1) // 2]) / 2


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    idx = int(len(s) * p)
    return s[min(idx, len(s) - 1)]


def _row4(
    label: str,
    sep_dec: list[dict],
    train: list[dict],
    test: list[dict],
    oos: list[dict],
    crit_full: dict,
    crit_base: dict,
) -> None:
    f  = _score_criteria(sep_dec, crit_full)
    tr = _score_criteria(train,   crit_full)
    te = _score_criteria(test,    crit_full)
    oo = _score_criteria(oos,     crit_base)
    print(
        f"  {label:<14}  "
        f"{f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
        f"{tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
        f"{te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
        f"{oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%  "
        f"TP={f['true_positives']:3d}  FP={f['false_positives']:3d}"
    )


def main() -> None:
    all_features = get_all_features()
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

    # ── SECTION 1: CC sector profiling on V38 ────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: Consumer Cyclical sector profiling on V38")
    print("  Which CC tickers/dates survive V38 (without cc_rsi gate)?")
    print("  RSI distributions prime vs control in CC sector, per split.")
    print("=" * 80)

    # V38 without cc_rsi gate to see raw CC survivors
    v38_no_cc_rsi = {k: v for k, v in V38.items() if k != "consumer_cyclical_rsi_max"}
    v38_no_cc_rsi_base = {k: v for k, v in v38_no_cc_rsi.items() if k not in _IV_KEYS}

    print("\n  1A. CC primes and controls that pass V38 (no cc_rsi gate), per split")
    for label, rows in [
        ("Train Sep-Oct", train),
        ("Test  Nov-Dec", test),
        ("2026 OOS",      oos),
        ("Sep-Dec full",  sep_dec),
    ]:
        crit = v38_no_cc_rsi if label != "2026 OOS" else v38_no_cc_rsi_base
        cc_tp = [f for f in rows if f["is_prime"]==1
                 and f.get("sector")=="Consumer Cyclical"
                 and _apply_criteria(f, crit)]
        cc_fp = [f for f in rows if f["is_prime"]==0
                 and f.get("sector")=="Consumer Cyclical"
                 and _apply_criteria(f, crit)]
        # Ticker breakdown
        from collections import Counter
        tp_tickers = Counter(f["ticker"] for f in cc_tp)
        fp_tickers = Counter(f["ticker"] for f in cc_fp)
        print(f"\n  {label}: {len(cc_tp)} CC TPs, {len(cc_fp)} CC FPs")
        if cc_tp:
            print(f"    TP tickers: {dict(tp_tickers.most_common(10))}")
        if cc_fp:
            print(f"    FP tickers: {dict(fp_tickers.most_common(10))}")

    # 1B. RSI distributions in CC sector (no V38 pre-filter — raw sector data)
    print("\n  1B. RSI distribution: CC primes vs CC controls, per split (no V38 filter)")
    print(f"  {'Period':<20}  {'n_prime':>7}  {'n_ctrl':>7}  "
          f"{'prime_med':>9}  {'ctrl_med':>9}  {'prime_p75':>9}  {'ctrl_p75':>9}  {'KS':>6}")
    print(f"  {'-'*20}  {'-'*7}  {'-'*7}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*6}")
    for label, rows in [
        ("Train Sep-Oct", train),
        ("Test  Nov-Dec", test),
        ("2026 OOS",      oos),
        ("Sep-Dec full",  sep_dec),
    ]:
        cc_prime = [f.get("rsi") for f in rows
                    if f["is_prime"]==1 and f.get("sector")=="Consumer Cyclical"
                    and f.get("rsi") is not None]
        cc_ctrl  = [f.get("rsi") for f in rows
                    if f["is_prime"]==0 and f.get("sector")=="Consumer Cyclical"
                    and f.get("rsi") is not None]
        if not cc_prime or not cc_ctrl:
            print(f"  {label:<20}  — no data")
            continue
        ks = _ks(cc_prime, cc_ctrl)
        print(f"  {label:<20}  {len(cc_prime):7d}  {len(cc_ctrl):7d}  "
              f"{_median(cc_prime):9.1f}  {_median(cc_ctrl):9.1f}  "
              f"{_percentile(cc_prime, 0.75):9.1f}  {_percentile(cc_ctrl, 0.75):9.1f}  "
              f"{ks:6.3f}")

    # 1C. RSI distribution within V38 survivors (cc_rsi gate removed)
    print("\n  1C. RSI distribution within V38 survivors (no cc_rsi gate), Sep-Dec full")
    crit = v38_no_cc_rsi
    cc_v38_tp = [f for f in sep_dec if f["is_prime"]==1
                 and f.get("sector")=="Consumer Cyclical"
                 and _apply_criteria(f, crit)]
    cc_v38_fp = [f for f in sep_dec if f["is_prime"]==0
                 and f.get("sector")=="Consumer Cyclical"
                 and _apply_criteria(f, crit)]
    if cc_v38_tp and cc_v38_fp:
        tp_rsi = [f["rsi"] for f in cc_v38_tp if f.get("rsi") is not None]
        fp_rsi = [f["rsi"] for f in cc_v38_fp if f.get("rsi") is not None]
        print(f"  V38 CC TPs: n={len(cc_v38_tp)}, RSI med={_median(tp_rsi):.1f}, "
              f"p75={_percentile(tp_rsi, 0.75):.1f}, max={max(tp_rsi):.1f}")
        print(f"  V38 CC FPs: n={len(cc_v38_fp)}, RSI med={_median(fp_rsi):.1f}, "
              f"p75={_percentile(fp_rsi, 0.75):.1f}, max={max(fp_rsi):.1f}")
        ks = _ks(tp_rsi, fp_rsi)
        print(f"  KS(RSI, TP vs FP within V38 CC survivors) = {ks:.3f}")
        print("\n  TP rows: ticker / date / RSI")
        for f in sorted(cc_v38_tp, key=lambda x: (x["ticker"], x["date"])):
            blocked = not _apply_criteria(f, {"consumer_cyclical_rsi_max": 44})
            print(f"    {f['ticker']:<8} {f['date']}  RSI={f.get('rsi', 'N/A'):>6.1f}"
                  f"  {'← blocked by cc_rsi=44' if blocked else ''}")
        print("\n  FP rows (RSI > 44): ticker / date / RSI")
        for f in sorted(cc_v38_fp, key=lambda x: x.get("rsi") or 0, reverse=True)[:15]:
            if (f.get("rsi") or 0) > 44:
                print(f"    {f['ticker']:<8} {f['date']}  RSI={f.get('rsi', 'N/A'):>6.1f}")
    else:
        print(f"  V38 CC TPs: {len(cc_v38_tp)}, FPs: {len(cc_v38_fp)}")

    # ── SECTION 2: cc_rsi sweep on V38 ───────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: consumer_cyclical_rsi_max sweep on V38 — full 4-split scorecard")
    print("  Baseline V38 uses cc_rsi_max=44 (inherited from V31A).")
    print("=" * 80)

    print(f"\n  {'cc_rsi_max':>10}  {'full P/R':>10}  {'train P/R':>10}  "
          f"{'test P/R':>10}  {'oos P/R':>10}  sd_TP  sd_FP  oos_TP")
    print(f"  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*5}  {'-'*5}  {'-'*6}")

    rsi_results: dict[int | str, dict] = {}
    sweep_vals: list[int | None] = [38, 40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 65]
    for rsi_val in sweep_vals:
        crit = {**V38, "consumer_cyclical_rsi_max": rsi_val}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        f  = _score_criteria(sep_dec, crit)
        tr = _score_criteria(train,   crit)
        te = _score_criteria(test,    crit)
        oo = _score_criteria(oos,     crit_base)
        rsi_results[rsi_val] = {"full": f, "train": tr, "test": te, "oos": oo}
        print(
            f"  {rsi_val:>10}  "
            f"{f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
            f"{tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
            f"{te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
            f"{oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%  "
            f"{f['true_positives']:5d}  {f['false_positives']:5d}  "
            f"{oo['true_positives']:6d}"
        )

    # Also test removing cc_rsi gate entirely
    crit_no_cc = {k: v for k, v in V38.items() if k != "consumer_cyclical_rsi_max"}
    crit_no_cc_base = {k: v for k, v in crit_no_cc.items() if k not in _IV_KEYS}
    f  = _score_criteria(sep_dec, crit_no_cc)
    tr = _score_criteria(train,   crit_no_cc)
    te = _score_criteria(test,    crit_no_cc)
    oo = _score_criteria(oos,     crit_no_cc_base)
    rsi_results["none"] = {"full": f, "train": tr, "test": te, "oos": oo}
    print(
        f"  {'none':>10}  "
        f"{f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
        f"{tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
        f"{te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
        f"{oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%  "
        f"{f['true_positives']:5d}  {f['false_positives']:5d}  "
        f"{oo['true_positives']:6d}"
    )

    # ── SECTION 3: V39 definition ────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: V39 definition")
    print("  Find best cc_rsi_max that maximises test P without hurting OOS recall.")
    print("=" * 80)

    v38_oos_r  = rsi_results[44]["oos"]["recall"]
    v38_te_p   = rsi_results[44]["test"]["precision"]
    v38_sd_tp  = rsi_results[44]["full"]["true_positives"]

    print(f"\n  V38 baseline (cc_rsi=44): OOS_R={v38_oos_r*100:.1f}%, Test_P={v38_te_p*100:.1f}%")

    # Look for improvements: test P up OR oos R up, without the other going down
    print("\n  Candidates vs V38 (cc_rsi=44):")
    print(f"  {'cc_rsi_max':>10}  {'ΔfullP':>7}  {'ΔfullR':>7}  "
          f"{'ΔtrainP':>8}  {'ΔtestP':>7}  {'ΔtestR':>7}  {'ΔoosP':>6}  {'ΔoosR':>6}  {'Δsd_TP':>7}")
    print(f"  {'-'*10}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*7}")

    baseline = rsi_results[44]
    for rsi_val, res in rsi_results.items():
        if rsi_val == 44:
            continue
        df = (res["full"]["precision"]  - baseline["full"]["precision"])  * 100
        dr = (res["full"]["recall"]     - baseline["full"]["recall"])     * 100
        dtr_p = (res["train"]["precision"] - baseline["train"]["precision"]) * 100
        dte_p = (res["test"]["precision"]  - baseline["test"]["precision"])  * 100
        dte_r = (res["test"]["recall"]     - baseline["test"]["recall"])     * 100
        doo_p = (res["oos"]["precision"]   - baseline["oos"]["precision"])   * 100
        doo_r = (res["oos"]["recall"]      - baseline["oos"]["recall"])      * 100
        dsd   = res["full"]["true_positives"] - v38_sd_tp
        print(
            f"  {str(rsi_val):>10}  {df:+6.1f}pp  {dr:+6.1f}pp  "
            f"{dtr_p:+7.1f}pp  {dte_p:+6.1f}pp  {dte_r:+6.1f}pp  "
            f"{doo_p:+5.1f}pp  {doo_r:+5.1f}pp  {dsd:+6d}"
        )

    # Pick best: highest test P among values that don't drop OOS recall below V38
    numeric_results = {k: v for k, v in rsi_results.items() if isinstance(k, int)}
    candidates = {
        rsi_val: res
        for rsi_val, res in numeric_results.items()
        if res["oos"]["recall"] >= v38_oos_r - 0.001
    }
    if candidates:
        best_rsi = max(candidates, key=lambda r: candidates[r]["test"]["precision"])
        best_res = candidates[best_rsi]
        print(f"\n  Best by test P (OOS_R >= {v38_oos_r*100:.1f}%): cc_rsi_max={best_rsi}")
        print(f"    Test P: {best_res['test']['precision']*100:.1f}% "
              f"({(best_res['test']['precision']-v38_te_p)*100:+.1f}pp vs V38)")
        print(f"    OOS recall: {best_res['oos']['recall']*100:.1f}%")

        if best_rsi != 44:
            V39 = {**V38, "consumer_cyclical_rsi_max": best_rsi}
            V39_BASE = {k: v for k, v in V39.items() if k not in _IV_KEYS}

            print(f"\n  V39 = V38 + consumer_cyclical_rsi_max=44 → {best_rsi}")
            print(f"\n  {'Model':<14}  {'full P/R':>10}  {'train P/R':>10}  "
                  f"{'test P/R':>10}  {'oos P/R':>10}  sd_TP  sd_FP")
            print(f"  {'-'*14}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*5}  {'-'*5}")
            _row4("V38",  sep_dec, train, test, oos, V38,  V38_BASE)
            _row4("V39",  sep_dec, train, test, oos, V39,  V39_BASE)
        else:
            print("\n  cc_rsi_max=44 is already optimal on V38 — no V39 improvement.")
    else:
        print(f"\n  No candidates maintain OOS recall >= {v38_oos_r*100:.1f}% — keep cc_rsi=44.")

    # ── SECTION 4: Date-level breakdown of CC prime rows blocked by cc_rsi=44 ─
    print("\n" + "=" * 80)
    print("SECTION 4: CC prime rows blocked by cc_rsi_max=44 (V38 without cc_rsi)")
    print("  Shows which genuine prime picks are filtered by the current CC RSI gate.")
    print("=" * 80)

    cc_rsi_gate = {"consumer_cyclical_rsi_max": 44}
    v38_no_gate = {k: v for k, v in V38.items() if k != "consumer_cyclical_rsi_max"}

    for label, rows in [
        ("Sep-Dec full",  sep_dec),
        ("Train Sep-Oct", train),
        ("Test  Nov-Dec", test),
        ("2026 OOS",      oos),
    ]:
        crit_no = v38_no_gate if label != "2026 OOS" else {
            k: v for k, v in v38_no_gate.items() if k not in _IV_KEYS
        }
        blocked = [
            f for f in rows
            if f["is_prime"] == 1
            and f.get("sector") == "Consumer Cyclical"
            and _apply_criteria(f, crit_no)        # passes V38 minus cc_rsi
            and not _apply_criteria(f, cc_rsi_gate) # fails cc_rsi=44 specifically
        ]
        if not blocked:
            print(f"\n  {label}: 0 CC primes blocked by cc_rsi=44")
            continue
        print(f"\n  {label}: {len(blocked)} CC prime(s) blocked by cc_rsi=44")
        print(f"  {'ticker':<8}  {'date':<12}  {'RSI':>6}  {'bb_width%':>9}  "
              f"{'volume_r':>8}  {'pct_52wk':>8}  {'ema200%':>8}")
        print(f"  {'-'*8}  {'-'*12}  {'-'*6}  {'-'*9}  {'-'*8}  {'-'*8}  {'-'*8}")
        for f in sorted(blocked, key=lambda x: (x["ticker"], x["date"])):
            print(
                f"  {f['ticker']:<8}  {f['date']:<12}  "
                f"{(f.get('rsi') or 0):6.1f}  "
                f"{(f.get('bb_width_pct') or 0):9.2f}  "
                f"{(f.get('volume_ratio') or 0):8.3f}  "
                f"{(f.get('pct_from_52wk_high') or 0):8.2f}  "
                f"{(f.get('price_vs_ema200_pct') or 0):8.2f}"
            )

    # ── SECTION 5: CC sector KS comparison — regime stability ────────────────
    print("\n" + "=" * 80)
    print("SECTION 5: CC sector feature KS across regimes")
    print("  Is cc_rsi still discriminating in 2026 OOS, or regime-specific?")
    print("  (Compare pattern to tech_rsi, which was too tight in test/OOS.)")
    print("=" * 80)

    cc_features = ["rsi", "bb_width_pct", "volume_ratio", "price_vs_ema200_pct",
                   "pct_from_52wk_high", "rv20"]
    print(f"\n  {'feature':<25}  {'KS_train':>8}  {'KS_test':>8}  {'KS_oos':>8}  "
          f"{'n_prime_oos':>11}  {'n_ctrl_oos':>10}")
    print(f"  {'-'*25}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*11}  {'-'*10}")
    for feat in cc_features:
        ks_vals = []
        counts = []
        for rows in [train, test, oos]:
            pv = [f.get(feat) for f in rows
                  if f["is_prime"]==1 and f.get("sector")=="Consumer Cyclical"
                  and f.get(feat) is not None]
            cv = [f.get(feat) for f in rows
                  if f["is_prime"]==0 and f.get("sector")=="Consumer Cyclical"
                  and f.get(feat) is not None]
            ks_vals.append(_ks(pv, cv) if pv and cv else float("nan"))
            counts.append((len(pv), len(cv)))
        oos_p, oos_c = counts[2]
        print(f"  {feat:<25}  {ks_vals[0]:8.3f}  {ks_vals[1]:8.3f}  {ks_vals[2]:8.3f}  "
              f"{oos_p:11d}  {oos_c:10d}")

    print("\n--- Session 26 complete ---")


if __name__ == "__main__":
    main()
