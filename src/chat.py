"""Core trade chatbot logic — ticker detection, formatting, prompt building, LLM call."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from .screener.options_lookup import detect_options_intent, fetch_options_grid
from .screener.stocks import screen_stocks

logger = logging.getLogger(__name__)

TICKER_SKIP_WORDS: frozenset[str] = frozenset({
    "RSI", "IV", "ADR", "EMA", "SMA", "ATM", "CSP", "DTE", "VIX",
    "ROC", "OTM", "ITM", "VCP", "SPY", "QQQ", "IWM", "ETF", "OI",
    "FCF", "PEG", "USD", "CEO", "CFO", "LOL", "IMO", "FYI", "TBH",
    "DD", "TA", "FA", "ML", "AI", "API", "LLM", "EOD", "MA", "BB",
    "PE", "PL", "WTF", "IDK", "BTW", "PM", "AM", "EST", "PST",
})


def detect_tickers(text: str, universe: set[str]) -> list[str]:
    """Return unique tickers mentioned in text, in order of first mention.

    Pass 1 catches explicit $TICKER (bypasses universe check).
    Pass 2 catches bare UPPERCASE tokens that are in the screener universe.
    """
    found: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(r'\$([A-Z]{1,5})\b', text):
        t = match.group(1)
        if t not in seen:
            found.append(t)
            seen.add(t)

    for match in re.finditer(r'\b([A-Z]{2,5})\b', text):
        t = match.group(1)
        if t not in seen and t not in TICKER_SKIP_WORDS and t in universe:
            found.append(t)
            seen.add(t)

    return found


_THREAD_TITLE_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "on", "in", "to", "for", "my", "i",
    "of", "and", "or", "this", "that", "with", "about", "what", "do",
    "you", "think", "we", "should", "can", "would", "it", "be", "at",
    "so", "just", "will", "was", "were",
})


def build_thread_title(content: str, universe: set[str]) -> str:
    """Build a Discord thread title like "AAPL, TSLA: Earnings Pullback".

    Falls back to "Trade Chat — {date}" when no ticker or topic word
    survives filtering (e.g. a bare greeting with no tickers).
    """
    from datetime import datetime

    tickers = detect_tickers(content, universe)
    ticker_set = {t.upper() for t in tickers}

    topic_words: list[str] = []
    for word in re.findall(r"[a-zA-Z']+", content):
        if word.upper() in ticker_set or word.upper() in TICKER_SKIP_WORDS:
            continue
        if word.lower() in _THREAD_TITLE_STOPWORDS:
            continue
        topic_words.append(word.capitalize())
        if len(topic_words) == 3:
            break

    if tickers:
        prefix = ", ".join(tickers[:3])
        title = f"{prefix}: {' '.join(topic_words)}" if topic_words else prefix
    elif topic_words:
        title = " ".join(topic_words)
    else:
        title = f"Trade Chat — {datetime.now().strftime('%b %d')}"

    return title[:100]


def format_screener_block(ticker: str, data: dict) -> str:
    """Render a compact screener data block for injection into the LLM prompt."""
    from datetime import date

    lines = [f"[{ticker} — live data, {date.today()}]"]

    # Price + performance
    price = data.get("price")
    if price not in (None, "N/A"):
        perf_parts = []
        for key, label in (("pct_1d", "1d"), ("pct_1w", "1w"), ("pct_1m", "1m")):
            v = data.get(key)
            if v not in (None, "N/A"):
                perf_parts.append(f"{float(v):+.1f}% {label}")
        perf = f" ({', '.join(perf_parts)})" if perf_parts else ""
        lines.append(f"Price: ${price}{perf}")

    # TA
    ta_parts = []
    rsi = data.get("rsi")
    if rsi not in (None, "N/A"):
        ta_parts.append(f"RSI: {rsi}")
    bb_w = data.get("bb_width_pct")
    if bb_w not in (None, "N/A"):
        bb_u = data.get("bb_upper")
        bb_l = data.get("bb_lower")
        detail = (
            f" (upper: ${bb_u} / lower: ${bb_l})"
            if bb_u not in (None, "N/A") and bb_l not in (None, "N/A")
            else ""
        )
        ta_parts.append(f"BB width: {bb_w}%{detail}")
    if ta_parts:
        lines.append(" | ".join(ta_parts))

    # Moving averages
    ma_parts = []
    for key, pct_key, label in (
        ("sma_200", "sma_200_pct", "SMA200"),
        ("ema_200", "ema_200_pct", "EMA200"),
    ):
        val = data.get(key)
        pct = data.get(pct_key)
        if val not in (None, "N/A") and pct not in (None, "N/A"):
            ma_parts.append(f"{label}: ${val} ({float(pct):+.1f}%)")
    sma_50 = data.get("sma_50")
    if sma_50 not in (None, "N/A"):
        ma_parts.append(f"SMA50: ${sma_50}")
    if ma_parts:
        lines.append(" | ".join(ma_parts))

    # 52wk / volume / ADR
    misc_parts = []
    pfh = data.get("pct_from_52wk_high")
    if pfh not in (None, "N/A"):
        misc_parts.append(f"vs 52wk high: {float(pfh):+.1f}%")
    vr = data.get("volume_ratio")
    if vr not in (None, "N/A"):
        misc_parts.append(f"Vol ratio: {vr}")
    adr = data.get("adr20")
    if adr not in (None, "N/A"):
        misc_parts.append(f"ADR20: {adr}%")
    if misc_parts:
        lines.append(" | ".join(misc_parts))

    # IV
    iv_parts = []
    for key, label, suffix in (
        ("atm_iv", "IV (ATM)", "%"),
        ("iv_percentile", "IV pct", "%"),
        ("atm_iv_rv20", "IV/RV", ""),
        ("rv20", "RV20", "%"),
    ):
        v = data.get(key)
        if v not in (None, "N/A"):
            iv_parts.append(f"{label}: {v}{suffix}")
    if iv_parts:
        lines.append(" | ".join(iv_parts))

    # Sector / market cap / beta
    meta_parts = []
    sector = data.get("sector")
    if sector and sector != "N/A":
        meta_parts.append(f"Sector: {sector}")
    mcap = data.get("market_cap")
    if mcap not in (None, "N/A"):
        try:
            m = float(mcap)
            if m >= 1e12:
                meta_parts.append(f"Mkt cap: ${m / 1e12:.2f}T")
            elif m >= 1e9:
                meta_parts.append(f"Mkt cap: ${m / 1e9:.1f}B")
            else:
                meta_parts.append(f"Mkt cap: ${m / 1e6:.0f}M")
        except (TypeError, ValueError):
            pass
    beta = data.get("beta")
    if beta not in (None, "N/A"):
        meta_parts.append(f"Beta: {beta}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    # Valuation
    val_parts = []
    for key, label in (("pe", "PE"), ("forward_pe", "Fwd PE"), ("peg_ratio", "PEG")):
        v = data.get(key)
        if v not in (None, "N/A"):
            val_parts.append(f"{label}: {v}")
    if val_parts:
        lines.append("Valuation: " + " | ".join(val_parts))

    # Fundamentals
    fund_parts = []
    for key, label in (
        ("eps_growth", "EPS growth"),
        ("revenue_growth", "Rev growth"),
    ):
        v = data.get(key)
        if v not in (None, "N/A"):
            try:
                fund_parts.append(f"{label}: {float(v):+.0f}%")
            except (TypeError, ValueError):
                pass
    fcf = data.get("fcf")
    if fcf not in (None, "N/A"):
        fund_parts.append(f"FCF: ${fcf}B")
    de = data.get("debt_to_equity")
    if de not in (None, "N/A"):
        fund_parts.append(f"D/E: {de}")
    if fund_parts:
        lines.append("Fundamentals: " + " | ".join(fund_parts))

    return "\n".join(lines)


def format_options_block(ticker: str, option_type: str, rows: list[dict]) -> str:
    """Render a compact live options grid for injection into the LLM prompt."""
    from datetime import date

    if not rows:
        return f"[{ticker}: no options data available]"

    suffix = "C" if option_type == "call" else "P"
    label = "calls" if option_type == "call" else "puts"

    lines = [f"[{ticker} — live {label} chain, {date.today()}]"]

    by_expiration: dict[date, list[dict]] = {}
    for row in rows:
        by_expiration.setdefault(row["expiration"], []).append(row)

    for expiration in sorted(by_expiration):
        exp_rows = by_expiration[expiration]
        dte = exp_rows[0]["dte"]
        contract_parts = []
        for row in exp_rows:
            strike_str = f"{row['strike']:g}{suffix}"
            part = f"{strike_str} {row['bid']:.2f}/{row['ask']:.2f} (mid {row['mid']:.2f})"
            if row.get("iv") is not None:
                part += f" IV {row['iv']:.0f}%"
            if row.get("delta") is not None:
                part += f" Δ{row['delta']:.2f}"
            if row.get("spread_pct") is not None and row["spread_pct"] > 20:
                part += " (wide spread)"
            contract_parts.append(part)
        lines.append(
            f"Exp {expiration.month}/{expiration.day} ({dte} DTE): "
            + " | ".join(contract_parts)
        )

    return "\n".join(lines)


async def gather_chat_blocks(tickers: list[str], message_content: str) -> list[str]:
    """Fetch screener data, and live options grid data when relevant, per ticker.

    Returns formatted blocks ready for injection via build_prompt(). Options
    data is only fetched for tickers whose screener call succeeded and
    returned a usable numeric price — fetch_options_grid() needs the live
    price to size its strike window — and only when detect_options_intent()
    finds options-related language in the message.
    """
    if not tickers:
        return []

    blocks: list[str] = []
    prices: dict[str, float] = {}

    screener_results = await asyncio.gather(
        *[asyncio.to_thread(screen_stocks, [t], False) for t in tickers],
        return_exceptions=True,
    )
    for ticker, result in zip(tickers, screener_results):
        if isinstance(result, Exception) or not result:
            blocks.append(f"[{ticker}: data unavailable]")
            continue
        data = result[0]
        blocks.append(format_screener_block(ticker, data))
        price = data.get("price")
        if isinstance(price, int | float) and price > 0:
            prices[ticker] = float(price)

    options_intent = detect_options_intent(message_content)
    if options_intent and prices:
        grid_tickers = list(prices.keys())
        grid_results = await asyncio.gather(
            *[
                asyncio.to_thread(fetch_options_grid, t, prices[t], options_intent)
                for t in grid_tickers
            ],
            return_exceptions=True,
        )
        for ticker, rows in zip(grid_tickers, grid_results):
            if isinstance(rows, Exception):
                continue
            blocks.append(format_options_block(ticker, options_intent, rows))

    return blocks


def build_prompt(
    system_prompt: str,
    history: list[dict],
    user_message: str,
    screener_blocks: list[str],
) -> str:
    """Assemble the full prompt for `claude -p`.

    Format: system prompt → conversation history (alternating User/Assistant) →
    current user message with screener data injected → trailing 'Assistant:' cue.
    """
    parts = [system_prompt.strip(), "---"]

    for turn in history:
        label = "User" if turn["role"] == "user" else "Assistant"
        parts.append(f"{label}: {turn['content']}")

    current = user_message
    if screener_blocks:
        current += "\n\n" + "\n\n".join(screener_blocks)
    parts.append(f"User: {current}")
    parts.append("Assistant:")

    return "\n\n".join(parts)


_MCP_CONFIG_PATH = Path(__file__).parent.parent / "discord_bot" / "alpaca-mcp.json"

# Must stay in sync with ALPACA_TOOLSETS=options-data,assets,stock-data in
# discord_bot/alpaca-mcp.json — --allowedTools has no MCP wildcard support
# (mcp__alpaca__* is not accepted), so every enabled tool is listed by name.
_ALPACA_ALLOWED_TOOLS: tuple[str, ...] = (
    # options-data toolset
    "mcp__alpaca__get_option_chain",
    "mcp__alpaca__get_option_snapshot",
    "mcp__alpaca__get_option_latest_quote",
    "mcp__alpaca__get_option_latest_trade",
    "mcp__alpaca__get_option_bars",
    "mcp__alpaca__get_option_trades",
    "mcp__alpaca__get_option_exchange_codes",
    # assets toolset
    "mcp__alpaca__get_option_contracts",
    "mcp__alpaca__get_option_contract",
    "mcp__alpaca__get_all_assets",
    "mcp__alpaca__get_asset",
    "mcp__alpaca__get_calendar",
    "mcp__alpaca__get_clock",
    "mcp__alpaca__get_corporate_action_announcements",
    "mcp__alpaca__get_corporate_action_announcement",
    # stock-data toolset
    "mcp__alpaca__get_stock_bars",
    "mcp__alpaca__get_stock_quotes",
    "mcp__alpaca__get_stock_trades",
    "mcp__alpaca__get_stock_latest_bar",
    "mcp__alpaca__get_stock_latest_quote",
    "mcp__alpaca__get_stock_latest_trade",
    "mcp__alpaca__get_stock_snapshot",
    "mcp__alpaca__get_most_active_stocks",
    "mcp__alpaca__get_market_movers",
)

_SCHWAB_MCP_CONFIG_PATH = Path(__file__).parent.parent / "discord_bot" / "schwab-mcp.json"

# Read-only account/market-data tools only — no preview_*/place_*/cancel_*
# tool is ever listed here, regardless of what the schwab-mcp server exposes.
# See docs/superpowers/specs/2026-07-15-schwab-mcp-trade-chat-design.md.
_SCHWAB_ALLOWED_TOOLS: tuple[str, ...] = (
    # positions/balances
    "mcp__schwab__get_accounts",
    "mcp__schwab__get_account",
    # orders/transactions
    "mcp__schwab__get_orders",
    "mcp__schwab__get_order",
    "mcp__schwab__get_transactions",
    "mcp__schwab__get_transaction",
    # quotes/chains
    "mcp__schwab__get_quotes",
    "mcp__schwab__get_option_chain",
    "mcp__schwab__get_advanced_option_chain",
    "mcp__schwab__get_option_expiration_chain",
    "mcp__schwab__get_advanced_price_history",
    "mcp__schwab__get_movers",
    "mcp__schwab__get_market_hours",
    "mcp__schwab__get_instruments",
    "mcp__schwab__create_option_symbol",
    "mcp__schwab__get_datetime",
)

_MI_MCP_CONFIG_PATH = Path(__file__).parent.parent / "discord_bot" / "mi-mcp.json"

# Austin's own Market Intelligence watchlist/scanner data — see src/mi_client.py
# for the underlying HTTP calls and discord_bot/mi_mcp_server.py for the MCP
# wrapper. Read-only, no arguments, no auth (internal Docker network only).
_MI_ALLOWED_TOOLS: tuple[str, ...] = (
    "mcp__mi__get_csp_watchlist",
    "mcp__mi__get_stock_watchlist",
    "mcp__mi__get_csp_candidates",
    "mcp__mi__get_leaps_candidates",
    "mcp__mi__get_market_posture",
)


async def call_claude_chat(prompt: str, timeout: int = 240) -> str | None:
    """Call `claude -p` with the assembled prompt. Returns None on any failure.

    240s, not the original 120s: live reproduction (2026-07-16) of a
    no-named-ticker "screen for candidates" prompt showed the model
    correctly using ToolSearch + several Alpaca tool calls to answer with
    real data, but reliably taking ~130-140s to do so — past the old 120s
    timeout on every trial. That silently discarded the correct answer and
    fell through to the tool-less Gemini fallback in
    discord_bot/commands/chat.py, which fabricated ticker-specific numbers.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--mcp-config",
            str(_MCP_CONFIG_PATH),
            str(_SCHWAB_MCP_CONFIG_PATH),
            str(_MI_MCP_CONFIG_PATH),
            # ToolSearch is required for the model to discover MCP tool schemas
            # at all — without it, mcp__alpaca__*/mcp__schwab__* tools connect
            # successfully (server-side "hasTools:true") but the model has no
            # way to learn they exist, and silently never calls them. Found via
            # live reproduction (2026-07-15): identical prompts reliably failed
            # to call any Schwab tool without ToolSearch (3/3) and reliably
            # succeeded with it (2/2, correct real data both times).
            "--tools", "WebSearch", "ToolSearch",
            "--allowedTools",
            "WebSearch",
            "ToolSearch",
            *_ALPACA_ALLOWED_TOOLS,
            *_SCHWAB_ALLOWED_TOOLS,
            *_MI_ALLOWED_TOOLS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()), timeout=timeout
        )
        if proc.returncode == 0:
            output = stdout_bytes.decode().strip()
            if output:
                return output
            logger.warning("chat: claude -p returned empty output")
            return None
        logger.warning(
            "chat: claude -p exited with code %d — stdout: %s | stderr: %s",
            proc.returncode,
            stdout_bytes.decode().strip()[:300],
            stderr_bytes.decode().strip()[:300],
        )
        return None
    except TimeoutError:
        logger.warning("chat: claude -p timed out after %ds", timeout)
        return None
    except FileNotFoundError:
        logger.warning("chat: 'claude' binary not found")
        return None
    except Exception as exc:
        logger.exception("chat: claude -p unexpected error: %s", exc)
        return None
