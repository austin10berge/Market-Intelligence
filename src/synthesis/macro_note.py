"""Weekly macro note generator — Exhibit 2C + 2D + 2E (fully automated).

Full pipeline (one command, no user input needed):
  1. Run CSP wheel scan
  2. Score top candidates (DeepSeek Exhibit 2B + 2C per stock)
  3. Fetch macro context (SPY/VIX + Wikipedia + AV news) — Exhibit 2C
  4. Generate 30-day macro forecast — Exhibit 2D
  5. Generate regime assessment + trading plan + position actions — Exhibit 2E
  6. Load open positions from wheel tracker DB
  7. Render and write complete note (no blank template sections)

Usage:
    docker compose run --rm pipeline python3 -m src.synthesis.macro_note

Output: ./data/trade-memos/YYYY-WW.md (ISO week number)  (copy to Obsidian after)
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from ..config import settings
from ..screener.csp_scanner import ScannerParams, run_csp_scan
from ..screener.wheel_scorer import score_wheel_candidates
from ..wheel_tracker.store import get_open_positions
from .llm import synthesize
from .macro_context import (
    build_macro_context_str,
    fetch_spy_vix_snapshot,
    fetch_wiki_events,
    format_spy_vix_str,
)

logger = logging.getLogger(__name__)

_DEFAULT_OUT_DIR = Path(__file__).parents[2] / "data" / "trade-memos"


def find_latest_note(trade_memos_dir: Path) -> Path | None:
    """Return the most recently written YYYY-WW.md note, or None."""
    notes = sorted(trade_memos_dir.glob("????-??.md"), reverse=True)
    return notes[0] if notes else None


# ── Scheduled event helpers ───────────────────────────────────────────────────

def _third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of the given month (standard monthly options expiry)."""
    fridays = [
        date(year, month, d)
        for d in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, d).weekday() == 4
    ]
    return fridays[2]


def _expiry_table_rows(n: int = 3) -> str:
    rows: list[str] = []
    today = date.today()
    y, m = today.year, today.month
    for _ in range(n):
        exp = _third_friday(y, m)
        dte = (exp - today).days
        label = exp.strftime("%b %Y")
        rows.append(
            f"| {exp.strftime('%Y-%m-%d')} | Monthly options expiry ({label}) | {dte}d |"
        )
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return "\n".join(rows)


# ── Open positions from DB ────────────────────────────────────────────────────

def _load_open_positions() -> list[dict]:
    try:
        with closing(sqlite3.connect(settings.db_path)) as conn:
            return get_open_positions(conn)
    except Exception as exc:
        logger.warning("Could not load open positions: %s", exc)
        return []


def _format_positions_for_prompt(positions: list[dict]) -> str:
    if not positions:
        return "No open wheel positions."
    lines: list[str] = []
    for p in positions:
        sym = p.get("underlying") or p.get("symbol", "?")
        otype = p.get("option_type") or p.get("asset_type", "")
        strike = p.get("strike", "")
        exp = p.get("expiration", "")
        dte = p.get("dte", "")
        pnl = p.get("unrealized_pnl")
        pnl_str = f"${pnl:+,.0f}" if pnl is not None else "N/A"
        lines.append(f"- {sym} {otype} ${strike} exp {exp} ({dte}d) uPnL: {pnl_str}")
    return "\n".join(lines)


def _format_positions_for_note(positions: list[dict]) -> str:
    if not positions:
        return "| — | — | — | — | — | — |\n_No open positions._"
    rows: list[str] = []
    for p in positions:
        sym = p.get("underlying") or p.get("symbol", "?")
        otype = p.get("option_type") or p.get("asset_type", "EQUITY")
        strike = p.get("strike") or "—"
        exp = p.get("expiration") or "—"
        dte = p.get("dte") or "—"
        pnl = p.get("unrealized_pnl")
        pnl_str = f"${pnl:+,.0f}" if pnl is not None else "—"
        rows.append(f"| {sym} | {otype} | ${strike} | {exp} | {dte}d | {pnl_str} |")
    return "\n".join(rows)


# ── Wheel scan parameters (applied every trade-review run) ───────────────────

_WHEEL_SCAN_PARAMS = ScannerParams(
    adr20_pct_min=3.5,
    max_vol_pct=65.0,
    min_days_to_earnings=20,
)

# ── Exhibit 2D: 30-day macro forecast ────────────────────────────────────────

_FORECAST_SYSTEM = """\
You are a macroeconomic analyst and market strategist. \
Speak in the third person. You do not mention your credentials. \
Do not use the word "provided" — use "recent" or "latest" instead. \
Do not recommend specific stocks or ETFs.\
"""


def _build_forecast_prompt(macro_context: str) -> str:
    today_str = date.today().strftime("%B %d, %Y")
    return (
        f"Here is context to update your knowledge to the current date:\n\n"
        f"{macro_context}\n\n"
        f"Today is {today_str}.\n\n"
        "Provide a complete expected timeline of the most important economic, "
        "technological, and political events for the next 30 days in the USA. "
        "Include not only scheduled events and known forecasts, but also best "
        "expectations about their realization.\n\n"
        "Output a markdown table with columns: Timeframe | Event | "
        "Market Expectation | Your Forecast | Implication for Options Premium Sellers.\n\n"
        "Include forecasts for: interest rates (Fed decisions), inflation (CPI), "
        "tariffs, government spending/budget, market sentiment, consumer confidence, "
        "labor market (jobs report), S&P 500 levels and returns, VIX trajectory, "
        "Gold prices, BTC prices, and any major sector or tech developments.\n\n"
        "First, state your expectation for S&P 500 level and return by end of month "
        "(today's level is in the market snapshot above). "
        "Then output the 30-day table.\n\n"
        "Keep your entire response under 800 words. Be concise."
    )


async def _generate_forecast(macro_context: str) -> str:
    logger.info("Macro note: generating 30-day forecast (Exhibit 2D)")
    result = await synthesize(_FORECAST_SYSTEM, _build_forecast_prompt(macro_context))
    if not result:
        return "_LLM forecast unavailable._"
    max_chars = 6000
    if len(result) > max_chars:
        truncated = result[:max_chars]
        last_break = truncated.rfind("\n\n")
        truncated = truncated[:last_break] if last_break > max_chars // 2 else truncated
        result = truncated.rstrip() + "\n\n_[output truncated]_"
    return result


# ── Exhibit 2E: regime assessment + wheel trading plan ───────────────────────

_REGIME_SYSTEM = """\
You are a professional options trader specializing in the Cash-Secured Put \
and Covered Call Wheel strategy. \
Speak in the third person. You do not mention your credentials. \
Do not use the word "provided" — use "recent" or "latest" instead. \
Do not recommend buying stock outright — only wheel-eligible options strategies.\
"""


def _build_regime_prompt(
    forecast: str,
    snapshot: dict,
    top_candidates: list[dict],
    open_positions: list[dict],
) -> str:
    spy_str = format_spy_vix_str(snapshot)

    def _fmt(v: object, suffix: str = "", dec: int = 1) -> str:
        try:
            return f"{float(v):.{dec}f}{suffix}"  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "N/A"

    cand_lines: list[str] = []
    for c in top_candidates[:10]:
        sym = c.get("symbol", "?")
        sector = c.get("sector", "N/A")
        iv = _fmt(c.get("impliedVolatility"), "%")
        adr = _fmt(c.get("adr20_pct"), "%")
        ann_roc = _fmt(c.get("annualized_roc"), "%")
        delta = _fmt(c.get("delta"), dec=2)
        dte = c.get("dte", "N/A")
        strike = _fmt(c.get("strike"), "$", dec=0)
        beta = _fmt(c.get("beta"), dec=2)
        rsi = _fmt(c.get("rsi"), dec=0)
        score = c.get("wheel_score") or c.get("composite_score", "N/A")
        thesis = c.get("wheel_thesis", "")
        cand_lines.append(
            f"- {sym} | {sector} | IV:{iv} ADR:{adr} AnnROC:{ann_roc} "
            f"Δ{delta} {dte}DTE {strike}P | Beta:{beta} RSI:{rsi} WheelScore:{score}"
            + (f" | {thesis}" if thesis else "")
        )
    cands_str = "\n".join(cand_lines) or "No candidates from scan."

    positions_str = _format_positions_for_prompt(open_positions)

    return (
        f"30-day macro forecast:\n\n{forecast}\n\n"
        f"Current market: {spy_str}\n\n"
        f"Current open wheel positions:\n{positions_str}\n\n"
        f"Top wheel candidates from scan:\n{cands_str}\n\n"
        "Based on the macro forecast and market conditions above, provide a complete "
        "monthly wheel trading plan for the next 30 days. "
        "Output exactly the following sections:\n\n"
        "**Regime:** [Bull / Sideways / Bear] — [one sentence rationale]\n\n"
        "**SPX Month-end Expectation:** [specific level and % return]\n\n"
        "**Trading Parameters:**\n"
        "- Max delta (CSP): ...\n"
        "- IV range target: ...\n"
        "- Max position size: ...\n"
        "- DTE range: ...\n"
        "- Sectors to avoid: ...\n\n"
        "**Thesis / Edge:** [2-3 sentences: why selling premium has edge this month]\n\n"
        "**Risks:**\n1. ...\n2. ...\n3. ...\n\n"
        "**Top Candidates:**\n"
        "| Ticker | Score | Sector | Ann ROC% | Thesis (one sentence) |\n"
        "|--------|-------|--------|----------|-----------------------|\n"
        "[one row per candidate, max 10 rows]\n\n"
        "**Open Position Actions:**\n"
        "| Ticker | Type | Strike | Expiry | DTE | uPnL | Action |\n"
        "|--------|------|--------|--------|-----|------|--------|\n"
        "[one row per open position; Action = Hold / Roll / Close with brief reason]\n\n"
        "Keep your entire response under 1200 words. Be direct and specific."
    )


async def _generate_regime_plan(
    forecast: str,
    snapshot: dict,
    top_candidates: list[dict],
    open_positions: list[dict],
) -> str:
    logger.info("Macro note: generating regime + trading plan (Exhibit 2E)")
    prompt = _build_regime_prompt(forecast, snapshot, top_candidates, open_positions)
    result = await synthesize(_REGIME_SYSTEM, prompt)
    if not result:
        return "_LLM regime assessment unavailable._"
    max_chars = 8000
    if len(result) > max_chars:
        truncated = result[:max_chars]
        last_break = truncated.rfind("\n\n")
        truncated = truncated[:last_break] if last_break > max_chars // 2 else truncated
        result = truncated.rstrip() + "\n\n_[output truncated]_"
    return result


# ── Note renderer ─────────────────────────────────────────────────────────────

def _render_note(
    snapshot: dict,
    wiki: str,
    forecast: str,
    regime_plan: str,
    open_positions: list[dict],
    target_week: date,
    candidate_count: int,
) -> str:
    iso_cal = target_week.isocalendar()
    month_str = f"Week {iso_cal.week}, {iso_cal.year}"
    month_key = f"{iso_cal.year}-{iso_cal.week:02d}"
    generated_str = date.today().strftime("%Y-%m-%d")
    # Next review = following Sunday
    days_to_sunday = (6 - target_week.weekday()) % 7 or 7
    rebalance = (target_week + timedelta(days=days_to_sunday)).strftime("%Y-%m-%d")

    spy_str = format_spy_vix_str(snapshot)
    spy_price = snapshot.get("spy_price", "N/A")
    vix = snapshot.get("vix", "N/A")
    vix_regime = snapshot.get("vix_regime", "")
    vs200 = snapshot.get("spy_vs_sma200", "N/A")

    expiry_rows = _expiry_table_rows()
    pos_rows = _format_positions_for_note(open_positions)

    return f"""\
# Trade Memo — {month_str}

> Auto-generated: {generated_str} | Next rebalance target: ~{rebalance}
> Scan candidates scored: {candidate_count}

---

## Market Snapshot

{spy_str}

| Metric | Value |
|--------|-------|
| SPY Price | ${spy_price} |
| SPY vs 200 SMA | {vs200} |
| VIX | {vix} |
| VIX Regime | {vix_regime} |

---

## Current Events Context (Exhibit 2C)

{wiki if wiki else "_Wikipedia unavailable._"}

---

## 30-Day Macro Forecast (Exhibit 2D)

{forecast}

---

## Upcoming Options Expiries

| Date | Event | DTE |
|------|-------|-----|
{expiry_rows}

---

## Monthly Wheel Trading Plan (Exhibit 2E)

{regime_plan}

---

## Current Open Positions

| Ticker | Type | Strike | Expiry | DTE | uPnL |
|--------|------|--------|--------|-----|------|
{pos_rows}

---

_Tags: #trade-memo #{month_key} #wheel #options_
"""


# ── Entry point ───────────────────────────────────────────────────────────────

async def generate_macro_note(
    out_dir: Path | str | None = None,
    target_week: date | None = None,
) -> Path:
    """Run the full weekly macro note pipeline and write to out_dir.

    Pipeline:
      1. CSP scan (ADR≥3.5%, IV≤65%, earnings>20d) + macro context + positions (parallel)
      2. Score all scan candidates individually (Exhibit 2B financials + per-stock LLM)
      3. 30-day macro forecast (Exhibit 2D)
      4. Regime + trading plan (Exhibit 2E) using LLM-scored candidates
      5. Render + write complete note

    Args:
        out_dir:     Output directory. Defaults to ./data/trade-memos/.
        target_week: Week the note covers (any day in that ISO week). Defaults to today.

    Returns:
        Path to the written file.
    """
    if target_week is None:
        target_week = date.today()

    if out_dir is None:
        out_dir = _DEFAULT_OUT_DIR
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    iso_cal = target_week.isocalendar()
    out_file = out_dir / f"{iso_cal.year}-{iso_cal.week:02d}.md"

    # Steps 1-2: Run scan + fetch macro context + open positions in parallel
    logger.info("Macro note: running CSP scan and fetching macro context")
    (scan_result, macro_str, snapshot, wiki, open_positions) = await asyncio.gather(
        asyncio.to_thread(run_csp_scan, _WHEEL_SCAN_PARAMS),
        build_macro_context_str(),
        asyncio.to_thread(fetch_spy_vix_snapshot),
        asyncio.to_thread(fetch_wiki_events),
        asyncio.to_thread(_load_open_positions),
    )
    scan_candidates = scan_result.get("candidates", [])
    logger.info(
        "Macro note: scan complete — %d candidates, %d positions",
        len(scan_candidates), len(open_positions),
    )

    # Step 3: Score candidates (Exhibit 2B per-stock financials + individual LLM)
    logger.info("Macro note: scoring %d candidates via wheel scorer", len(scan_candidates))
    top_candidates = await score_wheel_candidates(scan_candidates, macro_context=macro_str)

    # Step 4: 30-day macro forecast (Exhibit 2D)
    forecast = await _generate_forecast(macro_str)

    # Step 5: Regime + trading plan (Exhibit 2E) — uses LLM-scored candidates
    regime_plan = await _generate_regime_plan(forecast, snapshot, top_candidates, open_positions)

    # Step 6: Render + write
    note = _render_note(
        snapshot, wiki, forecast, regime_plan, open_positions,
        target_week, len(scan_candidates),
    )
    out_file.write_text(note, encoding="utf-8")
    logger.info("Macro note written: %s (%d bytes)", out_file, len(note.encode()))

    return out_file


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out_dir_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result_path = asyncio.run(generate_macro_note(out_dir=out_dir_arg))
    print(f"Written: {result_path}")
