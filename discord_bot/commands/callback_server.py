"""Lightweight HTTP server that receives scan results from FastAPI and posts them to Discord."""

from __future__ import annotations

import logging
import os
from typing import Any

import discord
from aiohttp import web
from discord.ext import commands

from utils.embeds import build_result_embed, build_error_embed

logger = logging.getLogger(__name__)

CALLBACK_PORT = int(os.getenv("DISCORD_BOT_CALLBACK_PORT", "9000"))
BOT_SECRET = os.getenv("DISCORD_BOT_SECRET", "")


class CallbackServer(commands.Cog):
    """Runs a small aiohttp server inside the bot process to receive FastAPI callbacks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._runner: web.AppRunner | None = None

    async def cog_load(self) -> None:
        """Start the callback HTTP server when the cog loads."""
        app = web.Application()
        app.router.add_post("/callback", self._handle_callback)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", CALLBACK_PORT)
        await site.start()
        logger.info(f"Callback server listening on http://0.0.0.0:{CALLBACK_PORT}")

    async def cog_unload(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def _handle_callback(self, request: web.Request) -> web.Response:
        """Receive scan results from FastAPI and post embed to Discord channel."""
        # Validate secret
        secret = request.headers.get("x-bot-secret", "")
        if secret != BOT_SECRET:
            return web.Response(status=401, text="Unauthorized")

        try:
            body: dict[str, Any] = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        channel_id = int(body.get("channel_id", 0))
        result = body.get("result")

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            logger.warning(f"Channel {channel_id} not found — cannot post results")
            return web.Response(status=404, text="Channel not found")

        if result and result.get("status") == "complete":
            embed = build_result_embed(result)
        else:
            embed = build_error_embed("Pipeline did not return a valid result.")

        await channel.send(embed=embed)
        return web.Response(status=200, text="OK")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CallbackServer(bot))
