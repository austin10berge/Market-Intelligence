"""Session 34 — fundamentals KS re-analysis on the narrow 84-ticker universe.

Context:
  peg_ratio, debt_to_equity, revenue_growth, earnings_growth, and forward_pe were
  KS-ranked on the full un-narrowed universe early in the project. The narrow dataset
  (84 tickers) may show very different distributions and gate candidates.

  From the QCOM post: he checks FCF, debt/equity, payout ratio (~30%) before trading.
  "Companies must be historically profitable and not overvalued."

  Current V41 already uses: forward_pe_max=50, technology_fcf_min=0.01, dividend_yield_max=2.5.
  Unexplored as gates: peg_ratio_max, debt_to_equity_max, revenue_growth_min,
                        earnings_growth_min.

  Also: forward_pe_max=50 is global. Tech stocks trade at much higher PE than Industrials.
  A sector-split forward_pe cap might tighten precision.

Sections:
  1. NULL coverage — what % of rows have data for each fundamentals feature?
  2. KS ranking for fundamentals features on Sep-Dec (narrow) vs 2026 OOS
  3. TP vs FP distributions for top fundamentals features (median + 10/90th pct)
  4. debt_to_equity_max sweep on V41_BASE (1.0→5.0)
  5. Sector-specific forward_pe analysis (which sectors drive FPs?)
  6. peg_ratio_max sweep on V41_BASE
  7. Combined fundamentals gate candidates

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session34
"""

from __future__ import annotations

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _apply_criteria
from .store import get_all_features

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
V41_BASE = {k: v for k, v in V41.items() if k not in _IV_KEYS}

_FUNDAMENTALS = [
    "peg_ratio",
    "debt_to_equity",
    "revenue_growth",
    "earnings_growth",
    "forward_pe",
    "fcf",
    "market_cap_b",
    "beta",
    "dividend_yield",
]


def _row4(
    label: str,
    sep_dec: list[dict],
    train: list[dict],
    test: list[dict],
    oos: list[dict],
    crit_base: dict,
) -> None:
    def _stats(rows: list[dict], crit: dict) -> tuple[float, float, int]:
        hits = [f for f in rows if _apply_criteria(f, crit)]
        tp = sum(1 for f in hits if f["is_prime"] == 1)
        total = len(hits)
        total_p = sum(1 for f in rows if f["is_prime"] == 1)
        prec = tp / total * 100 if total else 0.0
        rec = tp / total_p * 100 if total_p else 0.0
        return prec, rec, tp

    sd_p, sd_r, sd_tp = _stats(sep_dec, crit_base)
    tr_p, tr_r, _ = _stats(train, crit_base)
    te_p, te_r, _ = _stats(test, crit_base)
    oo_p, oo_r, oo_tp = _stats(oos, crit_base)
    print(
        f"  {label:<40} "
        f"sd={sd_p:5.1f}%/{sd_r:4.1f}%  "
        f"tr={tr_p:5.1f}%/{tr_r:4.1f}%  "
        f"te={te_p:5.1f}%/{te_r:4.1f}%  "
        f"oos={oo_p:5.1f}%/{oo_r:4.1f}%  "
        f"sdTP={sd_tp}  oosTP={oo_tp}"
    )


def _ks_for_feat(rows: list[dict], feat: str) -> tuple[float, float, float, float, float] | None:
    prime_vals = [f[feat] for f in rows if f["is_prime"] == 1 and f.get(feat) is not None]
    ctrl_vals  = [f[feat] for f in rows if f["is_prime"] == 0 and f.get(feat) is not None]
    if len(prime_vals) < 5 or len(ctrl_vals) < 5:
        return None
    stat, _ = ks_2samp(prime_vals, ctrl_vals)
    return (
        round(stat, 3),
        round(float(np.median(prime_vals)), 3),
        round(float(np.median(ctrl_vals)), 3),
        round(float(np.percentile(prime_vals, 10)), 3),
        round(float(np.percentile(prime_vals, 90)), 3),
    )


def main() -> None:
    all_features = get_all_features()
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    all_features = [f for f in all_features if f["ticker"] in prime_tickers]

    sep_dec = [f for f in all_features if f["date"] < OOS_START]
    train   = [f for f in sep_dec if f["date"] <= TRAIN_CUTOFF]
    test    = [f for f in sep_dec if f["date"] > TRAIN_CUTOFF]
    oos     = [f for f in all_features if f["date"] >= OOS_START]

    sd_prime  = sum(1 for f in sep_dec if f["is_prime"] == 1)
    sd_ctrl   = sum(1 for f in sep_dec if f["is_prime"] == 0)
    oos_prime = sum(1 for f in oos if f["is_prime"] == 1)

    print(f"Narrow universe: {len(prime_tickers)} tickers")
    print(f"Sep-Dec 2025 : {len(sep_dec)} rows  ({sd_prime} prime, {sd_ctrl} control)")
    print(f"2026 OOS     : {len(oos)} rows  ({oos_prime} prime)")
    print("\n  (header: sd=Sep-Dec full  tr=Train Sep-Oct  te=Test Nov-Dec  oos=2026 OOS)")

    # ── SECTION 1: NULL coverage ──────────────────────────────────────────────
    print()
    print("=" * 80)
    print("SECTION 1: Fundamentals NULL coverage on Sep-Dec (narrow)")
    print("  (Fundamentals are current snapshot backfilled — per-ticker, not per-date)")
    print("=" * 80)
    print()
    print(f"  {'Feature':<25} {'Coverage%':>10}  {'Prime coverage%':>16}  {'Ctrl coverage%':>15}")  # noqa: E501
    for feat in _FUNDAMENTALS:
        all_n   = sum(1 for f in sep_dec if f.get(feat) is not None)
        prime_n = sum(1 for f in sep_dec if f["is_prime"] == 1 and f.get(feat) is not None)
        ctrl_n  = sum(1 for f in sep_dec if f["is_prime"] == 0 and f.get(feat) is not None)
        cov     = all_n / len(sep_dec) * 100 if sep_dec else 0
        pcov    = prime_n / sd_prime * 100 if sd_prime else 0
        ccov    = ctrl_n / sd_ctrl * 100 if sd_ctrl else 0
        print(f"  {feat:<25} {cov:>9.1f}%  {pcov:>15.1f}%  {ccov:>14.1f}%")

    # ── SECTION 2: KS ranking for fundamentals (Sep-Dec vs OOS) ──────────────
    print()
    print("=" * 80)
    print("SECTION 2: KS ranking for fundamentals — Sep-Dec (narrow) vs 2026 OOS")
    print("=" * 80)
    print()
    print(f"  {'Feature':<25} {'KS_sd':>7}  {'TP_med_sd':>10}  {'FP_med_sd':>10}  {'KS_oos':>7}  {'TP_med_oos':>11}  {'FP_med_oos':>11}")  # noqa: E501
    for feat in sorted(_FUNDAMENTALS):
        sd_res  = _ks_for_feat(sep_dec, feat)
        oos_res = _ks_for_feat(oos, feat)
        if sd_res is None and oos_res is None:
            continue
        ks_sd  = f"{sd_res[0]:.3f}"  if sd_res  else "  n/a "
        tm_sd  = f"{sd_res[1]:.3f}"  if sd_res  else "   n/a"
        fm_sd  = f"{sd_res[2]:.3f}"  if sd_res  else "   n/a"
        ks_oos = f"{oos_res[0]:.3f}" if oos_res else "  n/a "
        tm_oos = f"{oos_res[1]:.3f}" if oos_res else "   n/a"
        fm_oos = f"{oos_res[2]:.3f}" if oos_res else "   n/a"
        print(f"  {feat:<25} {ks_sd:>7}  {tm_sd:>10}  {fm_sd:>10}  {ks_oos:>7}  {tm_oos:>11}  {fm_oos:>11}")  # noqa: E501

    # Also show TP 10th/90th pct for debt_to_equity and forward_pe (for threshold guidance)
    print()
    print("  TP distribution details (Sep-Dec narrow):")
    for feat in ["debt_to_equity", "forward_pe", "peg_ratio", "revenue_growth", "earnings_growth"]:
        prime_vals = [f[feat] for f in sep_dec if f["is_prime"] == 1 and f.get(feat) is not None]
        if not prime_vals:
            continue
        p10 = np.percentile(prime_vals, 10)
        p25 = np.percentile(prime_vals, 25)
        p50 = np.percentile(prime_vals, 50)
        p75 = np.percentile(prime_vals, 75)
        p90 = np.percentile(prime_vals, 90)
        print(f"  {feat:<25}  p10={p10:.2f}  p25={p25:.2f}  p50={p50:.2f}  p75={p75:.2f}  p90={p90:.2f}  n={len(prime_vals)}")  # noqa: E501

    # ── SECTION 3: Per-ticker fundamentals (which TPs have high debt?) ────────
    print()
    print("=" * 80)
    print("SECTION 3: Per-ticker debt_to_equity and forward_pe (narrow Sep-Dec TPs)")
    print("  Are any high-debt or high-PE tickers frequent TPs? FPs?")
    print("=" * 80)
    print()

    # Ticker-level stats: how many prime days vs control days per ticker + their d/e and fpe
    ticker_stats: dict[str, dict] = {}
    for f in sep_dec:
        t = f["ticker"]
        if t not in ticker_stats:
            ticker_stats[t] = {
                "prime": 0, "ctrl": 0,
                "de": f.get("debt_to_equity"), "fpe": f.get("forward_pe"),
                "peg": f.get("peg_ratio"), "sector": f.get("sector"),
            }
        if f["is_prime"] == 1:
            ticker_stats[t]["prime"] += 1
        else:
            ticker_stats[t]["ctrl"] += 1

    # Sort by ratio of FP days (mostly-control tickers with debt issues)
    sorted_tickers = sorted(
        ticker_stats.items(),
        key=lambda x: x[1]["ctrl"] / max(x[1]["prime"] + x[1]["ctrl"], 1),
        reverse=True,
    )
    print(f"  {'Ticker':<7}  {'Prime':>5}  {'Ctrl':>5}  {'D/E':>6}  {'fPE':>6}  {'PEG':>6}  Sector")
    for ticker, s in sorted_tickers:
        de  = f"{s['de']:.2f}"  if s["de"]  is not None else "  n/a"
        fpe = f"{s['fpe']:.1f}" if s["fpe"] is not None else " n/a"
        peg = f"{s['peg']:.2f}" if s["peg"] is not None else "  n/a"
        print(f"  {ticker:<7}  {s['prime']:>5}  {s['ctrl']:>5}  {de:>6}  {fpe:>6}  {peg:>6}  {s['sector'] or ''}")  # noqa: E501

    # ── SECTION 4: debt_to_equity_max sweep ──────────────────────────────────
    print()
    print("=" * 80)
    print("SECTION 4: debt_to_equity_max sweep on V41_BASE")
    print("  His QCOM post: 'FCF/debt/payout ratio ~30%' — high-debt tickers may be FPs.")
    print("  NULL d/e: passes (many profitable large-caps have no long-term debt).")
    print("=" * 80)
    print()
    print(f"  {'Criteria':<40}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V41 (baseline, no d/e gate)", sep_dec, train, test, oos, V41_BASE)
    for de_max in [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]:
        crit = {**V41_BASE, "debt_to_equity_max": de_max}
        _row4(f"V41+de_max={de_max:.1f}", sep_dec, train, test, oos, crit)

    # Show which OOS TPs would be blocked at each threshold
    print("\n  4A. OOS TPs blocked by debt_to_equity_max at each threshold:")
    v41_oos_tps = {(f["date"], f["ticker"]) for f in oos if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE)}  # noqa: E501
    for de_max in [0.5, 1.0, 1.5, 2.0, 3.0]:
        crit = {**V41_BASE, "debt_to_equity_max": de_max}
        new_oos_tps = {(f["date"], f["ticker"]) for f in oos if f["is_prime"] == 1 and _apply_criteria(f, crit)}  # noqa: E501
        lost = v41_oos_tps - new_oos_tps
        if lost:
            for date, ticker in sorted(lost):
                row = next(f for f in oos if f["date"] == date and f["ticker"] == ticker)
                de_val = row.get("debt_to_equity")
                print(f"    de_max={de_max:.1f}: LOSES {date} {ticker:6s}  d/e={de_val}")
        else:
            print(f"    de_max={de_max:.1f}: no OOS TPs lost")

    # Show new Sep-Dec FPs removed by each threshold
    print("\n  4B. Sep-Dec FPs removed by debt_to_equity_max at each threshold:")
    v41_sd_fps = {(f["date"], f["ticker"]) for f in sep_dec if f["is_prime"] == 0 and _apply_criteria(f, V41_BASE)}  # noqa: E501
    for de_max in [0.5, 1.0, 1.5, 2.0, 3.0]:
        crit = {**V41_BASE, "debt_to_equity_max": de_max}
        new_sd_fps = {(f["date"], f["ticker"]) for f in sep_dec if f["is_prime"] == 0 and _apply_criteria(f, crit)}  # noqa: E501
        removed = v41_sd_fps - new_sd_fps
        if removed:
            # Sector breakdown
            sector_counts: dict[str, int] = {}
            for date, ticker in removed:
                row = next(f for f in sep_dec if f["date"] == date and f["ticker"] == ticker)
                sec = row.get("sector") or "Unknown"
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
            sec_str = ", ".join(f"{s}:{n}" for s, n in sorted(sector_counts.items(), key=lambda x: -x[1]))  # noqa: E501
            print(f"    de_max={de_max:.1f}: removes {len(removed)} FPs  [{sec_str}]")
        else:
            print(f"    de_max={de_max:.1f}: removes 0 FPs")

    # Also: show high-d/e FP tickers that get removed
    print("\n  4C. Sep-Dec FP tickers with d/e > 2.0 (removed by de_max=2.0):")
    crit_de2 = {**V41_BASE, "debt_to_equity_max": 2.0}
    removed_tickers: dict[str, dict] = {}
    for f in sep_dec:
        if f["is_prime"] == 0 and _apply_criteria(f, V41_BASE) and not _apply_criteria(f, crit_de2):
            t = f["ticker"]
            if t not in removed_tickers:
                removed_tickers[t] = {
                    "days": 0, "de": f.get("debt_to_equity"), "sector": f.get("sector"),
                }
            removed_tickers[t]["days"] += 1
    for t, s in sorted(removed_tickers.items(), key=lambda x: -(x[1]["de"] or 0)):
        print(f"    {t:<7}  d/e={s['de']:.2f}  fp_days={s['days']}  sector={s['sector']}")

    # ── SECTION 5: Sector-specific forward_pe analysis ───────────────────────
    print()
    print("=" * 80)
    print("SECTION 5: Sector-specific forward_pe analysis")
    print("  Current gate: forward_pe_max=50 (global). Tech trades at 25-35x, Industrials 15-25x.")
    print("  Are there sector-specific FPs with high PE that a tighter sector cap would catch?")
    print("=" * 80)
    print()

    # PE distribution by sector (Sep-Dec)
    sector_pe: dict[str, dict] = {}
    for f in sep_dec:
        sec = f.get("sector") or "Unknown"
        pe = f.get("forward_pe")
        if pe is None:
            continue
        if sec not in sector_pe:
            sector_pe[sec] = {"tp": [], "fp": []}
        if f["is_prime"] == 1:
            sector_pe[sec]["tp"].append(pe)
        else:
            sector_pe[sec]["fp"].append(pe)

    print(f"  {'Sector':<28}  {'TP_med':>7}  {'TP_p90':>7}  {'FP_med':>7}  {'FP_p90':>7}  {'n_tp':>5}  {'n_fp':>5}")  # noqa: E501
    for sec in sorted(sector_pe.keys()):
        tp_v = sector_pe[sec]["tp"]
        fp_v = sector_pe[sec]["fp"]
        if not tp_v and not fp_v:
            continue
        tp_med = f"{np.median(tp_v):.1f}" if tp_v else "  n/a"
        tp_p90 = f"{np.percentile(tp_v, 90):.1f}" if tp_v else "  n/a"
        fp_med = f"{np.median(fp_v):.1f}" if fp_v else "  n/a"
        fp_p90 = f"{np.percentile(fp_v, 90):.1f}" if fp_v else "  n/a"
        print(f"  {sec:<28}  {tp_med:>7}  {tp_p90:>7}  {fp_med:>7}  {fp_p90:>7}  {len(tp_v):>5}  {len(fp_v):>5}")  # noqa: E501

    # Test sector-specific PE gates
    print()
    print(f"  {'Criteria':<40}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V41 (global pe_max=50)", sep_dec, train, test, oos, V41_BASE)

    # Tighter global caps
    for pe_max in [35.0, 40.0, 45.0]:
        crit = {**V41_BASE, "forward_pe_max": pe_max}
        _row4(f"V41+forward_pe_max={pe_max:.0f}", sep_dec, train, test, oos, crit)

    # Loosening global cap (some tech primes may have PE > 50)
    crit_loose = {**V41_BASE, "forward_pe_max": 60.0}
    _row4("V41+forward_pe_max=60", sep_dec, train, test, oos, crit_loose)

    # Show OOS TPs blocked by pe_max=40
    print("\n  5A. OOS TPs blocked by forward_pe_max=40:")
    crit_pe40 = {**V41_BASE, "forward_pe_max": 40.0}
    for f in oos:
        if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE) and not _apply_criteria(f, crit_pe40):  # noqa: E501
            pe_val = f.get("forward_pe")
            print(f"    {f['date']} {f['ticker']:6s}  pe={pe_val}  sector={f.get('sector')}")

    # ── SECTION 6: peg_ratio_max sweep ───────────────────────────────────────
    print()
    print("=" * 80)
    print("SECTION 6: peg_ratio_max sweep on V41_BASE")
    print("  PEG < 1 = undervalued, > 1 = overvalued. He avoids 'overvalued' names.")
    print("  NULL PEG: passes (mature companies with steady earnings often have no PEG).")
    print("=" * 80)
    print()
    print(f"  {'Criteria':<40}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V41 (no peg gate)", sep_dec, train, test, oos, V41_BASE)
    for peg_max in [1.0, 1.5, 2.0, 3.0, 5.0]:
        crit = {**V41_BASE, "peg_ratio_max": peg_max}
        _row4(f"V41+peg_max={peg_max:.1f}", sep_dec, train, test, oos, crit)

    print("\n  6A. OOS TPs blocked by peg_ratio_max at each threshold:")
    for peg_max in [1.0, 1.5, 2.0, 3.0]:
        crit = {**V41_BASE, "peg_ratio_max": peg_max}
        for f in oos:
            if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE) and not _apply_criteria(f, crit):
                print(f"    peg_max={peg_max:.1f}: LOSES {f['date']} {f['ticker']:6s}  peg={f.get('peg_ratio')}")  # noqa: E501

    # ── SECTION 7: Combined gate candidates (best non-blocking combinations) ──
    print()
    print("=" * 80)
    print("SECTION 7: Combined fundamentals candidates (oosTP-preserving)")
    print("  Find the tightest combination that blocks 0 OOS TPs and maximizes FP removal.")
    print("=" * 80)
    print()

    base_oos_tp_count = sum(1 for f in oos if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE))
    print(f"  V41_BASE OOS TPs: {base_oos_tp_count}")
    print()

    candidates = [
        # (label, extra_criteria)
        ("de_max=2.0", {"debt_to_equity_max": 2.0}),
        ("de_max=3.0", {"debt_to_equity_max": 3.0}),
        ("peg_max=3.0", {"peg_ratio_max": 3.0}),
        ("peg_max=5.0", {"peg_ratio_max": 5.0}),
        ("fpe_max=40", {"forward_pe_max": 40.0}),
        ("fpe_max=45", {"forward_pe_max": 45.0}),
        ("de_max=2.0 + peg_max=5.0", {"debt_to_equity_max": 2.0, "peg_ratio_max": 5.0}),
        ("de_max=2.0 + peg_max=3.0", {"debt_to_equity_max": 2.0, "peg_ratio_max": 3.0}),
        ("de_max=3.0 + fpe_max=45",  {"debt_to_equity_max": 3.0, "forward_pe_max": 45.0}),
        ("de_max=2.0 + fpe_max=45",  {"debt_to_equity_max": 2.0, "forward_pe_max": 45.0}),
    ]

    print(f"  {'Label':<32}  {'oosTP':>5}  {'sdTP':>5}  {'sd_fps':>6}  {'fp_removed':>10}  {'δP_sd':>8}")  # noqa: E501
    base_sd_fps = sum(1 for f in sep_dec if f["is_prime"] == 0 and _apply_criteria(f, V41_BASE))
    base_sd_all = sum(1 for f in sep_dec if _apply_criteria(f, V41_BASE))
    base_sd_tp  = sum(1 for f in sep_dec if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE))
    base_p = base_sd_tp / base_sd_all * 100 if base_sd_all else 0.0

    for label, extra in candidates:
        crit = {**V41_BASE, **extra}
        oos_tp  = sum(1 for f in oos    if f["is_prime"] == 1 and _apply_criteria(f, crit))
        sd_tp   = sum(1 for f in sep_dec if f["is_prime"] == 1 and _apply_criteria(f, crit))
        sd_fps  = sum(1 for f in sep_dec if f["is_prime"] == 0 and _apply_criteria(f, crit))
        sd_all  = sd_tp + sd_fps
        new_p   = sd_tp / sd_all * 100 if sd_all else 0.0
        delta_p = new_p - base_p
        fp_removed = base_sd_fps - sd_fps
        oostp_marker = " ⚠" if oos_tp < base_oos_tp_count else ""
        print(
            f"  {label:<32}  {oos_tp:>5}{oostp_marker}  {sd_tp:>5}  {sd_fps:>6}  "
            f"{fp_removed:>10}  {delta_p:>+7.1f}pp"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
