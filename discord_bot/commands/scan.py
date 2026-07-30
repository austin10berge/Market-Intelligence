"""Slash commands: /scan, /scan-history, /scan-status."""

from __future__ import annotations

import logging
import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

API_BASE = os.getenv("MARKET_INTELLIGENCE_API_URL", "http://127.0.0.1:8000")
BOT_SECRET = os.getenv("DISCORD_BOT_SECRET", "")
# The callback server listens on this port so FastAPI can POST results back
CALLBACK_PORT = int(os.getenv("DISCORD_BOT_CALLBACK_PORT", "9000"))


class ScanCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /scan ────────────────────────────────────────────────────────────────

    @app_commands.command(name="scan", description="Trigger a full market sentiment scan")
    async def scan(self, interaction: discord.Interaction) -> None:
        """Triggers the pipeline and posts results back when complete."""
        await interaction.response.defer(thinking=True)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{API_BASE}/api/scan/trigger",
                    json={
                        "channel_id": str(interaction.channel_id),
                        "requested_by": str(interaction.user),
                    },
                    headers={
                        "x-bot-token": BOT_SECRET,
                        "x-bot-callback-url": f"http://discord-bot:{CALLBACK_PORT}",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(
                embed=_error_embed(f"API returned {e.response.status_code}: {e.response.text}")
            )
            return
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(str(e)))
            return

        if payload.get("status") == "already_running":
            embed = discord.Embed(
                title="⏳ Scan Already Running",
                description=(
                    "A scan is already in progress — hang tight, results will "
                    "post to this channel shortly."
                ),
                color=discord.Color.orange(),
            )
        else:
            embed = discord.Embed(
                title="⏳ Scan Queued",
                description=(
                    f"Full pipeline is running. Results will appear in this channel "
                    f"in ~30–60 seconds.\n\n*Requested by {interaction.user.mention}*"
                ),
                color=discord.Color.blue(),
            )
        await interaction.followup.send(embed=embed)

    # ── /scan-history ────────────────────────────────────────────────────────

    @app_commands.command(name="scan-history", description="Show recent scan results")
    @app_commands.describe(count="Number of past results to show (1–10)")
    async def scan_history(
        self, interaction: discord.Interaction, count: int = 5
    ) -> None:
        await interaction.response.defer(thinking=True)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{API_BASE}/api/scan/history",
                    params={"limit": min(max(count, 1), 10)},
                    headers={"x-bot-token": BOT_SECRET},
                )
                resp.raise_for_status()
                data = resp.json()

        except Exception as e:
            await interaction.followup.send(embed=_error_embed(str(e)))
            return

        history = data.get("history", [])
        if not history:
            await interaction.followup.send("No scan history found yet.")
            return

        embed = discord.Embed(
            title=f"📋 Last {len(history)} Scan(s)",
            color=discord.Color.blurple(),
        )
        for entry in history:
            composite = entry.get("composite_score", 0)
            posture = entry.get("posture", "Unknown")
            summary = entry.get("llm_summary") or "No summary available."
            embed.add_field(
                name=f"📅 {entry['date']}  ·  {posture}  ·  Score: {composite:+.3f}",
                value=summary[:300] + ("…" if len(summary) > 300 else ""),
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    # ── /scan-status ─────────────────────────────────────────────────────────

    @app_commands.command(name="scan-status", description="Check if a scan is currently running")
    async def scan_status(self, interaction: discord.Interaction) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{API_BASE}/api/health",
                    headers={"x-bot-token": BOT_SECRET},
                )
                resp.raise_for_status()

            await interaction.response.send_message(
                "✅ Market Intelligence API is **online** and ready.", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ API unreachable: `{e}`", ephemeral=True
            )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="❌ Error",
        description=f"```{message[:1000]}```",
        color=discord.Color.red(),
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScanCommands(bot))
