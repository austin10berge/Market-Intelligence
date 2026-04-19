"""Discord embed builders — maps ScoredSignal data to rich Discord embeds."""

from __future__ import annotations

import discord

# Maps signal direction to a colored circle emoji
DIRECTION_EMOJI = {
    "bullish": "🟢",
    "neutral": "🟡",
    "bearish": "🔴",
}

# Maps market posture string to a Discord embed color
POSTURE_COLOR: dict[str, discord.Color] = {
    "Strongly Bullish":   discord.Color.from_rgb(0, 200, 100),
    "Bullish":            discord.Color.green(),
    "Cautiously Bullish": discord.Color.from_rgb(144, 238, 144),
    "Neutral":            discord.Color.light_gray(),
    "Cautiously Bearish": discord.Color.orange(),
    "Bearish":            discord.Color.red(),
    "Strongly Bearish":   discord.Color.dark_red(),
}

# Human-readable labels for each signal source
SOURCE_LABEL: dict[str, str] = {
    "fear_greed":     "😱 Fear & Greed",
    "vix":            "📉 VIX",
    "put_call":       "🔄 Put/Call Ratio",
    "sector_etf":     "🏭 Sector Rotation",
    "gex":            "⚙️ GEX (Gamma Exposure)",
    "credit_spreads": "💳 Credit Spreads",
    "liquidity":      "💧 Liquidity",
}


def build_result_embed(result: dict) -> discord.Embed:
    """Build a rich embed from a completed pipeline result dict."""
    posture = result.get("posture", "Unknown")
    composite = result.get("composite_score", 0.0)
    signals = result.get("signals", [])
    llm_summary = result.get("llm_summary", "")
    extreme_count = result.get("extreme_count", 0)
    scan_date = result.get("date", "Unknown date")

    # Header description
    description_lines = [
        f"**Composite Score:** `{composite:+.3f}`  *(range: −1.0 to +1.0)*",
    ]
    if extreme_count > 0:
        description_lines.append(f"⚠️ **{extreme_count} extreme reading(s) detected**")

    embed = discord.Embed(
        title=f"📊 Market Sentiment Scan — {posture}",
        description="\n".join(description_lines),
        color=POSTURE_COLOR.get(posture, discord.Color.greyple()),
        timestamp=discord.utils.utcnow(),
    )

    # One field per signal
    for s in signals:
        direction = s.get("direction", "neutral")
        extreme = s.get("extreme", False)
        source = s.get("source", "")
        summary = s.get("summary") or s.get("reasoning", "No data.")

        emoji = DIRECTION_EMOJI.get(direction, "⚪")
        label = SOURCE_LABEL.get(source, source.replace("_", " ").title())
        extreme_flag = "  ⚠️" if extreme else ""

        embed.add_field(
            name=f"{emoji} {label}{extreme_flag}",
            value=summary[:1020],  # Discord field value limit
            inline=False,
        )

    # LLM digest section
    if llm_summary and "Unable to generate" not in llm_summary and "LLM unavailable" not in llm_summary:
        embed.add_field(
            name="🤖 AI Analysis",
            value=llm_summary[:1020],
            inline=False,
        )

    embed.set_footer(text=f"Scan date: {scan_date}")
    return embed


def build_error_embed(message: str) -> discord.Embed:
    """Build a simple error embed."""
    return discord.Embed(
        title="❌ Scan Failed",
        description=f"```{message[:1000]}```",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
