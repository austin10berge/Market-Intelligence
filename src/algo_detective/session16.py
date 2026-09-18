"""Session 16 — v33 candidate sweep: pcr_vol_max + iv_rv_min stacking.

Session 15 found two gates that improve on v31a (P=45.2%, R=40.2%):
  - pcr_vol_max=2.0: P=47.0%, R=39.1% (+1.8pp)
  - iv_rv_min=1.1:   P=46.1%, R=35.2% (+0.9pp)

Goals:
  1. Confirm v33a (v31a + pcr_vol_max=2.0) — rerun cleanly with TP/FP breakdown
  2. Test v33b (v31a + pcr_vol_max=2.0 + iv_rv_min=1.1) — primary new test
  3. Sweep all iv_rv_min x pcr_vol_max combinations to find the Pareto frontier
  4. TP/FP sector breakdown for best v33 variant — what did we cut vs keep?
  5. Identify which prime observations v33b loses vs v31a (ticker + date)

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session16
"""

from __future__ import annotations

from collections import Counter

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


def _sector_breakdown(rows: list[dict], crit: dict, label: str) -> None:
    """Print TP/FP count by sector for a given criteria on the narrow universe."""
    prime = [f for f in rows if f["is_prime"] == 1]
    control = [f for f in rows if f["is_prime"] == 0]

    tp_by_sector: Counter = Counter()
    fp_by_sector: Counter = Counter()

    for f in prime:
        if _apply_criteria(f, crit):
            tp_by_sector[f.get("sector", "Unknown")] += 1

    for f in control:
        if _apply_criteria(f, crit):
            fp_by_sector[f.get("sector", "Unknown")] += 1

    all_sectors = sorted(set(tp_by_sector) | set(fp_by_sector))
    print(f"\n  Sector breakdown — {label}:")
    print(f"  {'Sector':<32}  {'TP':>4}  {'FP':>4}  {'P%':>6}")
    print(f"  {'-'*32}  {'-'*4}  {'-'*4}  {'-'*6}")
    for s in all_sectors:
        tp = tp_by_sector[s]
        fp = fp_by_sector[s]
        p = tp / (tp + fp) * 100 if (tp + fp) > 0 else 0.0
        print(f"  {s:<32}  {tp:4d}  {fp:4d}  {p:5.1f}%")


def main() -> None:
    all_features = get_all_features()

    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)
    prime = [f for f in narrow if f["is_prime"] == 1]

    print(f"Narrow universe: {len(narrow)} rows, {len(prime)} prime, {len(narrow)-len(prime)} control")
    print(f"pcr_vol coverage: {sum(1 for f in narrow if f.get('pcr_vol') is not None)}/{len(narrow)}")

    # ── SECTION 1: v33a confirmation ─────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 1: v33a = v31a + pcr_vol_max=2.0 (confirmation)")
    print("=" * 70)

    base = _score_criteria(narrow, V31A)
    base_p = base["precision"]
    base_r = base["recall"]
    print(f"\n  v31a: P={base_p*100:.1f}%  R={base_r*100:.1f}%  TP={base['true_positives']}  FP={base['false_positives']}")

    V33A = {**V31A, "pcr_vol_max": 2.0}
    r33a = _score_criteria(narrow, V33A)
    dp = (r33a["precision"] - base_p) * 100
    dr = (r33a["recall"] - base_r) * 100
    print(f"  v33a: P={r33a['precision']*100:.1f}%  R={r33a['recall']*100:.1f}%  TP={r33a['true_positives']}  FP={r33a['false_positives']}  ΔP={dp:+.1f}pp  ΔR={dr:+.1f}pp")

    # What FPs did pcr_vol_max cut?
    base_tp_set = {(f["ticker"], f["date"]) for f in prime if _apply_criteria(f, V31A)}
    v33a_tp_set = {(f["ticker"], f["date"]) for f in prime if _apply_criteria(f, V33A)}
    base_fp_set = {(f["ticker"], f["date"]) for f in narrow if not f["is_prime"] and _apply_criteria(f, V31A)}
    v33a_fp_set = {(f["ticker"], f["date"]) for f in narrow if not f["is_prime"] and _apply_criteria(f, V33A)}

    lost_tps = base_tp_set - v33a_tp_set
    cut_fps = base_fp_set - v33a_fp_set

    print(f"\n  FPs cut by pcr_vol_max=2.0: {len(cut_fps)}")
    cut_fp_tickers = Counter(t for t, _ in cut_fps)
    for t, c in sorted(cut_fp_tickers.items(), key=lambda x: -x[1])[:10]:
        print(f"    {t}: {c} FPs removed")

    print(f"\n  TPs lost by pcr_vol_max=2.0: {len(lost_tps)}")
    lost_tp_tickers = Counter(t for t, _ in lost_tps)
    for t, c in sorted(lost_tp_tickers.items(), key=lambda x: -x[1]):
        row = next((f for f in prime if f["ticker"] == t and _apply_criteria(f, V31A)), None)
        pcr = row.get("pcr_vol") if row else None
        pcr_str = f"pcr_vol={pcr:.3f}" if pcr is not None else "pcr_vol=NULL"
        print(f"    {t}: {c} TPs lost ({pcr_str})")

    # ── SECTION 2: v33b = v31a + pcr_vol_max=2.0 + iv_rv_min=1.1 ────────────
    print("\n" + "=" * 70)
    print("SECTION 2: v33b = v31a + pcr_vol_max=2.0 + iv_rv_min=1.1")
    print("=" * 70)

    V33B = {**V31A, "pcr_vol_max": 2.0, "iv_rv_min": 1.1}
    r33b = _score_criteria(narrow, V33B)
    dp_b = (r33b["precision"] - base_p) * 100
    dr_b = (r33b["recall"] - base_r) * 100
    print(f"\n  v33b: P={r33b['precision']*100:.1f}%  R={r33b['recall']*100:.1f}%  TP={r33b['true_positives']}  FP={r33b['false_positives']}  ΔP={dp_b:+.1f}pp  ΔR={dr_b:+.1f}pp")

    v33b_tp_set = {(f["ticker"], f["date"]) for f in prime if _apply_criteria(f, V33B)}
    v33b_fp_set = {(f["ticker"], f["date"]) for f in narrow if not f["is_prime"] and _apply_criteria(f, V33B)}

    # What does iv_rv_min=1.1 cut incrementally vs v33a?
    incremental_tp_loss = v33a_tp_set - v33b_tp_set
    incremental_fp_cut  = v33a_fp_set - v33b_fp_set
    print("\n  iv_rv_min=1.1 adds (vs v33a):")
    print(f"    FPs cut: {len(incremental_fp_cut)}")
    print(f"    TPs lost: {len(incremental_tp_loss)}")

    if incremental_tp_loss:
        print("\n  TPs lost by iv_rv_min=1.1 (incremental vs v33a):")
        for ticker, dt in sorted(incremental_tp_loss):
            row = next((f for f in prime if f["ticker"] == ticker and f["date"] == dt), None)
            ivrv = row.get("iv_rv") if row else None
            ivrv_str = f"iv_rv={ivrv:.3f}" if ivrv is not None else "iv_rv=NULL"
            print(f"    {dt}  {ticker:<8}  {ivrv_str}  sector={row.get('sector') if row else '?'}")

    # All TPs lost vs v31a baseline
    all_lost_v33b = base_tp_set - v33b_tp_set
    print(f"\n  All TPs lost vs v31a baseline ({len(all_lost_v33b)}):")
    for ticker, dt in sorted(all_lost_v33b):
        row = next((f for f in prime if f["ticker"] == ticker and f["date"] == dt), None)
        pcr = row.get("pcr_vol") if row else None
        ivrv = row.get("iv_rv") if row else None
        gate = "pcr_vol" if (pcr is not None and pcr > 2.0) else "iv_rv"
        val = f"pcr_vol={pcr:.3f}" if (pcr is not None and pcr > 2.0) else (f"iv_rv={ivrv:.3f}" if ivrv is not None else "both/NULL")
        print(f"    {dt}  {ticker:<8}  {val}  (gate: {gate})")

    # ── SECTION 3: iv_rv × pcr_vol grid ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 3: iv_rv_min × pcr_vol_max grid (Pareto frontier)")
    print("=" * 70)

    iv_rv_vals  = [0.9, 1.0, 1.1, 1.2]
    pcr_vol_vals = [None, 1.5, 2.0, 3.0]  # None = no pcr_vol gate

    print(f"\n  {'iv_rv_min':>10}  {'pcr_vol_max':>11}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}  {'Beats v31a?':>12}")
    print(f"  {'-'*10}  {'-'*11}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}  {'-'*12}")

    best_p_with_recall = base_p
    best_combo: dict | None = None
    best_combo_label = ""

    for iv_rv in iv_rv_vals:
        for pcr_max in pcr_vol_vals:
            crit = {**V31A, "iv_rv_min": iv_rv}
            if pcr_max is not None:
                crit["pcr_vol_max"] = pcr_max
            res = _score_criteria(narrow, crit)
            dp_g = (res["precision"] - base_p) * 100
            beats = (
                "YES (P+R)" if res["precision"] > base_p and res["recall"] >= base_r
                else "YES (P only)" if res["precision"] > base_p and res["recall"] >= 0.35
                else "—"
            )
            pcr_label = f"{pcr_max:.1f}" if pcr_max is not None else "none"
            label = f"iv={iv_rv} / pcr≤{pcr_label}"
            print(
                f"  {iv_rv:10.1f}  {pcr_label:>11}  {res['precision']*100:6.1f}%  "
                f"{res['recall']*100:6.1f}%  {res['true_positives']:5d}  {res['false_positives']:5d}  "
                f"{dp_g:+6.1f}pp  {beats:>12}"
            )
            if res["recall"] >= 0.35 and res["precision"] > best_p_with_recall:
                best_p_with_recall = res["precision"]
                best_combo = crit
                best_combo_label = label

    print(f"\n  Best combination (recall≥35%): {best_combo_label}  P={best_p_with_recall*100:.1f}%")

    # ── SECTION 4: Sector breakdown of best variant ───────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 4: Sector breakdown")
    print("=" * 70)

    _sector_breakdown(narrow, V31A, "v31a (baseline)")
    _sector_breakdown(narrow, V33A, "v33a")
    _sector_breakdown(narrow, V33B, "v33b")
    if best_combo and best_combo != V33B:
        _sector_breakdown(narrow, best_combo, f"best combo ({best_combo_label})")

    # ── SECTION 5: Summary table ──────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 5: Summary — all v31/v33 variants")
    print("=" * 70)

    summary = [
        ("v31a (baseline)",                    V31A),
        ("v33a = v31a + pcr_vol_max=2.0",      V33A),
        ("v33b = v31a + pcr≤2.0 + iv_rv≥1.1", V33B),
        ("v31a + iv_rv_min=1.1",               {**V31A, "iv_rv_min": 1.1}),
        ("v31a + iv_rv_min=1.0",               {**V31A, "iv_rv_min": 1.0}),
        ("v31a + pcr_vol_max=1.5",             {**V31A, "pcr_vol_max": 1.5}),
    ]
    if best_combo and best_combo not in [v for _, v in summary]:
        summary.append((f"best: {best_combo_label}", best_combo))

    print(f"\n  {'Version':<38}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}")
    print(f"  {'-'*38}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")
    for name, crit in summary:
        res = _score_criteria(narrow, crit)
        dp_s = (res["precision"] - base_p) * 100
        mark = " <-- NEW BEST" if res["precision"] > base_p and res["recall"] >= 0.35 and dp_s > 1.5 else ""
        print(
            f"  {name:<38}  {res['precision']*100:6.1f}%  {res['recall']*100:6.1f}%  "
            f"{res['true_positives']:5d}  {res['false_positives']:5d}  {dp_s:+6.1f}pp{mark}"
        )

    print("\n--- Session 16 complete ---")


if __name__ == "__main__":
    main()
