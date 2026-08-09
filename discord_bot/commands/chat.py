"""Trade chat cog — designated channel listener and conversational trading partner."""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from src.chat import (
    build_prompt,
    build_thread_title,
    call_claude_chat,
    detect_tickers,
    gather_chat_blocks,
)
from src.db import (
    get_stock_watchlist,
    get_trade_chat_channel_id,
    get_trade_chat_history,
    is_trade_chat_thread,
    save_trade_chat_message,
    set_trade_chat_channel_id,
)
from src.synthesis.llm import synthesize

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "trade_system_prompt.txt"
_UNIVERSE_TTL_SECONDS = 300  # re-check the watchlist periodically instead of caching forever


def _load_system_prompt() -> str:
    if _SYSTEM_PROMPT_PATH.exists():
        return _SYSTEM_PROMPT_PATH.read_text().strip()
    logger.warning("trade_system_prompt.txt not found — using empty system prompt")
    return "You are a trading partner."


def _split_message(text: str, limit: int = 1990) -> list[str]:
    """Split a string into chunks that fit Discord's 2000-char message limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


class TradeChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.system_prompt = _load_system_prompt()
        self.trade_channel_id: int | None = None
        self._universe: set[str] | None = None
        self._universe_loaded_at: float | None = None

    async def cog_load(self) -> None:
        channel_id = get_trade_chat_channel_id()
        if channel_id:
            self.trade_channel_id = int(channel_id)
            logger.info("Trade chat channel configured: %s", channel_id)
        else:
            logger.warning("Trade chat: no channel configured. Run /trade-setup.")

    @property
    def universe(self) -> set[str]:
        """Screener ticker universe for bare-word detection, refreshed every
        _UNIVERSE_TTL_SECONDS so new watchlist tickers show up without a bot restart."""
        now = time.monotonic()
        stale = self._universe_loaded_at is None or (now - self._universe_loaded_at) > _UNIVERSE_TTL_SECONDS
        if stale:
            self._universe = set(get_stock_watchlist())
            self._universe_loaded_at = now
        return self._universe

    @app_commands.command(
        name="trade-setup",
        description="Set this channel as the trade chat channel",
    )
    async def trade_setup(self, interaction: discord.Interaction) -> None:
        channel_id = str(interaction.channel_id)
        set_trade_chat_channel_id(channel_id)
        self.trade_channel_id = int(channel_id)
        logger.info("Trade chat channel set to %s", channel_id)
        await interaction.response.send_message(
            f"Trade chat channel set to <#{channel_id}>. "
            "Send a message here to start a conversation thread.",
            ephemeral=True,
        )

    async def _handle_note(self, message: discord.Message) -> None:
        """Handle !note trade:<id> <text> or !note cycle:<id> <text>."""
        try:
            from src.config import settings
            from src.wheel_tracker.store import insert_note
        except ImportError:
            await message.reply("Wheel tracker not available.")
            return

        parts = message.content[len("!note "):].strip().split(None, 1)
        if len(parts) < 2 or ":" not in parts[0]:
            await message.reply(
                "Usage: `!note trade:<id> <text>` or `!note cycle:<id> <text>`"
            )
            return

        ref, content = parts[0], parts[1]
        kind, _, raw_id = ref.partition(":")
        if kind not in ("trade", "cycle"):
            await message.reply("Use `trade:<id>` or `cycle:<id>`.")
            return
        try:
            entity_id = int(raw_id)
        except ValueError:
            await message.reply(f"Invalid ID: `{raw_id}`")
            return

        trade_id = entity_id if kind == "trade" else None
        cycle_id = entity_id if kind == "cycle" else None

        with closing(sqlite3.connect(settings.db_path)) as conn:
            insert_note(conn, {
                "trade_id": trade_id,
                "cycle_id": cycle_id,
                "source": "discord",
                "content": content.strip(),
            })

        await message.add_reaction("✅")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        # !note command — save a note to a trade or cycle. Checked first so it
        # short-circuits before the trade-chat channel/thread routing below and
        # works in any channel the bot can see, not just the configured one.
        if message.content.startswith("!note "):
            await self._handle_note(message)
            return

        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if self.trade_channel_id is None:
            return

        channel = message.channel

        # Top-level message in the designated channel → create a thread
        if (
            not isinstance(channel, discord.Thread)
            and channel.id == self.trade_channel_id
        ):
            if is_trade_chat_thread(str(channel.id)):
                # An earlier create_thread() call for this channel already failed
                # and fell back to replying directly in it — keep using that
                # fallback session instead of retrying create_thread (which would
                # just orphan the history saved under this channel's id again).
                await self._handle_message(channel, message)  # type: ignore[arg-type]
                return
            try:
                thread = await message.create_thread(
                    name=build_thread_title(message.content, self.universe),
                    auto_archive_duration=1440,
                )
            except discord.HTTPException as exc:
                logger.warning("Failed to create trade thread: %s", exc)
                thread = channel  # type: ignore[assignment]
            await self._handle_message(thread, message)
            return

        # Message inside a known trade thread
        if (
            isinstance(channel, discord.Thread)
            and channel.parent_id == self.trade_channel_id
            and is_trade_chat_thread(str(channel.id))
        ):
            await self._handle_message(channel, message)

    async def _handle_message(
        self, thread: discord.Thread, message: discord.Message
    ) -> None:
        thread_id = str(thread.id)

        async with thread.typing():
            tickers = detect_tickers(message.content, self.universe)
            screener_blocks = await gather_chat_blocks(tickers, message.content)

            history = get_trade_chat_history(thread_id)
            prompt = build_prompt(
                self.system_prompt, history, message.content, screener_blocks
            )

            response = await call_claude_chat(prompt)

            if response is None:
                # Gemini fallback has no tool access and no live data — it must not
                # be allowed to state ticker-specific numbers as if verified (this is
                # exactly how a real fabrication incident happened: the primary path
                # timed out, this fallback fired silently, and Gemini invented
                # specific RSI/price/BB figures for real tickers). Tell it plainly
                # it has no live data, and tag the reply so it's never mistaken for
                # a tool-grounded answer.
                history_text = "".join(
                    f"{'User' if t['role'] == 'user' else 'Assistant'}: {t['content']}\n\n"
                    for t in history
                )
                user_prompt = (
                    "NOTE: you have no live market data or tools available for this "
                    "reply — the live lookup failed or timed out. Do not state "
                    "specific numeric claims (price, RSI, IV, BB width, volume, %-move, "
                    "etc.) for any real ticker as if verified. Speak in general/"
                    "qualitative terms, or say plainly you can't verify current numbers "
                    "right now.\n\n" + history_text + message.content
                )
                if screener_blocks:
                    user_prompt += "\n\n" + "\n\n".join(screener_blocks)
                response = await synthesize(self.system_prompt, user_prompt)
                if response:
                    response = (
                        "*(live data lookup timed out — this reply has no verified "
                        "current numbers; ask again to retry)*\n\n" + response
                    )

            if not response:
                response = (
                    "Sorry, I'm having trouble reaching the LLM right now. "
                    "Try again in a moment."
                )

            # Persist — store user message with injected data so history is self-contained
            user_content = message.content
            if screener_blocks:
                user_content += "\n\n" + "\n\n".join(screener_blocks)
            save_trade_chat_message(thread_id, "user", user_content)
            save_trade_chat_message(thread_id, "assistant", response)

        for chunk in _split_message(response):
            await thread.send(chunk)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradeChatCog(bot))
