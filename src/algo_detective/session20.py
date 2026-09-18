"""Session 20 — Regime-robust criteria: trend-strength floor + relaxed VCP ceilings.

Session 19 showed that the top discriminating features shifted dramatically in 2026:
- Long-term trend position (price_vs_sma150_pct, sma50_above_sma150, price_vs_ema200_pct)
  rose to rank #1-4 (were #25-36 in Sep-Oct 2025)
- VCP features (rv20, bb_width_pct, volume_ratio) dropped — everything has elevated
  vol/volume in a selloff, so the VCP pattern is no longer selective

Strategy:
  1. Add a trend-strength floor: raise price_vs_ema200_pct_min (currently 0%) or add
     price_vs_sma150_pct_min. Primes are ~18% above EMA200 in 2026 (vs 10% control).
  2. Relax VCP ceilings modestly (bb_width_pct_max 14→18, volume_ratio_max 1.10→1.20)
     to recover selloff-regime trades without flooding FPs.
  3. Evaluate on THREE datasets side-by-side:
       a. Sep-Oct 2025 train (in-sample)
       b. Nov-Dec 2025 test (temporal holdout)
       c. Jan-Jun 2026 OOS (true OOS, base criteria — no IV gates)
  4. Find the combination that best balances all three.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session20
"""

from __future__ import annotations

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

V34 = {**V31A, "iv_rv_min": 1.0, "pcr_vol_max": 2.0}
V31A_BASE = {k: v for k, v in V31A.items() if k not in _IV_KEYS}
V34_BASE  = {k: v for k, v in V34.items()  if k not in _IV_KEYS}


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


def _row(label: str, train_res: dict, test_res: dict, oos_res: dict) -> None:
    """Print one summary row across all three splits."""
    print(
        f"  {label:<46}  "
        f"{train_res['precision']*100:5.1f}%/{train_res['recall']*100:4.1f}%  "
        f"{test_res['precision']*100:5.1f}%/{test_res['recall']*100:4.1f}%  "
        f"{oos_res['precision']*100:5.1f}%/{oos_res['recall']*100:4.1f}%"
    )


def main() -> None:
    all_features = get_all_features()
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)

    train  = [f for f in narrow if f["date"] <= TRAIN_CUTOFF]
    test   = [f for f in narrow if f["date"] >= TEST_START and f["date"] <= "2025-12-31"]
    oos    = [f for f in narrow if f["date"] >= OOS_START]

    print(f"Train  (Sep-Oct 2025): {len(train)} rows, "
          f"{sum(1 for f in train if f['is_prime']==1)} prime")
    print(f"Test   (Nov-Dec 2025): {len(test)} rows, "
          f"{sum(1 for f in test  if f['is_prime']==1)} prime")
    print(f"OOS    (Jan-Jun 2026): {len(oos)} rows, "
          f"{sum(1 for f in oos   if f['is_prime']==1)} prime")

    hdr = f"  {'Criteria':<46}  {'train P/R':>10}  {'test P/R':>10}  {'oos P/R':>10}"
    sep = f"  {'-'*46}  {'-'*10}  {'-'*10}  {'-'*10}"

    # ── SECTION 1: Baselines ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: Baselines")
    print("=" * 80)
    print("  Note: OOS uses base criteria (no IV keys); train/test use full criteria")
    print(f"\n{hdr}\n{sep}")
    _row("v31a (full / full / base)",
         _score_criteria(train, V31A),
         _score_criteria(test,  V31A),
         _score_criteria(oos,   V31A_BASE))
    _row("v34  (full / full / base)",
         _score_criteria(train, V34),
         _score_criteria(test,  V34),
         _score_criteria(oos,   V34_BASE))

    # ── SECTION 2: Trend-strength floor sweep ─────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 2: Trend-strength floor — raise price_vs_ema200_pct_min")
    print("  (Currently 0%; 2026 prime mean=18.7%, ctrl=10.0% → room to raise)")
    print(f"\n{hdr}\n{sep}")
    for ema200_floor in [0, 5, 8, 10, 12, 15]:
        crit_full = {**V31A_BASE, "price_vs_ema200_pct_min": ema200_floor}
        crit_iv   = {**V31A,     "price_vs_ema200_pct_min": ema200_floor}
        _row(f"v31a_base + ema200_floor={ema200_floor}",
             _score_criteria(train, crit_iv),
             _score_criteria(test,  crit_iv),
             _score_criteria(oos,   crit_full))

    # ── SECTION 3: SMA150 floor sweep (new gate, top KS feature in 2026) ─────
    print("\n" + "=" * 80)
    print("SECTION 3: New gate — price_vs_sma150_pct_min")
    print("  (#1 KS feature in 2026: prime mean=17.0%, ctrl=7.5%)")
    print(f"\n{hdr}\n{sep}")
    for sma150_floor in [0, 5, 8, 10, 12, 15]:
        crit_full = {**V31A_BASE, "price_vs_sma150_pct_min": sma150_floor}
        crit_iv   = {**V31A,     "price_vs_sma150_pct_min": sma150_floor}
        _row(f"v31a_base + sma150_floor={sma150_floor}",
             _score_criteria(train, crit_iv),
             _score_criteria(test,  crit_iv),
             _score_criteria(oos,   crit_full))

    # ── SECTION 4: VCP relaxation with trend floor ────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: VCP relaxation (bb_width + volume_ratio) WITH trend floor")
    print("  Evaluating combos that improve OOS recall without collapsing train precision")
    print(f"\n{hdr}\n{sep}")
    # best trend floor from sections 2+3
    for ema200_f in [0, 5, 10]:
        for sma150_f in [0, 5, 10]:
            for bb_max in [14.0, 18.0]:
                for vr_max in [1.10, 1.20]:
                    base = {
                        **V31A_BASE,
                        "price_vs_ema200_pct_min": ema200_f,
                        "price_vs_sma150_pct_min": sma150_f,
                        "bb_width_pct_max": bb_max,
                        "volume_ratio_max": vr_max,
                    }
                    full = {
                        **V31A,
                        "price_vs_ema200_pct_min": ema200_f,
                        "price_vs_sma150_pct_min": sma150_f,
                        "bb_width_pct_max": bb_max,
                        "volume_ratio_max": vr_max,
                    }
                    r_tr = _score_criteria(train, full)
                    r_te = _score_criteria(test,  full)
                    r_oo = _score_criteria(oos,   base)
                    # only show if OOS recall improves over 13% baseline AND train P stays ≥40%
                    if r_oo["recall"] > 0.15 and r_tr["precision"] >= 0.40:
                        label = (f"ema200≥{ema200_f} sma150≥{sma150_f} "
                                 f"bb≤{bb_max:.0f} vr≤{vr_max:.2f}")
                        _row(label, r_tr, r_te, r_oo)

    # ── SECTION 5: Best candidates full grid (top by OOS recall) ─────────────
    print("\n" + "=" * 80)
    print("SECTION 5: Full grid — best candidates ranked by OOS recall")
    print("  Gate ranges: ema200_floor 0-15, sma150_floor 0-15,")
    print("               bb_max 14-20, vr_max 1.10-1.30, pfh_max 12-18")
    print(f"\n  {'Criteria':<50}  {'tr_P':>5}  {'tr_R':>5}  {'te_P':>5}  {'te_R':>5}  {'oo_P':>5}  {'oo_R':>5}")
    print(f"  {'-'*50}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}")

    candidates = []
    for ema200_f in [0, 5, 10]:
        for sma150_f in [0, 5, 10]:
            for bb_max in [14.0, 16.0, 18.0]:
                for vr_max in [1.10, 1.20, 1.30]:
                    for pfh_max in [12, 15, 18]:
                        base = {
                            **V31A_BASE,
                            "price_vs_ema200_pct_min": ema200_f,
                            "price_vs_sma150_pct_min": sma150_f,
                            "bb_width_pct_max": bb_max,
                            "volume_ratio_max": vr_max,
                            "pct_from_52wk_high_max": pfh_max,
                        }
                        full = {
                            **V31A,
                            "price_vs_ema200_pct_min": ema200_f,
                            "price_vs_sma150_pct_min": sma150_f,
                            "bb_width_pct_max": bb_max,
                            "volume_ratio_max": vr_max,
                            "pct_from_52wk_high_max": pfh_max,
                        }
                        r_tr = _score_criteria(train, full)
                        r_te = _score_criteria(test,  full)
                        r_oo = _score_criteria(oos,   base)
                        candidates.append((r_tr, r_te, r_oo, ema200_f, sma150_f, bb_max, vr_max, pfh_max))

    # Sort by OOS recall descending, then train precision descending
    candidates.sort(key=lambda x: (-x[2]["recall"], -x[0]["precision"]))
    for r_tr, r_te, r_oo, e2, s1, bb, vr, pfh in candidates[:20]:
        label = f"e2≥{e2} s1≥{s1} bb≤{bb:.0f} vr≤{vr:.2f} pfh≤{pfh}"
        print(
            f"  {label:<50}  "
            f"{r_tr['precision']*100:4.1f}%  {r_tr['recall']*100:4.1f}%  "
            f"{r_te['precision']*100:4.1f}%  {r_te['recall']*100:4.1f}%  "
            f"{r_oo['precision']*100:4.1f}%  {r_oo['recall']*100:4.1f}%"
        )

    print("\n--- Session 20 complete ---")


if __name__ == "__main__":
    main()
