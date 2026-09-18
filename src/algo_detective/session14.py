"""Session 14 — Dividend yield cap revision + portfolio ticker cross-reference.

Reddit scrape of u/GarbageTimePro confirmed he trades NEE (div_yield=2.89%) and
GILD (div_yield=2.58%), both excluded by our current cap of 2.5%.  He also confirmed
XYZ is a real traded ticker (not a data artifact).

Goals:
  1. Sweep dividend_yield_max from 2.5 → 3.5 on narrow universe
  2. Define v32: v31a + dividend_yield_max=3.0
  3. Cross-reference his 47-ticker portfolio universe vs our prime_tickers.csv
  4. Identify which of his tickers are in our prime set vs not

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session14
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
        {**f, "best_iv": index.get((f["date"], f["ticker"]), {}).get("best_iv"),
               "pcr_vol": index.get((f["date"], f["ticker"]), {}).get("pcr_vol"),
               "pcr_oi": index.get((f["date"], f["ticker"]), {}).get("pcr_oi")}
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

# His 47-ticker portfolio universe (from mlabstrading.com snapshot, Jul 2025–May 2026)
MLABS_TICKERS = {
    "NVDA", "GOOG", "ANET", "QCOM", "UAL", "AAPL", "ORCL", "LRCX", "MSFT",
    "HOOD", "DELL", "HPE", "DG", "BIDU", "VST", "DAL", "NEE", "SMCI", "PLTR",
    "FSLR", "AA", "HAL", "AXP", "CHWY", "EQT", "WMT", "GE", "XYZ", "IBKR",
    "WPM", "FCX", "AEO", "AMD", "DOCN", "TSM", "AAL", "B", "ATI", "XOM",
    "EBAY", "ROST", "C", "GILD", "M", "DVN", "META",
    # SPAXX excluded (money market, not a tradeable stock)
}


def main() -> None:
    conn = _get_connection()
    all_features = get_all_features()

    # Narrow universe: only the 74 prime tickers
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)
    prime = [f for f in narrow if f["is_prime"] == 1]
    n_prime = len(prime)

    print(f"Narrow universe: {len(narrow)} rows, {n_prime} prime, {len(narrow)-n_prime} control")
    print(f"Unique prime tickers: {len(prime_tickers)}")

    # ── SECTION 1: Baseline v31a ─────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 1: v31a baseline")
    print("=" * 70)
    base = _score_criteria(narrow, V31A)
    base_p = base["precision"]
    base_r = base["recall"]
    print(f"  v31a: P={base_p*100:.1f}%  R={base_r*100:.1f}%  TP={base['true_positives']}  FP={base['false_positives']}")

    # ── SECTION 2: Dividend yield sweep ─────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 2: dividend_yield_max sweep (base = v31a)")
    print("=" * 70)
    print(f"\n  {'Cap':>6}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}  {'New TPs'}")
    print(f"  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}  {'-'*30}")

    # Track which primes pass at baseline
    base_tp_set = {(f["ticker"], f["date"]) for f in prime if _apply_criteria(f, V31A)}

    for cap in [2.5, 2.6, 2.7, 2.8, 2.9, 3.0, 3.1, 3.2, 3.5, 4.0]:
        crit = {**V31A, "dividend_yield_max": cap}
        res = _score_criteria(narrow, crit)
        delta_p = (res["precision"] - base_p) * 100
        # Which NEW primes were recovered?
        new_tp_set = {(f["ticker"], f["date"]) for f in prime if _apply_criteria(f, crit)}
        newly_added = new_tp_set - base_tp_set
        tickers_added = Counter(t for t, _ in newly_added)
        added_str = ", ".join(f"{t}x{c}" for t, c in sorted(tickers_added.items())) if tickers_added else "—"
        print(
            f"  {cap:6.1f}  {res['precision']*100:6.1f}%  {res['recall']*100:6.1f}%  "
            f"{res['true_positives']:5d}  {res['false_positives']:5d}  {delta_p:+6.1f}pp  {added_str}"
        )

    # ── SECTION 3: v32 = v31a + dividend_yield_max=3.0 ──────────────────────
    print("\n" + "=" * 70)
    print("SECTION 3: v32 definition = v31a + dividend_yield_max=3.0")
    print("=" * 70)
    V32 = {**V31A, "dividend_yield_max": 3.0}
    v32 = _score_criteria(narrow, V32)
    print(f"\n  v32: P={v32['precision']*100:.1f}%  R={v32['recall']*100:.1f}%  TP={v32['true_positives']}  FP={v32['false_positives']}")
    delta_p = (v32["precision"] - base_p) * 100
    delta_r = (v32["recall"] - base_r) * 100
    print(f"  vs v31a: ΔP={delta_p:+.1f}pp  ΔR={delta_r:+.1f}pp")

    # Show newly recovered primes
    v32_tp_set = {(f["ticker"], f["date"]) for f in prime if _apply_criteria(f, V32)}
    recovered = v32_tp_set - base_tp_set
    lost = base_tp_set - v32_tp_set
    print(f"\n  Recovered TPs ({len(recovered)}):")
    for ticker, date in sorted(recovered):
        row = next(f for f in prime if f["ticker"] == ticker and f["date"] == date)
        dy = row.get("dividend_yield")
        print(f"    {date}  {ticker:<8}  div_yield={dy:.2f}%  sector={row.get('sector')}")
    if lost:
        print(f"\n  Lost TPs ({len(lost)}): {sorted(lost)}")

    # New FPs introduced
    base_fp_set = {(f["ticker"], f["date"]) for f in narrow if not f["is_prime"] and _apply_criteria(f, V31A)}
    v32_fp_set = {(f["ticker"], f["date"]) for f in narrow if not f["is_prime"] and _apply_criteria(f, V32)}
    new_fps = v32_fp_set - base_fp_set
    if new_fps:
        fp_tickers = Counter(t for t, _ in new_fps)
        print(f"\n  New FPs introduced ({len(new_fps)}) by raising div cap:")
        for t, c in sorted(fp_tickers.items(), key=lambda x: -x[1]):
            print(f"    {t}: {c} rows")
    else:
        print("\n  No new FPs introduced (div_yield filter only affects prime tickers in narrow universe)")

    # ── SECTION 4: Cross-reference prime tickers vs his portfolio ────────────
    print("\n" + "=" * 70)
    print("SECTION 4: Prime ticker universe vs his mlabstrading.com ticker list")
    print("=" * 70)

    in_both = prime_tickers & MLABS_TICKERS
    only_prime = prime_tickers - MLABS_TICKERS
    only_mlabs = MLABS_TICKERS - prime_tickers

    print(f"\n  Our prime tickers: {len(prime_tickers)}")
    print(f"  His mlabs tickers: {len(MLABS_TICKERS)}")
    print(f"  In BOTH: {len(in_both)}")
    print(f"  Only in our prime set (not in his 11mo snapshot): {len(only_prime)}")
    print(f"  Only in his mlabs set (he trades but not in our Sep-Dec 2025 primes): {len(only_mlabs)}")

    print(f"\n  Tickers in BOTH ({len(in_both)}):")
    print(f"    {sorted(in_both)}")

    print(f"\n  Our prime tickers NOT in his snapshot ({len(only_prime)}):")
    # Count occurrences in prime data
    prime_counts = Counter(f["ticker"] for f in prime)
    for t in sorted(only_prime, key=lambda x: -prime_counts[x]):
        print(f"    {t}: {prime_counts[t]} prime obs")

    print(f"\n  His mlabs tickers NOT in our Sep-Dec 2025 prime set ({len(only_mlabs)}):")
    print(f"    {sorted(only_mlabs)}")

    # ── SECTION 5: Dividend yield of tickers in his set that we miss ─────────
    print("\n" + "=" * 70)
    print("SECTION 5: Dividend yield profile of his tickers vs our prime tickers")
    print("=" * 70)

    conn2 = _get_connection()
    ticker_divs = {}
    for ticker in MLABS_TICKERS | prime_tickers:
        row = conn2.execute(
            "SELECT dividend_yield FROM universe_fundamentals WHERE symbol = ?", (ticker,)
        ).fetchone()
        if row and row[0] is not None:
            ticker_divs[ticker] = row[0]
    conn2.close()

    # Show tickers with div_yield > 2.5% that are in his universe
    high_div_mlabs = [(t, ticker_divs[t]) for t in MLABS_TICKERS if ticker_divs.get(t, 0) > 2.5]
    if high_div_mlabs:
        print("\n  His tickers with div_yield > 2.5% (currently excluded by v31a):")
        for t, dy in sorted(high_div_mlabs, key=lambda x: -x[1]):
            in_prime = "✓ prime" if t in prime_tickers else "not prime"
            print(f"    {t:<8}  div_yield={dy:.2f}%  {in_prime}")

    # Also show which prime tickers have high div yield (being excluded)
    high_div_prime = [
        (f["ticker"], f.get("dividend_yield"))
        for f in prime
        if f.get("dividend_yield") is not None and f["dividend_yield"] > 2.5
    ]
    if high_div_prime:
        ticker_div_prime = Counter()
        for t, dy in high_div_prime:
            ticker_div_prime[t] = dy
        print("\n  Prime observations excluded by div_yield > 2.5% gate:")
        for t, dy in sorted(set((t, d) for t, d in high_div_prime), key=lambda x: -x[1]):
            count = sum(1 for f in prime if f["ticker"] == t and f.get("dividend_yield", 0) > 2.5)
            print(f"    {t:<8}  div_yield={dy:.2f}%  {count} prime rows excluded")

    # ── SECTION 6: v32 vs v31b comparison ────────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 6: Full comparison of v31a, v31b, v32")
    print("=" * 70)
    V31B = {**V31A, "consumer_cyclical_rsi_max": 52, "technology_rsi_max": 58}
    v31b = _score_criteria(narrow, V31B)
    V32B = {**V31B, "dividend_yield_max": 3.0}
    v32b = _score_criteria(narrow, V32B)

    rows = [
        ("v31a", V31A, base),
        ("v31b", V31B, v31b),
        ("v32  (v31a + div=3.0)", V32, v32),
        ("v32b (v31b + div=3.0)", V32B, v32b),
    ]
    print(f"\n  {'Version':<28}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    print(f"  {'-'*28}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}")
    for name, _, res in rows:
        print(
            f"  {name:<28}  {res['precision']*100:6.1f}%  {res['recall']*100:6.1f}%  "
            f"{res['true_positives']:5d}  {res['false_positives']:5d}"
        )

    print("\n--- Session 14 complete ---")


if __name__ == "__main__":
    main()
