"""Session 19 — KS feature-ranking comparison: 2026 OOS vs Sep-Oct 2025 train.

Goal: understand which features still discriminate prime from control in 2026,
and which have shifted — informing which VCP gates to relax in Session 20.

Four sections:
  1. KS rankings on 2026 data (84-ticker narrow universe, Jan-Jun 2026)
  2. KS rankings on Sep-Oct 2025 train data (same universe) for side-by-side
  3. Shift table: features whose KS rank moved the most between regimes
  4. The 6 passing TPs: what do they look like? What do the 40 FNs look like?

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session19
"""

from __future__ import annotations

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _BOOLEAN_FEATURES, _NUMERIC_FEATURES, _apply_criteria
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

V31A_BASE = {k: v for k, v in V31A.items() if k not in _IV_KEYS}


def _ks_rank(rows: list[dict]) -> dict[str, dict]:
    """Compute KS stat for every feature; return {feature: {ks, pval, p_mean, c_mean}}."""
    prime   = [f for f in rows if f["is_prime"] == 1]
    control = [f for f in rows if f["is_prime"] == 0]
    out = {}

    for feat in _NUMERIC_FEATURES:
        p_vals = [f[feat] for f in prime   if f.get(feat) is not None]
        c_vals = [f[feat] for f in control if f.get(feat) is not None]
        if len(p_vals) < 5 or len(c_vals) < 5:
            continue
        stat, pval = ks_2samp(p_vals, c_vals)
        out[feat] = {
            "ks": round(float(stat), 4),
            "pval": float(pval),
            "p_mean": round(float(np.mean(p_vals)), 3),
            "c_mean": round(float(np.mean(c_vals)), 3),
            "p_n": len(p_vals),
        }

    for feat in _BOOLEAN_FEATURES:
        p_vals = [f[feat] for f in prime   if f.get(feat) is not None]
        c_vals = [f[feat] for f in control if f.get(feat) is not None]
        if len(p_vals) < 5 or len(c_vals) < 5:
            continue
        p_rate = float(np.mean(p_vals))
        c_rate = float(np.mean(c_vals))
        stat = abs(p_rate - c_rate)
        out[feat] = {
            "ks": round(stat, 4),
            "pval": None,
            "p_mean": round(p_rate, 3),
            "c_mean": round(c_rate, 3),
            "p_n": len(p_vals),
        }

    return out


def _print_rankings(label: str, rankings: dict[str, dict], top_n: int = 20) -> None:
    sorted_feats = sorted(rankings, key=lambda f: -rankings[f]["ks"])
    print(f"\n  Top {top_n} features by KS/separation — {label}")
    print(f"  {'Feature':<36}  {'KS':>6}  {'prime_mean':>10}  {'ctrl_mean':>10}  {'n_prime':>7}")
    print(f"  {'-'*36}  {'-'*6}  {'-'*10}  {'-'*10}  {'-'*7}")
    for feat in sorted_feats[:top_n]:
        r = rankings[feat]
        print(
            f"  {feat:<36}  {r['ks']:6.4f}  {r['p_mean']:10.3f}  "
            f"{r['c_mean']:10.3f}  {r['p_n']:7d}"
        )


def main() -> None:
    all_features = get_all_features()
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]

    train  = [f for f in narrow if f["date"] <= TRAIN_CUTOFF]
    oos    = [f for f in narrow if f["date"] >= OOS_START]

    print(f"Train (Sep-Oct 2025) : {len(train)} rows, "
          f"{sum(1 for f in train if f['is_prime']==1)} prime")
    print(f"2026 OOS             : {len(oos)} rows, "
          f"{sum(1 for f in oos if f['is_prime']==1)} prime")

    train_ks = _ks_rank(train)
    oos_ks   = _ks_rank(oos)

    # ── SECTION 1: 2026 KS rankings ──────────────────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 1: 2026 OOS feature rankings (prime vs control, 84-ticker narrow)")
    print("=" * 72)
    _print_rankings("2026 OOS", oos_ks, top_n=25)

    # ── SECTION 2: Sep-Oct 2025 train KS rankings ────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 2: Sep-Oct 2025 TRAIN feature rankings (same universe)")
    print("=" * 72)
    _print_rankings("Sep-Oct 2025 train", train_ks, top_n=25)

    # ── SECTION 3: Shift table ────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 3: Feature ranking shifts (2026 rank − train rank, top 30 features)")
    print("  Negative shift = feature became MORE discriminating in 2026")
    print("  Positive shift = feature became LESS discriminating in 2026")
    print("=" * 72)

    common = sorted(
        [f for f in oos_ks if f in train_ks],
        key=lambda f: -oos_ks[f]["ks"],
    )
    train_sorted = sorted(train_ks, key=lambda f: -train_ks[f]["ks"])
    train_rank   = {f: i for i, f in enumerate(train_sorted)}
    oos_sorted   = sorted(oos_ks,   key=lambda f: -oos_ks[f]["ks"])
    oos_rank     = {f: i for i, f in enumerate(oos_sorted)}

    shifts = [(f, oos_rank[f] - train_rank[f]) for f in common]
    shifts.sort(key=lambda x: x[1])  # most negative (rose in 2026) first

    print(f"\n  {'Feature':<36}  {'train_KS':>8}  {'oos_KS':>8}  {'train_r':>8}  {'oos_r':>8}  {'shift':>6}")
    print(f"  {'-'*36}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*6}")
    for feat, shift in shifts[:30]:
        t = train_ks[feat]
        o = oos_ks[feat]
        marker = " <-- rose in 2026" if shift <= -5 else (" <-- fell in 2026" if shift >= 5 else "")
        print(
            f"  {feat:<36}  {t['ks']:8.4f}  {o['ks']:8.4f}  "
            f"{train_rank[feat]:8d}  {oos_rank[feat]:8d}  {shift:+6d}{marker}"
        )

    # ── SECTION 4: The 6 passing primes and the 40 FNs ───────────────────────
    print("\n" + "=" * 72)
    print("SECTION 4: 2026 prime rows — TPs vs FNs (V31A_BASE criteria)")
    print("=" * 72)

    tps = [f for f in oos if f["is_prime"] == 1 and _apply_criteria(f, V31A_BASE)]
    fns = [f for f in oos if f["is_prime"] == 1 and not _apply_criteria(f, V31A_BASE)]

    print(f"\n  TPs ({len(tps)}):")
    print(f"  {'date':<12}  {'ticker':<8}  {'sector':<22}  {'bb_w%':>6}  {'vr':>6}  {'pfh%':>6}  {'rv20':>6}  {'adx':>6}  {'rsi':>6}")
    print(f"  {'-'*12}  {'-'*8}  {'-'*22}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}")
    for f in sorted(tps, key=lambda x: x["date"]):
        print(
            f"  {f['date']:<12}  {f['ticker']:<8}  {(f.get('sector') or ''):<22}  "
            f"{f.get('bb_width_pct') or 0:6.1f}  {f.get('volume_ratio') or 0:6.2f}  "
            f"{f.get('pct_from_52wk_high') or 0:6.1f}  {f.get('rv20') or 0:6.3f}  "
            f"{f.get('adx') or 0:6.1f}  {f.get('rsi') or 0:6.1f}"
        )

    print(f"\n  FNs ({len(fns)}) — key feature values and which V31A_BASE gate fails first:")
    print(f"  {'date':<12}  {'ticker':<8}  {'sector':<22}  {'bb_w%':>6}  {'vr':>6}  {'pfh%':>6}  {'rv20':>6}  {'adx':>6}  {'rsi':>6}  first_fail")
    print(f"  {'-'*12}  {'-'*8}  {'-'*22}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  ----------")
    for f in sorted(fns, key=lambda x: x["date"]):
        first_fail = "?"
        for key, val in V31A_BASE.items():
            one_gate = {key: val}
            if not _apply_criteria(f, one_gate):
                first_fail = key
                break
        print(
            f"  {f['date']:<12}  {f['ticker']:<8}  {(f.get('sector') or ''):<22}  "
            f"{f.get('bb_width_pct') or 0:6.1f}  {f.get('volume_ratio') or 0:6.2f}  "
            f"{f.get('pct_from_52wk_high') or 0:6.1f}  {f.get('rv20') or 0:6.3f}  "
            f"{f.get('adx') or 0:6.1f}  {f.get('rsi') or 0:6.1f}  {first_fail}"
        )

    # ── SECTION 5: VCP gate relaxation preview ───────────────────────────────
    print("\n" + "=" * 72)
    print("SECTION 5: VCP gate relaxation preview on 2026 OOS (V31A_BASE variants)")
    print("  How many additional FNs would V31A_BASE recover at each relaxed threshold?")
    print("=" * 72)
    print(f"\n  {'bb_w_max':>8}  {'vr_max':>7}  {'pfh_max':>8}  {'rv20_max':>9}  "
          f"{'R':>7}  {'TP':>5}  {'FP':>5}  {'P':>7}")
    print(f"  {'-'*8}  {'-'*7}  {'-'*8}  {'-'*9}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*7}")

    from .analyze import _score_criteria

    base_no_vcp = {k: v for k, v in V31A_BASE.items()
                   if k not in {"bb_width_pct_max", "volume_ratio_max",
                                "pct_from_52wk_high_max", "rv20_max"}}

    for bb_max in [14.0, 16.0, 18.0, 20.0]:
        for vr_max in [1.10, 1.20, 1.30]:
            for pfh_max in [12, 15, 18]:
                for rv_max in [0.45, 0.55]:
                    crit = {
                        **base_no_vcp,
                        "bb_width_pct_max": bb_max,
                        "volume_ratio_max": vr_max,
                        "pct_from_52wk_high_max": pfh_max,
                        "rv20_max": rv_max,
                    }
                    res = _score_criteria(oos, crit)
                    # only print if recall improves over baseline (13%)
                    if res["recall"] > 0.14:
                        print(
                            f"  {bb_max:8.1f}  {vr_max:7.2f}  {pfh_max:8d}  {rv_max:9.2f}  "
                            f"{res['recall']*100:6.1f}%  {res['true_positives']:5d}  "
                            f"{res['false_positives']:5d}  {res['precision']*100:6.1f}%"
                        )

    print("\n--- Session 19 complete ---")


if __name__ == "__main__":
    main()
