"""Session 32 — V41 confirmation + financials_volume_ratio sweep.

Context:
  V41 = V40 + bb_width_pct_max=21.0
  Session 31 showed bb_width=21 recovers NVDA (bb=20.07) and C (bb=20.20) with
  0 Sep-Dec TPs lost and +7 FPs. This session confirms exact 4-split scorecard.

  Also: C (2026-06-09) is blocked ONLY by financials_volume_ratio_max=0.9 (actual=1.14).
  Global volume_ratio passes (1.14 < 1.15). Relaxing fin_volratio to 1.15 might
  recover C Jun9 at low FP cost — worth a targeted sweep.

  Also checks: price_vs_ema200_pct_max=42 ceiling (blocks LRCX/AMAT but both are
  multi-gate failures in OOS; check Sep-Dec impact and FP cost of relaxing).

Sections:
  1. V41 confirmation: full 4-split scorecard
  2. financials_volume_ratio_max sweep: 0.9→1.15 (recover C Jun9?)
  3. Combined V41 + relaxed fin_volratio candidates
  4. price_vs_ema200_pct_max ceiling sweep

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session32
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
    "technology_rsi_max": 60,
}

V39 = {**V38, "adr20_pct_max": 4.0}

V40 = {
    **V39,
    "industrials_rsi_max": 70,
    "financials_rsi_max": 70,
    "healthcare_rsi_max": 60,
}

V41 = {**V40, "bb_width_pct_max": 21.0}

V40_BASE = {k: v for k, v in V40.items() if k not in _IV_KEYS}
V41_BASE = {k: v for k, v in V41.items() if k not in _IV_KEYS}


# ── helpers ────────────────────────────────────────────────────────────────────

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
        f"  {label:<32}  "
        f"sd={f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
        f"tr={tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
        f"te={te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
        f"oos={oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%  "
        f"sdTP={f['true_positives']:3d}  oosTP={oo['true_positives']:2d}"
    )


def _median(vals: list[float]) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    n = len(s)
    return (s[n // 2] + s[(n - 1) // 2]) / 2


# ── main ──────────────────────────────────────────────────────────────────────

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
    print(f"2026 OOS     : {len(oos)} rows, "
          f"{sum(1 for f in oos if f['is_prime']==1)} prime")
    print("\n  (header: sd=Sep-Dec full  tr=Train Sep-Oct  te=Test Nov-Dec  oos=2026 OOS)")

    # ── SECTION 1: V41 confirmation ───────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: V41 = V40 + bb_width_pct_max=21.0 — 4-split scorecard")
    print("  Session 31 sweep showed: +2 OOS TPs (NVDA, C Apr28), 0 sd TPs lost, +7 FPs.")
    print("=" * 80)

    print(f"\n  {'Criteria':<32}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V40 (baseline)", sep_dec, train, test, oos, V40, V40_BASE)
    _row4("V41 (bb_width<=21)", sep_dec, train, test, oos, V41, V41_BASE)

    # Show which OOS TPs are newly captured in V41
    v40_oos_hits = {(f["date"], f["ticker"]) for f in oos if f["is_prime"]==1 and _apply_criteria(f, V40_BASE)}  # noqa: E501
    v41_oos_hits = {(f["date"], f["ticker"]) for f in oos if f["is_prime"]==1 and _apply_criteria(f, V41_BASE)}  # noqa: E501
    newly_captured = v41_oos_hits - v40_oos_hits
    print("\n  1A. Newly captured OOS TPs in V41 (vs V40):")
    if newly_captured:
        for date, ticker in sorted(newly_captured):
            row = next(f for f in oos if f["date"]==date and f["ticker"]==ticker)
            print(f"    {date} {ticker:6s}  bb_width_pct={row.get('bb_width_pct', 'N/A'):.2f}")
    else:
        print("    None")

    # Show newly added Sep-Dec FPs
    v40_sd_fps = {(f["date"], f["ticker"]) for f in sep_dec if f["is_prime"]==0 and _apply_criteria(f, V40_BASE)}  # noqa: E501
    v41_sd_fps = {(f["date"], f["ticker"]) for f in sep_dec if f["is_prime"]==0 and _apply_criteria(f, V41_BASE)}  # noqa: E501
    new_fps = v41_sd_fps - v40_sd_fps
    print(f"\n  1B. New Sep-Dec FPs added by bb_width relaxation ({len(new_fps)} total):")
    for date, ticker in sorted(new_fps):
        row = next(f for f in sep_dec if f["date"]==date and f["ticker"]==ticker)
        print(
            f"    {date} {ticker:6s}  sector={row.get('sector','?')[:20]:<20}  "
            f"bb_width={row.get('bb_width_pct', 'N/A'):.2f}"
        )

    # ── SECTION 2: financials_volume_ratio_max sweep ──────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: financials_volume_ratio_max sweep on V41")
    print("  C (2026-06-09): volume_ratio=1.14, blocked ONLY by fin_volratio_max=0.9.")
    print("  Global volume_ratio passes (1.14 < 1.15). Can we recover C Jun9 cheaply?")
    print("=" * 80)

    # Show financials FP volume_ratio distribution in V41_BASE
    fin_fps_v41 = [
        f for f in sep_dec
        if f["is_prime"]==0
        and f.get("sector")=="Financial Services"
        and _apply_criteria(f, V41_BASE)
    ]
    fin_vr_vals = [f["volume_ratio"] for f in fin_fps_v41 if f.get("volume_ratio") is not None]
    fin_tps_v41 = [
        f for f in sep_dec
        if f["is_prime"]==1
        and f.get("sector")=="Financial Services"
        and _apply_criteria(f, V41_BASE)
    ]
    fin_tp_vr = [f["volume_ratio"] for f in fin_tps_v41 if f.get("volume_ratio") is not None]

    print(f"\n  Financials in V41 (Sep-Dec): {len(fin_tps_v41)} TPs, {len(fin_fps_v41)} FPs")
    print(f"  Financials TP vr: med={_median(fin_tp_vr):.3f}  "
          f"(all should be <=0.9 since fin_volratio gate already filters)")
    print(f"  Financials FP vr: med={_median(fin_vr_vals):.3f}")

    print("\n  2A. Financials FPs by volume_ratio bucket (V41_BASE):")
    buckets = [(0.0, 0.90), (0.90, 0.95), (0.95, 1.00), (1.00, 1.05), (1.05, 1.10),
               (1.10, 1.15), (1.15, 1.20), (1.20, 9.99)]
    for lo, hi in buckets:
        n = sum(1 for v in fin_vr_vals if lo <= v < hi)
        label = f"[{lo:.2f}, {hi:.2f})"
        print(f"    {label}: {n} FPs")

    print("\n  2B. 4-split scorecard: financials_volume_ratio_max sweep on V41")
    print(f"  {'Criteria':<32}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V41 (baseline)", sep_dec, train, test, oos, V41, V41_BASE)

    for fvr in [0.90, 0.95, 1.00, 1.05, 1.10, 1.15]:
        crit = {**V41, "financials_volume_ratio_max": fvr}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V41+fin_vr<={fvr:.2f}", sep_dec, train, test, oos, crit, crit_base)

    print("\n  2C. OOS primes blocked by fin_volratio at each threshold (show unblocked TPs):")
    for fvr in [0.90, 0.95, 1.00, 1.05, 1.10, 1.15]:
        crit_base = {**V41_BASE, "financials_volume_ratio_max": fvr}
        oos_hits = [f for f in oos if f["is_prime"]==1 and _apply_criteria(f, crit_base)]
        newly = [f for f in oos_hits if (f["date"], f["ticker"]) not in v41_oos_hits]
        if newly:
            for f in newly:
                print(f"    fvr<={fvr:.2f}: recovers {f['date']} {f['ticker']:6s}  "
                      f"volume_ratio={f.get('volume_ratio', 'N/A'):.3f}")
        else:
            print(f"    fvr<={fvr:.2f}: no new OOS TPs recovered")

    print("\n  2D. Sep-Dec TPs blocked by each fin_volratio threshold:")
    for fvr in [0.90, 0.95, 1.00, 1.05, 1.10, 1.15]:
        crit_full = {**V41, "financials_volume_ratio_max": fvr}
        crit_base = {k: v for k, v in crit_full.items() if k not in _IV_KEYS}
        sd_tps = sum(1 for f in sep_dec if f["is_prime"]==1 and _apply_criteria(f, crit_base))
        base_sd_tps = sum(1 for f in sep_dec if f["is_prime"]==1 and _apply_criteria(f, V41_BASE))
        delta = sd_tps - base_sd_tps
        print(f"    fvr<={fvr:.2f}: sdTP={sd_tps} (Δ{delta:+d} vs V41)")

    # ── SECTION 3: Combined V41 + fin_volratio ────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: Combined V41 + best fin_volratio — V42 candidate")
    print("=" * 80)

    print(f"\n  {'Criteria':<36}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V40", sep_dec, train, test, oos, V40, V40_BASE)
    _row4("V41 (bb_width<=21)", sep_dec, train, test, oos, V41, V41_BASE)

    for fvr in [1.00, 1.05, 1.10, 1.15]:
        crit = {**V41, "financials_volume_ratio_max": fvr}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V41+fin_vr<={fvr:.2f}", sep_dec, train, test, oos, crit, crit_base)

    # ── SECTION 4: price_vs_ema200_pct_max ceiling ───────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: price_vs_ema200_pct_max=42 ceiling — revisit")
    print("  Session 31: LRCX (57.54) and AMAT (53.74) fail this ceiling in OOS.")
    print("  Both are also blocked by rv20 + volume_ratio — removing ceiling alone")
    print("  won't recover them. Check Sep-Dec impact and FP cost.")
    print("=" * 80)

    # Who does the ceiling block in Sep-Dec?
    sd_blocked_by_ceiling = [
        f for f in sep_dec
        if f.get("price_vs_ema200_pct") is not None
        and f["price_vs_ema200_pct"] > 42
        and _apply_criteria(f, {k: v for k, v in V41_BASE.items() if k != "price_vs_ema200_pct_max"})  # noqa: E501
    ]
    sd_tp_blocked = [f for f in sd_blocked_by_ceiling if f["is_prime"]==1]
    sd_fp_blocked = [f for f in sd_blocked_by_ceiling if f["is_prime"]==0]

    print("\n  Rows blocked by ceiling in Sep-Dec (after all other V41 gates):")
    print(f"    TPs blocked: {len(sd_tp_blocked)}")
    for f in sd_tp_blocked:
        print(f"      {f['date']} {f['ticker']:6s}  pct_from_ema200={f['price_vs_ema200_pct']:.1f}%")  # noqa: E501
    print(f"    FPs blocked: {len(sd_fp_blocked)}")

    print("\n  4A. Ceiling sweep on V41:")
    print(f"  {'Criteria':<32}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V41 (baseline)", sep_dec, train, test, oos, V41, V41_BASE)
    for ceil in [42, 50, 60, 75, 100]:
        crit = {**V41, "price_vs_ema200_pct_max": ceil}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V41+ema200_max<={ceil}", sep_dec, train, test, oos, crit, crit_base)

    # Remove ceiling entirely
    crit_no_ceil = {k: v for k, v in V41.items() if k != "price_vs_ema200_pct_max"}
    crit_no_ceil_base = {k: v for k, v in crit_no_ceil.items() if k not in _IV_KEYS}
    _row4("V41 (no ceiling)", sep_dec, train, test, oos, crit_no_ceil, crit_no_ceil_base)

    print("\nDone.")


if __name__ == "__main__":
    main()
