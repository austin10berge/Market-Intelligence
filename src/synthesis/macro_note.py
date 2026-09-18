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


# ── AI-infrastructure thesis watchlist ───────────────────────────────────────
# Jevons Paradox applied to software: AI calls these platforms as infrastructure,
# driving API consumption even as per-unit efficiency improves.
# Watch trigger: name appears in CSP scan (wheel-eligible) or moves >10% in a week.

_THESIS_WATCHLIST: dict[str, str] = {
    # Tier 1 — direct thesis, no AI rerate yet
    "BAND": "Voice/SMS API backbone; AI agent communication infrastructure",
    "GTLB": "CI/CD consumption rises with AI code generation; discount to GitHub multiple",
    "YEXT": "Enterprise AI search/answer layer; massively beaten down",
    "ESTC": "Native vector search + observability; direct AI infra play",
    "APPN": "BPM workflow orchestration for AI agents; peak ~$200, no rerate",
    "FSLY": "Edge compute for AI inference; recovering but not rerated",
    "API":  "Real-time video/voice SDK (Agora); AI live interactions; extreme value",
    "DOCU": "AI triggers e-sig/doc workflows; peak $300, no AI rerate",
    "S":    "AI security (SentinelOne); AI expands attack surface + AI-powered product",
    # Tier 2 — solid thesis, partial rerate
    "PATH": "AI+RPA enterprise automation (UiPath); peak $85",
    "CFLT": "Real-time event streaming for AI agents (Confluent Kafka)",
    "RPD":  "Security ops platform; heavily beaten down, possible go-private floor",
    "VRNS": "Data security/governance; AI data access creates urgent need",
    "BOX":  "Document AI platform; Content API consumption play",
    "HUBS": "CRM + marketing; AI personalizes at 100x volume through HubSpot",
    "DT":   "APM/observability (Dynatrace); AI workloads multiply monitoring complexity",
    "TENB": "Vulnerability management API; AI expands scannable attack surface",
    "MQ":   "Card-issuing API (Marqeta); AI fintech apps issue cards on demand",
    "KVYO": "Marketing delivery layer; AI-generated messages route through Klaviyo",
}


def _fetch_watchlist_snapshot(scan_symbols: set[str]) -> list[dict]:
    """Fetch price + 52-week metrics for the thesis watchlist. Runs in a thread."""
    import yfinance as yf

    symbols = list(_THESIS_WATCHLIST.keys())
    rows: list[dict] = []
    try:
        hist = yf.download(
            symbols,
            period="1y",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        close = (
            hist["Close"] if len(symbols) > 1
            else hist[["Close"]].rename(columns={"Close": symbols[0]})
        )
        for sym in symbols:
            try:
                series = close[sym].dropna() if sym in close.columns else None
                if series is None or series.empty:
                    raise ValueError("no data")
                price = float(series.iloc[-1])
                hi52 = float(series.max())
                lo52 = float(series.min())
                vs_hi = (price - hi52) / hi52 * 100
                vs_lo = (price - lo52) / lo52 * 100
                w1_chg = (
                    (price - float(series.iloc[-6])) / float(series.iloc[-6]) * 100
                    if len(series) >= 6 else None
                )
                rows.append({
                    "symbol": sym,
                    "price": round(price, 2),
                    "vs_52w_hi": round(vs_hi, 1),
                    "vs_52w_lo": round(vs_lo, 1),
                    "w1_chg": round(w1_chg, 1) if w1_chg is not None else None,
                    "in_scan": sym in scan_symbols,
                    "thesis": _THESIS_WATCHLIST[sym],
                })
            except Exception:
                rows.append({
                    "symbol": sym, "price": None, "vs_52w_hi": None,
                    "vs_52w_lo": None, "w1_chg": None,
                    "in_scan": sym in scan_symbols, "thesis": _THESIS_WATCHLIST[sym],
                })
    except Exception as exc:
        logger.warning("Watchlist snapshot failed: %s", exc)
        rows = [
            {
                "symbol": sym, "price": None, "vs_52w_hi": None,
                "vs_52w_lo": None, "w1_chg": None,
                "in_scan": sym in scan_symbols, "thesis": _THESIS_WATCHLIST[sym],
            }
            for sym in symbols
        ]
    return rows


def _format_watchlist_for_note(rows: list[dict]) -> str:
    lines = [
        "| Ticker | Price | vs 52W Hi | vs 52W Lo | 1W Chg | In Scan | Thesis |",
        "|--------|-------|-----------|-----------|--------|---------|--------|",
    ]
    for r in rows:
        price = f"${r['price']:.2f}" if r["price"] is not None else "—"
        vs_hi = f"{r['vs_52w_hi']:+.1f}%" if r["vs_52w_hi"] is not None else "—"
        vs_lo = f"{r['vs_52w_lo']:+.1f}%" if r["vs_52w_lo"] is not None else "—"
        w1 = f"{r['w1_chg']:+.1f}%" if r["w1_chg"] is not None else "—"
        in_scan = "✓ **YES**" if r["in_scan"] else "—"
        thesis = r["thesis"][:65]
        lines.append(
            f"| {r['symbol']} | {price} | {vs_hi} | {vs_lo} | {w1} | {in_scan} | {thesis} |"
        )
    return "\n".join(lines)


def _format_watchlist_for_prompt(rows: list[dict]) -> str:
    scan_hits = [r for r in rows if r["in_scan"] and r["price"] is not None]
    big_movers = [r for r in rows if r["w1_chg"] is not None and abs(r["w1_chg"]) >= 10]
    lines = ["AI-infrastructure thesis watchlist (Jevons Paradox):"]
    for r in rows:
        if r["price"] is None:
            continue
        scan_flag = " [IN SCAN — wheel-eligible]" if r["in_scan"] else ""
        move_flag = (
            f" [ALERT: {r['w1_chg']:+.1f}% this week]"
            if r["w1_chg"] is not None and abs(r["w1_chg"]) >= 10 else ""
        )
        lines.append(
            f"- {r['symbol']} ${r['price']} ({r['vs_52w_hi']:+.1f}% from 52w hi)"
            f"{scan_flag}{move_flag} — {r['thesis']}"
        )
    if scan_hits:
        lines.append(
            f"\nSCAN HITS this week: {', '.join(r['symbol'] for r in scan_hits)}"
            " — these passed CSP filters; flag in trading plan."
        )
    if big_movers:
        lines.append(
            f"NOTABLE MOVES: {', '.join(r['symbol'] for r in big_movers)}"
            " — flag any with relevant catalyst."
        )
    return "\n".join(lines)


# ── Wheel scan parameters (applied every trade-review run) ───────────────────
# min_market_cap_b: lowered to 1.5 (default 10.0) so beaten-down thesis names
#   ($2–9B range: GTLB, PATH, CFLT, ESTC, S, APPN, FSLY, BOX, MQ …) enter the scan.
# max_vol_pct: raised to 85.0 (was 65.0) — thesis plays are expected to have
#   elevated IV; capping at 65 was excluding exactly the names we want to find.
# max_beta: raised to 3.0 (default 2.4) — high-growth beaten-down names are
#   more volatile; the thesis is speculative by design.

_WHEEL_SCAN_PARAMS = ScannerParams(
    min_market_cap_b=1.5,
    max_vol_pct=85.0,
    max_beta=3.0,
    adr20_pct_min=3.5,
    min_days_to_earnings=20,
    max_rsi=70.0,
    min_adx=0.0,
    max_adx=100.0,
    min_fcf_b=None,
    max_debt_to_equity=None,
    min_revenue_growth=None,
    min_earnings_growth=None,
)

# ── Exhibits 2D + 2E: combined forecast + wheel trading plan (one LLM call) ───

_COMBINED_SYSTEM = """\
You are a macroeconomic analyst and professional options trader \
specializing in the Cash-Secured Put and Covered Call Wheel strategy. \
Speak in the third person. You do not mention your credentials. \
Do not use the word "provided" — use "recent" or "latest" instead. \
Do not recommend buying stock outright — only wheel-eligible options strategies. \
Do not recommend specific stocks or ETFs in the forecast section.\
"""

_PLAN_DELIMITER      = "---PLAN---"
_DISCOVERY_DELIMITER = "---DISCOVERY---"
_DISCOVERY_FALLBACK  = "_LLM discovery unavailable._"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_break = truncated.rfind("\n\n")
    truncated = truncated[:last_break] if last_break > max_chars // 2 else truncated
    return truncated.rstrip() + "\n\n_[output truncated]_"


def _build_combined_prompt(
    macro_context: str,
    snapshot: dict,
    top_candidates: list[dict],
    open_positions: list[dict],
    watchlist_rows: list[dict] | None = None,
) -> str:
    today_str = date.today().strftime("%B %d, %Y")
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
        thesis_tag = " [THESIS]" if sym in _THESIS_WATCHLIST else ""
        cand_lines.append(
            f"- {sym}{thesis_tag} | {sector} | IV:{iv} ADR:{adr} AnnROC:{ann_roc} "
            f"Δ{delta} {dte}DTE {strike}P | Beta:{beta} RSI:{rsi} WheelScore:{score}"
            + (f" | {thesis}" if thesis else "")
        )
    cands_str = "\n".join(cand_lines) or "No candidates from scan."
    positions_str = _format_positions_for_prompt(open_positions)
    watchlist_str = _format_watchlist_for_prompt(watchlist_rows) if watchlist_rows else ""

    return (
        f"Here is context to update your knowledge to the current date:\n\n"
        f"{macro_context}\n\n"
        f"Today is {today_str}. Current market: {spy_str}\n\n"
        f"Top wheel candidates from scan:\n{cands_str}\n\n"
        f"Current open wheel positions:\n{positions_str}\n\n"
        + (f"{watchlist_str}\n\n" if watchlist_str else "")
        + "## Part 1 — 30-Day Macro Forecast\n\n"
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
        "Then output the 30-day table. Keep Part 1 under 800 words.\n\n"
        f"After Part 1, output exactly this line on its own: {_PLAN_DELIMITER}\n\n"
        "## Part 2 — Monthly Wheel Trading Plan\n\n"
        "Based on the macro forecast you just wrote and the market conditions above, "
        "provide a complete monthly wheel trading plan for the next 30 days. "
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
        "| Ticker | Score | Sector | Ann ROC% | Type | Thesis (one sentence) |\n"
        "|--------|-------|--------|----------|------|-----------------------|\n"
        "[one row per candidate, max 10 rows; set Type = 'Wheel' for standard names "
        "or 'Thesis' for any candidate tagged [THESIS] above — thesis candidates are "
        "speculative, expect higher IV and beta, recommend 0.15–0.20 delta and "
        "smaller position size vs standard wheel names]\n\n"
        "**Open Position Actions:**\n"
        "| Ticker | Type | Strike | Expiry | DTE | uPnL | Action |\n"
        "|--------|------|--------|--------|-----|------|--------|\n"
        "[one row per open position; Action = Hold / Roll / Close with brief reason]\n\n"
        "Keep Part 2 under 1200 words. Be direct and specific.\n\n"
        f"After Part 2, output exactly this line on its own: {_DISCOVERY_DELIMITER}\n\n"
        "## Part 3 — New Thesis Candidate Discovery\n\n"
        "Using the AI-infrastructure / Jevons Paradox thesis above "
        "(AI calls existing software platforms as infrastructure, driving API and "
        "consumption revenue even as per-unit efficiency improves), identify 3–5 NEW "
        "companies NOT already on the watchlist above that may fit this thesis. "
        "Draw on any recent news or catalysts in the macro context you received.\n\n"
        "Criteria for inclusion:\n"
        "- Software, data, developer-tools, or communications company with an "
        "API/consumption-driven revenue model\n"
        "- Meaningfully below its all-time high (ideally ≥40% off peak)\n"
        "- Has NOT yet rerated to an AI-infrastructure multiple in the market\n"
        "- Bonus: a recent catalyst connects it to AI adoption or AI use of its platform\n\n"
        "Be contrarian and open-minded — avoid names the market has already rerated. "
        "Smaller-cap or overlooked names are welcome.\n\n"
        "Output a markdown table with columns:\n"
        "Ticker | Company | Why it fits the AI-infrastructure thesis | "
        "Recent catalyst or risk | Est. drawdown from ATH\n\n"
        "Keep Part 3 to 3–5 rows only."
    )


async def _generate_forecast_and_plan(
    macro_context: str,
    snapshot: dict,
    top_candidates: list[dict],
    open_positions: list[dict],
    watchlist_rows: list[dict] | None = None,
) -> tuple[str, str, str]:
    """Generate Exhibits 2D, 2E, and 2F in one LLM call.

    Returns:
        (forecast_text, plan_text, discovery_text) ready to drop into the note template.
    """
    logger.info("Macro note: generating forecast + plan + discovery (Exhibits 2D+2E+2F)")
    prompt = _build_combined_prompt(
        macro_context, snapshot, top_candidates, open_positions, watchlist_rows
    )
    result = await synthesize(_COMBINED_SYSTEM, prompt)

    fallback_forecast  = "_LLM forecast unavailable._"
    fallback_plan      = "_LLM regime assessment unavailable._"
    fallback_discovery = _DISCOVERY_FALLBACK

    if not result:
        return fallback_forecast, fallback_plan, fallback_discovery

    # Split on discovery delimiter first (it comes last), then plan delimiter.
    if _DISCOVERY_DELIMITER in result:
        body, discovery = result.split(_DISCOVERY_DELIMITER, maxsplit=1)
        discovery = discovery.strip()
    else:
        body      = result
        discovery = fallback_discovery
        logger.warning("Macro note: discovery delimiter missing from LLM output")

    if _PLAN_DELIMITER in body:
        parts    = body.split(_PLAN_DELIMITER, maxsplit=1)
        forecast = parts[0].strip()
        plan     = parts[1].strip()
    else:
        logger.warning("Macro note: plan delimiter missing — attempting heuristic split")
        for heading in ("**Regime:**", "## Part 2", "## Monthly"):
            if heading in body:
                idx      = body.index(heading)
                forecast = body[:idx].strip()
                plan     = body[idx:].strip()
                break
        else:
            logger.warning("Macro note: could not split output — treating all as plan")
            forecast = fallback_forecast
            plan     = body.strip()

    return _truncate(forecast, 6000), _truncate(plan, 8000), _truncate(discovery, 2000)


# ── Note renderer ─────────────────────────────────────────────────────────────

def _render_note(
    snapshot: dict,
    wiki: str,
    forecast: str,
    regime_plan: str,
    open_positions: list[dict],
    target_week: date,
    candidate_count: int,
    watchlist_rows: list[dict] | None = None,
    discovery_text: str = "",
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
    watchlist_section = (
        "\n\n---\n\n## AI Infrastructure Thesis Watchlist\n\n"
        "> Thesis: AI calls these software platforms as infrastructure (Jevons Paradox).\n"
        "> **Watch triggers:** ✓ in _In Scan_ column = wheel-eligible entry signal. "
        "1W Chg >±10% = investigate catalyst.\n\n"
        + _format_watchlist_for_note(watchlist_rows)
    ) if watchlist_rows else ""

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
{watchlist_section}
{f"""

---

## New Thesis Candidates — Discovery (Exhibit 2F)

> AI calls existing software as infrastructure (Jevons Paradox). LLM-suggested names
> outside the static watchlist, refreshed each week from current news + macro context.

{discovery_text}""" if discovery_text and discovery_text != _DISCOVERY_FALLBACK else ""}

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
         + fetch thesis watchlist snapshot (parallel with scoring)
      3. 30-day macro forecast (Exhibit 2D) + regime plan (Exhibit 2E)
         + new thesis candidate discovery (Exhibit 2F) — one combined LLM call
      4. Render + write complete note

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
    # Run watchlist snapshot in parallel with scoring — no dependency between them.
    scan_symbols = {c.get("symbol", "") for c in scan_candidates}
    logger.info(
        "Macro note: scoring %d candidates + fetching watchlist snapshot", len(scan_candidates)
    )
    top_candidates, watchlist_rows = await asyncio.gather(
        score_wheel_candidates(scan_candidates, top_n=10, macro_context=macro_str),
        asyncio.to_thread(_fetch_watchlist_snapshot, scan_symbols),
    )

    # Steps 4+5+6: forecast (2D) + regime plan (2E) + discovery (2F) — one combined call
    forecast, regime_plan, discovery_text = await _generate_forecast_and_plan(
        macro_str, snapshot, top_candidates, open_positions, watchlist_rows
    )

    # Step 7: Render + write
    note = _render_note(
        snapshot, wiki, forecast, regime_plan, open_positions,
        target_week, len(scan_candidates), watchlist_rows, discovery_text,
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
