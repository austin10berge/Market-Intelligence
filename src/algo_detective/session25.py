"""Session 25 — V38 (technology_rsi_max relaxation) + pct_from_52wk_high audit.

Two experiments:

A) technology_rsi_max sweep (SECTIONS 1-2):
   Session 24 found that relaxing tech_rsi_max 54→58-66 improves temporal test P/R
   significantly WITHOUT needing ANET. Run a full 4-split scorecard for each value
   and pick the best V38 = V37 + optimal tech_rsi_max.

B) pct_from_52wk_high_max audit (SECTION 3):
   How much does this gate actually help? Is it genuinely discriminating across
   all four splits, or is it an in-sample artifact?
   - Feature distribution: prime vs control per split period
   - KS statistic per period
   - Effect of removing the gate entirely from V37 and V38
   - Threshold sweep on V37 (8, 10, 12, 15, 18, 20, none)
   - How many 2026 OOS primes does it block?

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session25
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

V37 = {
    **V31A,
    "price_vs_ema200_pct_min": 5,
    "sma50_above_sma150": 1,
    "bb_width_pct_max": 20.0,
    "volume_ratio_max": 1.15,
    "iv_rv_min": 1.0,
    "pcr_vol_max": 2.0,
    "consumer_cyclical_price_vs_ema200_pct_min": 0,
}

V37_BASE = {k: v for k, v in V37.items() if k not in _IV_KEYS}


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
    """Two-sample KS statistic (max absolute difference in empirical CDFs)."""
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

    # ── SECTION 1: technology_rsi_max sweep on V37 ───────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: technology_rsi_max sweep on V37 — full 4-split scorecard")
    print("  Baseline V37 uses tech_rsi_max=54.")
    print("  Session 24 found 58-66 improves test P/R; this section shows OOS impact.")
    print("=" * 80)

    hdr = (f"\n  {'tech_rsi_max':>12}  {'full P/R':>10}  {'train P/R':>10}  "
           f"{'test P/R':>10}  {'oos P/R':>10}  sd_TP  sd_FP  oos_TP")
    print(hdr)
    print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*5}  {'-'*5}  {'-'*6}")

    rsi_results = {}
    for rsi_val in [54, 56, 58, 60, 62, 64, 66, 70, 75, 100]:
        crit = {**V37, "technology_rsi_max": rsi_val}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        f  = _score_criteria(sep_dec, crit)
        tr = _score_criteria(train,   crit)
        te = _score_criteria(test,    crit)
        oo = _score_criteria(oos,     crit_base)
        rsi_results[rsi_val] = {"full": f, "train": tr, "test": te, "oos": oo}
        print(
            f"  {rsi_val:>12}  "
            f"{f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
            f"{tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
            f"{te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
            f"{oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%  "
            f"{f['true_positives']:5d}  {f['false_positives']:5d}  {oo['true_positives']:6d}"
        )

    # ── SECTION 2: V38 definition — best tech_rsi_max ────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: V38 definition")
    print("  Pick best technology_rsi_max that maximises test P without hurting OOS recall.")
    print("  V38 = V37 + technology_rsi_max=<best value>")
    print("=" * 80)

    # Find best value: highest test P among candidates with oos_R >= V37 baseline
    v37_oos_r = rsi_results[54]["oos"]["recall"]
    v37_te_p  = rsi_results[54]["test"]["precision"]

    candidates = {
        rsi_val: res
        for rsi_val, res in rsi_results.items()
        if res["oos"]["recall"] >= v37_oos_r - 0.001  # allow tiny float slop
    }

    best_rsi = max(candidates, key=lambda r: candidates[r]["test"]["precision"])
    print(f"\n  V37 OOS baseline recall: {v37_oos_r*100:.1f}% "
          f"(must not regress to define V38)")
    print(f"  Candidates with OOS_R >= {v37_oos_r*100:.1f}%: "
          f"{sorted(candidates.keys())}")
    print(f"  Best by test P: tech_rsi_max={best_rsi} "
          f"(test P={candidates[best_rsi]['test']['precision']*100:.1f}%)")

    V38 = {**V37, "technology_rsi_max": best_rsi}
    V38_BASE = {k: v for k, v in V38.items() if k not in _IV_KEYS}

    print(f"\n  V38 = V37 + technology_rsi_max=54 → {best_rsi}")
    print(f"\n  {'Model':<14}  {'full P/R':>10}  {'train P/R':>10}  "
          f"{'test P/R':>10}  {'oos P/R':>10}  sd_TP  sd_FP")
    print(f"  {'-'*14}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*5}  {'-'*5}")
    _row4("V37",  sep_dec, train, test, oos, V37,  V37_BASE)
    _row4("V38",  sep_dec, train, test, oos, V38,  V38_BASE)

    # Delta
    v37f = _score_criteria(sep_dec, V37)
    v38f = _score_criteria(sep_dec, V38)
    v37t = _score_criteria(train,   V37)
    v38t = _score_criteria(train,   V38)
    v37e = _score_criteria(test,    V37)
    v38e = _score_criteria(test,    V38)
    v37o = _score_criteria(oos,     V37_BASE)
    v38o = _score_criteria(oos,     V38_BASE)
    print(
        f"\n  {'V37→V38 Δ':<14}  "
        f"{(v38f['precision']-v37f['precision'])*100:+4.1f}pp/{(v38f['recall']-v37f['recall'])*100:+4.1f}pp  "
        f"{(v38t['precision']-v37t['precision'])*100:+4.1f}pp/{(v38t['recall']-v37t['recall'])*100:+4.1f}pp  "
        f"{(v38e['precision']-v37e['precision'])*100:+4.1f}pp/{(v38e['recall']-v37e['recall'])*100:+4.1f}pp  "
        f"{(v38o['precision']-v37o['precision'])*100:+4.1f}pp/{(v38o['recall']-v37o['recall'])*100:+4.1f}pp"
    )

    # V38 sector breakdown
    print("\n  V38 sector breakdown — Sep-Dec 2025 full:")
    print(f"  {'Sector':<26}  {'V37_TP':>6}  {'V37_FP':>6}  "
          f"{'V38_TP':>6}  {'V38_FP':>6}  {'V38_P':>7}  {'ΔTP':>4}")
    print(f"  {'-'*26}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*4}")
    sectors: dict[str, list] = {}
    for f in sep_dec:
        sectors.setdefault(f.get("sector") or "Unknown", []).append(f)
    for sector in sorted(sectors):
        rows = sectors[sector]
        tp37 = sum(1 for f in rows if f["is_prime"]==1 and _apply_criteria(f, V37))
        fp37 = sum(1 for f in rows if f["is_prime"]==0 and _apply_criteria(f, V37))
        tp38 = sum(1 for f in rows if f["is_prime"]==1 and _apply_criteria(f, V38))
        fp38 = sum(1 for f in rows if f["is_prime"]==0 and _apply_criteria(f, V38))
        p38  = tp38 / (tp38 + fp38) if (tp38 + fp38) > 0 else 0.0
        if tp37 != tp38 or fp37 != fp38:
            print(f"  {sector:<26}  {tp37:6d}  {fp37:6d}  {tp38:6d}  {fp38:6d}  "
                  f"{p38*100:6.1f}%  {tp38-tp37:+4d}")

    # ── SECTION 3: pct_from_52wk_high audit ──────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: pct_from_52wk_high_max audit")
    print("  Current value: 12%. How much is it actually helping?")
    print("  Is it genuinely discriminating, or an in-sample artifact?")
    print("=" * 80)

    # 3A. Feature distribution per split period
    print("\n  3A. Feature distribution — prime vs control, per period")
    print(f"  {'Period':<20}  {'n_prime':>7}  {'n_ctrl':>7}  "
          f"{'prime_med':>9}  {'ctrl_med':>9}  {'prime_p90':>9}  {'ctrl_p90':>9}  {'KS':>6}")
    print(f"  {'-'*20}  {'-'*7}  {'-'*7}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*6}")

    feat = "pct_from_52wk_high"
    for label, rows in [
        ("Train Sep-Oct", train),
        ("Test  Nov-Dec", test),
        ("2026 OOS",      oos),
        ("Sep-Dec full",  sep_dec),
    ]:
        prime_vals = [f.get(feat) for f in rows if f["is_prime"]==1 and f.get(feat) is not None]
        ctrl_vals  = [f.get(feat) for f in rows if f["is_prime"]==0 and f.get(feat) is not None]
        if not prime_vals or not ctrl_vals:
            continue
        p90_p = sorted(prime_vals)[int(len(prime_vals)*0.9)]
        p90_c = sorted(ctrl_vals )[int(len(ctrl_vals )*0.9)]
        ks    = _ks(prime_vals, ctrl_vals)
        print(f"  {label:<20}  {len(prime_vals):7d}  {len(ctrl_vals):7d}  "
              f"{_median(prime_vals):9.2f}  {_median(ctrl_vals):9.2f}  "
              f"{p90_p:9.2f}  {p90_c:9.2f}  {ks:6.3f}")

    # 3B. Gate impact: how many TPs and FPs does pfh_max=12 remove?
    print("\n  3B. Gate impact of pct_from_52wk_high_max=12 on V37")
    print("  (rows that FAIL this single gate on each split)")
    print(f"  {'Period':<20}  {'prime_blocked':>13}  {'ctrl_blocked':>12}  "
          f"{'prime_pass_rate':>15}  {'ctrl_pass_rate':>14}")
    print(f"  {'-'*20}  {'-'*13}  {'-'*12}  {'-'*15}  {'-'*14}")

    pfh_gate = {"pct_from_52wk_high_max": 12}
    for label, rows in [
        ("Train Sep-Oct", train),
        ("Test  Nov-Dec", test),
        ("2026 OOS",      oos),
        ("Sep-Dec full",  sep_dec),
    ]:
        prime_rows = [f for f in rows if f["is_prime"]==1]
        ctrl_rows  = [f for f in rows if f["is_prime"]==0]
        prime_pass = sum(1 for f in prime_rows if _apply_criteria(f, pfh_gate))
        ctrl_pass  = sum(1 for f in ctrl_rows  if _apply_criteria(f, pfh_gate))
        prime_blocked = len(prime_rows) - prime_pass
        ctrl_blocked  = len(ctrl_rows)  - ctrl_pass
        print(f"  {label:<20}  {prime_blocked:13d}  {ctrl_blocked:12d}  "
              f"{prime_pass/len(prime_rows)*100:14.1f}%  "
              f"{ctrl_pass/len(ctrl_rows)*100:13.1f}%")

    # 3C. Full effect: remove pfh gate entirely from V37 and V38
    print("\n  3C. Effect of removing pct_from_52wk_high_max from V37 and V38")
    V37_no_pfh = {k: v for k, v in V37.items() if k != "pct_from_52wk_high_max"}
    V38_no_pfh = {k: v for k, v in V38.items() if k != "pct_from_52wk_high_max"}
    V37_no_pfh_base = {k: v for k, v in V37_no_pfh.items() if k not in _IV_KEYS}
    V38_no_pfh_base = {k: v for k, v in V38_no_pfh.items() if k not in _IV_KEYS}

    print(f"\n  {'Model':<20}  {'full P/R':>10}  {'train P/R':>10}  "
          f"{'test P/R':>10}  {'oos P/R':>10}  sd_TP  sd_FP")
    print(f"  {'-'*20}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*5}  {'-'*5}")
    _row4("V37 (with pfh)", sep_dec, train, test, oos, V37, V37_BASE)
    _row4("V37 (no  pfh)", sep_dec, train, test, oos, V37_no_pfh, V37_no_pfh_base)
    _row4("V38 (with pfh)", sep_dec, train, test, oos, V38, V38_BASE)
    _row4("V38 (no  pfh)", sep_dec, train, test, oos, V38_no_pfh, V38_no_pfh_base)

    # 3D. Threshold sweep: pfh_max at 8, 10, 12, 15, 18, 20, none — on V37
    print("\n  3D. pct_from_52wk_high_max threshold sweep on V37")
    print(f"  {'pfh_max':>8}  {'full P/R':>10}  {'train P/R':>10}  "
          f"{'test P/R':>10}  {'oos P/R':>10}  sd_TP  sd_FP  oos_TP")
    print(f"  {'-'*8}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*5}  {'-'*5}  {'-'*6}")

    for pfh_val in [8, 10, 12, 15, 18, 20, None]:
        if pfh_val is None:
            crit = {k: v for k, v in V37.items() if k != "pct_from_52wk_high_max"}
            label = "none"
        else:
            crit = {**V37, "pct_from_52wk_high_max": pfh_val}
            label = str(pfh_val)
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        f  = _score_criteria(sep_dec, crit)
        tr = _score_criteria(train,   crit)
        te = _score_criteria(test,    crit)
        oo = _score_criteria(oos,     crit_base)
        print(
            f"  {label:>8}  "
            f"{f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
            f"{tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
            f"{te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
            f"{oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%  "
            f"{f['true_positives']:5d}  {f['false_positives']:5d}  {oo['true_positives']:6d}"
        )

    # 3E. Which 2026 OOS prime tickers are blocked by pfh_max=12?
    print("\n  3E. 2026 OOS prime rows blocked by pct_from_52wk_high_max=12")
    oos_prime = sorted(
        [f for f in oos if f["is_prime"] == 1],
        key=lambda x: (x["ticker"], x["date"]),
    )
    blocked_by_pfh = [
        f for f in oos_prime
        if not _apply_criteria(f, pfh_gate)
    ]
    # Also show which of those pass V37_no_pfh (i.e., pfh is the only reason they fail V37)
    print(f"  Total 2026 OOS primes: {len(oos_prime)}, "
          f"blocked by pfh>12: {len(blocked_by_pfh)}")
    print(f"\n  {'ticker':<8}  {'date':<12}  {'pfh%':>6}  {'pass_V37_no_pfh':>16}  first_other_fail")
    print(f"  {'-'*8}  {'-'*12}  {'-'*6}  {'-'*16}  ---------------")
    for f in blocked_by_pfh:
        passes_no_pfh = _apply_criteria(f, V37_no_pfh)
        first_fail = "—"
        if not passes_no_pfh:
            for key, val in V37_no_pfh.items():
                if not _apply_criteria(f, {key: val}):
                    first_fail = key
                    break
        print(
            f"  {f['ticker']:<8}  {f['date']:<12}  "
            f"{(f.get('pct_from_52wk_high') or 0):6.1f}  "
            f"  {'✓' if passes_no_pfh else '✗':>15}  {first_fail}"
        )

    # 3F. KS comparison across features — is pfh a strong discriminator?
    print("\n  3F. KS comparison: pfh vs other key features (prime vs ctrl, Sep-Dec full)")
    print("  (Higher KS = more discriminating)")
    print(f"\n  {'feature':<30}  {'KS_sepDec':>9}  {'KS_train':>8}  {'KS_test':>8}  {'KS_oos':>8}")
    print(f"  {'-'*30}  {'-'*9}  {'-'*8}  {'-'*8}  {'-'*8}")
    key_features = [
        "pct_from_52wk_high", "bb_width_pct", "volume_ratio", "rv20",
        "price_vs_sma150_pct", "price_vs_ema200_pct", "rsi", "adx",
    ]
    for feat in key_features:
        ks_vals = []
        for rows in [sep_dec, train, test, oos]:
            prime_vals = [f.get(feat) for f in rows if f["is_prime"]==1 and f.get(feat) is not None]
            ctrl_vals  = [f.get(feat) for f in rows if f["is_prime"]==0 and f.get(feat) is not None]
            ks_vals.append(_ks(prime_vals, ctrl_vals) if prime_vals and ctrl_vals else 0.0)
        print(f"  {feat:<30}  {ks_vals[0]:9.3f}  {ks_vals[1]:8.3f}  "
              f"{ks_vals[2]:8.3f}  {ks_vals[3]:8.3f}")

    print("\n--- Session 25 complete ---")


if __name__ == "__main__":
    main()
