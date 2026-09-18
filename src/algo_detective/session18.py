"""Session 18 — True 2026 OOS evaluation + regime filter analysis.

Two experiments:

A) 2026 OOS recall test (Jan–Jun 2026, 36 dates, ~46 primes)
   No options data available for 2026, so IV-dependent gates are stripped.
   We test V31A_BASE (non-IV criteria only) to see if the technical / fundamental
   filters generalize to new data.  Focus metric: recall on actual trades.

B) Regime filter on Sep–Dec 2025 temporal holdout
   Adds breadth_pct_min gate to v34 and tests whether filtering out bad-regime days
   reduces the train→test degradation seen in session 17.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session18
"""

from __future__ import annotations

from .analyze import _score_criteria
from .store import _get_connection, get_all_features

# ── Criteria ──────────────────────────────────────────────────────────────────

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

# Base: IV-free subset (all options-dependent keys removed).
# Required for 2026 dates where best_iv = NULL everywhere.
_IV_KEYS = {
    "options_iv_min", "iv_rv_min", "pcr_vol_max",
    "industrials_iv_min", "consumer_cyclical_iv_min", "healthcare_iv_min",
    "consumer_defensive_iv_max", "energy_iv_min", "basic_materials_iv_min",
    "utilities_iv_min",
}
V31A_BASE = {k: v for k, v in V31A.items() if k not in _IV_KEYS}
V34_BASE  = {k: v for k, v in V34.items()  if k not in _IV_KEYS}

TRAIN_CUTOFF = "2025-10-31"
TEST_START   = "2025-11-01"
OOS_START    = "2026-01-01"

# ── Helpers ───────────────────────────────────────────────────────────────────


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


def _compute_breadth() -> dict[str, float]:
    """Return {date: breadth_pct} for all dates in detective_features."""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT date,
                   CAST(SUM(sma50_above_sma200) AS REAL) / COUNT(*) * 100 AS pct
            FROM detective_features
            GROUP BY date
        """).fetchall()
    finally:
        conn.close()
    return {r["date"]: r["pct"] for r in rows}


def _report(label: str, rows: list[dict], crit: dict) -> dict:
    res = _score_criteria(rows, crit)
    n_prime = sum(1 for f in rows if f["is_prime"] == 1)
    print(
        f"  {label:<34}  P={res['precision']*100:5.1f}%  R={res['recall']*100:5.1f}%  "
        f"TP={res['true_positives']:4d}  FP={res['false_positives']:4d}  "
        f"(prime={n_prime})"
    )
    return res


def main() -> None:
    all_features = get_all_features()
    breadth = _compute_breadth()

    # ── Universe setup ────────────────────────────────────────────────────────
    # Narrow = all tickers ever marked prime (Sep-Dec 2025 + 2026 combined)
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)

    # Split
    sep_dec = [f for f in narrow if f["date"] <= "2025-12-31"]
    train   = [f for f in sep_dec if f["date"] <= TRAIN_CUTOFF]
    test    = [f for f in sep_dec if f["date"] >= TEST_START]
    oos2026 = [f for f in narrow if f["date"] >= OOS_START]

    n_oos_dates  = len(sorted(set(f["date"] for f in oos2026 if f["is_prime"] == 1)))
    n_oos_prime  = sum(1 for f in oos2026 if f["is_prime"] == 1)
    n_oos_tickers = len({f["ticker"] for f in oos2026 if f["is_prime"] == 1})

    print(f"Full narrow universe : {len(narrow)} rows, {len(prime_tickers)} tickers")
    print(f"Sep-Dec 2025         : {len(sep_dec)} rows  (train={len(train)}, test={len(test)})")
    print(f"2026 OOS             : {len(oos2026)} rows, {n_oos_prime} prime, "
          f"{n_oos_dates} dates, {n_oos_tickers} unique prime tickers")

    # ── SECTION 1: 2026 OOS — base criteria (no IV gates) ────────────────────
    print("\n" + "=" * 72)
    print("SECTION 1: 2026 OOS — V31A_BASE and V34_BASE (IV gates removed)")
    print("=" * 72)
    print("  (best_iv=NULL for all 2026 rows — IV gates would zero-out recall)")
    print(f"\n  {'Criteria':<34}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'Prime':>7}")
    print(f"  {'-'*34}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")
    r1_v31a_base = _report("v31a_base (2026, no IV)", oos2026, V31A_BASE)
    r1_v34_base  = _report("v34_base  (2026, no IV)", oos2026, V34_BASE)

    # Compare base criteria on Sep-Dec 2025 (sanity check)
    print("\n  Reference (Sep-Dec 2025, same base criteria):")
    _report("v31a_base (sep-dec, no IV)", sep_dec, V31A_BASE)
    _report("v34_base  (sep-dec, no IV)", sep_dec, V34_BASE)
    _report("v31a      (sep-dec, full)", sep_dec, V31A)
    _report("v34       (sep-dec, full)", sep_dec, V34)

    # ── SECTION 2: 2026 recall by month ──────────────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 2: 2026 OOS recall by month (V31A_BASE)")
    print("=" * 72)
    months = {}
    for f in oos2026:
        m = f["date"][:7]
        months.setdefault(m, []).append(f)

    print(f"\n  {'Month':<8}  {'breadth':>8}  {'P':>7}  {'R':>7}  {'TP':>4}  {'FP':>4}  "
          f"{'prime':>5}  {'ctrl':>5}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*4}  {'-'*4}  {'-'*5}  {'-'*5}")
    for m in sorted(months):
        mrows = months[m]
        # avg breadth for this month
        mdates = {f["date"] for f in mrows}
        avg_b = sum(breadth.get(d, 0) for d in mdates) / len(mdates) if mdates else 0
        res = _score_criteria(mrows, V31A_BASE)
        mp = sum(1 for f in mrows if f["is_prime"] == 1)
        mc = sum(1 for f in mrows if f["is_prime"] == 0)
        print(
            f"  {m:<8}  {avg_b:7.1f}%  {res['precision']*100:6.1f}%  "
            f"{res['recall']*100:6.1f}%  {res['true_positives']:4d}  "
            f"{res['false_positives']:4d}  {mp:5d}  {mc:5d}"
        )

    # ── SECTION 3: Breadth distribution ──────────────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 3: Breadth per date — all dates")
    print("=" * 72)
    print(f"\n  {'Date':<12}  {'breadth':>8}  {'prime':>6}  {'era'}")
    print(f"  {'-'*12}  {'-'*8}  {'-'*6}  {'-'*15}")
    prime_map = {}
    for f in all_features:
        if f["is_prime"] == 1:
            prime_map[f["date"]] = prime_map.get(f["date"], 0) + 1
    for d in sorted(breadth):
        era = ("TRAIN" if d <= TRAIN_CUTOFF
               else "TEST " if d <= "2025-12-31"
               else "2026 ")
        print(f"  {d:<12}  {breadth[d]:7.1f}%  {prime_map.get(d,0):6d}  {era}")

    # ── SECTION 4: Regime filter on Sep-Dec 2025 temporal holdout ────────────
    print("\n" + "=" * 72)
    print("SECTION 4: Regime filter on Sep-Dec 2025 holdout")
    print("  Base: v34 (full criteria).  Filter: drop dates with breadth < threshold.")
    print("=" * 72)
    print(f"\n  {'breadth_min':>11}  {'train_P':>8}  {'train_R':>8}  {'test_P':>8}  {'test_R':>8}  "
          f"{'ΔP':>6}  {'train_d':>8}  {'test_d':>8}")
    print(f"  {'-'*11}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*6}  {'-'*8}  {'-'*8}")

    for threshold in [0.0, 65.0, 68.0, 69.0, 70.0, 71.0, 72.0]:
        tr = [f for f in train if breadth.get(f["date"], 0) >= threshold]
        te = [f for f in test  if breadth.get(f["date"], 0) >= threshold]
        tr_dates = len(set(f["date"] for f in tr if f["is_prime"] == 1))
        te_dates = len(set(f["date"] for f in te if f["is_prime"] == 1))
        if not tr or not te:
            print(f"  {threshold:>10.1f}%  (no data at this threshold)")
            continue
        r_tr = _score_criteria(tr, V34)
        r_te = _score_criteria(te, V34)
        dp = (r_te["precision"] - r_tr["precision"]) * 100
        print(
            f"  {threshold:>10.1f}%  {r_tr['precision']*100:7.1f}%  {r_tr['recall']*100:7.1f}%  "
            f"{r_te['precision']*100:7.1f}%  {r_te['recall']*100:7.1f}%  "
            f"{dp:+5.1f}pp  {tr_dates:8d}  {te_dates:8d}"
        )

    # ── SECTION 5: Regime filter on 2026 OOS (base criteria) ─────────────────
    print("\n" + "=" * 72)
    print("SECTION 5: Regime filter on 2026 OOS (V31A_BASE)")
    print("=" * 72)
    print(f"\n  {'breadth_min':>11}  {'P':>8}  {'R':>8}  {'TP':>5}  {'FP':>5}  "
          f"{'dates_kept':>10}")
    print(f"  {'-'*11}  {'-'*8}  {'-'*8}  {'-'*5}  {'-'*5}  {'-'*10}")
    for threshold in [0.0, 60.0, 62.0, 65.0, 67.0, 68.0, 70.0]:
        subset = [f for f in oos2026 if breadth.get(f["date"], 0) >= threshold]
        dates_kept = len(set(f["date"] for f in subset if f["is_prime"] == 1))
        if not subset:
            print(f"  {threshold:>10.1f}%  (no data)")
            continue
        res = _score_criteria(subset, V31A_BASE)
        print(
            f"  {threshold:>10.1f}%  {res['precision']*100:7.1f}%  "
            f"{res['recall']*100:7.1f}%  {res['true_positives']:5d}  "
            f"{res['false_positives']:5d}  {dates_kept:10d}"
        )

    print("\n--- Session 18 complete ---")


if __name__ == "__main__":
    main()
