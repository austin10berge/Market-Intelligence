"""Session 23 — V36 definition, M/AEO gate investigation, combined re-optimization.

Three experiments:

A) V36 definition and validation (SECTIONS 1-2):
   V36 = V35_CC_FIX with sma50_above_sma150=1 replacing price_vs_sma150_pct_min=5.
   sma50_above_sma150=1 is slightly better on all Sep-Dec splits (session 22 §3) and
   cleaner (no threshold to tune). CC override (ema200_pct_min=0 for CC rows) stays.
   Validate on all four splits. Summary table: V35 / V35_CC_FIX / V36.

B) M/AEO gate failure investigation (SECTION 3):
   M (2026-01-14) and AEO (2026-02-02) are the 2026 OOS CC primes that still fail
   V35_CC_FIX_BASE. Both have strong trend position (sma150_pct 25%/39%), so the CC
   ema200/sma150 override doesn't help them. Show ALL V36_BASE gate failures one by one
   to identify what's blocking them.

C) True combined re-optimization grid sweep (SECTION 4):
   Sweep key parameters evaluated SIMULTANEOUSLY on Sep-Dec 2025 (full IV) and 2026 OOS
   (base, no IV). Joint metric: harmonic_mean(R_sepDec, R_2026) — penalizes zero on either
   regime. Start from V36 as anchor; vary ema200_floor, bb_max, vr_max, pfh_max, sma150_gate.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session23
"""

from __future__ import annotations

from itertools import product

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

V34 = {**V31A, "iv_rv_min": 1.0, "pcr_vol_max": 2.0}

V35 = {
    **V31A,
    "price_vs_ema200_pct_min": 5,
    "price_vs_sma150_pct_min": 5,
    "bb_width_pct_max": 18.0,
    "volume_ratio_max": 1.20,
    "iv_rv_min": 1.0,
    "pcr_vol_max": 2.0,
}

V35_CC_FIX = {
    **V35,
    "consumer_cyclical_price_vs_ema200_pct_min": 0,
    "consumer_cyclical_price_vs_sma150_pct_min": 0,
}

# V36: replace pct floor with boolean; keep CC ema200 override; drop sma150 CC override
# (no longer needed since there's no global pct floor to override)
V36 = {
    **V31A,
    "price_vs_ema200_pct_min": 5,
    "sma50_above_sma150": 1,            # replaces price_vs_sma150_pct_min=5
    "bb_width_pct_max": 18.0,
    "volume_ratio_max": 1.20,
    "iv_rv_min": 1.0,
    "pcr_vol_max": 2.0,
    "consumer_cyclical_price_vs_ema200_pct_min": 0,  # CC ema200 override
}

V31A_BASE     = {k: v for k, v in V31A.items()     if k not in _IV_KEYS}
V34_BASE      = {k: v for k, v in V34.items()      if k not in _IV_KEYS}
V35_BASE      = {k: v for k, v in V35.items()      if k not in _IV_KEYS}
V35_CC_BASE   = {k: v for k, v in V35_CC_FIX.items() if k not in _IV_KEYS}
V36_BASE      = {k: v for k, v in V36.items()      if k not in _IV_KEYS}

# IV keys contributed by V34 (for Sep-Dec full-criteria evaluation)
_IV_GATE_DICT = {k: v for k, v in V34.items() if k in _IV_KEYS}


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


def _harmonic_mean(a: float, b: float) -> float:
    return 2 * a * b / (a + b) if (a + b) > 0 else 0.0


def _four_splits(
    label: str,
    sep_dec: list[dict],
    train: list[dict],
    test: list[dict],
    oos: list[dict],
    crit_full: dict,
    crit_base: dict,
) -> dict[str, dict]:
    f  = _score_criteria(sep_dec, crit_full)
    tr = _score_criteria(train,   crit_full)
    te = _score_criteria(test,    crit_full)
    oo = _score_criteria(oos,     crit_base)
    print(
        f"  {label:<44}  "
        f"{f['precision']*100:5.1f}%/{f['recall']*100:4.1f}%  "
        f"{tr['precision']*100:5.1f}%/{tr['recall']*100:4.1f}%  "
        f"{te['precision']*100:5.1f}%/{te['recall']*100:4.1f}%  "
        f"{oo['precision']*100:5.1f}%/{oo['recall']*100:4.1f}%"
    )
    return {"full": f, "train": tr, "test": te, "oos": oo}


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

    hdr = (f"\n  {'Criteria':<44}  {'full P/R':>10}  {'train P/R':>10}  "
           f"{'test P/R':>10}  {'oos P/R':>10}")
    sep_line = f"  {'-'*44}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}"

    # ── SECTION 1: V36 definition ─────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 1: V36 definition")
    print("  V36 = V35_CC_FIX with sma50_above_sma150=1 replacing price_vs_sma150_pct_min=5")
    print("  Changes from v35:")
    print("    price_vs_sma150_pct_min=5 → removed")
    print("    sma50_above_sma150=1      → added (cleaner, slightly better on all Sep-Dec splits)")
    print("    consumer_cyclical_price_vs_ema200_pct_min=0 → added (CC ema200 override)")
    print("=" * 80)

    print("\n  V36 keys changed from v35:")
    v35_keys = set(V35.keys())
    v36_keys = set(V36.keys())
    added   = v36_keys - v35_keys
    removed = v35_keys - v36_keys
    changed = {k for k in v35_keys & v36_keys if V35[k] != V36[k]}
    for k in sorted(removed): print(f"    REMOVED: {k} = {V35[k]}")
    for k in sorted(added):   print(f"    ADDED:   {k} = {V36[k]}")
    for k in sorted(changed): print(f"    CHANGED: {k}: {V35[k]} → {V36[k]}")

    # ── SECTION 2: All-splits comparison V35 / V35_CC_FIX / V36 ──────────────
    print("\n" + "=" * 80)
    print("SECTION 2: All-splits comparison — V35, V35_CC_FIX, V36")
    print("=" * 80)
    print(hdr); print(sep_line)
    r_v35     = _four_splits("v35",           sep_dec, train, test, oos, V35,        V35_BASE)
    r_v35_cc  = _four_splits("v35_cc_fix",    sep_dec, train, test, oos, V35_CC_FIX, V35_CC_BASE)
    r_v36     = _four_splits("v36",           sep_dec, train, test, oos, V36,        V36_BASE)

    # Sector breakdown for v36 Sep-Dec 2025
    print("\n  V36 sector breakdown — Sep-Dec 2025 full:")
    print(f"  {'Sector':<26}  {'TP':>4}  {'FP':>4}  {'P':>7}  {'prime':>6}")
    print(f"  {'-'*26}  {'-'*4}  {'-'*4}  {'-'*7}  {'-'*6}")
    sectors: dict[str, list] = {}
    for f in sep_dec:
        sectors.setdefault(f.get("sector") or "Unknown", []).append(f)
    total_tp = total_fp = 0
    for sector in sorted(sectors):
        rows = sectors[sector]
        tp = sum(1 for f in rows if f["is_prime"] == 1 and _apply_criteria(f, V36))
        fp = sum(1 for f in rows if f["is_prime"] == 0 and _apply_criteria(f, V36))
        n_prime = sum(1 for f in rows if f["is_prime"] == 1)
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        total_tp += tp; total_fp += fp
        print(f"  {sector:<26}  {tp:4d}  {fp:4d}  {p*100:6.1f}%  {n_prime:6d}")
    tot_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    print(f"  {'TOTAL':<26}  {total_tp:4d}  {total_fp:4d}  {tot_p*100:6.1f}%")

    # ── SECTION 3: M/AEO gate failure investigation ───────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 3: M and AEO gate failure investigation (2026 OOS CC primes)")
    print("  Both pass CC ema200/sma150 overrides but still fail V36_BASE.")
    print("  Printing ALL gate failures for each ticker on their prime dates.")
    print("=" * 80)

    cc_prime_oos = [
        f for f in oos
        if f["is_prime"] == 1 and f.get("sector") == "Consumer Cyclical"
    ]

    for f in sorted(cc_prime_oos, key=lambda x: x["date"]):
        print(f"\n  {f['ticker']} on {f['date']} — sector={f.get('sector')} "
              f"(pass_v36_base={'✓' if _apply_criteria(f, V36_BASE) else '✗'})")
        print(f"  {'Gate':<46}  {'Required':>12}  {'Actual':>12}  {'Pass?':>6}")
        print(f"  {'-'*46}  {'-'*12}  {'-'*12}  {'-'*6}")
        for key, val in V36_BASE.items():
            # Evaluate single-gate pass/fail
            single = {key: val}
            passed = _apply_criteria(f, single)
            # Get actual feature value for display
            if key.endswith("_min"):
                feat = key[:-4]
                actual = f.get(feat)
                req_str = f">= {val}"
            elif key.endswith("_max"):
                feat = key[:-4]
                actual = f.get(feat)
                req_str = f"<= {val}"
            elif key == "sma50_above_sma200":
                actual = f.get("sma50_above_sma200")
                req_str = f"== {val}"
            elif key == "sma50_above_sma150":
                actual = f.get("sma50_above_sma150")
                req_str = f"== {val}"
            elif key == "real_estate_block":
                actual = f.get("sector")
                req_str = "!= Real Estate"
            elif key.startswith("consumer_cyclical_") and key.endswith("_pct_min"):
                feat = key.replace("consumer_cyclical_", "").replace("_min", "")
                actual = f.get(feat)
                req_str = f">= {val} (CC)"
            elif key.startswith("financials_") or key.startswith("technology_") or \
                 key.startswith("consumer_cyclical_") or key.startswith("communication_"):
                # sector-specific, only applies to matching sector
                if not _apply_criteria(f, {key: val}):
                    actual = "see below"
                    req_str = "sector gate"
                else:
                    continue  # passes, skip
            else:
                actual = f.get(key)
                req_str = f"== {val}"

            if not passed:
                actual_str = f"{actual:.3f}" if isinstance(actual, float) else str(actual)
                print(f"  {key:<46}  {req_str:>12}  {actual_str:>12}  {'✗ FAIL':>6}")
            # Only print failures to keep output readable

    # ── SECTION 4: Combined re-optimization grid sweep ────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: Combined re-optimization — joint metric sweep")
    print("  Evaluating BOTH Sep-Dec 2025 (full IV) and 2026 OOS (base) simultaneously.")
    print("  Joint metric: harmonic_mean(R_sepDec, R_2026) — penalizes zero on either regime.")
    print("  Fixed: all V31A non-sweep gates + CC ema200 override + V34 IV gates.")
    print("  Swept: ema200_floor, bb_max, vr_max, pfh_max, sma150_gate_style.")
    print("=" * 80)

    # Anchor: V31A_BASE with sweep-variable keys removed; CC override always on
    _SWEEP_VARS = {
        "price_vs_ema200_pct_min", "bb_width_pct_max",
        "volume_ratio_max", "pct_from_52wk_high_max",
        "price_vs_sma150_pct_min", "sma50_above_sma150",
    }
    _ANCHOR = {k: v for k, v in V31A_BASE.items() if k not in _SWEEP_VARS}
    _ANCHOR["consumer_cyclical_price_vs_ema200_pct_min"] = 0

    candidates = []
    for ema200_f, bb_max, vr_max, pfh_max, sma150_style in product(
        [0, 3, 5, 8, 10],          # price_vs_ema200_pct_min
        [14.0, 16.0, 18.0, 20.0],  # bb_width_pct_max
        [1.10, 1.15, 1.20, 1.30],  # volume_ratio_max
        [10, 12, 15, 18],           # pct_from_52wk_high_max
        ["bool", 0, 5, 10],         # sma150 gate: "bool"=boolean, int=pct_min (0=none)
    ):
        base = {
            **_ANCHOR,
            "price_vs_ema200_pct_min": ema200_f,
            "bb_width_pct_max": bb_max,
            "volume_ratio_max": vr_max,
            "pct_from_52wk_high_max": pfh_max,
        }
        if sma150_style == "bool":
            base["sma50_above_sma150"] = 1
        elif sma150_style > 0:
            base["price_vs_sma150_pct_min"] = sma150_style
        # sma150_style == 0: no sma150 gate

        crit_full = {**base, **_IV_GATE_DICT}
        r_sd = _score_criteria(sep_dec, crit_full)
        r_oo = _score_criteria(oos, base)
        jm = _harmonic_mean(r_sd["recall"], r_oo["recall"])
        candidates.append((jm, r_sd, r_oo, ema200_f, bb_max, vr_max, pfh_max, sma150_style))

    # Sort by joint metric (harmonic mean of recalls), then Sep-Dec precision
    candidates.sort(key=lambda x: (-x[0], -x[1]["precision"]))

    print("\n  Joint metric = harmonic_mean(R_sepDec, R_2026)")
    print(f"\n  {'Criteria':<50}  {'JM':>5}  {'sd_P':>5}  {'sd_R':>5}  "
          f"{'oo_P':>5}  {'oo_R':>5}  {'tr_P':>5}  {'tr_R':>5}  "
          f"{'te_P':>5}  {'te_R':>5}")
    print(f"  {'-'*50}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  "
          f"{'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}")

    # Print top 25
    for jm, r_sd, r_oo, e2, bb, vr, pfh, s150 in candidates[:25]:
        s150_str = "bool" if s150 == "bool" else (f"s≥{s150}" if s150 > 0 else "none")
        label = f"e2≥{e2} bb≤{bb:.0f} vr≤{vr:.2f} pfh≤{pfh} s150={s150_str}"
        # Get train/test for this combo
        base2 = {
            **_ANCHOR,
            "price_vs_ema200_pct_min": e2,
            "bb_width_pct_max": bb,
            "volume_ratio_max": vr,
            "pct_from_52wk_high_max": pfh,
        }
        if s150 == "bool":
            base2["sma50_above_sma150"] = 1
        elif s150 > 0:
            base2["price_vs_sma150_pct_min"] = s150
        full2 = {**base2, **_IV_GATE_DICT}
        r_tr = _score_criteria(train, full2)
        r_te = _score_criteria(test,  full2)
        print(
            f"  {label:<50}  {jm*100:4.1f}%  "
            f"{r_sd['precision']*100:4.1f}%  {r_sd['recall']*100:4.1f}%  "
            f"{r_oo['precision']*100:4.1f}%  {r_oo['recall']*100:4.1f}%  "
            f"{r_tr['precision']*100:4.1f}%  {r_tr['recall']*100:4.1f}%  "
            f"{r_te['precision']*100:4.1f}%  {r_te['recall']*100:4.1f}%"
        )

    # Also show V36 for reference
    r_sd_v36 = _score_criteria(sep_dec, V36)
    r_oo_v36 = _score_criteria(oos,     V36_BASE)
    r_tr_v36 = _score_criteria(train,   V36)
    r_te_v36 = _score_criteria(test,    V36)
    jm_v36 = _harmonic_mean(r_sd_v36["recall"], r_oo_v36["recall"])
    print(f"\n  {'v36 (reference)':<50}  {jm_v36*100:4.1f}%  "
          f"{r_sd_v36['precision']*100:4.1f}%  {r_sd_v36['recall']*100:4.1f}%  "
          f"{r_oo_v36['precision']*100:4.1f}%  {r_oo_v36['recall']*100:4.1f}%  "
          f"{r_tr_v36['precision']*100:4.1f}%  {r_tr_v36['recall']*100:4.1f}%  "
          f"{r_te_v36['precision']*100:4.1f}%  {r_te_v36['recall']*100:4.1f}%")

    # Also show top 10 ranked by: R_2026 >= 25% AND maximize P_sepDec
    print("\n  --- Top 10 by Sep-Dec precision with R_2026 >= 25% ---")
    high_oos = [(jm, r_sd, r_oo, e2, bb, vr, pfh, s150)
                for jm, r_sd, r_oo, e2, bb, vr, pfh, s150 in candidates
                if r_oo["recall"] >= 0.25]
    high_oos.sort(key=lambda x: (-x[1]["precision"], -x[0]))
    print(f"\n  {'Criteria':<50}  {'sd_P':>5}  {'sd_R':>5}  "
          f"{'oo_P':>5}  {'oo_R':>5}  {'tr_P':>5}  {'tr_R':>5}  {'te_P':>5}  {'te_R':>5}")
    print(f"  {'-'*50}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}")
    for jm, r_sd, r_oo, e2, bb, vr, pfh, s150 in high_oos[:10]:
        s150_str = "bool" if s150 == "bool" else (f"s≥{s150}" if s150 > 0 else "none")
        label = f"e2≥{e2} bb≤{bb:.0f} vr≤{vr:.2f} pfh≤{pfh} s150={s150_str}"
        base2 = {
            **_ANCHOR,
            "price_vs_ema200_pct_min": e2,
            "bb_width_pct_max": bb,
            "volume_ratio_max": vr,
            "pct_from_52wk_high_max": pfh,
        }
        if s150 == "bool":
            base2["sma50_above_sma150"] = 1
        elif s150 > 0:
            base2["price_vs_sma150_pct_min"] = s150
        full2 = {**base2, **_IV_GATE_DICT}
        r_tr = _score_criteria(train, full2)
        r_te = _score_criteria(test,  full2)
        print(
            f"  {label:<50}  "
            f"{r_sd['precision']*100:4.1f}%  {r_sd['recall']*100:4.1f}%  "
            f"{r_oo['precision']*100:4.1f}%  {r_oo['recall']*100:4.1f}%  "
            f"{r_tr['precision']*100:4.1f}%  {r_tr['recall']*100:4.1f}%  "
            f"{r_te['precision']*100:4.1f}%  {r_te['recall']*100:4.1f}%"
        )

    print("\n--- Session 23 complete ---")


if __name__ == "__main__":
    main()
