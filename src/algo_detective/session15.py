"""Session 15 — Options chain quality gates: pcr_oi and iv_rv sweep.

Goals:
  1. KS analysis of pcr_oi on narrow 74-ticker universe — prime vs control
     direction and strength, broken down by sector
  2. Sweep pcr_oi_max and pcr_oi_min gates on v31a base
  3. Combined gates: best pcr_oi + v31a, pcr_oi + pcr_vol_max=2.0 stacking,
     and sector-specific pcr_oi gates where KS signal is strong
  4. iv_rv_min tightening sweep (currently 0.9 → 1.0, 1.1, 1.2, 1.5)
  5. Summary comparison table of best new variants vs v31a baseline

Prior context:
  - v31a baseline: P=45.2%  R=40.2%  TP=113  FP=137
  - pcr_vol_max=2.0 tested in session12 on v29: +1.2pp
  - pcr_oi NOT yet tested as a gate

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session15
"""

from __future__ import annotations

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _apply_criteria, _score_criteria
from .store import _get_connection, get_all_features


def _join_options(features: list[dict]) -> list[dict]:
    """Enrich feature rows with best_iv, pcr_vol, pcr_oi from detective_options."""
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
            "pcr_oi": index.get((f["date"], f["ticker"]), {}).get("pcr_oi"),
        }
        for f in features
    ]


# ── v31a (current best) ───────────────────────────────────────────────────────

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


def main() -> None:  # noqa: C901
    all_features = get_all_features()

    # Narrow universe: only the 74 prime tickers
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)
    prime = [f for f in narrow if f["is_prime"] == 1]
    control = [f for f in narrow if f["is_prime"] == 0]

    print(
        f"Narrow universe: {len(narrow)} rows, "
        f"{len(prime)} prime, {len(control)} control"
    )
    print(f"Unique prime tickers: {len(prime_tickers)}")

    # Coverage check
    with_pcr_oi = sum(1 for f in narrow if f.get("pcr_oi") is not None)
    with_pcr_vol = sum(1 for f in narrow if f.get("pcr_vol") is not None)
    print(
        f"Options coverage: pcr_oi={with_pcr_oi}/{len(narrow)} rows, "
        f"pcr_vol={with_pcr_vol}/{len(narrow)} rows"
    )

    # ── SECTION 1: KS analysis of pcr_oi ─────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 1: KS analysis — pcr_oi (prime vs control)")
    print("=" * 70)

    p_pcr_oi = [f["pcr_oi"] for f in prime if f.get("pcr_oi") is not None]
    c_pcr_oi = [f["pcr_oi"] for f in control if f.get("pcr_oi") is not None]

    if len(p_pcr_oi) >= 5 and len(c_pcr_oi) >= 5:
        stat, pval = ks_2samp(p_pcr_oi, c_pcr_oi)
        direction = "prime LOWER" if np.mean(p_pcr_oi) < np.mean(c_pcr_oi) else "prime HIGHER"
        print(f"\n  Global pcr_oi KS stat={stat:.4f}  p={pval:.4f}  ({direction})")
        print(
            f"  prime mean={np.mean(p_pcr_oi):.3f}  median={np.median(p_pcr_oi):.3f}  "
            f"n={len(p_pcr_oi)}"
        )
        print(
            f"  control mean={np.mean(c_pcr_oi):.3f}  median={np.median(c_pcr_oi):.3f}  "
            f"n={len(c_pcr_oi)}"
        )
        pct5, pct25, pct75, pct95 = (
            np.percentile(p_pcr_oi, 5),
            np.percentile(p_pcr_oi, 25),
            np.percentile(p_pcr_oi, 75),
            np.percentile(p_pcr_oi, 95),
        )
        print(
            f"  prime pct5={pct5:.3f}  p25={pct25:.3f}  p75={pct75:.3f}  p95={pct95:.3f}"
        )
    else:
        print(f"  Insufficient data: prime n={len(p_pcr_oi)}, control n={len(c_pcr_oi)}")

    # pcr_vol global KS for reference
    p_pcr_vol = [f["pcr_vol"] for f in prime if f.get("pcr_vol") is not None]
    c_pcr_vol = [f["pcr_vol"] for f in control if f.get("pcr_vol") is not None]
    if len(p_pcr_vol) >= 5 and len(c_pcr_vol) >= 5:
        stat_v, pval_v = ks_2samp(p_pcr_vol, c_pcr_vol)
        direction_v = (
            "prime LOWER" if np.mean(p_pcr_vol) < np.mean(c_pcr_vol) else "prime HIGHER"
        )
        print(
            f"\n  Reference: pcr_vol KS stat={stat_v:.4f}  p={pval_v:.4f}  ({direction_v})"
        )
        print(
            f"  prime mean={np.mean(p_pcr_vol):.3f}  control mean={np.mean(c_pcr_vol):.3f}"
        )

    # Sector-level KS breakdown
    print("\n  Sector-level pcr_oi breakdown:")
    print(f"  {'Sector':<30}  {'KS':>6}  {'pval':>7}  {'P-mean':>7}  {'C-mean':>7}  {'Dir':>13}  {'P-n':>4}  {'C-n':>4}")
    print(f"  {'-'*30}  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*13}  {'-'*4}  {'-'*4}")

    sectors = sorted(
        {f.get("sector") for f in narrow if f.get("sector") is not None}
    )
    sector_ks: dict[str, float] = {}
    sector_direction: dict[str, str] = {}

    for sector in sectors:
        sp = [
            f["pcr_oi"]
            for f in prime
            if f.get("sector") == sector and f.get("pcr_oi") is not None
        ]
        sc = [
            f["pcr_oi"]
            for f in control
            if f.get("sector") == sector and f.get("pcr_oi") is not None
        ]
        if len(sp) < 5 or len(sc) < 5:
            print(f"  {sector:<30}  {'—':>6}  {'—':>7}  n_p={len(sp)}  n_c={len(sc)}  (skip)")
            continue
        s, p = ks_2samp(sp, sc)
        pm, cm = np.mean(sp), np.mean(sc)
        direction = "prime LOWER" if pm < cm else "prime HIGHER"
        sector_ks[sector] = s
        sector_direction[sector] = direction
        print(
            f"  {sector:<30}  {s:6.4f}  {p:7.4f}  {pm:7.3f}  {cm:7.3f}  {direction:>13}  "
            f"{len(sp):4d}  {len(sc):4d}"
        )

    # ── SECTION 2: pcr_oi gate sweep on v31a base ─────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 2: pcr_oi gate sweep on v31a base")
    print("=" * 70)

    base = _score_criteria(narrow, V31A)
    base_p = base["precision"]
    base_r = base["recall"]
    print(
        f"\n  v31a baseline: P={base_p*100:.1f}%  R={base_r*100:.1f}%  "
        f"TP={base['true_positives']}  FP={base['false_positives']}"
    )

    # pcr_oi_max sweep
    print("\n  -- pcr_oi_max sweep (lower = tighter, allows only low-pcr_oi rows) --")
    print(
        f"  {'pcr_oi_max':>12}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}  "
        f"{'rows_removed':>12}"
    )
    print(f"  {'-'*12}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}  {'-'*12}")

    total_with_oi = sum(1 for f in narrow if f.get("pcr_oi") is not None)

    for cap in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]:
        crit = {**V31A, "pcr_oi_max": cap}
        res = _score_criteria(narrow, crit)
        delta_p = (res["precision"] - base_p) * 100
        removed = sum(
            1 for f in narrow if f.get("pcr_oi") is not None and f["pcr_oi"] > cap
        )
        print(
            f"  {cap:12.2f}  {res['precision']*100:6.1f}%  {res['recall']*100:6.1f}%  "
            f"{res['true_positives']:5d}  {res['false_positives']:5d}  {delta_p:+6.1f}pp  "
            f"{removed:12d}"
        )

    # pcr_oi_min sweep (for inverted signal — high pcr_oi might be good in some sectors)
    print("\n  -- pcr_oi_min sweep (higher = allows only high-pcr_oi rows) --")
    print(
        f"  {'pcr_oi_min':>12}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}"
    )
    print(f"  {'-'*12}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")

    for floor in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
        crit = {**V31A, "pcr_oi_min": floor}
        res = _score_criteria(narrow, crit)
        delta_p = (res["precision"] - base_p) * 100
        print(
            f"  {floor:12.2f}  {res['precision']*100:6.1f}%  {res['recall']*100:6.1f}%  "
            f"{res['true_positives']:5d}  {res['false_positives']:5d}  {delta_p:+6.1f}pp"
        )

    # ── SECTION 3: Combined gates ─────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 3: Combined gates")
    print("=" * 70)

    # Identify best pcr_oi_max from the sweep above (pick the one with best precision
    # while maintaining recall >= 35%)
    best_pcr_oi_max = None
    best_pcr_oi_max_p = base_p
    for cap in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]:
        crit = {**V31A, "pcr_oi_max": cap}
        res = _score_criteria(narrow, crit)
        if res["recall"] >= 0.35 and res["precision"] > best_pcr_oi_max_p:
            best_pcr_oi_max_p = res["precision"]
            best_pcr_oi_max = cap

    best_pcr_oi_min = None
    best_pcr_oi_min_p = base_p
    for floor in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
        crit = {**V31A, "pcr_oi_min": floor}
        res = _score_criteria(narrow, crit)
        if res["recall"] >= 0.35 and res["precision"] > best_pcr_oi_min_p:
            best_pcr_oi_min_p = res["precision"]
            best_pcr_oi_min = floor

    print(f"\n  Best pcr_oi_max (recall>=35%): {best_pcr_oi_max}  P={best_pcr_oi_max_p*100:.1f}%")
    print(f"  Best pcr_oi_min (recall>=35%): {best_pcr_oi_min}  P={best_pcr_oi_min_p*100:.1f}%")

    print("\n  -- Combined variant table --")
    print(
        f"  {'Variant':<45}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}"
    )
    print(f"  {'-'*45}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")

    def _show(label: str, crit: dict) -> dict:
        res = _score_criteria(narrow, crit)
        delta_p = (res["precision"] - base_p) * 100
        print(
            f"  {label:<45}  {res['precision']*100:6.1f}%  {res['recall']*100:6.1f}%  "
            f"{res['true_positives']:5d}  {res['false_positives']:5d}  {delta_p:+6.1f}pp"
        )
        return res

    _show("v31a (baseline)", V31A)

    if best_pcr_oi_max is not None:
        v_pcr_oi_max = {**V31A, "pcr_oi_max": best_pcr_oi_max}
        _show(f"v31a + pcr_oi_max={best_pcr_oi_max}", v_pcr_oi_max)

        # Stack with pcr_vol_max=2.0 (best from session12)
        v_stacked = {**V31A, "pcr_oi_max": best_pcr_oi_max, "pcr_vol_max": 2.0}
        _show(f"v31a + pcr_oi_max={best_pcr_oi_max} + pcr_vol_max=2.0", v_stacked)

    if best_pcr_oi_min is not None:
        v_pcr_oi_min = {**V31A, "pcr_oi_min": best_pcr_oi_min}
        _show(f"v31a + pcr_oi_min={best_pcr_oi_min}", v_pcr_oi_min)

    # pcr_vol_max=2.0 alone for reference (from session12)
    _show("v31a + pcr_vol_max=2.0", {**V31A, "pcr_vol_max": 2.0})

    # Sector-specific pcr_oi gates: apply only for sectors where KS >= 0.1
    strong_sectors = {
        s: d for s, d in sector_direction.items() if sector_ks.get(s, 0) >= 0.10
    }
    if strong_sectors:
        print("\n  -- Sector-specific pcr_oi gates (KS >= 0.10) --")
        for sector, direction in sorted(strong_sectors.items(), key=lambda x: -sector_ks[x[0]]):
            ks = sector_ks[sector]
            print(f"\n  Sector: {sector}  KS={ks:.4f}  signal direction: {direction}")
            # Normalize sector name to a key-safe prefix
            prefix = sector.lower().replace(" ", "_")
            gate_key = f"{prefix}_pcr_oi_max"

            # We apply this sector gate using a post-filter approach since
            # _apply_criteria doesn't have sector-specific pcr_oi support.
            # Instead, sweep pcr_oi_max caps and show what happens globally.
            # For a proper sector-specific gate we compute it manually.
            for cap in [0.75, 1.0, 1.25, 1.5, 2.0]:
                # Manually apply v31a + sector-specific pcr_oi cap
                def _sector_gate(row: dict, _sector: str = sector, _cap: float = cap) -> bool:
                    if not _apply_criteria(row, V31A):
                        return False
                    if row.get("sector") == _sector:
                        oi = row.get("pcr_oi")
                        if oi is not None and oi > _cap:
                            return False
                    return True

                tp_s = sum(1 for f in prime if _sector_gate(f))
                fp_s = sum(1 for f in control if _sector_gate(f))
                total_s = tp_s + fp_s
                prec_s = tp_s / total_s if total_s > 0 else 0.0
                rec_s = tp_s / len(prime) if prime else 0.0
                delta_s = (prec_s - base_p) * 100
                print(
                    f"    pcr_oi_max={cap:.2f} for {sector}: "
                    f"P={prec_s*100:.1f}%  R={rec_s*100:.1f}%  "
                    f"TP={tp_s}  FP={fp_s}  ΔP={delta_s:+.1f}pp"
                )
    else:
        print("\n  No sectors with KS >= 0.10 for sector-specific gates.")

    # ── SECTION 4: iv_rv_min tightening sweep ─────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 4: iv_rv_min sweep (currently 0.9 in v31a)")
    print("=" * 70)
    print(
        f"\n  {'iv_rv_min':>10}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}"
    )
    print(f"  {'-'*10}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")

    iv_rv_results = {}
    for val in [0.9, 1.0, 1.1, 1.2, 1.5]:
        crit = {**V31A, "iv_rv_min": val}
        res = _score_criteria(narrow, crit)
        delta_p = (res["precision"] - base_p) * 100
        iv_rv_results[val] = res
        mark = "  <-- baseline" if val == 0.9 else ""
        print(
            f"  {val:10.1f}  {res['precision']*100:6.1f}%  {res['recall']*100:6.1f}%  "
            f"{res['true_positives']:5d}  {res['false_positives']:5d}  {delta_p:+6.1f}pp{mark}"
        )

    # Best iv_rv_min with recall >= 35%
    best_iv_rv = max(
        (v for v in iv_rv_results if iv_rv_results[v]["recall"] >= 0.35),
        key=lambda v: iv_rv_results[v]["precision"],
        default=None,
    )
    if best_iv_rv and best_iv_rv != 0.9:
        print(f"\n  Best iv_rv_min (recall>=35%): {best_iv_rv}")
        # Stack best iv_rv with best pcr_oi
        if best_pcr_oi_max is not None:
            crit_stack = {**V31A, "iv_rv_min": best_iv_rv, "pcr_oi_max": best_pcr_oi_max}
            res_stack = _score_criteria(narrow, crit_stack)
            delta_stack = (res_stack["precision"] - base_p) * 100
            print(
                f"  v31a + iv_rv_min={best_iv_rv} + pcr_oi_max={best_pcr_oi_max}: "
                f"P={res_stack['precision']*100:.1f}%  R={res_stack['recall']*100:.1f}%  "
                f"TP={res_stack['true_positives']}  FP={res_stack['false_positives']}  "
                f"ΔP={delta_stack:+.1f}pp"
            )

    # ── SECTION 5: Summary comparison table ───────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 5: Summary comparison — best new variants vs v31a")
    print("=" * 70)

    summary_variants: list[tuple[str, dict]] = [("v31a (baseline)", V31A)]

    if best_pcr_oi_max is not None:
        summary_variants.append((f"v31a + pcr_oi_max={best_pcr_oi_max}", {**V31A, "pcr_oi_max": best_pcr_oi_max}))
        summary_variants.append((
            f"v31a + pcr_oi_max={best_pcr_oi_max} + pcr_vol_max=2.0",
            {**V31A, "pcr_oi_max": best_pcr_oi_max, "pcr_vol_max": 2.0},
        ))
    if best_pcr_oi_min is not None:
        summary_variants.append((f"v31a + pcr_oi_min={best_pcr_oi_min}", {**V31A, "pcr_oi_min": best_pcr_oi_min}))

    summary_variants.append(("v31a + pcr_vol_max=2.0", {**V31A, "pcr_vol_max": 2.0}))

    if best_iv_rv and best_iv_rv != 0.9:
        summary_variants.append((f"v31a + iv_rv_min={best_iv_rv}", {**V31A, "iv_rv_min": best_iv_rv}))
        if best_pcr_oi_max is not None:
            summary_variants.append((
                f"v31a + iv_rv_min={best_iv_rv} + pcr_oi_max={best_pcr_oi_max}",
                {**V31A, "iv_rv_min": best_iv_rv, "pcr_oi_max": best_pcr_oi_max},
            ))

    # iv_rv=1.0 always worth showing
    if best_iv_rv != 1.0 and iv_rv_results.get(1.0):
        summary_variants.append(("v31a + iv_rv_min=1.0", {**V31A, "iv_rv_min": 1.0}))

    print(
        f"\n  {'Variant':<50}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}  {'Beats v31a?':>12}"
    )
    print(f"  {'-'*50}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}  {'-'*12}")

    for label, crit in summary_variants:
        res = _score_criteria(narrow, crit)
        delta_p = (res["precision"] - base_p) * 100
        beats = (
            "YES (P+R)" if res["precision"] > base_p and res["recall"] >= base_r
            else "YES (P only)" if res["precision"] > base_p and res["recall"] >= 0.35
            else "—"
        )
        print(
            f"  {label:<50}  {res['precision']*100:6.1f}%  {res['recall']*100:6.1f}%  "
            f"{res['true_positives']:5d}  {res['false_positives']:5d}  {delta_p:+6.1f}pp  "
            f"{beats:>12}"
        )

    print("\n--- Session 15 complete ---")


if __name__ == "__main__":
    main()
