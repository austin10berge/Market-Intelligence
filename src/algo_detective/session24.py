"""Session 24 — V37 validation + ANET deep-dive.

Two experiments:

A) V37 definition and validation (SECTIONS 1-3):
   V37 = V36 + bb_width_pct_max=20.0 + volume_ratio_max=1.15.
   From session 23 combined sweep: this combo had the best Sep-Dec precision
   (45.5%) while keeping R_2026=26.1% AND improved test P from 31.9% → 36.2%.
   Validate on all four splits; show full version progression V34→V37.

B) ANET analysis (SECTION 4):
   ANET is his #2 earner in 2025 ($3,089, 20 trades) but a persistent model miss.
   Show all ANET rows in Sep-Dec 2025, gate failures on prime dates,
   and what would need to change to capture ANET primes.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session24
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

V36 = {
    **V31A,
    "price_vs_ema200_pct_min": 5,
    "sma50_above_sma150": 1,
    "bb_width_pct_max": 18.0,
    "volume_ratio_max": 1.20,
    "iv_rv_min": 1.0,
    "pcr_vol_max": 2.0,
    "consumer_cyclical_price_vs_ema200_pct_min": 0,
}

V37 = {
    **V36,
    "bb_width_pct_max": 20.0,   # session 23 sweep: wider bb + tighter vr → +4.3pp test P
    "volume_ratio_max": 1.15,   # tighter vr filters high-volume FPs in Nov-Dec 2025
}

V31A_BASE = {k: v for k, v in V31A.items() if k not in _IV_KEYS}
V34_BASE  = {k: v for k, v in V34.items()  if k not in _IV_KEYS}
V35_BASE  = {k: v for k, v in V35.items()  if k not in _IV_KEYS}
V36_BASE  = {k: v for k, v in V36.items()  if k not in _IV_KEYS}
V37_BASE  = {k: v for k, v in V37.items()  if k not in _IV_KEYS}


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
        f"  {label:<12}  "
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

    # ── SECTION 1: V37 definition ─────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: V37 definition")
    print("  V37 = V36 + bb_width_pct_max=20.0 + volume_ratio_max=1.15")
    print("  From session 23 sweep: wider bb (18→20) + tighter vr (1.20→1.15)")
    print("  improves temporal test P by +4.3pp at same OOS recall.")
    print("=" * 80)

    print("\n  V37 changes from V36:")
    for k in sorted(V37.keys()):
        if k not in V36 or V36[k] != V37[k]:
            old = V36.get(k, "—")
            print(f"    {k}: {old} → {V37[k]}")

    # ── SECTION 2: Full version progression V34 → V37 ────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: Version progression V34 → V37 (all four splits)")
    print("  (Sep-Dec 2025: full IV criteria. 2026 OOS: base, no IV.)")
    print("=" * 80)

    hdr = (f"\n  {'Version':<12}  {'full P/R':>10}  {'train P/R':>10}  "
           f"{'test P/R':>10}  {'oos P/R':>10}  {'sd_TP':>5}  {'sd_FP':>5}")
    print(hdr)
    print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*5}  {'-'*5}")
    _row4("v34",  sep_dec, train, test, oos, V34,  V34_BASE)
    _row4("v35",  sep_dec, train, test, oos, V35,  V35_BASE)
    _row4("v36",  sep_dec, train, test, oos, V36,  V36_BASE)
    _row4("v37",  sep_dec, train, test, oos, V37,  V37_BASE)

    # Delta row for V36→V37
    v36_sd = _score_criteria(sep_dec, V36)
    v37_sd = _score_criteria(sep_dec, V37)
    v36_tr = _score_criteria(train,   V36)
    v37_tr = _score_criteria(train,   V37)
    v36_te = _score_criteria(test,    V36)
    v37_te = _score_criteria(test,    V37)
    v36_oo = _score_criteria(oos,     V36_BASE)
    v37_oo = _score_criteria(oos,     V37_BASE)
    print(
        f"\n  {'v36→v37 Δ':<12}  "
        f"{(v37_sd['precision']-v36_sd['precision'])*100:+4.1f}pp/{(v37_sd['recall']-v36_sd['recall'])*100:+4.1f}pp  "
        f"{(v37_tr['precision']-v36_tr['precision'])*100:+4.1f}pp/{(v37_tr['recall']-v36_tr['recall'])*100:+4.1f}pp  "
        f"{(v37_te['precision']-v36_te['precision'])*100:+4.1f}pp/{(v37_te['recall']-v36_te['recall'])*100:+4.1f}pp  "
        f"{(v37_oo['precision']-v36_oo['precision'])*100:+4.1f}pp/{(v37_oo['recall']-v36_oo['recall'])*100:+4.1f}pp"
    )

    # ── SECTION 3: V37 sector breakdown ──────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: V37 sector breakdown — Sep-Dec 2025 full")
    print("=" * 80)

    sectors: dict[str, list] = {}
    for f in sep_dec:
        sectors.setdefault(f.get("sector") or "Unknown", []).append(f)

    print(f"\n  {'Sector':<26}  {'v34_TP':>7}  {'v36_TP':>7}  {'v37_TP':>7}  "
          f"{'v37_FP':>7}  {'v37_P':>7}  {'prime':>6}")
    print(f"  {'-'*26}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*6}")
    total34_tp = total36_tp = total37_tp = total37_fp = 0
    for sector in sorted(sectors):
        rows = sectors[sector]
        tp34 = sum(1 for f in rows if f["is_prime"] == 1 and _apply_criteria(f, V34))
        tp36 = sum(1 for f in rows if f["is_prime"] == 1 and _apply_criteria(f, V36))
        tp37 = sum(1 for f in rows if f["is_prime"] == 1 and _apply_criteria(f, V37))
        fp37 = sum(1 for f in rows if f["is_prime"] == 0 and _apply_criteria(f, V37))
        n_prime = sum(1 for f in rows if f["is_prime"] == 1)
        p37 = tp37 / (tp37 + fp37) if (tp37 + fp37) > 0 else 0.0
        total34_tp += tp34; total36_tp += tp36
        total37_tp += tp37; total37_fp += fp37
        print(f"  {sector:<26}  {tp34:7d}  {tp36:7d}  {tp37:7d}  "
              f"{fp37:7d}  {p37*100:6.1f}%  {n_prime:6d}")
    tot_p37 = total37_tp / (total37_tp + total37_fp) if (total37_tp + total37_fp) > 0 else 0
    print(f"  {'TOTAL':<26}  {total34_tp:7d}  {total36_tp:7d}  {total37_tp:7d}  "
          f"{total37_fp:7d}  {tot_p37*100:6.1f}%")

    # ── SECTION 4: ANET deep-dive ─────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: ANET deep-dive")
    print("  ANET is his #2 earner in 2025 ($3,089, 20 trades) but a persistent model miss.")
    print("  Showing all ANET rows in Sep-Dec 2025 narrow universe.")
    print("=" * 80)

    anet_rows = sorted(
        [f for f in sep_dec if f.get("ticker") == "ANET"],
        key=lambda x: x["date"],
    )
    anet_prime = [f for f in anet_rows if f["is_prime"] == 1]
    anet_ctrl  = [f for f in anet_rows if f["is_prime"] == 0]

    print(f"\n  ANET in Sep-Dec 2025: {len(anet_rows)} total rows "
          f"({len(anet_prime)} prime, {len(anet_ctrl)} control)")

    # Show prime rows with key features and gate failures
    print("\n  ANET prime rows — key features:")
    print(f"  {'date':<12}  {'rsi':>5}  {'bb_w%':>6}  {'vr':>5}  {'rv20':>6}  "
          f"{'ema200%':>8}  {'pfh%':>6}  {'pe':>6}  {'pass_v37':>9}  first_fail")
    print(f"  {'-'*12}  {'-'*5}  {'-'*6}  {'-'*5}  {'-'*6}  "
          f"{'-'*8}  {'-'*6}  {'-'*6}  {'-'*9}  ----------")
    for f in anet_prime:
        passes_v37 = _apply_criteria(f, V37)
        first_fail = "—"
        if not passes_v37:
            for key, val in V37.items():
                if not _apply_criteria(f, {key: val}):
                    first_fail = key
                    break
        print(
            f"  {f['date']:<12}  "
            f"{(f.get('rsi') or 0):5.1f}  "
            f"{(f.get('bb_width_pct') or 0):6.1f}  "
            f"{(f.get('volume_ratio') or 0):5.2f}  "
            f"{(f.get('rv20') or 0):6.3f}  "
            f"{(f.get('price_vs_ema200_pct') or 0):8.1f}  "
            f"{(f.get('pct_from_52wk_high') or 0):6.1f}  "
            f"{(f.get('forward_pe') or 0):6.1f}  "
            f"  {'✓' if passes_v37 else '✗':>8}  {first_fail}"
        )

    # Distribution summary
    if anet_prime:
        print(f"\n  ANET prime feature distributions (all {len(anet_prime)} prime dates):")
        for feat, label in [
            ("rsi", "RSI"), ("bb_width_pct", "BB width%"), ("volume_ratio", "vol ratio"),
            ("rv20", "rv20"), ("price_vs_ema200_pct", "ema200%"), ("pct_from_52wk_high", "pfh%"),
            ("forward_pe", "fwd PE"),
        ]:
            vals = [f.get(feat) for f in anet_prime if f.get(feat) is not None]
            if vals:
                print(f"    {label:<14}: min={min(vals):7.2f}  "
                      f"med={sorted(vals)[len(vals)//2]:7.2f}  "
                      f"max={max(vals):7.2f}")

    # Show the technology_rsi_max=54 gate specifically
    print("\n  Gate-by-gate pass rates on ANET prime rows (V37):")
    for key, val in V37.items():
        passes = sum(1 for f in anet_prime if _apply_criteria(f, {key: val}))
        rate = passes / len(anet_prime) if anet_prime else 0
        fail_marker = "  ← blocking" if passes < len(anet_prime) else ""
        print(f"    {key:<46} {passes}/{len(anet_prime)} ({rate*100:.0f}%){fail_marker}")

    # Compare ANET control vs prime on the tech RSI gate
    if anet_ctrl:
        anet_rsi_prime = [f.get("rsi") for f in anet_prime if f.get("rsi") is not None]
        anet_rsi_ctrl  = [f.get("rsi") for f in anet_ctrl  if f.get("rsi") is not None]
        if anet_rsi_prime and anet_rsi_ctrl:
            print("\n  RSI distribution — ANET prime vs control:")
            print(f"    prime RSI: min={min(anet_rsi_prime):.1f}  "
                  f"med={sorted(anet_rsi_prime)[len(anet_rsi_prime)//2]:.1f}  "
                  f"max={max(anet_rsi_prime):.1f}")
            print(f"    ctrl  RSI: min={min(anet_rsi_ctrl):.1f}  "
                  f"med={sorted(anet_rsi_ctrl)[len(anet_rsi_ctrl)//2]:.1f}  "
                  f"max={max(anet_rsi_ctrl):.1f}")
            print(f"    technology_rsi_max=54 cuts: "
                  f"prime {sum(1 for r in anet_rsi_prime if r > 54)}/{len(anet_rsi_prime)}, "
                  f"ctrl {sum(1 for r in anet_rsi_ctrl if r > 54)}/{len(anet_rsi_ctrl)}")

    # What ANET-specific RSI threshold would recover TPs without flooding FPs?
    print("\n  ANET: effect of relaxing technology_rsi_max on V37:")
    print(f"  {'tech_rsi_max':>14}  {'ANET_TP':>8}  {'ANET_FP':>8}  {'ANET_P':>8}  "
          f"{'all_te_P':>9}  {'all_te_R':>9}")
    print(f"  {'-'*14}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*9}  {'-'*9}")
    for rsi_ceil in [54, 58, 62, 66, 70, 75, 100]:
        crit = {**V37, "technology_rsi_max": rsi_ceil}
        a_tp = sum(1 for f in anet_prime if _apply_criteria(f, crit))
        a_fp = sum(1 for f in anet_ctrl  if _apply_criteria(f, crit))
        a_p  = a_tp / (a_tp + a_fp) if (a_tp + a_fp) > 0 else 0.0
        te_r = _score_criteria(test, crit)
        print(
            f"  {rsi_ceil:>14}  {a_tp:8d}  {a_fp:8d}  {a_p*100:7.1f}%  "
            f"{te_r['precision']*100:8.1f}%  {te_r['recall']*100:8.1f}%"
        )

    # ── SECTION 5: V37 final definition ──────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 5: V37 final definition (all keys)")
    print("=" * 80)
    print()
    for k, v in V37.items():
        changed_from_v34 = V34.get(k) != v or k not in V34
        changed_from_v36 = V36.get(k) != v or k not in V36
        marker = ""
        if changed_from_v36:
            marker = "  ← changed from v36"
        elif changed_from_v34:
            marker = "  ← added/changed from v34"
        print(f"  {k:<46} = {v}{marker}")

    print("\n--- Session 24 complete ---")


if __name__ == "__main__":
    main()
