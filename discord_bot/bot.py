"""Market Intelligence Discord Bot — entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load .env from the project root (one level up from discord_bot/)
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

if not DISCORD_BOT_TOKEN:
    logger.error("DISCORD_BOT_TOKEN is not set in .env — exiting.")
    sys.exit(1)


class MarketIntelligenceBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        # Required so the trade chat cog can read message text in the designated
        # channel/threads. Also enable this "Message Content Intent" for the bot
        # in the Discord Developer Portal, or message.content arrives empty.
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        """Load cogs and sync slash commands on startup."""
        await self.load_extension("commands.scan")
        await self.load_extension("commands.insider")
        await self.load_extension("commands.callback_server")
        await self.load_extension("commands.chat")
        synced = await self.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")

    async def on_ready(self) -> None:
        logger.info(f"✅ Logged in as {self.user} (ID: {self.user.id})")


async def main() -> None:
    async with MarketIntelligenceBot() as bot:
        await bot.start(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
