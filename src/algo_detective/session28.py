"""Session 28 — sma20_above_sma50, adr20_pct, IWM regime feature analysis.

Three findings from Session 27 Reddit/blog scrape that need testing:
  1. "20 > 50 > 200" MA structure (verbatim) — our model has sma50_above_sma150 but NOT
     sma20_above_sma50. Feature has KS=0.006 in Sep-Dec but KS=0.197 in OOS (same
     strengthening pattern as long-term MA gates that proved useful).
  2. "ADR < 4.0 preferably" — he explicitly dropped ANET when ADR exceeded 4.0.
     adr20_pct KS=0.205 in Sep-Dec. 95.7% of Sep-Dec TPs already clear 4.0, but
     stricter thresholds (3.5) may filter FPs with acceptable recall cost.
  3. "SPY, VIX, IWM, etc." as regime inputs — IWM not in our DB. Fetch from Alpaca
     and compute date-level IWM regime state for all scan dates.

Sections:
  1. sma20_above_sma50: KS + pass rates per split, 4-split scorecard sweep
  2. adr20_pct: distributions per split, sweep {3.5, 4.0, 4.5, 5.0}
  3. Combined candidates and V39 definition
  4. IWM regime: fetch, compute EMA200, analyze vs prime-day density

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session28
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from src.config import settings

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

V38_BASE = {k: v for k, v in V38.items() if k not in _IV_KEYS}


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


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(int(len(s) * p), len(s) - 1)]


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
        f"  {label:<22}  "
        f"sd={f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
        f"tr={tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
        f"te={te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
        f"oos={oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%  "
        f"sdTP={f['true_positives']:3d}  oosTP={oo['true_positives']:2d}"
    )


def _ema(prices: list[float], period: int) -> list[float]:
    """Compute EMA for a list of prices."""
    if not prices:
        return []
    k = 2 / (period + 1)
    ema_vals = [prices[0]]
    for p in prices[1:]:
        ema_vals.append(p * k + ema_vals[-1] * (1 - k))
    return ema_vals


def _fetch_iwm_bars(start: str = "2024-01-01", end: str = "2026-06-30") -> dict[str, float]:
    """Fetch IWM daily close prices from Alpaca. Returns {date: close}."""
    url = f"{settings.alpaca_data_url}/v2/stocks/IWM/bars"
    params = {
        "timeframe": "1Day",
        "start": start,
        "end": end,
        "adjustment": "all",
        "limit": 10000,
    }
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }
    all_bars: dict[str, float] = {}
    next_token: str | None = None
    while True:
        p = dict(params)
        if next_token:
            p["page_token"] = next_token
        full_url = url + "?" + urllib.parse.urlencode(p)
        req = urllib.request.Request(full_url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        for bar in data.get("bars", []):
            date_str = bar["t"][:10]
            all_bars[date_str] = bar["c"]
        next_token = data.get("next_page_token")
        if not next_token:
            break
    return all_bars


def _compute_iwm_features(bars: dict[str, float], scan_dates: list[str]) -> dict[str, dict]:
    """Compute IWM EMA200 and 52wk-high features for each scan date."""
    sorted_dates = sorted(bars)
    closes = [bars[d] for d in sorted_dates]
    ema200_vals = _ema(closes, 200)
    date_to_ema200 = dict(zip(sorted_dates, ema200_vals))

    result: dict[str, dict] = {}
    for scan_date in scan_dates:
        if scan_date not in bars:
            result[scan_date] = {"close": None, "ema200": None, "above_ema200": None, "pct_from_52wk_high": None}
            continue
        close = bars[scan_date]
        ema200 = date_to_ema200.get(scan_date)
        # 52-week high: max close over trailing 252 trading days
        idx = sorted_dates.index(scan_date)
        trailing = closes[max(0, idx - 251):idx + 1]
        high_52wk = max(trailing) if trailing else close
        pct_from_high = (high_52wk - close) / high_52wk * 100 if high_52wk > 0 else None
        result[scan_date] = {
            "close": close,
            "ema200": ema200,
            "above_ema200": 1 if (ema200 and close > ema200) else 0,
            "pct_from_52wk_high": round(pct_from_high, 2) if pct_from_high is not None else None,
        }
    return result


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
    print("  (header: sd=Sep-Dec full  tr=Train Sep-Oct  te=Test Nov-Dec  oos=2026 OOS)")

    # ── SECTION 1: sma20_above_sma50 ─────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: sma20_above_sma50 gate analysis")
    print("  His stated MA structure: '20 > 50 > 200'.")
    print("  We have sma50_above_sma150 but not sma20_above_sma50.")
    print("=" * 80)

    print("\n  1A. Pass rates (sma20_above_sma50 == 1) — prime vs ctrl per split")
    print(f"  {'Split':<16}  {'TP pass%':>8}  {'FP pass%':>8}  {'KS':>6}  n_TP  n_FP")
    for label, rows in [
        ("Sep-Dec full", sep_dec),
        ("Train Sep-Oct", train),
        ("Test  Nov-Dec", test),
        ("2026 OOS",      oos),
    ]:
        tp_vals = [f["sma20_above_sma50"] for f in rows if f["is_prime"]==1 and f.get("sma20_above_sma50") is not None]
        fp_vals = [f["sma20_above_sma50"] for f in rows if f["is_prime"]==0 and f.get("sma20_above_sma50") is not None]
        tp_pass = sum(v == 1 for v in tp_vals) / len(tp_vals) if tp_vals else float("nan")
        fp_pass = sum(v == 1 for v in fp_vals) / len(fp_vals) if fp_vals else float("nan")
        ks = _ks(tp_vals, fp_vals)
        print(f"  {label:<16}  {tp_pass*100:>7.1f}%  {fp_pass*100:>7.1f}%  {ks:>6.3f}  {len(tp_vals):4d}  {len(fp_vals):4d}")

    print("\n  1B. 4-split scorecard: V38 vs V38 + sma20_above_sma50=1")
    print(f"  {'Criteria':<22}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")
    _row4("V38 (baseline)", sep_dec, train, test, oos, V38, V38_BASE)
    V38_sma20 = {**V38, "sma20_above_sma50": 1}
    V38_sma20_base = {k: v for k, v in V38_sma20.items() if k not in _IV_KEYS}
    _row4("V38+sma20>sma50", sep_dec, train, test, oos, V38_sma20, V38_sma20_base)

    # Show which OOS TPs are blocked by the sma20 gate
    print("\n  1C. OOS prime rows blocked by sma20_above_sma50=1:")
    oos_blocked = [
        f for f in oos
        if f["is_prime"] == 1
        and _apply_criteria(f, V38_BASE)
        and f.get("sma20_above_sma50") != 1
    ]
    if oos_blocked:
        for f in oos_blocked:
            print(f"    {f['date']} {f['ticker']:6s}  sma20_above_sma50={f.get('sma20_above_sma50')}")
    else:
        print("    None — all OOS TPs pass the sma20 gate")

    # ── SECTION 2: adr20_pct ────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: adr20_pct gate analysis")
    print("  He said 'ADR < 4.0 preferably' and explicitly dropped ANET when ADR exceeded 4.0.")
    print("  adr20_pct = 20-day average (high-low)/close * 100.")
    print("=" * 80)

    print("\n  2A. adr20_pct distributions — prime vs ctrl per split")
    print(f"  {'Split':<16}  {'TP p5':>6}  {'TP med':>6}  {'TP p95':>6}  {'FP med':>6}  {'KS':>6}  {'TP<4.0':>7}  {'FP<4.0':>7}")
    for label, rows in [
        ("Sep-Dec full", sep_dec),
        ("Train Sep-Oct", train),
        ("Test  Nov-Dec", test),
        ("2026 OOS",      oos),
    ]:
        tp_v = [f["adr20_pct"] for f in rows if f["is_prime"]==1 and f.get("adr20_pct") is not None]
        fp_v = [f["adr20_pct"] for f in rows if f["is_prime"]==0 and f.get("adr20_pct") is not None]
        if not tp_v:
            print(f"  {label:<16}  (no data)")
            continue
        ks = _ks(tp_v, fp_v)
        tp_lt4 = sum(v < 4.0 for v in tp_v) / len(tp_v)
        fp_lt4 = sum(v < 4.0 for v in fp_v) / len(fp_v) if fp_v else float("nan")
        print(
            f"  {label:<16}  {_pct(tp_v,.05):>6.2f}  {_median(tp_v):>6.2f}  {_pct(tp_v,.95):>6.2f}  "
            f"{_median(fp_v):>6.2f}  {ks:>6.3f}  {tp_lt4*100:>6.1f}%  {fp_lt4*100:>6.1f}%"
        )

    print("\n  2B. 4-split scorecard: adr20_pct_max sweep on V38")
    print(f"  {'Criteria':<22}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")
    _row4("V38 (baseline)", sep_dec, train, test, oos, V38, V38_BASE)
    for thresh in [3.5, 4.0, 4.5, 5.0]:
        crit = {**V38, "adr20_pct_max": thresh}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V38+adr20<={thresh}", sep_dec, train, test, oos, crit, crit_base)

    print("\n  2C. OOS prime rows blocked at adr20_pct_max=4.0 (passing V38_BASE):")
    oos_adr_blocked = [
        f for f in oos
        if f["is_prime"] == 1
        and _apply_criteria(f, V38_BASE)
        and f.get("adr20_pct") is not None
        and f["adr20_pct"] > 4.0
    ]
    if oos_adr_blocked:
        for f in oos_adr_blocked:
            print(f"    {f['date']} {f['ticker']:6s}  adr20_pct={f['adr20_pct']:.2f}")
    else:
        print("    None — all OOS TPs pass adr20_pct<=4.0")

    # ── SECTION 3: Combined candidates and V39 ───────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: Combined candidates and V39 definition")
    print("=" * 80)
    print(f"\n  {'Criteria':<28}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")
    _row4("V38 (baseline)", sep_dec, train, test, oos, V38, V38_BASE)

    # All combinations of the two meaningful gates
    for sma20 in [False, True]:
        for adr_thresh in [None, 4.5, 4.0, 3.5]:
            if not sma20 and adr_thresh is None:
                continue
            crit = dict(V38)
            label_parts = []
            if sma20:
                crit["sma20_above_sma50"] = 1
                label_parts.append("sma20")
            if adr_thresh is not None:
                crit["adr20_pct_max"] = adr_thresh
                label_parts.append(f"adr<={adr_thresh}")
            crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
            _row4(f"V38+{'+'.join(label_parts)}", sep_dec, train, test, oos, crit, crit_base)

    # ── SECTION 4: IWM regime feature ────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: IWM regime feature")
    print("  He mentioned 'SPY, VIX, IWM, etc.' as regime inputs.")
    print("  Fetch IWM from Alpaca, compute EMA200 per scan date, check correlation")
    print("  with prime-day density and model precision.")
    print("=" * 80)

    # Get unique scan dates
    all_scan_dates = sorted({f["date"] for f in narrow})
    sd_dates  = sorted({f["date"] for f in sep_dec})
    oos_dates = sorted({f["date"] for f in oos})
    print(f"\n  Scan dates: {len(all_scan_dates)} total "
          f"({len(sd_dates)} Sep-Dec 2025, {len(oos_dates)} 2026 OOS)")

    print("\n  Fetching IWM daily bars from Alpaca (2024-01-01 to 2026-06-30)...")
    try:
        iwm_bars = _fetch_iwm_bars("2024-01-01", "2026-06-30")
        print(f"  Fetched {len(iwm_bars)} IWM trading days "
              f"({min(iwm_bars)} to {max(iwm_bars)})")
    except Exception as exc:
        print(f"  ERROR fetching IWM bars: {exc}")
        print("  Skipping IWM analysis.")
        _print_summary(sep_dec, train, test, oos, V38, V38_BASE)
        return

    iwm_features = _compute_iwm_features(iwm_bars, all_scan_dates)

    print("\n  4A. IWM state on all scan dates:")
    print(f"  {'Date':<12}  {'IWM':>7}  {'EMA200':>7}  {'Above?':>6}  {'%FromHigh':>9}  "
          f"{'#Primes':>7}  {'#Ctrl':>6}")
    for d in all_scan_dates:
        iwm = iwm_features.get(d, {})
        primes = sum(1 for f in narrow if f["date"]==d and f["is_prime"]==1)
        ctrl   = sum(1 for f in narrow if f["date"]==d and f["is_prime"]==0)
        close_s  = f"{iwm['close']:.2f}"    if iwm.get("close")  else "N/A"
        ema200_s = f"{iwm['ema200']:.2f}"   if iwm.get("ema200") else "N/A"
        above_s  = str(iwm.get("above_ema200", "N/A"))
        pfh_s    = f"{iwm['pct_from_52wk_high']:.1f}%" if iwm.get("pct_from_52wk_high") is not None else "N/A"
        print(f"  {d}  {close_s:>7}  {ema200_s:>7}  {above_s:>6}  {pfh_s:>9}  {primes:>7}  {ctrl:>6}")

    # Split by IWM above/below EMA200
    print("\n  4B. Model precision/recall by IWM regime (V38 on Sep-Dec 2025 scan dates):")
    for label, rows, dates_set in [
        ("Sep-Dec 2025", sep_dec, set(sd_dates)),
        ("2026 OOS",     oos,     set(oos_dates)),
    ]:
        above_dates = {d for d in dates_set if iwm_features.get(d, {}).get("above_ema200") == 1}
        below_dates = {d for d in dates_set if iwm_features.get(d, {}).get("above_ema200") == 0}
        na_dates    = dates_set - above_dates - below_dates

        above_rows = [f for f in rows if f["date"] in above_dates]
        below_rows = [f for f in rows if f["date"] in below_dates]

        crit = V38 if label != "2026 OOS" else V38_BASE
        s_above = _score_criteria(above_rows, crit) if above_rows else None
        s_below = _score_criteria(below_rows, crit) if below_rows else None

        print(f"\n  {label}:")
        print(f"    IWM above EMA200: {len(above_dates)} dates, {len(above_rows)} rows")
        if s_above:
            print(f"      P={s_above['precision']*100:.1f}%  R={s_above['recall']*100:.1f}%  "
                  f"TP={s_above['true_positives']}  FP={s_above['false_positives']}")
        print(f"    IWM below EMA200: {len(below_dates)} dates, {len(below_rows)} rows")
        if s_below:
            print(f"      P={s_below['precision']*100:.1f}%  R={s_below['recall']*100:.1f}%  "
                  f"TP={s_below['true_positives']}  FP={s_below['false_positives']}")
        if na_dates:
            print(f"    IWM data missing: {len(na_dates)} dates — {sorted(na_dates)}")

    # IWM pct-from-high vs prime density correlation
    print("\n  4C. IWM pct_from_52wk_high vs prime count per date (Sep-Dec 2025):")
    date_stats = []
    for d in sd_dates:
        iwm = iwm_features.get(d, {})
        pfh = iwm.get("pct_from_52wk_high")
        n_prime = sum(1 for f in sep_dec if f["date"]==d and f["is_prime"]==1)
        if pfh is not None:
            date_stats.append((d, pfh, n_prime))
    if date_stats:
        # Sort by IWM health (ascending = closer to high = healthier)
        date_stats.sort(key=lambda x: x[1])
        n = len(date_stats)
        top_half = date_stats[:n//2]   # IWM closer to high
        bot_half = date_stats[n//2:]   # IWM farther from high
        top_prime = sum(x[2] for x in top_half) / len(top_half)
        bot_prime = sum(x[2] for x in bot_half) / len(bot_half)
        print(f"  IWM closer to high (median pfh={_median([x[1] for x in top_half]):.1f}%): "
              f"avg primes/date={top_prime:.1f}")
        print(f"  IWM farther from high (median pfh={_median([x[1] for x in bot_half]):.1f}%): "
              f"avg primes/date={bot_prime:.1f}")

    _print_summary(sep_dec, train, test, oos, V38, V38_BASE)


def _print_summary(sep_dec, train, test, oos, V38, V38_BASE) -> None:
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\n  {'Criteria':<22}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")
    _row4("V38 (baseline)", sep_dec, train, test, oos, V38, V38_BASE)

    candidates = {
        "V38+sma20>50":             {**V38, "sma20_above_sma50": 1},
        "V38+adr20<=4.0":           {**V38, "adr20_pct_max": 4.0},
        "V38+adr20<=4.5":           {**V38, "adr20_pct_max": 4.5},
        "V38+sma20+adr20<=4.5":     {**V38, "sma20_above_sma50": 1, "adr20_pct_max": 4.5},
        "V38+sma20+adr20<=4.0":     {**V38, "sma20_above_sma50": 1, "adr20_pct_max": 4.0},
    }
    for label, crit in candidates.items():
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(label, sep_dec, train, test, oos, crit, crit_base)


if __name__ == "__main__":
    main()
