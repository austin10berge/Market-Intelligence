"""Session 05 — Sector-specific thresholds, FCF gate, and NEE recovery.

Experiments:
  1. v18 + financials_market_cap_b_min=100  (cut FP financials)
  2. v18 + dividend_yield_max=3.0           (recover NEE x5)
  3. v18 + technology_fcf_min=0.01          (cut speculative tech FPs)
  4. v18 + all three combined               (best-of-all candidate)
  5. v18b + financials_market_cap_b_min=100 (high-recall variant with fin gate)
  6. Diagnose remaining prime misses at v18
  7. FCF null-rate check in prime vs control

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session05
"""

from __future__ import annotations

import logging

from .analyze import _apply_criteria
from .store import _get_connection, get_all_features, get_options_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

V18 = {
    "sma50_above_sma200": 1,
    "market_cap_b_min": 25,
    "price_vs_ema200_pct_min": 2,
    "price_vs_ema200_pct_max": 42,
    "pct_from_52wk_high_max": 18,
    "rv20_max": 0.45,
    "dividend_yield_max": 2.5,
    "options_iv_min": 0.20,
}

V18B = {
    "sma50_above_sma200": 1,
    "market_cap_b_min": 25,
    "price_vs_ema200_pct_min": 2,
    "price_vs_ema200_pct_max": 42,
    "pct_from_52wk_high_max": 25,
    "rv20_max": 0.55,
    "dividend_yield_max": 2.5,
    "options_iv_min": 0.20,
}


def _load_sp500_features_with_options():
    features = get_all_features()
    conn = _get_connection()
    try:
        sp500 = {r["symbol"] for r in conn.execute(
            "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%sp500%'"
        ).fetchall()}
    finally:
        conn.close()
    filtered = [f for f in features if f["is_prime"] == 1 or f["ticker"] in sp500]

    options_idx = get_options_index()
    return [
        {**f, "best_iv": options_idx.get((f["date"], f["ticker"]), {}).get("best_iv")}
        for f in filtered
    ]


def score(features, criteria):
    prime = [f for f in features if f["is_prime"] == 1]
    control = [f for f in features if f["is_prime"] == 0]
    tp = sum(1 for f in prime if _apply_criteria(f, criteria))
    fp = sum(1 for f in control if _apply_criteria(f, criteria))
    fn = len(prime) - tp
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / len(prime) if prime else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "tp": tp, "fp": fp, "fn": fn}


def print_result(label, r):
    print(f"  {label:<50}  P={r['precision']:.1%}  R={r['recall']:.1%}  TP={r['tp']}  FP={r['fp']}")


def main():
    print("\n=== Session 05 — Sector-specific thresholds & FCF gate ===\n")

    features = _load_sp500_features_with_options()
    prime = [f for f in features if f["is_prime"] == 1]
    control = [f for f in features if f["is_prime"] == 0]
    print(f"Universe: {len(prime)} prime rows, {len(control)} control rows\n")

    # ── Exp 0: FCF null-rate in prime vs control ──────────────────────────────
    print("--- Exp 0: FCF null-rate ---")
    prime_fcf_null = sum(1 for f in prime if f.get("fcf") is None)
    ctrl_fcf_null = sum(1 for f in control if f.get("fcf") is None)
    prime_fcf_neg = sum(1 for f in prime if f.get("fcf") is not None and f["fcf"] < 0)
    ctrl_fcf_neg = sum(1 for f in control if f.get("fcf") is not None and f["fcf"] < 0)
    prime_fcf_zero_or_pos = sum(1 for f in prime if f.get("fcf") is not None and f["fcf"] >= 0.01)
    ctrl_fcf_zero_or_pos = sum(1 for f in control if f.get("fcf") is not None and f["fcf"] >= 0.01)
    print(f"  Prime  : {prime_fcf_null}/{len(prime)} null ({prime_fcf_null/len(prime):.1%})  "
          f"{prime_fcf_neg} negative  {prime_fcf_zero_or_pos} >= 0.01B")
    print(f"  Control: {ctrl_fcf_null}/{len(control)} null ({ctrl_fcf_null/len(control):.1%})  "
          f"{ctrl_fcf_neg} negative  {ctrl_fcf_zero_or_pos} >= 0.01B\n")

    # Tech-only FCF breakdown
    prime_tech = [f for f in prime if f.get("sector") == "Technology"]
    ctrl_tech = [f for f in control if f.get("sector") == "Technology"]
    ptf_null = sum(1 for f in prime_tech if f.get("fcf") is None)
    ctf_null = sum(1 for f in ctrl_tech if f.get("fcf") is None)
    ptf_pos = sum(1 for f in prime_tech if f.get("fcf") is not None and f["fcf"] >= 0.01)
    ctf_pos = sum(1 for f in ctrl_tech if f.get("fcf") is not None and f["fcf"] >= 0.01)
    print(f"  Tech prime  : {ptf_null}/{len(prime_tech)} null  {ptf_pos} >= 0.01B")
    print(f"  Tech control: {ctf_null}/{len(ctrl_tech)} null  {ctf_pos} >= 0.01B\n")

    # ── Exp 1: Baseline v18 ───────────────────────────────────────────────────
    print("--- Exp 1: Criteria grid ---")
    baseline = score(features, V18)
    print_result("v18 (baseline)", baseline)

    # ── Exp 2: v18 + financials mcap >= 100B ─────────────────────────────────
    v18_fin = {**V18, "financials_market_cap_b_min": 100}
    r_fin = score(features, v18_fin)
    print_result("v18 + financials_mcap>=100B", r_fin)

    # Test sensitivity: 50B, 75B, 150B, 200B
    for threshold in [50, 75, 150, 200]:
        c = {**V18, "financials_market_cap_b_min": threshold}
        r = score(features, c)
        print_result(f"  v18 + financials_mcap>={threshold}B", r)

    print()

    # ── Exp 3: v18 + dividend_yield_max=3.0 (NEE recovery) ───────────────────
    v18_nee = {**V18, "dividend_yield_max": 3.0}
    r_nee = score(features, v18_nee)
    print_result("v18 + div_yield_max=3.0 (NEE)", r_nee)

    # ── Exp 4: v18 + technology_fcf_min=0.01 ─────────────────────────────────
    v18_fcf = {**V18, "technology_fcf_min": 0.01}
    r_fcf = score(features, v18_fcf)
    print_result("v18 + tech_fcf_min=0.01B", r_fcf)

    # ── Exp 5: v18 + all three combined ──────────────────────────────────────
    v19 = {**V18, "financials_market_cap_b_min": 100, "technology_fcf_min": 0.01}
    r_v19 = score(features, v19)
    print_result("v19: v18 + fin_mcap>=100 + tech_fcf>=0.01", r_v19)

    v19b = {**V18, "financials_market_cap_b_min": 100, "technology_fcf_min": 0.01, "dividend_yield_max": 3.0}
    r_v19b = score(features, v19b)
    print_result("v19b: v19 + div_yield=3.0", r_v19b)

    print()

    # ── Exp 6: v18b variants (high-recall) ───────────────────────────────────
    print("--- Exp 6: v18b recall-focus variants ---")
    r_v18b = score(features, V18B)
    print_result("v18b (baseline)", r_v18b)

    v18b_fin = {**V18B, "financials_market_cap_b_min": 100}
    r_v18b_fin = score(features, v18b_fin)
    print_result("v18b + financials_mcap>=100B", r_v18b_fin)

    v18b_all = {**V18B, "financials_market_cap_b_min": 100, "technology_fcf_min": 0.01}
    r_v18b_all = score(features, v18b_all)
    print_result("v18b + fin_mcap>=100 + tech_fcf>=0.01", r_v18b_all)

    print()

    # ── Exp 7: Remaining missed primes at v19 ────────────────────────────────
    print("--- Exp 7: Missed primes at v19 ---")
    v19_misses = [f for f in prime if not _apply_criteria(f, v19)]
    from collections import Counter
    by_ticker = Counter(f["ticker"] for f in v19_misses)
    print(f"  Total missed: {len(v19_misses)} ({len(v19_misses)/len(prime):.1%})")
    print("  Top missed tickers:")
    for ticker, count in by_ticker.most_common(15):
        sample = next(f for f in v19_misses if f["ticker"] == ticker)
        reason_parts = []
        if sample.get("sma50_above_sma200") != 1:
            reason_parts.append("sma50<sma200")
        if sample.get("market_cap_b") and sample["market_cap_b"] < 25:
            reason_parts.append(f"mcap={sample['market_cap_b']:.0f}B")
        pve = sample.get("price_vs_ema200_pct")
        if pve is not None and (pve < 2 or pve > 42):
            reason_parts.append(f"ema200%={pve:.1f}")
        pfh = sample.get("pct_from_52wk_high")
        if pfh is not None and pfh > 18:
            reason_parts.append(f"52wkHigh%={pfh:.1f}")
        rv = sample.get("rv20")
        if rv is not None and rv > 0.45:
            reason_parts.append(f"rv20={rv:.3f}")
        dy = sample.get("dividend_yield")
        if dy is not None and dy > 2.5:
            reason_parts.append(f"div={dy:.2f}%")
        iv = sample.get("best_iv")
        if iv is None:
            reason_parts.append("IV=null")
        elif iv < 0.20:
            reason_parts.append(f"IV={iv:.2f}")
        print(f"    {ticker:<6} x{count}  {' | '.join(reason_parts)}")

    print()

    # ── Exp 8: FP breakdown at v19 by sector ─────────────────────────────────
    print("--- Exp 8: FP by sector at v19 ---")
    v19_fps = [f for f in control if _apply_criteria(f, v19)]
    fp_by_sector = Counter(f.get("sector") or "Unknown" for f in v19_fps)
    print(f"  Total FPs at v19: {len(v19_fps)}")
    for sector, count in fp_by_sector.most_common():
        print(f"    {sector:<35} {count}")

    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=== Summary table ===")
    rows = [
        ("v18 (options gate baseline)", baseline),
        ("v18 + fin_mcap>=100B", r_fin),
        ("v18 + div_yield=3.0", r_nee),
        ("v18 + tech_fcf>=0.01B", r_fcf),
        ("v19: v18+fin+tech_fcf", r_v19),
        ("v19b: v19+div_yield=3.0", r_v19b),
        ("v18b (recall focus)", r_v18b),
        ("v18b+fin_mcap>=100B", r_v18b_fin),
        ("v18b+fin+tech_fcf", r_v18b_all),
    ]
    print(f"  {'Label':<45}  {'Precision':>9}  {'Recall':>7}  {'TP':>4}  {'FP':>5}")
    for label, r in rows:
        print(f"  {label:<45}  {r['precision']:.1%}  {r['recall']:.1%}  {r['tp']:>4}  {r['fp']:>5}")


if __name__ == "__main__":
    main()
