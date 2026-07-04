"""Core trade chatbot logic — ticker detection, formatting, prompt building, LLM call."""

from __future__ import annotations

import asyncio
import logging
import re

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
        if t not in seen and t not in TICKER_SKIP_WORDS:
            found.append(t)
            seen.add(t)

    return found


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


async def call_claude_chat(prompt: str, timeout: int = 120) -> str | None:
    """Call `claude -p` with the assembled prompt. Returns None on any failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()), timeout=timeout
        )
        if proc.returncode == 0:
            output = stdout_bytes.decode().strip()
            return output if output else None
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
