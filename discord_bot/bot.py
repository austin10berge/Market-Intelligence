"""Market Intelligence Discord Bot — entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands, tasks
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
        self._claude_auth_loop.start()

    @tasks.loop(hours=24)
    async def _claude_auth_loop(self) -> None:
        # OAuth tokens in the isolated discord-bot-config expire (~30 days).
        # Runs once immediately on startup, then every 24h, so expiry is caught
        # proactively rather than discovered on the first failing user message.
        # Loki alert watches for "CLAUDE AUTH EXPIRED" in this container's logs.
        # To re-auth: docker exec -it market-intelligence-discord-bot claude
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", "--output-format", "json",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(input=b"ping"), timeout=30)
            if proc.returncode != 0 or b"Not logged in" in stdout:
                logger.error(
                    "❌ CLAUDE AUTH EXPIRED — bot will fall back to Gemini for all "
                    "responses. Re-auth: docker exec -it market-intelligence-discord-bot claude"
                )
            else:
                logger.info("✅ Claude auth OK")
        except Exception as exc:
            logger.warning("Claude auth check failed: %s", exc)


async def main() -> None:
    async with MarketIntelligenceBot() as bot:
        await bot.start(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
