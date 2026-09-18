"""Session 29 — IWM regime feature analysis via yfinance.

Session 28 was blocked by Alpaca 403 on stock bars. This session fetches
IWM bars via yfinance and computes date-level regime features:
  - iwm_above_ema200: IWM close > IWM EMA200 on scan date
  - iwm_pct_from_52wk_high: how far IWM is from its 52-week high

Questions:
  1. What is IWM's regime state on each Sep-Dec 2025 and 2026 OOS scan date?
  2. KS: does iwm_above_ema200 / iwm_pct_from_52wk_high discriminate prime vs FP rows?
  3. Prime-day density split by IWM regime (bull vs bear)
  4. V39 scorecard split by IWM regime
  5. iwm_pct_from_52wk_high threshold sweep as a regime filter

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session29
"""

from __future__ import annotations

import yfinance as yf

from .analyze import _score_criteria
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


def _median(vals: list[float]) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    n = len(s)
    return (s[n // 2] + s[(n - 1) // 2]) / 2


def _ema(prices: list[float], period: int) -> list[float]:
    if not prices:
        return []
    k = 2 / (period + 1)
    ema_vals = [prices[0]]
    for p in prices[1:]:
        ema_vals.append(p * k + ema_vals[-1] * (1 - k))
    return ema_vals


def _score_label(rows: list[dict], crit: dict) -> str:
    s = _score_criteria(rows, crit)
    return (
        f"P={s['precision']*100:5.1f}%  R={s['recall']*100:4.1f}%  "
        f"TP={s['true_positives']:3d}  FP={s['false_positives']:3d}"
    )


def _fetch_iwm_bars(start: str = "2023-12-01", end: str = "2026-07-01") -> dict[str, float]:
    """Fetch IWM daily adjusted close prices via yfinance. Returns {date_str: close}."""
    ticker = yf.Ticker("IWM")
    hist = ticker.history(start=start, end=end, interval="1d", auto_adjust=True)
    result: dict[str, float] = {}
    for dt, close in hist["Close"].items():
        date_str = dt.strftime("%Y-%m-%d")
        result[date_str] = float(close)
    return result


def _compute_iwm_features(bars: dict[str, float], scan_dates: list[str]) -> dict[str, dict]:
    """Compute EMA200, EMA50, and 52wk-high pct for each scan date."""
    sorted_dates = sorted(bars)
    closes = [bars[d] for d in sorted_dates]
    ema200_vals = _ema(closes, 200)
    ema50_vals  = _ema(closes, 50)
    date_to_ema200 = dict(zip(sorted_dates, ema200_vals))
    date_to_ema50  = dict(zip(sorted_dates, ema50_vals))

    result: dict[str, dict] = {}
    for scan_date in scan_dates:
        if scan_date not in bars:
            result[scan_date] = {
                "close": None, "ema200": None, "ema50": None,
                "above_ema200": None, "above_ema50": None,
                "pct_from_52wk_high": None,
            }
            continue
        close  = bars[scan_date]
        ema200 = date_to_ema200[scan_date]
        ema50  = date_to_ema50[scan_date]
        idx    = sorted_dates.index(scan_date)
        # 52-week high: trailing 252 trading days (≈1 year)
        trailing = closes[max(0, idx - 251): idx + 1]
        high_52wk = max(trailing) if trailing else close
        pct_from_high = (high_52wk - close) / high_52wk * 100
        result[scan_date] = {
            "close":              round(close, 2),
            "ema200":             round(ema200, 2),
            "ema50":              round(ema50, 2),
            "above_ema200":       1 if close > ema200 else 0,
            "above_ema50":        1 if close > ema50 else 0,
            "pct_from_52wk_high": round(pct_from_high, 2),
        }
    return result


def _enrich_with_iwm(rows: list[dict], iwm: dict[str, dict]) -> list[dict]:
    """Add IWM regime fields to each feature row."""
    out = []
    for f in rows:
        state = iwm.get(f["date"], {})
        out.append({
            **f,
            "iwm_above_ema200":       state.get("above_ema200"),
            "iwm_above_ema50":        state.get("above_ema50"),
            "iwm_pct_from_52wk_high": state.get("pct_from_52wk_high"),
        })
    return out


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

    sep_dec_dates = sorted({f["date"] for f in sep_dec})
    oos_dates     = sorted({f["date"] for f in oos})
    all_scan_dates = sorted(set(sep_dec_dates) | set(oos_dates))

    print(f"Sep-Dec 2025 : {len(sep_dec)} rows, "
          f"{sum(1 for f in sep_dec if f['is_prime']==1)} prime, "
          f"{len(sep_dec_dates)} scan dates")
    print(f"  Train      : {len(train)} rows, "
          f"{sum(1 for f in train if f['is_prime']==1)} prime")
    print(f"  Test       : {len(test)} rows, "
          f"{sum(1 for f in test if f['is_prime']==1)} prime")
    print(f"2026 OOS     : {len(oos)} rows, "
          f"{sum(1 for f in oos if f['is_prime']==1)} prime, "
          f"{len(oos_dates)} scan dates")

    # ── SECTION 1: Fetch IWM and show regime state per scan date ─────────────
    print("\n" + "=" * 80)
    print("SECTION 1: IWM regime state on each scan date (via yfinance)")
    print("=" * 80)

    print("\n  Fetching IWM daily bars from yfinance ...", flush=True)
    bars = _fetch_iwm_bars()
    print(f"  Got {len(bars)} trading days ({min(bars)} → {max(bars)})")

    iwm = _compute_iwm_features(bars, all_scan_dates)

    print("\n  1A. Sep-Dec 2025 scan dates — IWM state")
    print(f"  {'Date':<12}  {'Close':>7}  {'EMA200':>7}  {'EMA50':>7}  {'AbvE200':>8}  {'AbvE50':>7}  {'Pct52wkH':>9}")
    for d in sep_dec_dates:
        s = iwm[d]
        if s["close"] is None:
            print(f"  {d:<12}  (no data)")
            continue
        print(
            f"  {d:<12}  {s['close']:>7.2f}  {s['ema200']:>7.2f}  {s['ema50']:>7.2f}  "
            f"{'YES' if s['above_ema200'] else 'NO ':>8}  "
            f"{'YES' if s['above_ema50'] else 'NO ':>7}  "
            f"{s['pct_from_52wk_high']:>8.2f}%"
        )

    print("\n  1B. 2026 OOS scan dates — IWM state")
    print(f"  {'Date':<12}  {'Close':>7}  {'EMA200':>7}  {'EMA50':>7}  {'AbvE200':>8}  {'AbvE50':>7}  {'Pct52wkH':>9}")
    for d in oos_dates:
        s = iwm[d]
        if s["close"] is None:
            print(f"  {d:<12}  (no data)")
            continue
        print(
            f"  {d:<12}  {s['close']:>7.2f}  {s['ema200']:>7.2f}  {s['ema50']:>7.2f}  "
            f"{'YES' if s['above_ema200'] else 'NO ':>8}  "
            f"{'YES' if s['above_ema50'] else 'NO ':>7}  "
            f"{s['pct_from_52wk_high']:>8.2f}%"
        )

    # Summary counts
    sd_bull = sum(1 for d in sep_dec_dates if iwm[d]["above_ema200"] == 1)
    sd_bear = sum(1 for d in sep_dec_dates if iwm[d]["above_ema200"] == 0)
    oos_bull = sum(1 for d in oos_dates if iwm[d]["above_ema200"] == 1)
    oos_bear = sum(1 for d in oos_dates if iwm[d]["above_ema200"] == 0)
    print(f"\n  Sep-Dec: {sd_bull}/{len(sep_dec_dates)} dates IWM > EMA200,  "
          f"{sd_bear}/{len(sep_dec_dates)} dates IWM ≤ EMA200")
    print(f"  OOS:     {oos_bull}/{len(oos_dates)} dates IWM > EMA200,  "
          f"{oos_bear}/{len(oos_dates)} dates IWM ≤ EMA200")

    # ── SECTION 2: KS analysis ───────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: KS discriminability — IWM features (prime vs ctrl per split)")
    print("  Note: IWM is date-level. All rows on the same date share the same IWM value.")
    print("  KS here measures whether prime rows happen on 'better' IWM regime dates.")
    print("=" * 80)

    sep_dec_e = _enrich_with_iwm(sep_dec, iwm)
    train_e   = _enrich_with_iwm(train,   iwm)
    test_e    = _enrich_with_iwm(test,    iwm)
    oos_e     = _enrich_with_iwm(oos,     iwm)

    print(f"\n  {'Feature':<28}  {'sd KS':>6}  {'tr KS':>6}  {'te KS':>6}  {'oos KS':>7}")
    for feat in ["iwm_above_ema200", "iwm_above_ema50", "iwm_pct_from_52wk_high"]:
        row_ks = []
        for rows in [sep_dec_e, train_e, test_e, oos_e]:
            tp = [f[feat] for f in rows if f["is_prime"]==1 and f.get(feat) is not None]
            fp = [f[feat] for f in rows if f["is_prime"]==0 and f.get(feat) is not None]
            row_ks.append(_ks(tp, fp))
        print(f"  {feat:<28}  {row_ks[0]:>6.3f}  {row_ks[1]:>6.3f}  {row_ks[2]:>6.3f}  {row_ks[3]:>7.3f}")

    # Pass rates for binary features
    print("\n  2A. iwm_above_ema200 pass rates — prime vs ctrl per split")
    print(f"  {'Split':<16}  {'TP pct':>7}  {'FP pct':>7}  {'TP n':>6}  {'FP n':>6}")
    for label, rows in [
        ("Sep-Dec full", sep_dec_e),
        ("Train Sep-Oct", train_e),
        ("Test  Nov-Dec", test_e),
        ("2026 OOS",      oos_e),
    ]:
        tp = [f["iwm_above_ema200"] for f in rows if f["is_prime"]==1 and f.get("iwm_above_ema200") is not None]
        fp = [f["iwm_above_ema200"] for f in rows if f["is_prime"]==0 and f.get("iwm_above_ema200") is not None]
        tp_pct = sum(tp) / len(tp) if tp else float("nan")
        fp_pct = sum(fp) / len(fp) if fp else float("nan")
        print(f"  {label:<16}  {tp_pct*100:>6.1f}%  {fp_pct*100:>6.1f}%  {len(tp):>6d}  {len(fp):>6d}")

    print("\n  2B. iwm_pct_from_52wk_high distributions — prime vs ctrl per split")
    print(f"  {'Split':<16}  {'TP med':>7}  {'TP p95':>7}  {'FP med':>7}  {'FP p95':>7}")
    for label, rows in [
        ("Sep-Dec full", sep_dec_e),
        ("Train Sep-Oct", train_e),
        ("Test  Nov-Dec", test_e),
        ("2026 OOS",      oos_e),
    ]:
        def _pct95(vals: list) -> float:
            s = sorted(vals)
            return s[min(int(len(s) * 0.95), len(s) - 1)] if s else float("nan")
        tp_v = [f["iwm_pct_from_52wk_high"] for f in rows if f["is_prime"]==1 and f.get("iwm_pct_from_52wk_high") is not None]
        fp_v = [f["iwm_pct_from_52wk_high"] for f in rows if f["is_prime"]==0 and f.get("iwm_pct_from_52wk_high") is not None]
        print(
            f"  {label:<16}  {_median(tp_v):>6.2f}%  {_pct95(tp_v):>6.2f}%  "
            f"{_median(fp_v):>6.2f}%  {_pct95(fp_v):>6.2f}%"
        )

    # ── SECTION 3: Prime density by IWM regime ───────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: Prime density per scan date, grouped by IWM regime")
    print("=" * 80)

    print("\n  3A. Sep-Dec 2025 — prime density per date, sorted by IWM state")
    print(f"  {'Date':<12}  {'IWM?':>5}  {'PfHi%':>6}  {'Primes':>7}  {'Total':>6}  {'Prime%':>7}")
    for label in ["YES", "NO"]:
        is_bull = label == "YES"
        dates_in_group = [d for d in sep_dec_dates if iwm[d]["above_ema200"] == (1 if is_bull else 0)]
        for d in dates_in_group:
            rows_on_date = [f for f in sep_dec if f["date"] == d]
            n_prime = sum(1 for f in rows_on_date if f["is_prime"] == 1)
            n_total = len(rows_on_date)
            pfh = iwm[d]["pct_from_52wk_high"]
            print(f"  {d:<12}  {label:>5}  {pfh:>5.2f}%  {n_prime:>7d}  {n_total:>6d}  {n_prime/n_total*100:>6.1f}%")

    print("\n  3B. 2026 OOS — prime density per date, sorted by IWM state")
    print(f"  {'Date':<12}  {'IWM?':>5}  {'PfHi%':>6}  {'Primes':>7}  {'Total':>6}  {'Prime%':>7}")
    for label in ["YES", "NO"]:
        is_bull = label == "YES"
        dates_in_group = [d for d in oos_dates if iwm[d]["above_ema200"] == (1 if is_bull else 0)]
        for d in dates_in_group:
            rows_on_date = [f for f in oos if f["date"] == d]
            n_prime = sum(1 for f in rows_on_date if f["is_prime"] == 1)
            n_total = len(rows_on_date)
            pfh = iwm[d]["pct_from_52wk_high"]
            print(f"  {d:<12}  {label:>5}  {pfh:>5.2f}%  {n_prime:>7d}  {n_total:>6d}  {n_prime/n_total*100:>6.1f}%")

    # ── SECTION 4: V39 scorecard split by IWM regime ────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: V39 scorecard — IWM regime splits")
    print("  'Bull' = IWM above EMA200 on scan date.  'Bear' = IWM at/below EMA200.")
    print("  Note: Sep-Dec splits use V39 (with IV gates). OOS uses V39_BASE.")
    print("=" * 80)

    def _split_regime(rows: list[dict], bull: bool) -> list[dict]:
        val = 1 if bull else 0
        return [f for f in rows if f.get("iwm_above_ema200") == val]

    for regime_label, is_bull in [("IWM BULL (above EMA200)", True), ("IWM BEAR (at/below EMA200)", False)]:
        sd_r  = _split_regime(sep_dec_e, is_bull)
        tr_r  = _split_regime(train_e, is_bull)
        te_r  = _split_regime(test_e, is_bull)
        oos_r = _split_regime(oos_e, is_bull)
        sd_primes  = sum(1 for f in sd_r if f["is_prime"]==1)
        oos_primes = sum(1 for f in oos_r if f["is_prime"]==1)
        print(f"\n  {regime_label}")
        print(f"    Sep-Dec dates: {len({f['date'] for f in sd_r})} | "
              f"rows: {len(sd_r)} | primes: {sd_primes}")
        print(f"    OOS dates:     {len({f['date'] for f in oos_r})} | "
              f"rows: {len(oos_r)} | primes: {oos_primes}")
        if sd_r:
            s = _score_criteria(sd_r, V39)
            print(f"    V39 Sep-Dec:   {_score_label(sd_r, V39)}")
        if te_r:
            print(f"    V39 Test:      {_score_label(te_r, V39)}")
        if oos_r:
            print(f"    V39 OOS:       {_score_label(oos_r, V39_BASE)}")

    print("\n  4B. V39 full (all dates — baseline for comparison)")
    print(f"    V39 Sep-Dec:  {_score_label(sep_dec_e, V39)}")
    print(f"    V39 Test:     {_score_label(test_e, V39)}")
    print(f"    V39 OOS:      {_score_label(oos_e, V39_BASE)}")

    # ── SECTION 5: IWM pct_from_52wk_high threshold sweep ───────────────────
    print("\n" + "=" * 80)
    print("SECTION 5: iwm_pct_from_52wk_high regime filter sweep")
    print("  Only deploy on dates when IWM is within X% of its 52-week high.")
    print("  Lower pct = IWM closer to high = better regime.")
    print("=" * 80)

    print(f"\n  {'Max pfh52wk%':<14}  {'sd dates':>8}  sd P/R (sdTP)          te P/R (teTP)          oos P/R (oosTP)")
    for thresh in [5.0, 10.0, 15.0, 20.0, 25.0, None]:
        label = f"≤{thresh:.0f}%" if thresh is not None else "all (no gate)"
        def _filter_pfh(rows: list[dict], t: float | None) -> list[dict]:
            if t is None:
                return rows
            return [f for f in rows if f.get("iwm_pct_from_52wk_high") is not None
                    and f["iwm_pct_from_52wk_high"] <= t]

        sd_f  = _filter_pfh(sep_dec_e, thresh)
        te_f  = _filter_pfh(test_e,    thresh)
        oos_f = _filter_pfh(oos_e,     thresh)
        n_sd_dates = len({f["date"] for f in sd_f})

        sd_s  = _score_criteria(sd_f,  V39)
        te_s  = _score_criteria(te_f,  V39)
        oos_s = _score_criteria(oos_f, V39_BASE)

        print(
            f"  {label:<14}  {n_sd_dates:>8d}  "
            f"sd={sd_s['precision']*100:5.1f}%/{sd_s['recall']*100:4.1f}% ({sd_s['true_positives']:3d}TP)   "
            f"te={te_s['precision']*100:5.1f}%/{te_s['recall']*100:4.1f}% ({te_s['true_positives']:3d}TP)   "
            f"oos={oos_s['precision']*100:5.1f}%/{oos_s['recall']*100:4.1f}% ({oos_s['true_positives']:3d}TP)"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
