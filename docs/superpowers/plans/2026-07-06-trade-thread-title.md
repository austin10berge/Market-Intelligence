# Trade Chat Thread Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the date-only Discord thread title (`Trade Chat — Jul 06`) for new trade-chat threads with a ticker + topic title (e.g. `AAPL, TSLA: Earnings Pullback`), derived heuristically from the opening message with no extra LLM call.

**Architecture:** A new pure function `build_thread_title(content, universe)` in `src/chat.py` reuses the existing `detect_tickers()` helper and a small stopword filter to produce the title string. `discord_bot/commands/chat.py` calls it at thread-creation time instead of formatting the date directly.

**Tech Stack:** Python 3.12, pytest (via `docker compose run --rm test`), no new dependencies.

## Global Constraints

- Python 3.12, no local virtualenv — run tests via `docker compose run --rm test python3 -m pytest ...` (per `CLAUDE.md`).
- `ruff` auto-formats edited `.py` files via a `PostToolUse` hook — no manual format step needed.
- Discord thread names are capped at 100 characters — final title must be truncated to fit.
- No new LLM/network calls for title generation (per spec: heuristic only).
- Title is set once, at thread creation, from the message that opens the thread — no renaming of existing threads, no changes to in-thread message handling.

---

### Task 1: `build_thread_title()` helper in `src/chat.py`

**Files:**
- Modify: `src/chat.py` (add function after `detect_tickers`, before `format_screener_block`)
- Test: `tests/test_chat_logic.py` (add `TestBuildThreadTitle` class)

**Interfaces:**
- Consumes: `detect_tickers(text: str, universe: set[str]) -> list[str]` (existing, `src/chat.py`), `TICKER_SKIP_WORDS: frozenset[str]` (existing, `src/chat.py`)
- Produces: `build_thread_title(content: str, universe: set[str]) -> str` — used by Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_chat_logic.py`, after the `TestDetectTickers` class:

```python
class TestBuildThreadTitle:
    def test_ticker_and_topic_words(self):
        result = build_thread_title(
            "What do you think about $AAPL here, is this an earnings pullback?", UNIVERSE
        )
        assert result == "AAPL: Here Earnings Pullback"

    def test_multiple_tickers_joined(self):
        result = build_thread_title("$AAPL and $MSFT both breaking out today", UNIVERSE)
        assert result == "AAPL, MSFT: Both Breaking Out"

    def test_caps_at_three_tickers(self):
        result = build_thread_title(
            "$AAPL $MSFT $NVDA $GOOG all ripping", UNIVERSE
        )
        assert result == "AAPL, MSFT, NVDA: All Ripping"

    def test_ticker_only_no_topic_words(self):
        result = build_thread_title("$AAPL", UNIVERSE)
        assert result == "AAPL"

    def test_topic_words_only_no_ticker(self):
        result = build_thread_title("thinking about a swing trade setup", UNIVERSE)
        assert result == "Thinking Swing Trade"

    def test_falls_back_to_date_when_nothing_survives(self):
        result = build_thread_title("what do you think about this", UNIVERSE)
        assert result.startswith("Trade Chat — ")

    def test_truncates_to_100_chars(self):
        long_content = "$AAPL " + "x" * 60 + " " + "y" * 60 + " " + "z" * 60
        result = build_thread_title(long_content, UNIVERSE)
        assert len(result) == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py::TestBuildThreadTitle -v`
Expected: FAIL with `ImportError: cannot import name 'build_thread_title'` (it isn't imported yet — see Step 3b) or `NameError` once imported but undefined.

- [ ] **Step 3a: Add the import in the test file**

In `tests/test_chat_logic.py`, update the import block:

```python
from src.chat import (
    TICKER_SKIP_WORDS,
    build_prompt,
    build_thread_title,
    detect_tickers,
    format_screener_block,
)
```

- [ ] **Step 3b: Implement `build_thread_title` in `src/chat.py`**

Add this after the `detect_tickers` function (after line 41) and before `format_screener_block`:

```python
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
```

This implementation and the test expectations in Step 1 have been verified together by direct execution — running all 7 cases through this exact function body produces the expected strings, including `"AAPL: Here Earnings Pullback"` for `test_ticker_and_topic_words` (`"here"` is not a stopword, so it survives as the first topic word) and `"Trade Chat — {date}"` for `test_falls_back_to_date_when_nothing_survives` (every word in `"what do you think about this"` is a stopword).

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py::TestBuildThreadTitle -v`
Expected: `7 passed`

- [ ] **Step 5: Run the full test file to check for regressions**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py -v`
Expected: all tests pass (existing `TestDetectTickers`, `TestFormatScreenerBlock`, `TestBuildPrompt` classes plus new `TestBuildThreadTitle`).

- [ ] **Step 6: Commit**

```bash
git add src/chat.py tests/test_chat_logic.py
git commit -m "feat(chat): add build_thread_title for ticker/topic thread names"
```

---

### Task 2: Wire `build_thread_title` into the Discord thread-creation call site

**Files:**
- Modify: `discord_bot/commands/chat.py:1-31` (imports), `discord_bot/commands/chat.py:102-116` (`on_message` thread-creation block)

**Interfaces:**
- Consumes: `build_thread_title(content: str, universe: set[str]) -> str` (from Task 1, `src/chat.py`), `self.universe` property (existing, returns `set[str]`, `discord_bot/commands/chat.py:69-74`)
- Produces: n/a (leaf integration task)

- [ ] **Step 1: Update the import block**

In `discord_bot/commands/chat.py`, change:

```python
from src.chat import (
    build_prompt,
    call_claude_chat,
    detect_tickers,
    format_screener_block,
)
```

to:

```python
from src.chat import (
    build_prompt,
    build_thread_title,
    call_claude_chat,
    detect_tickers,
    format_screener_block,
)
```

- [ ] **Step 2: Remove the now-unused `datetime` import**

Remove line 7:

```python
from datetime import datetime
```

(It was only used for the old thread-name formatting being replaced in Step 3 below. Confirm no other usage remains: `grep -n datetime discord_bot/commands/chat.py` should return nothing after Step 3.)

- [ ] **Step 3: Update the thread-creation call site**

In the `on_message` handler, change:

```python
            try:
                thread = await message.create_thread(
                    name=f"Trade Chat — {datetime.now().strftime('%b %d')}",
                    auto_archive_duration=1440,
                )
```

to:

```python
            try:
                thread = await message.create_thread(
                    name=build_thread_title(message.content, self.universe),
                    auto_archive_duration=1440,
                )
```

- [ ] **Step 4: Verify no stray `datetime` references remain**

Run: `grep -n "datetime" discord_bot/commands/chat.py`
Expected: no output.

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -v`
Expected: all tests pass, no import errors from `discord_bot/commands/chat.py`.

- [ ] **Step 6: Lint check**

Run: `~/.local/bin/ruff check discord_bot/ src/chat.py`
Expected: no errors (the `PostToolUse` hook auto-formats on edit, but this confirms nothing was missed, e.g. the removed import).

- [ ] **Step 7: Commit**

```bash
git add discord_bot/commands/chat.py
git commit -m "feat(discord-bot): use ticker/topic thread titles instead of date-only"
```

---

## Manual Verification (post-implementation)

Not automatable without a live Discord connection — after both tasks are committed, note for the user:

1. Deploy to dev per `docs/superpowers/specs/2026-07-06-trade-thread-title-design.md`'s deployment topology notes in `CLAUDE.md` (rebuild `discord-bot` image if `pyproject.toml` changed — it didn't here, so a restart of the `discord-bot` container should suffice for the Python-only edit).
2. Post a message with a ticker (e.g. `"$NVDA earnings next week, thoughts?"`) in the configured trade-chat channel and confirm the created thread is named `"NVDA: Earnings Next Week"` (or similar, capped at 3 topic words).
3. Post a message with no ticker and no salvageable topic words (e.g. `"hey"`) and confirm it falls back to `"Trade Chat — {today's date}"`.
