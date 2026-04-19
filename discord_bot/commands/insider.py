"""Slash command: /insider — detailed insider trading and congressional trade breakdown."""

from __future__ import annotations

import logging
import os
from collections import defaultdict

import discord
import httpx
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

API_BASE = os.getenv("MARKET_INTELLIGENCE_API_URL", "http://127.0.0.1:8000")
BOT_SECRET = os.getenv("DISCORD_BOT_SECRET", "")


class InsiderCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="insider",
        description="Show detailed insider trading and congressional trade activity",
    )
    @app_commands.describe(
        view="Which data to show",
        ticker="Filter by a specific ticker symbol (optional)",
    )
    @app_commands.choices(view=[
        app_commands.Choice(name="Overview (both)",        value="overview"),
        app_commands.Choice(name="Corporate Insiders",     value="insiders"),
        app_commands.Choice(name="Congressional Trades",   value="congress"),
        app_commands.Choice(name="Convergence Alert",      value="convergence"),
    ])
    async def insider(
        self,
        interaction: discord.Interaction,
        view: str = "overview",
        ticker: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{API_BASE}/api/insider",
                    headers={"x-bot-token": BOT_SECRET},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            await interaction.followup.send(
                embed=_error_embed(f"API error {e.response.status_code}: {e.response.text[:300]}")
            )
            return
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(str(e)))
            return

        insider_data = data.get("insider_trading")
        congress_data = data.get("congressional_trades")
        ticker = ticker.upper() if ticker else None

        if view == "overview":
            embeds = []
            if insider_data:
                embeds.append(_build_insider_embed(insider_data, ticker_filter=ticker))
            if congress_data:
                embeds.append(_build_congress_embed(congress_data, ticker_filter=ticker))
            if not embeds:
                await interaction.followup.send("No insider or congressional data available yet. Run `/scan` first.")
                return
            for embed in embeds:
                await interaction.followup.send(embed=embed)

        elif view == "insiders":
            if not insider_data:
                await interaction.followup.send("No insider trading data available yet. Run `/scan` first.")
                return
            await interaction.followup.send(embed=_build_insider_embed(insider_data, ticker_filter=ticker, detailed=True))

        elif view == "congress":
            if not congress_data:
                await interaction.followup.send("No congressional trade data available yet. Run `/scan` first.")
                return
            await interaction.followup.send(embed=_build_congress_embed(congress_data, ticker_filter=ticker, detailed=True))

        elif view == "convergence":
            embed = _build_convergence_embed(insider_data, congress_data, ticker_filter=ticker)
            await interaction.followup.send(embed=embed)


# ── Embed builders ────────────────────────────────────────────────────────────

def _aggregate_ticker(txns: list[dict]) -> dict:
    """Compute aggregate stats for a list of transactions under one ticker."""
    total_shares = sum(t.get("shares", 0) or 0 for t in txns)
    total_value = sum(t.get("value", 0) or 0 for t in txns)
    unique_names = list(dict.fromkeys(t.get("name", "?") for t in txns))  # ordered, deduped
    # Sort transactions by dollar value descending so biggest shows first
    sorted_txns = sorted(txns, key=lambda t: t.get("value", 0) or 0, reverse=True)
    return {
        "total_shares": total_shares,
        "total_value": total_value,
        "unique_names": unique_names,
        "unique_count": len(unique_names),
        "txn_count": len(txns),
        "sorted_txns": sorted_txns,
    }


def _fmt_value(v: int | float) -> str:
    """Format a dollar value compactly: $1.2M, $386K, etc."""
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:,.0f}"


def _build_insider_embed(
    data: dict,
    ticker_filter: str | None = None,
    detailed: bool = False,
) -> discord.Embed:
    meta = data.get("metadata", {})
    buy_tickers: dict = meta.get("buy_tickers", {})
    sell_tickers: dict = meta.get("sell_tickers", {})
    lookback = meta.get("lookback_days", 7)
    cached_at = data.get("cached_at", "unknown")[:10]

    # Apply ticker filter
    if ticker_filter:
        buy_tickers = {k: v for k, v in buy_tickers.items() if k == ticker_filter}
        sell_tickers = {k: v for k, v in sell_tickers.items() if k == ticker_filter}

    total_buys = sum(len(v) for v in buy_tickers.values())
    total_sells = sum(len(v) for v in sell_tickers.values())
    total_buy_value = sum(t.get("value", 0) or 0 for txns in buy_tickers.values() for t in txns)
    total_sell_value = sum(t.get("value", 0) or 0 for txns in sell_tickers.values() for t in txns)
    unique_buyers = len({t.get("name") for txns in buy_tickers.values() for t in txns})
    unique_sellers = len({t.get("name") for txns in sell_tickers.values() for t in txns})

    if total_buys > total_sells:
        color = discord.Color.green()
        trend = "🟢 Net Buying"
    elif total_sells > total_buys:
        color = discord.Color.red()
        trend = "🔴 Net Selling"
    else:
        color = discord.Color.light_gray()
        trend = "🟡 Balanced"

    title = f"🏢 Corporate Insider Trades — {trend}"
    if ticker_filter:
        title += f" ({ticker_filter})"

    embed = discord.Embed(
        title=title,
        description=(
            f"Open-market Form 4 filings · past **{lookback} days** · as of {cached_at}\n"
            f"🟢 **{unique_buyers} buyer{'s' if unique_buyers != 1 else ''}**, {total_buys} txn(s) across {len(buy_tickers)} ticker(s) — **{_fmt_value(total_buy_value)} total**\n"
            f"🔴 **{unique_sellers} seller{'s' if unique_sellers != 1 else ''}**, {total_sells} txn(s) across {len(sell_tickers)} ticker(s) — **{_fmt_value(total_sell_value)} total**"
        ),
        color=color,
    )

    # Buys section
    if buy_tickers:
        sorted_buys = sorted(buy_tickers.items(), key=lambda x: len(x[1]), reverse=True)
        limit = len(sorted_buys) if detailed else min(5, len(sorted_buys))
        for sym, txns in sorted_buys[:limit]:
            agg = _aggregate_ticker(txns)
            if detailed:
                # Show every transaction sorted by dollar value
                lines = []
                for t in agg["sorted_txns"]:
                    val = _fmt_value(t["value"]) if t.get("value") else "undisclosed"
                    lines.append(f"• **{t.get('name', '?')}** — {t.get('shares', 0):,} shares ({val}) on {t.get('date', '?')}")
                field_val = "\n".join(lines)
            else:
                # Overview: one summary line + top seller by value
                top = agg["sorted_txns"][0]
                top_val = _fmt_value(top["value"]) if top.get("value") else "undisclosed"
                names_str = ", ".join(agg["unique_names"][:3])
                if agg["unique_count"] > 3:
                    names_str += f" +{agg['unique_count'] - 3} more"
                field_val = (
                    f"**{agg['unique_count']} seller{'s' if agg['unique_count'] != 1 else ''}** — "
                    f"{agg['total_shares']:,} shares — **{_fmt_value(agg['total_value'])} total**\n"
                    f"Biggest: **{top.get('name', '?')}** {top.get('shares', 0):,} sh ({top_val})\n"
                    f"*{names_str}*"
                )
            embed.add_field(
                name=f"🟢 {sym} — {agg['txn_count']} buy{'s' if agg['txn_count'] != 1 else ''} ({_fmt_value(agg['total_value'])})",
                value=field_val[:1020],
                inline=False,
            )

    # Sells section
    if sell_tickers:
        sorted_sells = sorted(sell_tickers.items(), key=lambda x: sum(t.get("value", 0) or 0 for t in x[1]), reverse=True)
        limit = len(sorted_sells) if detailed else min(5, len(sorted_sells))
        for sym, txns in sorted_sells[:limit]:
            agg = _aggregate_ticker(txns)
            if detailed:
                lines = []
                for t in agg["sorted_txns"]:
                    val = _fmt_value(t["value"]) if t.get("value") else "undisclosed"
                    lines.append(f"• **{t.get('name', '?')}** — {t.get('shares', 0):,} shares ({val}) on {t.get('date', '?')}")
                field_val = "\n".join(lines)
            else:
                top = agg["sorted_txns"][0]
                top_val = _fmt_value(top["value"]) if top.get("value") else "undisclosed"
                names_str = ", ".join(agg["unique_names"][:3])
                if agg["unique_count"] > 3:
                    names_str += f" +{agg['unique_count'] - 3} more"
                field_val = (
                    f"**{agg['unique_count']} seller{'s' if agg['unique_count'] != 1 else ''}** — "
                    f"{agg['total_shares']:,} shares — **{_fmt_value(agg['total_value'])} total**\n"
                    f"Biggest: **{top.get('name', '?')}** {top.get('shares', 0):,} sh ({top_val})\n"
                    f"*{names_str}*"
                )
            embed.add_field(
                name=f"🔴 {sym} — {agg['txn_count']} sell{'s' if agg['txn_count'] != 1 else ''} ({_fmt_value(agg['total_value'])})",
                value=field_val[:1020],
                inline=False,
            )

    if not buy_tickers and not sell_tickers:
        embed.add_field(
            name="No activity",
            value=f"No open-market exec trades found for {'`' + ticker_filter + '`' if ticker_filter else 'watchlist'} in the past {lookback} days.",
            inline=False,
        )

    embed.set_footer(text="Source: Finnhub / SEC Form 4 · Open-market P/S codes only")
    return embed


def _build_congress_embed(
    data: dict,
    ticker_filter: str | None = None,
    detailed: bool = False,
) -> discord.Embed:
    meta = data.get("metadata", {})
    buys: dict = meta.get("buys_by_ticker", {})
    sells: dict = meta.get("sells_by_ticker", {})
    high_profile: list = meta.get("high_profile_trades", [])
    lookback = meta.get("lookback_days", 30)
    cached_at = data.get("cached_at", "unknown")[:10]

    if ticker_filter:
        buys = {k: v for k, v in buys.items() if k == ticker_filter}
        sells = {k: v for k, v in sells.items() if k == ticker_filter}
        high_profile = [t for t in high_profile if ticker_filter in t]

    total_buys = sum(len(v) for v in buys.values())
    total_sells = sum(len(v) for v in sells.values())

    if total_buys > total_sells:
        color = discord.Color.green()
        trend = "🟢 Net Purchasing"
    elif total_sells > total_buys:
        color = discord.Color.red()
        trend = "🔴 Net Selling"
    else:
        color = discord.Color.light_gray()
        trend = "🟡 Balanced"

    title = f"🏛️ Congressional Trades — {trend}"
    if ticker_filter:
        title += f" ({ticker_filter})"

    embed = discord.Embed(
        title=title,
        description=(
            f"STOCK Act disclosures · past **{lookback} days** · as of {cached_at}\n"
            f"**{total_buys}** purchase(s) across **{len(buys)}** ticker(s)  |  "
            f"**{total_sells}** sale(s) across **{len(sells)}** ticker(s)"
        ),
        color=color,
    )

    # High-profile trades always shown first
    if high_profile:
        embed.add_field(
            name="⭐ Notable Politicians",
            value="\n".join(f"• {t}" for t in high_profile[:8]),
            inline=False,
        )

    # Purchases
    if buys:
        sorted_buys = sorted(buys.items(), key=lambda x: len(x[1]), reverse=True)
        limit = len(sorted_buys) if detailed else min(5, len(sorted_buys))
        for sym, txns in sorted_buys[:limit]:
            lines = []
            for t in txns[:4]:
                amount = t.get("amount", "undisclosed")
                chamber = t.get("chamber", "")
                lines.append(f"• **{t.get('politician', '?')}** ({chamber}) — {amount} on {t.get('date', '?')}")
            if len(txns) > 4:
                lines.append(f"*…and {len(txns) - 4} more*")
            embed.add_field(name=f"🟢 {sym} ({len(txns)} purchase{'s' if len(txns) > 1 else ''})", value="\n".join(lines), inline=False)

    # Sales
    if sells:
        sorted_sells = sorted(sells.items(), key=lambda x: len(x[1]), reverse=True)
        limit = len(sorted_sells) if detailed else min(3, len(sorted_sells))
        for sym, txns in sorted_sells[:limit]:
            lines = []
            for t in txns[:4]:
                amount = t.get("amount", "undisclosed")
                chamber = t.get("chamber", "")
                lines.append(f"• **{t.get('politician', '?')}** ({chamber}) — {amount} on {t.get('date', '?')}")
            if len(txns) > 4:
                lines.append(f"*…and {len(txns) - 4} more*")
            embed.add_field(name=f"🔴 {sym} ({len(txns)} sale{'s' if len(txns) > 1 else ''})", value="\n".join(lines), inline=False)

    if not buys and not sells:
        embed.add_field(
            name="No activity",
            value=f"No congressional trades found for {'`' + ticker_filter + '`' if ticker_filter else 'watchlist'} in the past {lookback} days.",
            inline=False,
        )

    embed.set_footer(text="Source: House/Senate Stock Watcher · STOCK Act disclosures (up to 45d lag)")
    return embed


def _build_convergence_embed(
    insider_data: dict | None,
    congress_data: dict | None,
    ticker_filter: str | None = None,
) -> discord.Embed:
    """Show tickers where both insiders and politicians are active on the same side."""
    if not insider_data or not congress_data:
        return discord.Embed(
            title="🚨 Convergence Check",
            description="Insufficient data — run `/scan` first to populate insider and congressional data.",
            color=discord.Color.orange(),
        )

    insider_buys = set(insider_data.get("metadata", {}).get("buy_tickers", {}).keys())
    insider_sells = set(insider_data.get("metadata", {}).get("sell_tickers", {}).keys())
    congress_buys = set(congress_data.get("metadata", {}).get("buys_by_ticker", {}).keys())
    congress_sells = set(congress_data.get("metadata", {}).get("sells_by_ticker", {}).keys())

    if ticker_filter:
        insider_buys = {ticker_filter} & insider_buys
        insider_sells = {ticker_filter} & insider_sells
        congress_buys = {ticker_filter} & congress_buys
        congress_sells = {ticker_filter} & congress_sells

    buy_overlap = sorted(insider_buys & congress_buys)
    sell_overlap = sorted(insider_sells & congress_sells)

    has_signal = bool(buy_overlap or sell_overlap)
    color = discord.Color.gold() if buy_overlap else (discord.Color.red() if sell_overlap else discord.Color.light_gray())

    embed = discord.Embed(
        title="🚨 Insider + Congressional Convergence",
        description=(
            "Tickers where **both** corporate insiders (Form 4) and politicians (STOCK Act) "
            "are trading on the **same side** — historically a high-conviction signal."
        ) if has_signal else "No convergence detected on current watchlist data.",
        color=color,
    )

    if buy_overlap:
        lines = []
        for sym in buy_overlap:
            exec_count = len(insider_data["metadata"]["buy_tickers"].get(sym, []))
            pol_count = len(congress_data["metadata"]["buys_by_ticker"].get(sym, []))
            lines.append(f"🟢 **{sym}** — {exec_count} exec buy(s) + {pol_count} congressional purchase(s)")
        embed.add_field(name="📈 Bullish Convergence", value="\n".join(lines), inline=False)

    if sell_overlap:
        lines = []
        for sym in sell_overlap:
            exec_count = len(insider_data["metadata"]["sell_tickers"].get(sym, []))
            pol_count = len(congress_data["metadata"]["sells_by_ticker"].get(sym, []))
            lines.append(f"🔴 **{sym}** — {exec_count} exec sell(s) + {pol_count} congressional sale(s)")
        embed.add_field(name="📉 Bearish Convergence", value="\n".join(lines), inline=False)

    if not has_signal:
        embed.add_field(
            name="All clear",
            value="No tickers show simultaneous insider + congressional activity in the same direction.",
            inline=False,
        )

    embed.set_footer(text="Note: congressional data has up to 45-day disclosure lag")
    return embed


def _error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="❌ Error",
        description=f"```{message[:1000]}```",
        color=discord.Color.red(),
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InsiderCommands(bot))
