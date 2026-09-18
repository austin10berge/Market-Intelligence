"""Session 30 — Industrials and Financials sector RSI gates on V39.

Context:
  V39: Sep-Dec P=44.4%/R=40.6% sdTP=114; OOS P=4.9%/R=26.1% oosTP=12.
  Remaining FP walls (Session 24): Industrials ~40 FPs (NO RSI gate),
  Financial Services ~21 FPs (financials_rsi_max in analyze.py but not in V39).
  Session 26 showed technology_rsi_max=54 was blocking real TPs; relaxed to 60.
  Same pattern may apply to Industrials and Financials.

Sections:
  1. Industrials deep-dive: feature distributions for sector TPs vs FPs
  2. industrials_rsi_max sweep on V39 (thresholds 44-70)
  3. Financials RSI sweep on V39
  4. Healthcare RSI sweep on V39
  5. Combined candidates and V40 definition

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session30
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
V39_BASE = {k: v for k, v in V39.items() if k not in _IV_KEYS}


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


def _ks(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    all_vals = sorted(set(a + b))
    def ecdf(vals: list[float], x: float) -> float:
        return sum(1 for v in vals if v <= x) / len(vals)
    return max(abs(ecdf(a, x) - ecdf(b, x)) for x in all_vals)


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(int(len(s) * p), len(s) - 1)]


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
        f"  {label:<30}  "
        f"sd={f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
        f"tr={tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
        f"te={te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
        f"oos={oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%  "
        f"sdTP={f['true_positives']:3d}  oosTP={oo['true_positives']:2d}"
    )


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
    print(f"  Train      : {len(train)} rows, "
          f"{sum(1 for f in train if f['is_prime']==1)} prime")
    print(f"  Test       : {len(test)} rows, "
          f"{sum(1 for f in test if f['is_prime']==1)} prime")
    print(f"2026 OOS     : {len(oos)} rows, "
          f"{sum(1 for f in oos if f['is_prime']==1)} prime")
    print("\n  (header: sd=Sep-Dec full  tr=Train Sep-Oct  te=Test Nov-Dec  oos=2026 OOS)")

    # ── SECTION 1: Industrials deep-dive ─────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: Industrials sector — numeric feature distributions (Sep-Dec 2025)")
    print("  Target: find gates that cut FPs without blocking TPs.")
    print("=" * 80)

    ind_sd = [f for f in sep_dec if f.get("sector") == "Industrials"]
    ind_oos = [f for f in oos if f.get("sector") == "Industrials"]
    ind_sd_tp = [f for f in ind_sd if f["is_prime"] == 1]
    ind_sd_fp = [f for f in ind_sd if f["is_prime"] == 0]
    ind_oos_tp = [f for f in ind_oos if f["is_prime"] == 1]

    print(f"\n  Industrials Sep-Dec: {len(ind_sd_tp)} TPs, {len(ind_sd_fp)} FPs")
    print(f"  Industrials 2026 OOS: {len(ind_oos_tp)} TPs")
    print(f"  Tickers  — Sep-Dec TPs: {sorted({f['ticker'] for f in ind_sd_tp})}")
    print(f"  Tickers  — Sep-Dec FPs: {sorted({f['ticker'] for f in ind_sd_fp})}")
    if ind_oos_tp:
        print(f"  Tickers  — OOS TPs: {sorted({f['ticker'] for f in ind_oos_tp})}")

    numeric_feats = [
        "rsi", "adx", "rv20", "bb_width_pct", "volume_ratio",
        "pct_from_52wk_high", "adr20_pct",
        "price_vs_ema200_pct", "price_vs_sma150_pct",
        "market_cap_b", "forward_pe",
    ]

    print(f"\n  {'Feature':<28}  {'TP p5':>6}  {'TP med':>6}  {'TP p95':>6}  {'FP p5':>6}  {'FP med':>6}  {'FP p95':>6}  {'KS':>6}  {'n_tp':>4}  {'n_fp':>4}")  # noqa: E501
    for feat in numeric_feats:
        tp_v = [f[feat] for f in ind_sd if f["is_prime"]==1 and f.get(feat) is not None]
        fp_v = [f[feat] for f in ind_sd if f["is_prime"]==0 and f.get(feat) is not None]
        if not tp_v:
            print(f"  {feat:<28}  (no data)")
            continue
        ks = _ks(tp_v, fp_v)
        print(
            f"  {feat:<28}  "
            f"{_pct(tp_v,.05):>6.2f}  {_median(tp_v):>6.2f}  {_pct(tp_v,.95):>6.2f}  "
            f"{_pct(fp_v,.05):>6.2f}  {_median(fp_v):>6.2f}  {_pct(fp_v,.95):>6.2f}  "
            f"{ks:>6.3f}  {len(tp_v):>4d}  {len(fp_v):>4d}"
        )

    # RSI detail: full distribution for Industrials
    print("\n  1B. Industrials RSI detail — per-row view of Sep-Dec TPs")
    print(f"  {'Date':<12}  {'Ticker':>6}  {'RSI':>6}  {'ADX':>6}  {'bb_w%':>6}  {'volR':>6}  {'pfh%':>6}")  # noqa: E501
    for f in sorted(ind_sd_tp, key=lambda x: x.get("rsi") or 0):
        print(
            f"  {f['date']:<12}  {f['ticker']:>6}  "
            f"{f.get('rsi', 'N/A'):>6.1f}  {f.get('adx', 0):>6.1f}  "
            f"{f.get('bb_width_pct', 0):>6.2f}  {f.get('volume_ratio', 0):>6.2f}  "
            f"{f.get('pct_from_52wk_high', 0):>6.2f}"
        )

    print("\n  1C. Industrials RSI detail — Sep-Dec FPs (sorted by RSI desc)")
    print(f"  {'Date':<12}  {'Ticker':>6}  {'RSI':>6}  {'ADX':>6}  {'bb_w%':>6}  {'volR':>6}  {'pfh%':>6}")  # noqa: E501
    for f in sorted(ind_sd_fp, key=lambda x: -(x.get("rsi") or 0))[:30]:
        print(
            f"  {f['date']:<12}  {f['ticker']:>6}  "
            f"{f.get('rsi', 'N/A'):>6.1f}  {f.get('adx', 0):>6.1f}  "
            f"{f.get('bb_width_pct', 0):>6.2f}  {f.get('volume_ratio', 0):>6.2f}  "
            f"{f.get('pct_from_52wk_high', 0):>6.2f}"
        )

    print("\n  1D. Industrials RSI detail — 2026 OOS TPs")
    if ind_oos_tp:
        print(f"  {'Date':<12}  {'Ticker':>6}  {'RSI':>6}  {'ADX':>6}  {'bb_w%':>6}  {'volR':>6}  {'pfh%':>6}")  # noqa: E501
        for f in sorted(ind_oos_tp, key=lambda x: x.get("rsi") or 0):
            print(
                f"  {f['date']:<12}  {f['ticker']:>6}  "
                f"{f.get('rsi', 'N/A'):>6.1f}  {f.get('adx', 0):>6.1f}  "
                f"{f.get('bb_width_pct', 0):>6.2f}  {f.get('volume_ratio', 0):>6.2f}  "
                f"{f.get('pct_from_52wk_high', 0):>6.2f}"
            )
    else:
        print("  (no OOS TPs in Industrials)")

    # ── SECTION 2: industrials_rsi_max sweep ─────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: industrials_rsi_max sweep on V39")
    print("  How many Industrials FPs can we cut without losing TPs?")
    print("=" * 80)

    print(f"\n  {'Criteria':<30}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V39 (baseline)", sep_dec, train, test, oos, V39, V39_BASE)

    for thresh in [44, 50, 55, 60, 65, 70]:
        crit = {**V39, "industrials_rsi_max": thresh}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V39+ind_rsi<={thresh}", sep_dec, train, test, oos, crit, crit_base)

    print("\n  2A. Sep-Dec TPs blocked at each Industrials RSI threshold:")
    for thresh in [44, 50, 55, 60, 65, 70]:
        blocked = [
            f for f in sep_dec
            if f["is_prime"] == 1
            and f.get("sector") == "Industrials"
            and _apply_criteria(f, V39_BASE)
            and f.get("rsi") is not None
            and f["rsi"] > thresh
        ]
        if blocked:
            tickers = [(b["date"], b["ticker"], round(b["rsi"], 1)) for b in blocked]
            print(f"    rsi<={thresh}: blocks {len(blocked)} TPs — {tickers}")
        else:
            print(f"    rsi<={thresh}: 0 TPs blocked")

    print("\n  2B. OOS TPs blocked at each Industrials RSI threshold:")
    for thresh in [44, 50, 55, 60, 65, 70]:
        blocked = [
            f for f in oos
            if f["is_prime"] == 1
            and f.get("sector") == "Industrials"
            and _apply_criteria(f, V39_BASE)
            and f.get("rsi") is not None
            and f["rsi"] > thresh
        ]
        if blocked:
            tickers = [(b["date"], b["ticker"], round(b["rsi"], 1)) for b in blocked]
            print(f"    rsi<={thresh}: blocks {len(blocked)} OOS TPs — {tickers}")
        else:
            print(f"    rsi<={thresh}: 0 OOS TPs blocked")

    print("\n  2C. Industrials FPs removed at each threshold (V39_BASE, Sep-Dec):")
    ind_fps_v39 = [
        f for f in sep_dec
        if f["is_prime"] == 0
        and f.get("sector") == "Industrials"
        and _apply_criteria(f, V39_BASE)
    ]
    print(f"  Total Industrials FPs in V39 (Sep-Dec): {len(ind_fps_v39)}")
    for thresh in [44, 50, 55, 60, 65, 70]:
        removed = [f for f in ind_fps_v39 if f.get("rsi") is not None and f["rsi"] > thresh]
        kept    = [f for f in ind_fps_v39 if f.get("rsi") is None or f["rsi"] <= thresh]
        print(f"    rsi<={thresh}: removes {len(removed)} FPs, keeps {len(kept)} FPs")

    # ── SECTION 3: Financials RSI sweep ──────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: financials_rsi_max sweep on V39")
    print("  Financial Services: ~21 FPs. financials_rsi_max is already in analyze.py.")
    print("=" * 80)

    fin_sd = [f for f in sep_dec if f.get("sector") == "Financial Services"]
    fin_oos = [f for f in oos if f.get("sector") == "Financial Services"]
    fin_sd_tp = [f for f in fin_sd if f["is_prime"] == 1]
    fin_sd_fp = [f for f in fin_sd if f["is_prime"] == 0]
    fin_oos_tp = [f for f in fin_oos if f["is_prime"] == 1]
    print(f"\n  Financials Sep-Dec: {len(fin_sd_tp)} TPs, {len(fin_sd_fp)} FPs")
    print(f"  Financials OOS: {len(fin_oos_tp)} TPs")
    print(f"  Sep-Dec TP tickers: {sorted({f['ticker'] for f in fin_sd_tp})}")

    print(f"\n  {'Criteria':<30}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V39 (baseline)", sep_dec, train, test, oos, V39, V39_BASE)

    for thresh in [44, 50, 55, 60, 65, 70]:
        crit = {**V39, "financials_rsi_max": thresh}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V39+fin_rsi<={thresh}", sep_dec, train, test, oos, crit, crit_base)

    print("\n  3A. Sep-Dec TPs blocked at each Financials RSI threshold:")
    for thresh in [44, 50, 55, 60, 65, 70]:
        blocked = [
            f for f in sep_dec
            if f["is_prime"] == 1
            and f.get("sector") == "Financial Services"
            and _apply_criteria(f, V39_BASE)
            and f.get("rsi") is not None
            and f["rsi"] > thresh
        ]
        if blocked:
            tickers = [(b["date"], b["ticker"], round(b["rsi"], 1)) for b in blocked]
            print(f"    rsi<={thresh}: blocks {len(blocked)} TPs — {tickers}")
        else:
            print(f"    rsi<={thresh}: 0 TPs blocked")

    print("\n  3B. OOS TPs blocked at each Financials RSI threshold:")
    for thresh in [44, 50, 55, 60, 65, 70]:
        blocked = [
            f for f in oos
            if f["is_prime"] == 1
            and f.get("sector") == "Financial Services"
            and _apply_criteria(f, V39_BASE)
            and f.get("rsi") is not None
            and f["rsi"] > thresh
        ]
        if blocked:
            tickers = [(b["date"], b["ticker"], round(b["rsi"], 1)) for b in blocked]
            print(f"    rsi<={thresh}: blocks {len(blocked)} OOS TPs — {tickers}")
        else:
            print(f"    rsi<={thresh}: 0 OOS TPs blocked")

    print("\n  3C. Financials FPs removed at each threshold (V39_BASE, Sep-Dec):")
    fin_fps_v39 = [
        f for f in sep_dec
        if f["is_prime"] == 0
        and f.get("sector") == "Financial Services"
        and _apply_criteria(f, V39_BASE)
    ]
    print(f"  Total Financials FPs in V39 (Sep-Dec): {len(fin_fps_v39)}")
    for thresh in [44, 50, 55, 60, 65, 70]:
        removed = [f for f in fin_fps_v39 if f.get("rsi") is not None and f["rsi"] > thresh]
        kept    = [f for f in fin_fps_v39 if f.get("rsi") is None or f["rsi"] <= thresh]
        print(f"    rsi<={thresh}: removes {len(removed)} FPs, keeps {len(kept)} FPs")

    # ── SECTION 4: Healthcare RSI sweep ──────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: healthcare_rsi_max sweep on V39 (bonus)")
    print("  Healthcare has an IV gate but no RSI gate. Quick sweep to see if useful.")
    print("=" * 80)

    hc_sd = [f for f in sep_dec if f.get("sector") == "Healthcare"]
    hc_sd_tp = [f for f in hc_sd if f["is_prime"] == 1]
    hc_sd_fp = [f for f in hc_sd if f["is_prime"] == 0]
    print(f"\n  Healthcare Sep-Dec: {len(hc_sd_tp)} TPs, {len(hc_sd_fp)} FPs")
    print(f"  Sep-Dec TP tickers: {sorted({f['ticker'] for f in hc_sd_tp})}")

    print(f"\n  {'Criteria':<30}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V39 (baseline)", sep_dec, train, test, oos, V39, V39_BASE)

    for thresh in [50, 55, 60, 65, 70]:
        crit = {**V39, "healthcare_rsi_max": thresh}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V39+hc_rsi<={thresh}", sep_dec, train, test, oos, crit, crit_base)

    print("\n  4A. Sep-Dec TPs blocked at each Healthcare RSI threshold:")
    for thresh in [50, 55, 60, 65, 70]:
        blocked = [
            f for f in sep_dec
            if f["is_prime"] == 1
            and f.get("sector") == "Healthcare"
            and _apply_criteria(f, V39_BASE)
            and f.get("rsi") is not None
            and f["rsi"] > thresh
        ]
        if blocked:
            tickers = [(b["date"], b["ticker"], round(b["rsi"], 1)) for b in blocked]
            print(f"    rsi<={thresh}: blocks {len(blocked)} TPs — {tickers}")
        else:
            print(f"    rsi<={thresh}: 0 TPs blocked")

    # ── SECTION 5: Combined candidates and V40 ────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 5: Combined candidates — best Industrials + Financials RSI gates")
    print("=" * 80)

    print(f"\n  {'Criteria':<40}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V39 (baseline)", sep_dec, train, test, oos, V39, V39_BASE)

    # Parametric grid: best non-blocking ind_rsi x best non-blocking fin_rsi
    for ind_thresh in [None, 70, 65, 60]:
        for fin_thresh in [None, 70, 65, 60]:
            if ind_thresh is None and fin_thresh is None:
                continue
            crit = dict(V39)
            parts = []
            if ind_thresh is not None:
                crit["industrials_rsi_max"] = ind_thresh
                parts.append(f"ind{ind_thresh}")
            if fin_thresh is not None:
                crit["financials_rsi_max"] = fin_thresh
                parts.append(f"fin{fin_thresh}")
            crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
            _row4(f"V39+{'+'.join(parts)}", sep_dec, train, test, oos, crit, crit_base)

    # Also try with healthcare if it looks beneficial from section 4
    for ind_thresh in [None, 65, 60]:
        for hc_thresh in [65, 60]:
            crit = {**V39, "healthcare_rsi_max": hc_thresh}
            parts = [f"hc{hc_thresh}"]
            if ind_thresh is not None:
                crit["industrials_rsi_max"] = ind_thresh
                parts.insert(0, f"ind{ind_thresh}")
            crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
            _row4(f"V39+{'+'.join(parts)}", sep_dec, train, test, oos, crit, crit_base)

    print("\nDone.")


if __name__ == "__main__":
    main()
