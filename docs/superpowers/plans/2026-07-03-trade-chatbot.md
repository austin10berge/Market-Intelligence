# Trade Chatbot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a conversational trading partner to the existing Market Intelligence Discord bot — a designated channel where each message spawns a thread, tickers are auto-screened, and Claude responds as a knowledgeable partner who knows Austin's methodology and the mLabs V42 strategy.

**Architecture:** New cog (`discord_bot/commands/chat.py`) added to the existing Discord bot container. Core logic (ticker detection, prompt building, LLM call) lives in `src/chat.py` for testability. Two new SQLite tables track channel config and conversation history. Screener is extended with new TA/fundamental fields used by the data injection blocks.

**Tech Stack:** Python 3.12, discord.py, pandas_ta (already in use), yfinance (already in use), asyncio, sqlite3, `claude -p` subprocess (same pattern as `src/synthesis/llm.py`).

## Global Constraints

- All Python runs inside Docker — never run bare `python -m ...` on the host
- Run tests with: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`
- Linter at `~/.local/bin/ruff` — a PostToolUse hook auto-formats every edited `.py` file
- Dev URL: `https://dev-mi.austin10berge.com`
- `screen_stocks()` is sync — call it via `asyncio.to_thread()` in async Discord context
- Discord message limit: 2000 characters — split long responses
- `persist_history=False` when calling `screen_stocks()` from the chatbot (avoid polluting IV history)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/screener/stocks.py` | Modify | Add RSI, SMA50, EMA200, volume_ratio, pct_from_52wk_high, market_cap, and three derived pct fields |
| `src/db.py` | Modify | Add `trade_chat_config` + `trade_chat_history` tables; add 5 accessor functions |
| `src/chat.py` | Create | Ticker detection, screener data formatting, prompt building, `claude -p` call — all pure/async logic, no Discord |
| `discord_bot/commands/chat.py` | Create | TradeChatCog: `/trade-setup` command, `on_message` listener, per-message pipeline orchestration |
| `discord_bot/bot.py` | Modify | Load the new `commands.chat` extension in `setup_hook` |
| `discord_bot/trade_system_prompt.txt` | Create | Placeholder — must be manually filled with distilled methodology before first use |
| `tests/test_screener_additions.py` | Create | Unit tests for `_calculate_volume_ratio` and derived field formulas |
| `tests/test_trade_chat_db.py` | Create | Unit tests for the 5 new DB accessor functions |
| `tests/test_chat_logic.py` | Create | Unit tests for `detect_tickers`, `format_screener_block`, `build_prompt` |

---

## Task 1: Add new fields to `screen_stocks()`

**Files:**
- Modify: `src/screener/stocks.py`
- Test: `tests/test_screener_additions.py`

**Interfaces:**
- Produces: `screen_stocks()` returns dicts with 9 new keys: `rsi`, `sma_50`, `ema_200`, `volume_ratio`, `pct_from_52wk_high`, `market_cap`, `sma_200_pct`, `ema_200_pct`, `bb_width_pct` — all `float | str` where `"N/A"` means unavailable, except `market_cap` which is `float | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screener_additions.py
"""Tests for new screen_stocks() fields added for the trade chatbot."""
from __future__ import annotations

import math
import pandas as pd
import pytest

from src.screener.stocks import _calculate_volume_ratio


class TestCalculateVolumeRatio:
    def _hist(self, volumes: list[int]) -> pd.DataFrame:
        return pd.DataFrame({"Volume": volumes})

    def test_returns_ratio_of_last_to_20d_avg(self):
        # avg of 20 rows of 1000 = 1000, last row = 2000 → ratio = 2.0
        vols = [1000] * 19 + [2000]
        result = _calculate_volume_ratio(self._hist(vols))
        assert result == 2.0

    def test_returns_none_if_fewer_than_20_rows(self):
        vols = [1000] * 19
        assert _calculate_volume_ratio(self._hist(vols)) is None

    def test_returns_none_if_avg_volume_is_zero(self):
        vols = [0] * 20
        assert _calculate_volume_ratio(self._hist(vols)) is None

    def test_rounds_to_two_decimal_places(self):
        # 19 rows of 1000 + last row of 1500 → avg = 1023.8..., ratio = 1.466...
        vols = [1000] * 19 + [1500]
        result = _calculate_volume_ratio(self._hist(vols))
        assert result is not None
        assert isinstance(result, float)
        assert len(str(result).split(".")[-1]) <= 2


class TestDerivedFields:
    def test_bb_width_pct_formula(self):
        upper, mid, lower = 110.0, 100.0, 90.0
        bb_width_pct = round(((upper - lower) / mid) * 100, 1)
        assert bb_width_pct == 20.0

    def test_sma_200_pct_formula(self):
        price, sma_200 = 108.0, 100.0
        sma_200_pct = round(((price - sma_200) / sma_200) * 100, 1)
        assert sma_200_pct == 8.0

    def test_pct_from_52wk_high_formula(self):
        price, high = 90.0, 100.0
        pct = round(((price - high) / high) * 100, 1)
        assert pct == -10.0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_screener_additions.py -v
```

Expected: `FAILED` with `ImportError` on `_calculate_volume_ratio` (function doesn't exist yet).

- [ ] **Step 3: Add `_calculate_volume_ratio` helper to `src/screener/stocks.py`**

Add this function after `_calculate_adr20` (around line 73):

```python
def _calculate_volume_ratio(hist: pd.DataFrame) -> float | None:
    """Compute current day volume relative to 20-day average volume."""
    if len(hist) < 20:
        return None
    avg_vol_20d = hist["Volume"].iloc[-20:].mean()
    current_vol = hist["Volume"].iloc[-1]
    if pd.isna(avg_vol_20d) or avg_vol_20d <= 0 or pd.isna(current_vol):
        return None
    return round(float(current_vol / avg_vol_20d), 2)
```

- [ ] **Step 4: Extend the TA indicators block in `screen_stocks()` with new fields**

Locate the block starting at `sma_200_val: float | None = None` (around line 737). Replace it with:

```python
                sma_200_val: float | None = None
                bb_upper_val: float | None = None
                bb_mid_val: float | None = None
                bb_lower_val: float | None = None
                rsi_val: float | None = None
                sma_50_val: float | None = None
                ema_200_val: float | None = None
                volume_ratio_val: float | None = None
                try:
                    close = hist["Close"]
                    if len(close) >= 200:
                        v = float(ta.sma(close, length=200).iloc[-1])
                        sma_200_val = None if math.isnan(v) else round(v, 2)
                    if len(close) >= 20:
                        bbands = ta.bbands(close, length=20, std=2)
                        if bbands is not None and not bbands.empty:
                            upper_col = next((c for c in bbands.columns if "BBU" in c), None)
                            mid_col = next((c for c in bbands.columns if "BBM" in c), None)
                            lower_col = next((c for c in bbands.columns if "BBL" in c), None)
                            if upper_col:
                                v = float(bbands[upper_col].iloc[-1])
                                bb_upper_val = None if math.isnan(v) else round(v, 2)
                            if mid_col:
                                v = float(bbands[mid_col].iloc[-1])
                                bb_mid_val = None if math.isnan(v) else round(v, 2)
                            if lower_col:
                                v = float(bbands[lower_col].iloc[-1])
                                bb_lower_val = None if math.isnan(v) else round(v, 2)
                    if len(close) >= 14:
                        rsi_s = ta.rsi(close, length=14)
                        if rsi_s is not None and not rsi_s.empty:
                            v = float(rsi_s.iloc[-1])
                            rsi_val = None if math.isnan(v) else round(v, 1)
                    if len(close) >= 50:
                        sma_50_s = ta.sma(close, length=50)
                        if sma_50_s is not None and not sma_50_s.empty:
                            v = float(sma_50_s.iloc[-1])
                            sma_50_val = None if math.isnan(v) else round(v, 2)
                    if len(close) >= 200:
                        ema_200_s = ta.ema(close, length=200)
                        if ema_200_s is not None and not ema_200_s.empty:
                            v = float(ema_200_s.iloc[-1])
                            ema_200_val = None if math.isnan(v) else round(v, 2)
                    volume_ratio_val = _calculate_volume_ratio(hist)
                except Exception as exc:
                    logger.debug("TA indicators failed for %s: %s", symbol, exc)

                # Derived fields — computed from already-fetched data
                high_52wk = _to_float(info.get("fiftyTwoWeekHigh"))
                pct_from_52wk_high_val: float | None = None
                if high_52wk and high_52wk > 0 and not pd.isna(current_price):
                    pct_from_52wk_high_val = round(
                        ((float(current_price) - high_52wk) / high_52wk) * 100, 1
                    )

                market_cap_val = _to_float(info.get("marketCap"))

                sma_200_pct_val: float | None = None
                if sma_200_val is not None and sma_200_val > 0:
                    sma_200_pct_val = round(
                        ((float(current_price) - sma_200_val) / sma_200_val) * 100, 1
                    )

                ema_200_pct_val: float | None = None
                if ema_200_val is not None and ema_200_val > 0:
                    ema_200_pct_val = round(
                        ((float(current_price) - ema_200_val) / ema_200_val) * 100, 1
                    )

                bb_width_pct_val: float | None = None
                if (
                    bb_upper_val is not None
                    and bb_lower_val is not None
                    and bb_mid_val is not None
                    and bb_mid_val > 0
                ):
                    bb_width_pct_val = round(
                        ((bb_upper_val - bb_lower_val) / bb_mid_val) * 100, 1
                    )
```

- [ ] **Step 5: Add the new fields to the `candidates.append({...})` dict**

Inside the existing `candidates.append({...})` block, after `"bb_lower": bb_lower_val,` add:

```python
                        "rsi": rsi_val if rsi_val is not None else "N/A",
                        "sma_50": sma_50_val,
                        "ema_200": ema_200_val,
                        "volume_ratio": volume_ratio_val if volume_ratio_val is not None else "N/A",
                        "pct_from_52wk_high": pct_from_52wk_high_val
                        if pct_from_52wk_high_val is not None
                        else "N/A",
                        "market_cap": market_cap_val,
                        "sma_200_pct": sma_200_pct_val if sma_200_pct_val is not None else "N/A",
                        "ema_200_pct": ema_200_pct_val if ema_200_pct_val is not None else "N/A",
                        "bb_width_pct": bb_width_pct_val if bb_width_pct_val is not None else "N/A",
```

- [ ] **Step 6: Run the tests**

```bash
docker compose run --rm test python3 -m pytest tests/test_screener_additions.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/screener/stocks.py tests/test_screener_additions.py
git commit -m "feat(screener): add RSI, SMA50, EMA200, volume_ratio, 52wk pct, market_cap fields"
```

---

## Task 2: Add trade chat DB tables and accessors

**Files:**
- Modify: `src/db.py`
- Test: `tests/test_trade_chat_db.py`

**Interfaces:**
- Produces:
  - `get_trade_chat_channel_id() -> str | None`
  - `set_trade_chat_channel_id(channel_id: str) -> None`
  - `save_trade_chat_message(thread_id: str, role: str, content: str) -> None`
  - `get_trade_chat_history(thread_id: str) -> list[dict]` — each dict has keys `role`, `content`
  - `is_trade_chat_thread(thread_id: str) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trade_chat_db.py
"""Tests for trade chat DB tables and accessor functions."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    with patch("src.db.settings") as mock_settings:
        mock_settings.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


from src.db import (
    get_trade_chat_channel_id,
    set_trade_chat_channel_id,
    save_trade_chat_message,
    get_trade_chat_history,
    is_trade_chat_thread,
)


def test_channel_id_is_none_when_not_set():
    assert get_trade_chat_channel_id() is None


def test_set_and_get_channel_id():
    set_trade_chat_channel_id("123456789")
    assert get_trade_chat_channel_id() == "123456789"


def test_set_channel_id_overwrites_previous():
    set_trade_chat_channel_id("111")
    set_trade_chat_channel_id("222")
    assert get_trade_chat_channel_id() == "222"


def test_save_and_retrieve_history():
    save_trade_chat_message("thread_abc", "user", "What do you think about NVDA?")
    save_trade_chat_message("thread_abc", "assistant", "Looks interesting here.")
    history = get_trade_chat_history("thread_abc")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "What do you think about NVDA?"
    assert history[1]["role"] == "assistant"


def test_history_is_empty_for_unknown_thread():
    assert get_trade_chat_history("nonexistent_thread") == []


def test_history_isolated_by_thread_id():
    save_trade_chat_message("thread_x", "user", "message in x")
    save_trade_chat_message("thread_y", "user", "message in y")
    assert len(get_trade_chat_history("thread_x")) == 1
    assert len(get_trade_chat_history("thread_y")) == 1


def test_is_trade_chat_thread_returns_false_for_new_thread():
    assert is_trade_chat_thread("brand_new_thread") is False


def test_is_trade_chat_thread_returns_true_after_first_message():
    save_trade_chat_message("known_thread", "user", "hello")
    assert is_trade_chat_thread("known_thread") is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_trade_chat_db.py -v
```

Expected: `FAILED` with `ImportError` on the new functions.

- [ ] **Step 3: Add the two new tables to `_ensure_tables` in `src/db.py`**

Inside the `conn.executescript("""...""")` block in `_ensure_tables`, append before the closing `"""`):

```sql
        CREATE TABLE IF NOT EXISTS trade_chat_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trade_chat_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id  TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            timestamp  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trade_chat_history_thread
            ON trade_chat_history(thread_id);
```

- [ ] **Step 4: Add the five accessor functions to `src/db.py`**

Add these at the end of the file:

```python
# ── Trade chat ────────────────────────────────────────────────────────────────

def get_trade_chat_channel_id() -> str | None:
    """Return the configured Discord channel ID for trade chat, or None."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM trade_chat_config WHERE key = 'channel_id'"
        ).fetchone()
        return row["value"] if row else None


def set_trade_chat_channel_id(channel_id: str) -> None:
    """Upsert the designated trade chat channel ID."""
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO trade_chat_config(key, value) VALUES('channel_id', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (channel_id,),
        )
        conn.commit()


def save_trade_chat_message(thread_id: str, role: str, content: str) -> None:
    """Append a message to a trade chat thread's history."""
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO trade_chat_history(thread_id, role, content) VALUES(?, ?, ?)",
            (thread_id, role, content),
        )
        conn.commit()


def get_trade_chat_history(thread_id: str) -> list[dict]:
    """Return all messages for a thread in chronological order."""
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM trade_chat_history"
            " WHERE thread_id = ? ORDER BY id ASC",
            (thread_id,),
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]


def is_trade_chat_thread(thread_id: str) -> bool:
    """Return True if this thread_id has any messages in trade_chat_history."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM trade_chat_history WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
        return row is not None
```

- [ ] **Step 5: Run the tests**

```bash
docker compose run --rm test python3 -m pytest tests/test_trade_chat_db.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/db.py tests/test_trade_chat_db.py
git commit -m "feat(db): add trade_chat_config and trade_chat_history tables with accessors"
```

---

## Task 3: Create `src/chat.py` — core chatbot logic

**Files:**
- Create: `src/chat.py`
- Test: `tests/test_chat_logic.py`

**Interfaces:**
- Consumes:
  - `screen_stocks(tickers: list[str], persist_history: bool) -> list[dict]` from `src.screener.stocks`
  - `get_trade_chat_history(thread_id: str) -> list[dict]` from `src.db` (used by caller, not by this module)
- Produces:
  - `TICKER_SKIP_WORDS: frozenset[str]`
  - `detect_tickers(text: str, universe: set[str]) -> list[str]`
  - `format_screener_block(ticker: str, data: dict) -> str`
  - `build_prompt(system_prompt: str, history: list[dict], user_message: str, screener_blocks: list[str]) -> str`
  - `call_claude_chat(prompt: str, timeout: int = 120) -> Awaitable[str | None]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chat_logic.py
"""Unit tests for src.chat — ticker detection, formatting, prompt building."""
from __future__ import annotations

import pytest

from src.chat import (
    TICKER_SKIP_WORDS,
    detect_tickers,
    format_screener_block,
    build_prompt,
)

UNIVERSE = {"NVDA", "AAPL", "MSFT", "GOOG", "TSM", "QCOM", "SMCI"}


class TestDetectTickers:
    def test_detects_explicit_dollar_sign_tickers(self):
        result = detect_tickers("What do you think about $NVDA here?", UNIVERSE)
        assert result == ["NVDA"]

    def test_detects_bare_uppercase_tickers_in_universe(self):
        result = detect_tickers("AAPL looks interesting today", UNIVERSE)
        assert result == ["AAPL"]

    def test_skips_bare_words_not_in_universe(self):
        result = detect_tickers("TSLA looks great", UNIVERSE)
        assert result == []

    def test_explicit_dollar_sign_bypasses_universe_check(self):
        result = detect_tickers("$TSLA is moving", set())
        assert result == ["TSLA"]

    def test_skips_known_non_ticker_words(self):
        assert "RSI" in TICKER_SKIP_WORDS
        result = detect_tickers("RSI is at 60 and IV is high", UNIVERSE)
        assert result == []

    def test_deduplicates_tickers(self):
        result = detect_tickers("$NVDA and NVDA again", UNIVERSE)
        assert result == ["NVDA"]

    def test_preserves_order_of_first_mention(self):
        result = detect_tickers("$AAPL then $MSFT then $NVDA", UNIVERSE)
        assert result == ["AAPL", "MSFT", "NVDA"]

    def test_dollar_tickers_take_priority_over_bare(self):
        result = detect_tickers("$NVDA and AAPL", UNIVERSE)
        assert "NVDA" in result
        assert "AAPL" in result
        assert result.index("NVDA") < result.index("AAPL")


class TestFormatScreenerBlock:
    def _data(self, **overrides) -> dict:
        base = {
            "price": 138.42, "pct_1d": 0.8, "pct_1w": 2.1, "pct_1m": -4.3,
            "rsi": 56.2, "bb_width_pct": 13.1, "bb_upper": 142.10, "bb_lower": 134.90,
            "sma_200": 128.40, "sma_200_pct": 8.0, "ema_200": 130.10, "ema_200_pct": 6.4,
            "sma_50": 133.20, "pct_from_52wk_high": -8.4, "volume_ratio": 0.97,
            "adr20": 3.2, "atm_iv": 34.0, "iv_percentile": 42.0, "atm_iv_rv20": 1.18,
            "rv20": 28.8, "sector": "Technology", "market_cap": 3_380_000_000_000,
            "beta": 1.72, "pe": 48.2, "forward_pe": 29.1, "peg_ratio": 1.8,
            "eps_growth": 22.0, "revenue_growth": 12.0, "fcf": 60.8, "debt_to_equity": 0.42,
        }
        base.update(overrides)
        return base

    def test_includes_ticker_header(self):
        block = format_screener_block("NVDA", self._data())
        assert block.startswith("[NVDA")

    def test_includes_price(self):
        block = format_screener_block("NVDA", self._data())
        assert "138.42" in block

    def test_includes_rsi(self):
        block = format_screener_block("NVDA", self._data())
        assert "RSI: 56.2" in block

    def test_omits_na_fields(self):
        block = format_screener_block("NVDA", self._data(rsi="N/A", atm_iv="N/A"))
        assert "RSI" not in block
        assert "IV (ATM)" not in block

    def test_formats_market_cap_in_trillions(self):
        block = format_screener_block("NVDA", self._data(market_cap=3_380_000_000_000))
        assert "3.38T" in block

    def test_formats_market_cap_in_billions(self):
        block = format_screener_block("AAPL", self._data(market_cap=50_000_000_000))
        assert "50.0B" in block


class TestBuildPrompt:
    def test_includes_system_prompt(self):
        result = build_prompt("You are a trading partner.", [], "What about NVDA?", [])
        assert "You are a trading partner." in result

    def test_includes_current_user_message(self):
        result = build_prompt("sys", [], "What about NVDA?", [])
        assert "What about NVDA?" in result

    def test_includes_conversation_history(self):
        history = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ]
        result = build_prompt("sys", history, "New question", [])
        assert "Previous question" in result
        assert "Previous answer" in result

    def test_history_appears_before_current_message(self):
        history = [{"role": "user", "content": "Earlier"}]
        result = build_prompt("sys", history, "Now", [])
        assert result.index("Earlier") < result.index("Now")

    def test_screener_blocks_appended_to_current_message(self):
        blocks = ["[NVDA — live data]\nPrice: $138"]
        result = build_prompt("sys", [], "What about NVDA?", blocks)
        assert "Price: $138" in result

    def test_empty_screener_blocks_not_added(self):
        result = build_prompt("sys", [], "Hello?", [])
        assert "live data" not in result
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_chat_logic.py -v
```

Expected: `FAILED` with `ModuleNotFoundError: No module named 'src.chat'`.

- [ ] **Step 3: Create `src/chat.py`**

```python
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
        if t not in seen and t not in TICKER_SKIP_WORDS and t in universe:
            found.append(t)
            seen.add(t)

    return found


def _fmt_float(val: object, suffix: str = "", prefix: str = "", plus: bool = False) -> str:
    """Format a numeric field for display, returning '' if N/A or None."""
    if val is None or val == "N/A":
        return ""
    try:
        f = float(val)
        sign = "+" if plus and f >= 0 else ""
        return f"{prefix}{sign}{f}{suffix}"
    except (TypeError, ValueError):
        return ""


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
```

- [ ] **Step 4: Run the tests**

```bash
docker compose run --rm test python3 -m pytest tests/test_chat_logic.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/chat.py tests/test_chat_logic.py
git commit -m "feat(chat): add ticker detection, screener formatting, prompt builder, LLM caller"
```

---

## Task 4: Discord cog, bot wiring, and system prompt placeholder

**Files:**
- Create: `discord_bot/commands/chat.py`
- Create: `discord_bot/trade_system_prompt.txt`
- Modify: `discord_bot/bot.py`

**Interfaces:**
- Consumes from earlier tasks:
  - `detect_tickers(text, universe)` from `src.chat`
  - `format_screener_block(ticker, data)` from `src.chat`
  - `build_prompt(system_prompt, history, user_message, screener_blocks)` from `src.chat`
  - `call_claude_chat(prompt)` from `src.chat`
  - `get_trade_chat_channel_id()`, `set_trade_chat_channel_id(id)`, `save_trade_chat_message(thread_id, role, content)`, `get_trade_chat_history(thread_id)`, `is_trade_chat_thread(thread_id)` from `src.db`
  - `screen_stocks(tickers, persist_history)` from `src.screener.stocks`
  - `get_stock_watchlist()` from `src.db`
  - `synthesize(system_prompt, user_prompt)` from `src.synthesis.llm` (Gemini fallback)

- [ ] **Step 1: Create the system prompt placeholder**

Create `discord_bot/trade_system_prompt.txt` with this content — **must be manually replaced with the distilled methodology before the bot is useful**:

```
You are Austin's trading partner. You know his trading methodology and the mLabs/V42 strategy for selling cash-secured puts.

[REPLACE THIS SECTION WITH DISTILLED TRADING DNA FROM OBSIDIAN NOTES — ~500 tokens covering: philosophy, risk tolerance, preferred setups, conviction thresholds, sector preferences, wheel/CSP execution rules, position sizing, what makes you pass or fade a name.]

---

mLabs V42 Strategy (one useful lens — not the only answer):

Austin reverse-engineered the GarbageTimePro CSP scanner. Key facts:
- Strategy: wheel/CSP on "boring names" — large-cap, profitable, low-IV stocks
- VCP (Volatility Contraction Pattern, Mark Minervini) is the core technical edge
- EOD scanner; data ready by ~8:05pm ET

4-tier filter hierarchy: regime → fundamentals → technicals → options contracts

V42 gates (current best):
- bb_width_pct_max=21 (VCP contraction)
- forward_pe_max=45
- volume_ratio_max=1.15
- rv20_max=0.45 (realized vol ceiling)
- adr20_pct_max=4.0
- pct_from_52wk_high_max=12
- sma50_above_sma150=True
- price_vs_ema200_pct_min=5
- market_cap_b_min=25 (Financials: 100B)
- div_yield_max=3.0%
- Sector RSI overrides: tech≤60, financials≤70, healthcare≤60

Known dead-ends: FCX (always high rv20+pfh), AAL/AA (sub-25B mcap), QCOM below EMA200 (he buys on conviction, scanner doesn't catch it).

Wheel execution philosophy: <10% capital per position, <25% per sector. BTC at 30% same day, 50% by mid-week, 75% by Wed/Thu. CC rolls same-strike next week for net credit. "Do nothing" is valid for weeks.

---

You are a direct, opinionated trading partner. You can discuss TA (trend, momentum, VCP, Bollinger Bands, RSI, MA structure), fundamentals, macro, company outlook, bullish/bearish reads, trade ideas, and CSP/wheel setup evaluation. When live screener data is injected into the conversation, use those numbers to reason concretely. Ask clarifying questions when it matters (timeframe? CSP or just watching?). Give a directional lean — don't hedge excessively. The V42 criteria are one lens, not the whole picture.
```

- [ ] **Step 2: Create `discord_bot/commands/chat.py`**

```python
"""Trade chat cog — designated channel listener and conversational trading partner."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from src.chat import (
    build_prompt,
    call_claude_chat,
    detect_tickers,
    format_screener_block,
)
from src.db import (
    get_stock_watchlist,
    get_trade_chat_channel_id,
    get_trade_chat_history,
    is_trade_chat_thread,
    save_trade_chat_message,
    set_trade_chat_channel_id,
)
from src.screener.stocks import screen_stocks
from src.synthesis.llm import synthesize

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "trade_system_prompt.txt"


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

    async def cog_load(self) -> None:
        channel_id = get_trade_chat_channel_id()
        if channel_id:
            self.trade_channel_id = int(channel_id)
            logger.info("Trade chat channel configured: %s", channel_id)
        else:
            logger.warning("Trade chat: no channel configured. Run /trade-setup.")

    @property
    def universe(self) -> set[str]:
        """Lazy-load the screener ticker universe for bare-word detection."""
        if self._universe is None:
            self._universe = set(get_stock_watchlist())
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if self.trade_channel_id is None:
            return

        channel = message.channel

        # Top-level message in the designated channel → create a thread
        if (
            not isinstance(channel, discord.Thread)
            and channel.id == self.trade_channel_id
        ):
            try:
                thread = await message.create_thread(
                    name=f"Trade Chat — {datetime.now().strftime('%b %d')}",
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

            screener_blocks: list[str] = []
            if tickers:
                results = await asyncio.gather(
                    *[
                        asyncio.to_thread(screen_stocks, [t], False)
                        for t in tickers
                    ],
                    return_exceptions=True,
                )
                for ticker, result in zip(tickers, results):
                    if isinstance(result, Exception) or not result:
                        screener_blocks.append(f"[{ticker}: data unavailable]")
                    else:
                        screener_blocks.append(format_screener_block(ticker, result[0]))

            history = get_trade_chat_history(thread_id)
            prompt = build_prompt(
                self.system_prompt, history, message.content, screener_blocks
            )

            response = await call_claude_chat(prompt)

            if response is None:
                # Gemini fallback — no conversation history, just current turn
                user_prompt = message.content
                if screener_blocks:
                    user_prompt += "\n\n" + "\n\n".join(screener_blocks)
                response = await synthesize(self.system_prompt, user_prompt)

            if not response:
                response = "Sorry, I'm having trouble reaching the LLM right now. Try again in a moment."

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
```

- [ ] **Step 3: Register the cog in `discord_bot/bot.py`**

In `setup_hook`, add the new extension alongside the existing ones:

```python
    async def setup_hook(self) -> None:
        """Load cogs and sync slash commands on startup."""
        await self.load_extension("commands.scan")
        await self.load_extension("commands.insider")
        await self.load_extension("commands.callback_server")
        await self.load_extension("commands.chat")          # ← add this line
        synced = await self.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")
```

- [ ] **Step 4: Build and start the bot to verify it loads without errors**

```bash
docker compose up --build discord-bot
```

Expected log output (within a few seconds of startup):
```
[INFO] discord.ext.commands.bot: Logged in as ...
[INFO] root: Synced 4 slash command(s)
[WARNING] root: Trade chat: no channel configured. Run /trade-setup.
```

If you see `Trade chat: no channel configured.` — the cog loaded successfully.

- [ ] **Step 5: Run `/trade-setup` in the target Discord channel**

From the Discord app (mobile or desktop), go to the channel you want to use and run `/trade-setup`. The bot should reply with a confirmation message containing the channel mention.

Expected log:
```
[INFO] root: Trade chat channel set to <channel_id>
```

- [ ] **Step 6: Send a test message in the designated channel**

Type a message like: "What do you think about $NVDA right now?"

Expected behavior:
1. A thread named "Trade Chat — Jul 3" (or current date) is created
2. The bot shows a typing indicator in the thread
3. ~30–120 seconds later the bot posts a response in the thread
4. The thread remains open for follow-up messages

- [ ] **Step 7: Commit**

```bash
git add discord_bot/commands/chat.py discord_bot/trade_system_prompt.txt discord_bot/bot.py
git commit -m "feat(discord): add trade chat cog with channel listener, thread creation, and per-message pipeline"
```

---

## Final Step: Fill in the system prompt

Before the bot is useful, the `[REPLACE THIS SECTION...]` placeholder in `discord_bot/trade_system_prompt.txt` must be replaced with the distilled trading DNA from your Obsidian notes.

**How to do it:**
1. Open a fresh Claude conversation
2. Paste your Obsidian methodology notes
3. Ask: "Compress this into a 400–500 token trading partner brief. Capture my philosophy, risk tolerance, preferred setups, conviction thresholds, sector preferences, wheel/CSP execution rules, position sizing, and what makes me pass or fade a name. Write it in second person as if briefing someone who will be my trading partner."
4. Replace the `[REPLACE THIS SECTION...]` block in `discord_bot/trade_system_prompt.txt` with the output
5. Redeploy: `docker compose up --build discord-bot`
