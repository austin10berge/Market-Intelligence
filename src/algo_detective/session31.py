"""Session 31 — OOS miss analysis: why do we capture only 12/46 OOS primes?

Context:
  V40 = V39 + industrials_rsi_max=70 + financials_rsi_max=70 + healthcare_rsi_max=60
  V39 = V38 + adr20_pct_max=4.0
  OOS window: 2026-01-01 to 2026-06-30, 46 prime (date, ticker) pairs, 12 captured.

The 34 missed OOS TPs are the primary signal ceiling. This session maps every gate
that blocks each missed prime, ranks gates by OOS-TP cost, and assesses relaxation
potential (how many FPs would relaxing each gate add vs how many OOS TPs recovered).

Sections:
  1. OOS prime inventory: all 46 pairs, pass/fail on V40_BASE
  2. Gate breakdown: for each missed prime, which gates block it (may be multiple)
  3. Gate ranking: sorted by number of OOS TPs blocked
  4. Relaxation analysis: for top blocking gates, sweep toward recovery
  5. Period analysis: are misses concentrated in tariff selloff (Apr-May) vs other months?
  6. Ticker analysis: are misses concentrated in specific tickers?

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session31
"""

from __future__ import annotations

from collections import defaultdict

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

V40_BASE = {k: v for k, v in V40.items() if k not in _IV_KEYS}


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


def _which_gates_block(row: dict, crit: dict) -> list[str]:
    """Return list of gate keys that individually block this row."""
    blocking = []
    for key, val in crit.items():
        single = {key: val}
        if not _apply_criteria(row, single):
            blocking.append(key)
    return blocking


def _gate_val(row: dict, gate_key: str) -> str:
    """Human-readable actual value for the feature the gate tests."""
    # Map gate keys to feature names
    mapping = {
        "sma50_above_sma200": "sma50_above_sma200",
        "sma50_above_sma150": "sma50_above_sma150",
        "market_cap_b_min": "market_cap_b",
        "price_vs_ema200_pct_min": "price_vs_ema200_pct",
        "price_vs_ema200_pct_max": "price_vs_ema200_pct",
        "pct_from_52wk_high_max": "pct_from_52wk_high",
        "rv20_max": "rv20",
        "dividend_yield_max": "dividend_yield",
        "adx_min": "adx",
        "bb_width_pct_min": "bb_width_pct",
        "bb_width_pct_max": "bb_width_pct",
        "volume_ratio_max": "volume_ratio",
        "forward_pe_max": "forward_pe",
        "iv_rv_min": None,  # composite
        "pcr_vol_max": "pcr_vol",
        "adr20_pct_max": "adr20_pct",
        "financials_adx_min": "adx",
        "financials_volume_ratio_max": "volume_ratio",
        "financials_market_cap_b_min": "market_cap_b",
        "technology_fcf_min": "fcf",
        "consumer_cyclical_rsi_max": "rsi",
        "technology_rsi_max": "rsi",
        "industrials_rsi_max": "rsi",
        "financials_rsi_max": "rsi",
        "healthcare_rsi_max": "rsi",
        "consumer_cyclical_price_vs_ema200_pct_min": "price_vs_ema200_pct",
        "communication_services_market_cap_b_min": "market_cap_b",
        "price_vs_sma150_pct_min": "price_vs_sma150_pct",
    }
    feat = mapping.get(gate_key)
    if feat is None:
        if gate_key == "iv_rv_min":
            iv = row.get("best_iv")
            rv = row.get("rv20")
            if iv is not None and rv is not None and rv > 0:
                return f"iv/rv={iv/rv:.2f}"
            return f"iv={iv},rv={rv}"
        return "?"
    v = row.get(feat)
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


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

    oos_primes = [f for f in oos if f["is_prime"] == 1]
    oos_ctrl   = [f for f in oos if f["is_prime"] == 0]

    print(f"Sep-Dec 2025 : {len(sep_dec)} rows, "
          f"{sum(1 for f in sep_dec if f['is_prime']==1)} prime")
    print(f"2026 OOS     : {len(oos)} rows, "
          f"{len(oos_primes)} prime, {len(oos_ctrl)} ctrl")
    print("\n  V40 baseline:")
    _row4("V40", sep_dec, train, test, oos, V40, V40_BASE)

    # ── SECTION 1: OOS prime inventory ───────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: All 46 OOS prime rows — V40_BASE pass/fail")
    print("=" * 80)

    # Sort by date, then ticker
    oos_primes_sorted = sorted(oos_primes, key=lambda f: (f["date"], f["ticker"]))
    hits   = [f for f in oos_primes_sorted if _apply_criteria(f, V40_BASE)]
    misses = [f for f in oos_primes_sorted if not _apply_criteria(f, V40_BASE)]

    print(f"\n  {len(hits)} pass V40_BASE (captured), {len(misses)} fail (missed)")
    print(f"\n  {'Date':<12}  {'Ticker':>6}  {'Sector':<22}  {'Pass?':>5}  Gates blocking")
    for f in oos_primes_sorted:
        passes = _apply_criteria(f, V40_BASE)
        blocking = [] if passes else _which_gates_block(f, V40_BASE)
        sector = (f.get("sector") or "")[:22]
        print(
            f"  {f['date']:<12}  {f['ticker']:>6}  {sector:<22}  "
            f"{'YES' if passes else 'NO':>5}  {', '.join(blocking) if blocking else ''}"
        )

    # ── SECTION 2: Gate breakdown for each missed prime ───────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: Missed OOS primes — gate-by-gate detail")
    print("  Showing actual feature values vs gate threshold.")
    print("=" * 80)

    gate_to_blocked: dict[str, list[dict]] = defaultdict(list)

    print(f"\n  {'Date':<12}  {'Ticker':>6}  {'Month':<6}  Blocking gates (key=threshold  actual=value)")  # noqa: E501
    for f in misses:
        blocking = _which_gates_block(f, V40_BASE)
        for g in blocking:
            gate_to_blocked[g].append(f)
        thresh_strs = []
        for g in blocking:
            thresh = V40_BASE[g]
            actual = _gate_val(f, g)
            thresh_strs.append(f"{g}={thresh}  actual={actual}")
        month = f["date"][:7]
        print(f"  {f['date']:<12}  {f['ticker']:>6}  {month:<6}  {' | '.join(thresh_strs)}")

    # ── SECTION 3: Gate ranking ───────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: Gate ranking — how many OOS TPs does each gate block?")
    print("  (A gate may block multiple TPs; counts are not mutually exclusive)")
    print("=" * 80)

    ranked = sorted(gate_to_blocked.items(), key=lambda x: -len(x[1]))
    print(f"\n  {'Gate':<38}  {'OOS TPs blocked':>15}  Threshold  Tickers")
    for gate, blocked_rows in ranked:
        tickers = sorted({r["ticker"] for r in blocked_rows})
        thresh = V40_BASE.get(gate, "?")
        print(
            f"  {gate:<38}  {len(blocked_rows):>15}  {str(thresh):<10}  "
            f"{', '.join(tickers)}"
        )

    # ── SECTION 4: Relaxation analysis ───────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: Relaxation analysis — top blocking gates")
    print("  For each top gate, sweep threshold toward recovery.")
    print("  Show: OOS TPs recovered, Sep-Dec TPs lost, Sep-Dec FPs added.")
    print("=" * 80)

    # Focus on the top gates by OOS TP cost
    top_gates = [g for g, _ in ranked[:8]]

    for gate in top_gates:
        blocked_count = len(gate_to_blocked[gate])
        if blocked_count == 0:
            continue

        print(f"\n  Gate: {gate} = {V40_BASE[gate]}  (blocks {blocked_count} OOS TPs)")

        # Determine the feature and direction
        feat_map = {
            "rv20_max": ("rv20", "max"),
            "bb_width_pct_max": ("bb_width_pct", "max"),
            "volume_ratio_max": ("volume_ratio", "max"),
            "pct_from_52wk_high_max": ("pct_from_52wk_high", "max"),
            "price_vs_ema200_pct_min": ("price_vs_ema200_pct", "min"),
            "price_vs_sma150_pct_min": ("price_vs_sma150_pct", "min"),
            "adr20_pct_max": ("adr20_pct", "max"),
            "market_cap_b_min": ("market_cap_b", "min"),
            "sma50_above_sma150": (None, "bool"),
            "sma50_above_sma200": (None, "bool"),
            "iv_rv_min": (None, "composite"),
            "adx_min": ("adx", "min"),
            "dividend_yield_max": ("dividend_yield", "max"),
            "forward_pe_max": ("forward_pe", "max"),
        }

        if gate not in feat_map:
            # Sector-specific gate — show actual values for blocked TPs
            print("  Sector-specific gate. Blocked OOS TPs and their values:")
            for f in gate_to_blocked[gate]:
                actual = _gate_val(f, gate)
                print(f"    {f['date']} {f['ticker']:6s} actual={actual}")
            continue

        feat_name, direction = feat_map[gate]
        if direction == "bool" or direction == "composite":
            print("  Boolean/composite gate — showing blocked TPs:")
            for f in gate_to_blocked[gate]:
                actual = _gate_val(f, gate)
                print(f"    {f['date']} {f['ticker']:6s} actual={actual}")
            continue

        # Get actual feature values for all blocked TPs
        blocked_vals = [
            (f["date"], f["ticker"], f.get(feat_name))
            for f in gate_to_blocked[gate]
            if f.get(feat_name) is not None
        ]
        if not blocked_vals:
            print(f"  No data for feature {feat_name}")
            continue

        vals_only = [v for _, _, v in blocked_vals]
        print(f"  Blocked OOS TP values for {feat_name}:")
        for date, ticker, v in sorted(blocked_vals, key=lambda x: x[2]):
            print(f"    {date} {ticker:6s}  {feat_name}={v:.3f}")

        if direction == "max":
            current_thresh = V40_BASE[gate]
            sorted_vals = sorted(vals_only)
            # Sweep: at each percentile of blocked values, how many recover?
            print(f"\n  Sweep {gate} upward (current={current_thresh}):")
            print(f"  {'Threshold':>12}  {'OOS recovered':>14}  {'OOS total':>10}  "
                  f"{'sd TPs lost':>12}  {'sd FPs added':>13}")  # noqa: E501
            for new_thresh in sorted(set([round(v * 1.05, 2) for v in sorted_vals] +
                                         [round(v * 1.10, 2) for v in sorted_vals] +
                                         [round(v * 1.20, 2) for v in sorted_vals] +
                                         [current_thresh])):
                if new_thresh < current_thresh:
                    continue
                new_crit = {**V40_BASE, gate: new_thresh}
                new_oos = _score_criteria(oos, new_crit)
                new_sd  = _score_criteria(sep_dec, new_crit)
                base_oos = _score_criteria(oos, V40_BASE)
                base_sd  = _score_criteria(sep_dec, V40_BASE)
                delta_tp_oos = new_oos["true_positives"] - base_oos["true_positives"]
                delta_tp_sd  = new_sd["true_positives"] - base_sd["true_positives"]
                delta_fp_sd  = new_sd["false_positives"] - base_sd["false_positives"]
                label = "(baseline)" if new_thresh == current_thresh else ""
                print(
                    f"  {new_thresh:>12.3f}  {delta_tp_oos:>+14d}  "
                    f"{new_oos['true_positives']:>10d}  "
                    f"{delta_tp_sd:>+12d}  {delta_fp_sd:>+13d}  {label}"
                )
        else:  # min gate
            current_thresh = V40_BASE[gate]
            sorted_vals = sorted(vals_only)
            print(f"\n  Sweep {gate} downward (current={current_thresh}):")
            print(f"  {'Threshold':>12}  {'OOS recovered':>14}  {'OOS total':>10}  "
                  f"{'sd TPs lost':>12}  {'sd FPs added':>13}")  # noqa: E501
            for new_thresh in sorted(set([round(v * 0.95, 2) for v in sorted_vals] +
                                         [round(v * 0.90, 2) for v in sorted_vals] +
                                         [round(v * 0.80, 2) for v in sorted_vals] +
                                         [current_thresh]), reverse=True):
                if new_thresh > current_thresh:
                    continue
                new_crit = {**V40_BASE, gate: new_thresh}
                new_oos = _score_criteria(oos, new_crit)
                new_sd  = _score_criteria(sep_dec, new_crit)
                base_oos = _score_criteria(oos, V40_BASE)
                base_sd  = _score_criteria(sep_dec, V40_BASE)
                delta_tp_oos = new_oos["true_positives"] - base_oos["true_positives"]
                delta_tp_sd  = new_sd["true_positives"] - base_sd["true_positives"]
                delta_fp_sd  = new_sd["false_positives"] - base_sd["false_positives"]
                label = "(baseline)" if new_thresh == current_thresh else ""
                print(
                    f"  {new_thresh:>12.3f}  {delta_tp_oos:>+14d}  "
                    f"{new_oos['true_positives']:>10d}  "
                    f"{delta_tp_sd:>+12d}  {delta_fp_sd:>+13d}  {label}"
                )

    # ── SECTION 5: Period analysis ────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 5: Period analysis — are misses concentrated in tariff selloff?")
    print("  Breadth: Jan-Mar 2026 ~66-72% (mixed), Apr-May 59-63% (selloff), Jun 61-63%")
    print("=" * 80)

    periods = {
        "Jan 2026":  ("2026-01-01", "2026-01-31"),
        "Feb 2026":  ("2026-02-01", "2026-02-28"),
        "Mar 2026":  ("2026-03-01", "2026-03-31"),
        "Apr 2026":  ("2026-04-01", "2026-04-30"),
        "May 2026":  ("2026-05-01", "2026-05-31"),
        "Jun 2026":  ("2026-06-01", "2026-06-30"),
    }

    print(f"\n  {'Period':<12}  {'Total primes':>12}  {'Captured':>9}  {'Missed':>7}  "
          f"{'Recall':>7}  Top blocking gates")
    for period_label, (start, end) in periods.items():
        period_primes = [
            f for f in oos_primes if start <= f["date"] <= end
        ]
        period_hits   = [f for f in period_primes if _apply_criteria(f, V40_BASE)]
        period_misses = [f for f in period_primes if not _apply_criteria(f, V40_BASE)]

        gate_counts: dict[str, int] = defaultdict(int)
        for f in period_misses:
            for g in _which_gates_block(f, V40_BASE):
                gate_counts[g] += 1
        top = sorted(gate_counts.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{g}({n})" for g, n in top)

        recall = len(period_hits) / len(period_primes) if period_primes else 0
        print(
            f"  {period_label:<12}  {len(period_primes):>12}  {len(period_hits):>9}  "
            f"{len(period_misses):>7}  {recall*100:>6.1f}%  {top_str}"
        )

    # ── SECTION 6: Ticker analysis ────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 6: Ticker analysis — are misses concentrated in specific tickers?")
    print("=" * 80)

    ticker_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "hit": 0, "miss": 0, "gates": defaultdict(int)})  # noqa: E501
    for f in oos_primes:
        t = f["ticker"]
        ticker_stats[t]["total"] += 1
        if _apply_criteria(f, V40_BASE):
            ticker_stats[t]["hit"] += 1
        else:
            ticker_stats[t]["miss"] += 1
            for g in _which_gates_block(f, V40_BASE):
                ticker_stats[t]["gates"][g] += 1

    print(f"\n  {'Ticker':<8}  {'Total':>6}  {'Hit':>5}  {'Miss':>6}  {'Recall':>7}  Top blocking gates")  # noqa: E501
    for ticker, stats in sorted(ticker_stats.items(), key=lambda x: -x[1]["total"]):
        top = sorted(stats["gates"].items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{g}({n})" for g, n in top)
        recall = stats["hit"] / stats["total"] if stats["total"] else 0
        print(
            f"  {ticker:<8}  {stats['total']:>6}  {stats['hit']:>5}  {stats['miss']:>6}  "
            f"{recall*100:>6.1f}%  {top_str}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
