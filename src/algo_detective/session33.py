"""Session 33 — market_cap_b_min sweep on V41.

Context:
  Playwright crawl of mlabstrading.com (session 32) found:
  - He traded AAL CSP on 2026-05-14 (mcap ~$10.6B) and AA CSP on 2026-05-12
    (mcap ~$8-15B) — both blocked by our market_cap_b_min=25.
  - Session 31 gate ranking: market_cap_b_min blocks 7 OOS TPs:
    AAL $10.6B, AA $15.7B, AEO $3B, M $6.4B, DOCN $18B (2 dates each for some).

  Current V41_BASE uses market_cap_b_min=25. If he trades names at $10-15B,
  lowering the floor might recover OOS TPs at acceptable Sep-Dec FP cost.

  Also: financials_market_cap_b_min=100 is a separate gate blocking JPM/BAC/GS
  from appearing unless their market cap exceeds $100B. This is a separate thing
  from the global floor.

Sections:
  1. market_cap_b_min sweep on V41: 25→15→10→5→none
     (how many Sep-Dec FPs does each value add? which OOS TPs are recovered?)
  2. Combined with financials_market_cap_b_min: is that gate still earning its keep?
  3. Sector breakdown of new FPs when we lower the floor

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session33
"""

from __future__ import annotations

from .analyze import _apply_criteria
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
    "technology_rsi_max": 60,
}

V39 = {**V38, "adr20_pct_max": 4.0}

V40 = {
    **V39,
    "industrials_rsi_max": 70,
    "financials_rsi_max": 70,
    "healthcare_rsi_max": 60,
}

V41 = {**V40, "bb_width_pct_max": 21.0}
V41_BASE = {k: v for k, v in V41.items() if k not in _IV_KEYS}


def _join_options(features: list[dict]) -> list[dict]:
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("SELECT date, ticker, pcr_vol, best_iv FROM detective_options")
    opts = {(r[0], r[1]): {"pcr_vol": r[2], "best_iv": r[3]} for r in cur.fetchall()}
    conn.close()
    out = []
    for f in features:
        key = (f["date"], f["ticker"])
        merged = {**f, **opts.get(key, {})}
        out.append(merged)
    return out


def _row4(
    label: str,
    sep_dec: list[dict],
    train: list[dict],
    test: list[dict],
    oos: list[dict],
    crit_full: dict,
    crit_base: dict,
) -> None:
    def _stats(rows: list[dict], crit: dict) -> tuple[float, float, int]:
        hits = [f for f in rows if _apply_criteria(f, crit)]
        tp = sum(1 for f in hits if f["is_prime"] == 1)
        total = len(hits)
        total_p = sum(1 for f in rows if f["is_prime"] == 1)
        prec = tp / total * 100 if total else 0.0
        rec = tp / total_p * 100 if total_p else 0.0
        return prec, rec, tp

    sd_p, sd_r, sd_tp = _stats(sep_dec, crit_base)
    tr_p, tr_r, _ = _stats(train, crit_base)
    te_p, te_r, _ = _stats(test, crit_base)
    oo_p, oo_r, oo_tp = _stats(oos, crit_base)
    print(
        f"  {label:<36} "  # noqa: E501
        f"sd={sd_p:5.1f}%/{sd_r:4.1f}%  "
        f"tr={tr_p:5.1f}%/{tr_r:4.1f}%  "
        f"te={te_p:5.1f}%/{te_r:4.1f}%  "
        f"oos={oo_p:5.1f}%/{oo_r:4.1f}%  "
        f"sdTP={sd_tp}  oosTP={oo_tp}"
    )


def main() -> None:
    all_features = get_all_features()
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    all_features = [f for f in all_features if f["ticker"] in prime_tickers]
    all_features = _join_options(all_features)

    sep_dec = [f for f in all_features if f["date"] < OOS_START]
    train   = [f for f in sep_dec if f["date"] <= TRAIN_CUTOFF]
    test    = [f for f in sep_dec if f["date"] > TRAIN_CUTOFF]
    oos     = [f for f in all_features if f["date"] >= OOS_START]

    print(f"Sep-Dec 2025 : {len(sep_dec)} rows, {sum(1 for f in sep_dec if f['is_prime']==1)} prime")  # noqa: E501
    print(f"2026 OOS     : {len(oos)} rows, {sum(1 for f in oos if f['is_prime']==1)} prime")
    print("\n  (header: sd=Sep-Dec full  tr=Train Sep-Oct  te=Test Nov-Dec  oos=2026 OOS)")

    # ── SECTION 1: market_cap_b_min sweep ────────────────────────────────────
    print()
    print("=" * 80)
    print("SECTION 1: market_cap_b_min sweep on V41 (25→15→10→5→none)")
    print("  Playwright found he traded AAL (~$10.6B) and AA (~$8-15B) in May 2026.")
    print("  Both blocked by market_cap_b_min=25. What's the FP cost of relaxing?")
    print("=" * 80)
    print()
    print(f"  {'Criteria':<36}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V41 (baseline, mcap>=25)", sep_dec, train, test, oos, V41, V41_BASE)
    for mcap in [20.0, 15.0, 10.0, 5.0]:
        crit = {**V41, "market_cap_b_min": mcap}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V41+mcap>={mcap:.0f}B", sep_dec, train, test, oos, crit, crit_base)
    # No market cap floor
    crit_no_floor = {k: v for k, v in V41.items() if k != "market_cap_b_min"}
    crit_no_floor_base = {k: v for k, v in crit_no_floor.items() if k not in _IV_KEYS}
    _row4("V41 (no mcap floor)", sep_dec, train, test, oos, crit_no_floor, crit_no_floor_base)

    # Enumerate which OOS TPs each threshold recovers
    print("\n  1A. OOS TPs recovered vs V41 at each market_cap threshold:")
    v41_oos_hits = {(f["date"], f["ticker"]) for f in oos if f["is_prime"]==1 and _apply_criteria(f, V41_BASE)}  # noqa: E501
    for mcap in [20.0, 15.0, 10.0, 5.0, None]:
        if mcap is not None:
            crit_b = {k: v for k, v in V41_BASE.items()}
            crit_b["market_cap_b_min"] = mcap
            label = f"mcap>={mcap:.0f}B"
        else:
            crit_b = {k: v for k, v in V41_BASE.items() if k != "market_cap_b_min"}
            label = "no floor"
        new_hits = {(f["date"], f["ticker"]) for f in oos if f["is_prime"]==1 and _apply_criteria(f, crit_b)}  # noqa: E501
        newly = new_hits - v41_oos_hits
        if newly:
            for date, ticker in sorted(newly):
                row = next(f for f in oos if f["date"]==date and f["ticker"]==ticker)
                mcap_val = row.get("market_cap_b")
                print(f"    {label}: {date} {ticker:6s}  market_cap={mcap_val:.1f}B")
        else:
            print(f"    {label}: no new OOS TPs recovered")

    # Show Sep-Dec new FPs at each threshold (sector breakdown)
    print("\n  1B. New Sep-Dec FPs added per threshold (sector breakdown):")
    v41_sd_fps = {(f["date"], f["ticker"]) for f in sep_dec if f["is_prime"]==0 and _apply_criteria(f, V41_BASE)}  # noqa: E501
    for mcap in [20.0, 15.0, 10.0, 5.0, None]:
        if mcap is not None:
            crit_b = {**V41_BASE, "market_cap_b_min": mcap}
            label = f"mcap>={mcap:.0f}B"
        else:
            crit_b = {k: v for k, v in V41_BASE.items() if k != "market_cap_b_min"}
            label = "no floor"
        new_sd_fps = {(f["date"], f["ticker"]) for f in sep_dec if f["is_prime"]==0 and _apply_criteria(f, crit_b)}  # noqa: E501
        added = new_sd_fps - v41_sd_fps
        sector_counts: dict[str, int] = {}
        for date, ticker in added:
            row = next(f for f in sep_dec if f["date"]==date and f["ticker"]==ticker)
            sec = row.get("sector", "Unknown") or "Unknown"
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
        total_added = len(added)
        if total_added == 0:
            print(f"    {label}: no new FPs")
        else:
            sec_str = ", ".join(f"{s}:{n}" for s, n in sorted(sector_counts.items(), key=lambda x: -x[1]))  # noqa: E501
            print(f"    {label}: +{total_added} FPs  [{sec_str}]")

    # ── SECTION 2: financials_market_cap_b_min=100 audit ─────────────────────
    print()
    print("=" * 80)
    print("SECTION 2: financials_market_cap_b_min=100 audit")
    print("  Separate gate: Financials sector must be >=$100B mcap.")
    print("  Currently C ($143B), JPM, BAC, GS all pass. What does lowering it do?")
    print("=" * 80)
    print()

    fin_rows = [f for f in sep_dec if f.get("sector") == "Financial Services"]
    print(f"  Sep-Dec Financials rows: {len(fin_rows)}")

    # Which Financials tickers appear in Sep-Dec and their market cap?
    fin_tickers: dict[str, list[float]] = {}
    for f in fin_rows:
        t = f["ticker"]
        mc = f.get("market_cap_b")
        if mc is not None:
            fin_tickers.setdefault(t, []).append(mc)
    print("\n  Financials tickers in Sep-Dec (avg mcap):")
    for t, vals in sorted(fin_tickers.items(), key=lambda x: -sum(x[1])/len(x[1])):
        avg_mc = sum(vals) / len(vals)
        is_prime = sum(1 for f in fin_rows if f["ticker"]==t and f["is_prime"]==1)
        print(f"    {t:6s}: avg_mcap={avg_mc:.0f}B  prime_days={is_prime}")

    print()
    print(f"  {'Criteria':<36}  sd=P/R        tr=P/R        te=P/R        oos=P/R       sdTP  oosTP")  # noqa: E501
    _row4("V41 (fin_mcap>=100B)", sep_dec, train, test, oos, V41, V41_BASE)
    for fin_mcap in [50.0, 25.0, 10.0]:
        crit = {**V41, "financials_market_cap_b_min": fin_mcap}
        crit_base = {k: v for k, v in crit.items() if k not in _IV_KEYS}
        _row4(f"V41+fin_mcap>={fin_mcap:.0f}B", sep_dec, train, test, oos, crit, crit_base)
    crit_no_fin = {k: v for k, v in V41.items() if k != "financials_market_cap_b_min"}
    crit_no_fin_base = {k: v for k, v in crit_no_fin.items() if k not in _IV_KEYS}
    _row4("V41 (no fin_mcap gate)", sep_dec, train, test, oos, crit_no_fin, crit_no_fin_base)

    # ── SECTION 3: Sep-Dec low-mcap FP deep-dive ──────────────────────────────
    print()
    print("=" * 80)
    print("SECTION 3: Who are the new FPs when we lower the global floor?")
    print("  V41 with mcap>=5B — list every new Sep-Dec FP with ticker, mcap, sector")
    print("=" * 80)
    print()
    crit_5b = {**V41_BASE, "market_cap_b_min": 5.0}
    v41_5b_fps = [(f["date"], f["ticker"], f.get("market_cap_b"), f.get("sector"))
                  for f in sep_dec if f["is_prime"]==0 and _apply_criteria(f, crit_5b)]
    v41_fps_set = {(f["date"], f["ticker"]) for f in sep_dec if f["is_prime"]==0 and _apply_criteria(f, V41_BASE)}  # noqa: E501
    new_5b_fps = [(d, t, mc, s) for d, t, mc, s in v41_5b_fps if (d, t) not in v41_fps_set]
    new_5b_fps.sort(key=lambda x: (x[1], x[0]))
    if new_5b_fps:
        print(f"  New Sep-Dec FPs with mcap>=5B ({len(new_5b_fps)} total):")
        for date, ticker, mc, sector in new_5b_fps:
            print(f"    {date} {ticker:6s}  mcap={mc:.1f}B  sector={sector}")
    else:
        print("  No new FPs added at mcap>=5B")

    # ── SECTION 4: OOS low-mcap ticker universe check ─────────────────────────
    print()
    print("=" * 80)
    print("SECTION 4: OOS TPs blocked by market_cap — deep dive")
    print("  Show what other gates also block each mcap-blocked OOS TP.")
    print("=" * 80)
    print()
    mcap_blocked_oos_tps = [
        f for f in oos
        if f["is_prime"] == 1
        and not _apply_criteria(f, V41_BASE)
        and (f.get("market_cap_b") is not None and f.get("market_cap_b") < 25)
    ]
    if mcap_blocked_oos_tps:
        print(f"  OOS TPs with mcap<25B blocked by V41_BASE ({len(mcap_blocked_oos_tps)} total):")
        for f in sorted(mcap_blocked_oos_tps, key=lambda x: (x["ticker"], x["date"])):
            mc = f.get("market_cap_b")
            # Find which gates also block them
            co_blockers = []
            for key, val in V41_BASE.items():
                if key == "market_cap_b_min":
                    continue
                single = {key: val}
                if not _apply_criteria(f, single):
                    co_blockers.append(key)
            co_str = ", ".join(co_blockers) if co_blockers else "none (sole blocker)"
            print(f"    {f['date']} {f['ticker']:6s}  mcap={mc:.1f}B  co-blockers: {co_str}")
    else:
        print("  No OOS TPs with mcap<25B found.")

    print("\nDone.")


if __name__ == "__main__":
    main()
