"""Session 04 experiments — universe restriction and follow-up analysis."""
from __future__ import annotations

from .store import _get_connection, get_all_features
from .validate import validate_criteria

V13 = {
    "sma50_above_sma200": 1,
    "market_cap_b_min": 15,
    "price_vs_ema200_pct_min": 2,
    "price_vs_ema200_pct_max": 35,
    "pct_from_52wk_high_max": 18,
    "rv20_max": 0.45,
    "dividend_yield_max": 2.5,
    "revenue_growth_min": 0.05,
}

V16 = {**V13, "market_cap_b_min": 20}


def _get_universe(tag: str) -> set[str]:
    conn = _get_connection()
    try:
        if tag == "sp500":
            rows = conn.execute(
                "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%sp500%'"
            ).fetchall()
        elif tag == "nasdaq100":
            rows = conn.execute(
                "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%nasdaq100%'"
            ).fetchall()
        elif tag == "nyse_large|nasdaq_large":
            rows = conn.execute(
                "SELECT symbol FROM universe_fundamentals "
                "WHERE universes LIKE '%nyse_large%' OR universes LIKE '%nasdaq_large%'"
            ).fetchall()
        else:
            raise ValueError(f"Unknown tag: {tag}")
        return {r[0] for r in rows}
    finally:
        conn.close()


def filter_to_universe(features: list[dict], universe: set[str]) -> list[dict]:
    """Keep all prime rows + only control rows whose ticker is in universe."""
    return [f for f in features if f["is_prime"] == 1 or f["ticker"] in universe]


def exp1_universe_restriction() -> None:
    """Experiment 1: Test v13/v16 against SP500, SP500|NDX100, and nyse_large|nasdaq_large."""
    print("\n" + "=" * 70)
    print("EXPERIMENT 1: Universe restriction")
    print("=" * 70)

    features = get_all_features()
    total_prime = sum(1 for f in features if f["is_prime"] == 1)
    total_control = sum(1 for f in features if f["is_prime"] == 0)
    print(f"Full feature matrix: {total_prime} prime rows, {total_control} control rows")

    sp500 = _get_universe("sp500")
    ndx100 = _get_universe("nasdaq100")
    nyse_ndx_large = _get_universe("nyse_large|nasdaq_large")

    universes = {
        "sp500": sp500,
        "sp500|nasdaq100": sp500 | ndx100,
        "nyse_large|nasdaq_large": nyse_ndx_large,
    }

    for label, u in universes.items():
        filtered = filter_to_universe(features, u)
        ctrl_count = sum(1 for f in filtered if f["is_prime"] == 0)
        prime_count = sum(1 for f in filtered if f["is_prime"] == 1)
        print(f"\n--- Universe: {label} ({len(u)} tickers) ---")
        print(f"  Filtered matrix: {prime_count} prime rows, {ctrl_count} control rows")

        for name, criteria in [("v13", V13), ("v16", V16)]:
            r = validate_criteria(criteria, features=filtered)
            print(
                f"  {name}: Precision={r['precision']:.1%}  Recall={r['recall']:.1%}  "
                f"TP={r['true_positives']}  FP={r['false_positives']}"
            )


def exp2_anet_investigation() -> None:
    """Experiment 2: Inspect ANET's feature values on prime dates."""
    print("\n" + "=" * 70)
    print("EXPERIMENT 2: ANET investigation (persistent miss)")
    print("=" * 70)

    features = get_all_features()
    anet_prime = [f for f in features if f["ticker"] == "ANET" and f["is_prime"] == 1]
    if not anet_prime:
        print("No ANET prime rows found.")
        return

    print(f"ANET appears in prime list on {len(anet_prime)} dates:")
    header = f"  {'date':<12} {'rv20':>6} {'ema200%':>8} {'52wkHi%':>8} {'mktcap':>8} {'sma50>200':>10}"
    print(header)
    for f in sorted(anet_prime, key=lambda x: x["date"]):
        print(
            f"  {f['date']:<12} {f.get('rv20') or 0:>6.3f} "
            f"{f.get('price_vs_ema200_pct') or 0:>8.1f} "
            f"{f.get('pct_from_52wk_high') or 0:>8.1f} "
            f"{f.get('market_cap_b') or 0:>8.1f} "
            f"{f.get('sma50_above_sma200') or 0:>10}"
        )

    rv20_vals = [f["rv20"] for f in anet_prime if f.get("rv20") is not None]
    if rv20_vals:
        print(f"\n  rv20 range: {min(rv20_vals):.3f} – {max(rv20_vals):.3f}  "
              f"(mean={sum(rv20_vals)/len(rv20_vals):.3f})")
        print(f"  All rv20 > 0.45? {all(v > 0.45 for v in rv20_vals)}")


def exp3_sector_stratified_ks() -> None:
    """Experiment 3: KS stats within Financial Services and Technology."""
    print("\n" + "=" * 70)
    print("EXPERIMENT 3: Sector-stratified KS analysis")
    print("=" * 70)

    from .analyze import rank_features

    features = get_all_features()
    for sector in ["Financial Services", "Technology"]:
        sector_features = [f for f in features if f.get("sector") == sector]
        prime_n = sum(1 for f in sector_features if f["is_prime"] == 1)
        ctrl_n = sum(1 for f in sector_features if f["is_prime"] == 0)
        print(f"\n--- {sector} ({prime_n} prime, {ctrl_n} control) ---")
        if prime_n < 5:
            print("  Too few prime rows for meaningful KS stats.")
            continue
        rankings = rank_features(sector_features)
        print("  Top 10 discriminating features:")
        for r in rankings[:10]:
            feat = r["feature"]
            stat = r["ks_stat"]
            pval = r["p_value"]
            print(f"    {feat:<35} KS={stat:.3f}  p={pval:.2e}  prime_mean={r['prime_mean']:.3f}  ctrl_mean={r['control_mean']:.3f}")


def exp4_iv_ratio() -> None:
    """Experiment 4: IV/RV ratio from the CSV iv column joined to features."""
    print("\n" + "=" * 70)
    print("EXPERIMENT 4: IV data from prime_tickers.csv")
    print("=" * 70)

    import csv
    from pathlib import Path

    csv_path = Path("data/detective/prime_tickers.csv")
    if not csv_path.exists():
        print(f"CSV not found at {csv_path}")
        return

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"CSV columns: {list(rows[0].keys())}")

    iv_col = next((c for c in rows[0] if "iv" in c.lower()), None)
    rv_col = "rv20"  # from features
    if not iv_col:
        print("No IV column found in CSV.")
        return

    features = get_all_features()
    feat_index = {(f["date"], f["ticker"]): f for f in features}

    ratios = []
    for row in rows:
        date = row.get("date", "")
        ticker = row.get("ticker", "")
        iv_str = row.get(iv_col, "")
        if not iv_str:
            continue
        try:
            iv = float(iv_str)
        except ValueError:
            continue
        feat = feat_index.get((date, ticker))
        if feat and feat.get("rv20"):
            ratio = iv / feat["rv20"]
            ratios.append({"date": date, "ticker": ticker, "iv": iv,
                           "rv20": feat["rv20"], "iv_rv_ratio": ratio})

    if not ratios:
        print("No IV/RV pairs found.")
        return

    ratios.sort(key=lambda x: x["iv_rv_ratio"])
    print(f"\nIV/RV ratio for {len(ratios)} prime picks:")
    print(f"  Min: {ratios[0]['iv_rv_ratio']:.2f} ({ratios[0]['ticker']} {ratios[0]['date']})")
    print(f"  Max: {ratios[-1]['iv_rv_ratio']:.2f} ({ratios[-1]['ticker']} {ratios[-1]['date']})")
    median_idx = len(ratios) // 2
    print(f"  Median: {ratios[median_idx]['iv_rv_ratio']:.2f}")
    below_1 = sum(1 for r in ratios if r["iv_rv_ratio"] < 1.0)
    print(f"  IV < RV (ratio < 1.0): {below_1}/{len(ratios)} ({below_1/len(ratios):.1%})")


def exp5_precision_at_sp500_with_tightened_criteria() -> None:
    """Experiment 5: Grid-search tighter criteria on SP500 universe to push precision higher."""
    print("\n" + "=" * 70)
    print("EXPERIMENT 5: Tighter criteria grid-search on SP500 universe")
    print("=" * 70)

    features = get_all_features()
    sp500 = _get_universe("sp500")
    filtered = filter_to_universe(features, sp500)

    # Test several tighter variants of v13 on the SP500 universe
    variants = {
        "v13 (baseline)": V13,
        "v13 + market_cap>=20B": {**V13, "market_cap_b_min": 20},
        "v13 + market_cap>=25B": {**V13, "market_cap_b_min": 25},
        "v13 + rv20<=0.40": {**V13, "rv20_max": 0.40},
        "v13 + rv20<=0.35": {**V13, "rv20_max": 0.35},
        "v13 + 52wk<=15%": {**V13, "pct_from_52wk_high_max": 15},
        "v13 + ema200%_max=30": {**V13, "price_vs_ema200_pct_max": 30},
        "v13 + rev_growth>=0.08": {**V13, "revenue_growth_min": 0.08},
        "v13 + rev_growth>=0.10": {**V13, "revenue_growth_min": 0.10},
        "v13 + mcap>=20 + rv20<=0.40": {**V13, "market_cap_b_min": 20, "rv20_max": 0.40},
        "v13 + mcap>=25 + rv20<=0.40": {**V13, "market_cap_b_min": 25, "rv20_max": 0.40},
    }

    print(f"  {'Variant':<40} {'Precision':>10} {'Recall':>8} {'TP':>5} {'FP':>6}")
    print("  " + "-" * 72)
    for name, crit in variants.items():
        r = validate_criteria(crit, features=filtered)
        flag = " *" if r["recall"] >= 0.65 and r["precision"] >= 0.15 else ""
        print(
            f"  {name:<40} {r['precision']:>9.1%} {r['recall']:>8.1%} "
            f"{r['true_positives']:>5} {r['false_positives']:>6}{flag}"
        )


if __name__ == "__main__":
    exp1_universe_restriction()
    exp2_anet_investigation()
    exp3_sector_stratified_ks()
    exp4_iv_ratio()
    exp5_precision_at_sp500_with_tightened_criteria()
