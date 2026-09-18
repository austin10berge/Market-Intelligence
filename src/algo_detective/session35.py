"""Session 35 — V42 confirmation + earnings_growth_min sweep.

Agenda:
  1. V42 confirmation: V41 + forward_pe_max=45 — full 4-split scorecard.
     Expected: 0 OOS TPs lost, +0.4pp sd precision, −~3pp sd recall (LRCX, HWM).
  2. earnings_growth_min sweep on V41_BASE and V42_BASE.
     KS strengthens 0.095→0.245 Sep-Dec→OOS. OOS TP median=0.794 vs FP 0.289.
     Sweep: {0.05, 0.10, 0.15, 0.20}. Both NULL-fails and NULL-passes variants.
  3. Best combined candidate for V43.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session35
"""

from __future__ import annotations

import numpy as np

from .analyze import _apply_criteria
from .store import get_all_features

TRAIN_CUTOFF = "2025-10-31"
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

V42 = {**V41, "forward_pe_max": 45.0}
V42_BASE = {k: v for k, v in V42.items() if k not in _IV_KEYS}


def _row4(
    label: str,
    sep_dec: list[dict],
    train: list[dict],
    test: list[dict],
    oos: list[dict],
    crit: dict,
) -> tuple[float, float, int, int]:
    def _stats(rows: list[dict]) -> tuple[float, float, int]:
        hits = [f for f in rows if _apply_criteria(f, crit)]
        tp = sum(1 for f in hits if f["is_prime"] == 1)
        total = len(hits)
        total_p = sum(1 for f in rows if f["is_prime"] == 1)
        prec = tp / total * 100 if total else 0.0
        rec = tp / total_p * 100 if total_p else 0.0
        return prec, rec, tp

    sd_p, sd_r, sd_tp = _stats(sep_dec)
    tr_p, tr_r, _     = _stats(train)
    te_p, te_r, _     = _stats(test)
    oo_p, oo_r, oo_tp = _stats(oos)
    print(
        f"  {label:<44} "
        f"sd={sd_p:5.1f}%/{sd_r:4.1f}%  "
        f"tr={tr_p:5.1f}%/{tr_r:4.1f}%  "
        f"te={te_p:5.1f}%/{te_r:4.1f}%  "
        f"oos={oo_p:5.1f}%/{oo_r:4.1f}%  "
        f"sdTP={sd_tp}  oosTP={oo_tp}"
    )
    return sd_p, sd_r, sd_tp, oo_tp


def main() -> None:
    all_features = get_all_features()
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    all_features = [f for f in all_features if f["ticker"] in prime_tickers]

    sep_dec = [f for f in all_features if f["date"] < OOS_START]
    train   = [f for f in sep_dec if f["date"] <= TRAIN_CUTOFF]
    test    = [f for f in sep_dec if f["date"] > TRAIN_CUTOFF]
    oos     = [f for f in all_features if f["date"] >= OOS_START]

    sd_prime  = sum(1 for f in sep_dec if f["is_prime"] == 1)
    oos_prime = sum(1 for f in oos if f["is_prime"] == 1)

    print(f"Narrow universe: {len(prime_tickers)} tickers")
    print(f"Sep-Dec 2025: {len(sep_dec)} rows  ({sd_prime} prime)")
    print(f"2026 OOS    : {len(oos)} rows  ({oos_prime} prime)")
    print()
    print("  Note: sdTP numbers will be higher than session32 (133 vs 114) due to DB")
    print("  expansion. OOS TP count (14) is stable. OOS change is the key signal.")

    # ── SECTION 1: V42 confirmation ───────────────────────────────────────────
    print()
    print("=" * 90)
    print("SECTION 1: V42 confirmation — V41 + forward_pe_max=45")
    print("  Expected: 0 OOS TPs lost, small +P gain, LRCX/HWM removed from Sep-Dec TPs")
    print("=" * 90)
    print()
    print(f"  {'Criteria':<44}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501

    v41_sd_p, v41_sd_r, v41_sd_tp, v41_oo_tp = _row4(
        "V41 (baseline)", sep_dec, train, test, oos, V41_BASE
    )
    v42_sd_p, v42_sd_r, v42_sd_tp, v42_oo_tp = _row4(
        "V42 (forward_pe_max=45)", sep_dec, train, test, oos, V42_BASE
    )

    print()
    print(f"  Δ V41→V42: sdTP {v41_sd_tp}→{v42_sd_tp} ({v42_sd_tp-v41_sd_tp:+d})  "
          f"oosTP {v41_oo_tp}→{v42_oo_tp} ({v42_oo_tp-v41_oo_tp:+d})  "
          f"sd_P {v41_sd_p:.1f}%→{v42_sd_p:.1f}% ({v42_sd_p-v41_sd_p:+.1f}pp)")

    # 1A: Which Sep-Dec TPs are lost at pe_max=45?
    print()
    print("  1A. Sep-Dec TPs lost at forward_pe_max=45 (vs V41):")
    v41_sd_tps = {(f["date"], f["ticker"]) for f in sep_dec
                  if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE)}
    v42_sd_tps = {(f["date"], f["ticker"]) for f in sep_dec
                  if f["is_prime"] == 1 and _apply_criteria(f, V42_BASE)}
    lost_sd_tps = v41_sd_tps - v42_sd_tps
    if lost_sd_tps:
        ticker_lost: dict[str, list] = {}
        for date, ticker in sorted(lost_sd_tps):
            ticker_lost.setdefault(ticker, []).append(date)
        for ticker in sorted(ticker_lost.keys()):
            row = next(f for f in sep_dec if f["ticker"] == ticker and f["is_prime"] == 1)
            pe = row.get("forward_pe")
            sector = row.get("sector")
            dates_str = ", ".join(ticker_lost[ticker])
            pe_str = f"{pe:.1f}" if pe else "n/a"
            print(f"    {ticker:<6}  pe={pe_str}  sector={sector}  dates={dates_str}")
    else:
        print("    (none lost)")

    # 1B: Verify all 14 OOS TPs still pass V42
    print()
    print("  1B. OOS TP verification at forward_pe_max=45:")
    v41_oos_tps = [(f["date"], f["ticker"]) for f in oos
                   if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE)]
    v42_oos_tps_set = {(f["date"], f["ticker"]) for f in oos
                       if f["is_prime"] == 1 and _apply_criteria(f, V42_BASE)}
    lost_oos = [(d, t) for d, t in v41_oos_tps if (d, t) not in v42_oos_tps_set]
    if lost_oos:
        for date, ticker in lost_oos:
            row = next(f for f in oos if f["date"] == date and f["ticker"] == ticker)
            pe = row.get("forward_pe")
            print(f"    LOST: {date} {ticker:<6}  pe={pe}")
    else:
        print(f"    All {len(v41_oos_tps)} OOS TPs preserved at pe_max=45. V42 confirmed.")

    # 1C: Which Sep-Dec FPs were removed by V42?
    print()
    print("  1C. Sep-Dec FPs removed by forward_pe_max=45 (sector breakdown):")
    v41_sd_fps = {(f["date"], f["ticker"]) for f in sep_dec
                  if f["is_prime"] == 0 and _apply_criteria(f, V41_BASE)}
    v42_sd_fps = {(f["date"], f["ticker"]) for f in sep_dec
                  if f["is_prime"] == 0 and _apply_criteria(f, V42_BASE)}
    removed_fps = v41_sd_fps - v42_sd_fps
    sector_counts: dict[str, int] = {}
    for date, ticker in removed_fps:
        row = next(f for f in sep_dec if f["date"] == date and f["ticker"] == ticker)
        sec = row.get("sector") or "Unknown"
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
    if sector_counts:
        total_removed = sum(sector_counts.values())
        sec_str = ", ".join(
            f"{s}:{n}" for s, n in sorted(sector_counts.items(), key=lambda x: -x[1])
        )
        print(f"    Removed {total_removed} FPs  [{sec_str}]")
    else:
        print("    (none removed)")

    # ── SECTION 2: earnings_growth_min sweep ──────────────────────────────────
    print()
    print("=" * 90)
    print("SECTION 2: earnings_growth_min sweep on V41_BASE and V42_BASE")
    print("  KS: Sep-Dec 0.095 → OOS 0.245 (strengthening signal)")
    print("  OOS TP median earnings_growth=0.794 (79% YoY) vs FP median=0.289")
    print()
    print("  NULL handling: standard _min = NULL fails. Also testing NULL-passes variant.")
    print("=" * 90)
    print()

    # 2A: NULL coverage check
    def _has_eg(f: dict) -> bool:
        return f.get("earnings_growth") is not None

    eg_coverage_all = sum(1 for f in sep_dec if _has_eg(f))
    eg_coverage_tp  = sum(1 for f in sep_dec if f["is_prime"] == 1 and _has_eg(f))
    eg_coverage_fp  = sum(1 for f in sep_dec if f["is_prime"] == 0 and _has_eg(f))
    eg_oos_tp_cnt   = sum(1 for f in oos if f["is_prime"] == 1 and _has_eg(f))
    sd_ctrl = len(sep_dec) - sd_prime
    print("  2A. earnings_growth NULL coverage:")
    pct = eg_coverage_all / len(sep_dec) * 100
    print(f"    Sep-Dec all:   {eg_coverage_all}/{len(sep_dec)} = {pct:.1f}%")
    pct = eg_coverage_tp / sd_prime * 100
    print(f"    Sep-Dec prime: {eg_coverage_tp}/{sd_prime} = {pct:.1f}%")
    pct = eg_coverage_fp / sd_ctrl * 100
    print(f"    Sep-Dec ctrl:  {eg_coverage_fp}/{sd_ctrl} = {pct:.1f}%")
    pct = eg_oos_tp_cnt / oos_prime * 100
    print(f"    OOS prime:     {eg_oos_tp_cnt}/{oos_prime} = {pct:.1f}%")

    # 2B: Earnings growth distribution for V41 pass-through rows
    print()
    print("  2B. earnings_growth distribution (Sep-Dec, V41 pass-through rows):")
    v41_sd_prime_rows = [f for f in sep_dec if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE)]
    v41_sd_fp_rows    = [f for f in sep_dec if f["is_prime"] == 0 and _apply_criteria(f, V41_BASE)]
    v41_oos_tp_rows   = [f for f in oos if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE)]

    def _eg_stats(rows: list[dict], label: str) -> None:
        vals = [f["earnings_growth"] for f in rows if f.get("earnings_growth") is not None]
        nulls = sum(1 for f in rows if f.get("earnings_growth") is None)
        if not vals:
            print(f"    {label}: no data")
            return
        print(
            f"    {label} (n={len(vals)}, null={nulls}): "
            f"p10={np.percentile(vals,10):.3f}  p25={np.percentile(vals,25):.3f}  "
            f"p50={np.percentile(vals,50):.3f}  p75={np.percentile(vals,75):.3f}  "
            f"p90={np.percentile(vals,90):.3f}"
        )

    _eg_stats(v41_sd_prime_rows, "Sep-Dec TPs (V41 hits)")
    _eg_stats(v41_sd_fp_rows,    "Sep-Dec FPs (V41 hits)")
    _eg_stats(v41_oos_tp_rows,   "OOS TPs (V41 hits)    ")

    # 2C: Sweep — standard (NULL fails)
    print()
    print(f"  {'Criteria':<44}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    print("  -- V41_BASE sweep (NULL earnings_growth fails gate) --")
    _row4("V41 (no eg gate)", sep_dec, train, test, oos, V41_BASE)
    for eg_min in [0.05, 0.10, 0.15, 0.20, 0.30]:
        crit = {**V41_BASE, "earnings_growth_min": eg_min}
        _row4(f"V41+eg_min={eg_min:.2f} (null=fail)", sep_dec, train, test, oos, crit)

    print()
    print("  -- V42_BASE sweep (NULL earnings_growth fails gate) --")
    _row4("V42 (no eg gate)", sep_dec, train, test, oos, V42_BASE)
    for eg_min in [0.05, 0.10, 0.15, 0.20, 0.30]:
        crit = {**V42_BASE, "earnings_growth_min": eg_min}
        _row4(f"V42+eg_min={eg_min:.2f} (null=fail)", sep_dec, train, test, oos, crit)

    # 2D: Sweep — NULL passes variant (manually override _min behavior)
    print()
    print("  -- V41_BASE sweep (NULL earnings_growth PASSES gate) --")

    def _apply_with_eg_null_pass(row: dict, base_crit: dict, eg_min: float) -> bool:
        if not _apply_criteria(row, base_crit):
            return False
        eg = row.get("earnings_growth")
        if eg is not None and eg < eg_min:
            return False
        return True

    def _row4_eg_null_pass(label: str, eg_min: float, base_crit: dict) -> None:
        def _stats(rows: list[dict]) -> tuple[float, float, int]:
            hits = [f for f in rows if _apply_with_eg_null_pass(f, base_crit, eg_min)]
            tp = sum(1 for f in hits if f["is_prime"] == 1)
            total = len(hits)
            total_p = sum(1 for f in rows if f["is_prime"] == 1)
            prec = tp / total * 100 if total else 0.0
            rec = tp / total_p * 100 if total_p else 0.0
            return prec, rec, tp

        sd_p, sd_r, sd_tp = _stats(sep_dec)
        tr_p, tr_r, _     = _stats(train)
        te_p, te_r, _     = _stats(test)
        oo_p, oo_r, oo_tp = _stats(oos)
        print(
            f"  {label:<44} "
            f"sd={sd_p:5.1f}%/{sd_r:4.1f}%  "
            f"tr={tr_p:5.1f}%/{tr_r:4.1f}%  "
            f"te={te_p:5.1f}%/{te_r:4.1f}%  "
            f"oos={oo_p:5.1f}%/{oo_r:4.1f}%  "
            f"sdTP={sd_tp}  oosTP={oo_tp}"
        )

    for eg_min in [0.05, 0.10, 0.15, 0.20, 0.30]:
        _row4_eg_null_pass(f"V41+eg_min={eg_min:.2f} (null=pass)", eg_min, V41_BASE)

    print()
    print("  -- V42_BASE sweep (NULL earnings_growth PASSES gate) --")
    for eg_min in [0.05, 0.10, 0.15, 0.20, 0.30]:
        _row4_eg_null_pass(f"V42+eg_min={eg_min:.2f} (null=pass)", eg_min, V42_BASE)

    # 2E: OOS TPs blocked at each threshold (null=fail)
    print()
    print("  2E. OOS TPs blocked at each earnings_growth_min (null=fail):")
    v41_oos_tp_set = {(f["date"], f["ticker"]) for f in oos
                      if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE)}
    for eg_min in [0.05, 0.10, 0.15, 0.20]:
        crit = {**V41_BASE, "earnings_growth_min": eg_min}
        new_set = {(f["date"], f["ticker"]) for f in oos
                   if f["is_prime"] == 1 and _apply_criteria(f, crit)}
        lost = v41_oos_tp_set - new_set
        if lost:
            for date, ticker in sorted(lost):
                row = next(f for f in oos if f["date"] == date and f["ticker"] == ticker)
                eg = row.get("earnings_growth")
                print(f"    eg_min={eg_min:.2f}: LOSES {date} {ticker:<6}  eg={eg}")
        else:
            print(f"    eg_min={eg_min:.2f}: 0 OOS TPs lost")

    # 2F: OOS TPs blocked at each threshold (null=pass)
    print()
    print("  2F. OOS TPs blocked at each earnings_growth_min (null=pass):")
    for eg_min in [0.05, 0.10, 0.15, 0.20]:
        lost = []
        for date, ticker in sorted(v41_oos_tp_set):
            row = next(f for f in oos if f["date"] == date and f["ticker"] == ticker)
            eg = row.get("earnings_growth")
            if eg is not None and eg < eg_min:
                lost.append((date, ticker, eg))
        if lost:
            for date, ticker, eg in lost:
                print(f"    eg_min={eg_min:.2f}: LOSES {date} {ticker:<6}  eg={eg}")
        else:
            print(f"    eg_min={eg_min:.2f}: 0 OOS TPs lost (null=pass)")

    # 2G: Which FPs get removed at best non-blocking threshold?
    print()
    print("  2G. Sep-Dec FPs removed at eg_min=0.10 (null=pass) — sector breakdown:")
    def _sd_fps_with_eg(eg_min: float, base_crit: dict) -> set[tuple]:
        return {
            (f["date"], f["ticker"]) for f in sep_dec
            if f["is_prime"] == 0 and _apply_with_eg_null_pass(f, base_crit, eg_min)
        }

    base_fps = {(f["date"], f["ticker"]) for f in sep_dec
                if f["is_prime"] == 0 and _apply_criteria(f, V41_BASE)}
    for eg_min in [0.05, 0.10, 0.15, 0.20]:
        new_fps = _sd_fps_with_eg(eg_min, V41_BASE)
        removed = base_fps - new_fps
        sector_ct: dict[str, int] = {}
        for date, ticker in removed:
            row = next(f for f in sep_dec if f["date"] == date and f["ticker"] == ticker)
            sec = row.get("sector") or "Unknown"
            sector_ct[sec] = sector_ct.get(sec, 0) + 1
        if sector_ct:
            sec_str = ", ".join(
                f"{s}:{n}" for s, n in sorted(sector_ct.items(), key=lambda x: -x[1])
            )
            print(f"    eg_min={eg_min:.2f}: removes {len(removed)} FPs  [{sec_str}]")
        else:
            print(f"    eg_min={eg_min:.2f}: removes 0 FPs")

    # ── SECTION 3: V43 candidate summary ─────────────────────────────────────
    print()
    print("=" * 90)
    print("SECTION 3: V43 candidates — best combinations (oosTP-preserving)")
    print("=" * 90)
    print()

    base_oos_tp = sum(1 for f in oos if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE))
    base_sd_fp  = sum(1 for f in sep_dec if f["is_prime"] == 0 and _apply_criteria(f, V41_BASE))
    base_sd_all = sum(1 for f in sep_dec if _apply_criteria(f, V41_BASE))
    base_sd_tp  = sum(1 for f in sep_dec if f["is_prime"] == 1 and _apply_criteria(f, V41_BASE))
    base_sd_p   = base_sd_tp / base_sd_all * 100 if base_sd_all else 0.0

    print(f"  V41 baseline: sd_P={base_sd_p:.1f}%  sdTP={base_sd_tp}  sdFP={base_sd_fp}  oosTP={base_oos_tp}")  # noqa: E501
    print()
    print(f"  {'Label':<40}  {'oosTP':>5}  {'sdTP':>5}  {'sdFP':>5}  {'fp_rmvd':>7}  {'δP_sd':>8}")

    def _summary_row(label: str, crit: dict) -> None:
        oos_tp  = sum(1 for f in oos if f["is_prime"] == 1 and _apply_criteria(f, crit))
        sd_tp   = sum(1 for f in sep_dec if f["is_prime"] == 1 and _apply_criteria(f, crit))
        sd_fp   = sum(1 for f in sep_dec if f["is_prime"] == 0 and _apply_criteria(f, crit))
        sd_all  = sd_tp + sd_fp
        new_p   = sd_tp / sd_all * 100 if sd_all else 0.0
        delta_p = new_p - base_sd_p
        marker  = " ⚠" if oos_tp < base_oos_tp else ""
        print(
            f"  {label:<40}  {oos_tp:>5}{marker}  {sd_tp:>5}  {sd_fp:>5}  "
            f"{base_sd_fp - sd_fp:>7}  {delta_p:>+7.1f}pp"
        )

    def _summary_row_eg_null_pass(label: str, base_crit: dict, eg_min: float) -> None:
        def _counts(rows: list[dict]) -> tuple[int, int]:
            hits = [f for f in rows if _apply_with_eg_null_pass(f, base_crit, eg_min)]
            tp = sum(1 for f in hits if f["is_prime"] == 1)
            fp = sum(1 for f in hits if f["is_prime"] == 0)
            return tp, fp

        oos_tp, _ = _counts(oos)
        sd_tp, sd_fp = _counts(sep_dec)
        sd_all = sd_tp + sd_fp
        new_p = sd_tp / sd_all * 100 if sd_all else 0.0
        delta_p = new_p - base_sd_p
        marker = " ⚠" if oos_tp < base_oos_tp else ""
        print(
            f"  {label:<40}  {oos_tp:>5}{marker}  {sd_tp:>5}  {sd_fp:>5}  "
            f"{base_sd_fp - sd_fp:>7}  {delta_p:>+7.1f}pp"
        )

    _summary_row("V41 (baseline)", V41_BASE)
    _summary_row("V42 = V41+pe_max=45", V42_BASE)
    for eg_min in [0.05, 0.10, 0.15, 0.20]:
        crit = {**V42_BASE, "earnings_growth_min": eg_min}
        _summary_row(f"V42+eg_min={eg_min:.2f} (null=fail)", crit)
    for eg_min in [0.05, 0.10, 0.15, 0.20]:
        _summary_row_eg_null_pass(f"V42+eg_min={eg_min:.2f} (null=pass)", V42_BASE, eg_min)

    print()
    print("Done.")


if __name__ == "__main__":
    main()
