# Trade Chat Thread Titles — Design

## Problem

Trade chat threads are currently titled `Trade Chat — {date}` (e.g. `Trade Chat — Jul 06`), which is uninformative once a channel has more than a couple of open threads. Titles should instead reflect the ticker(s) and topic of the conversation.

## Approach

Add a pure helper, `build_thread_title(content: str, universe: set[str]) -> str`, to `src/chat.py`, and call it at thread-creation time in `discord_bot/commands/chat.py` in place of the hardcoded date string.

No extra LLM call — the title is derived heuristically from the first message that opens the thread, using the same `detect_tickers()` helper already used later in the message-handling flow.

### Title construction

1. Run `detect_tickers(content, universe)` to find any tickers mentioned (e.g. `$AAPL`, bare `TSLA`).
2. Tokenize `content` into words (`\b[a-zA-Z']+\b`), lowercase them, and drop:
   - a small stopword list (a, an, the, is, are, on, in, to, for, my, i, of, and, or, this, that, with, about, what, do, you, think, we, should, can, would, it, be, at, so, just, will, was, were)
   - anything already in `TICKER_SKIP_WORDS`
   - anything matching a detected ticker (case-insensitive)
3. Take the first 3 remaining words, in original order, and title-case them as the "topic words."
4. Compose the title:
   - Tickers found + topic words found → `"{tickers joined by ', '}: {topic words joined by ' '}"` e.g. `"AAPL, TSLA: Earnings Pullback"`
   - Topic words only, no tickers → `"{topic words}"` e.g. `"Earnings Pullback"`
   - Neither tickers nor topic words survive filtering (e.g. message is just "hey" or emoji) → fall back to today's existing format: `"Trade Chat — {date}"`
5. Truncate the final string to Discord's 100-character thread-name limit.

### Call site

In `discord_bot/commands/chat.py`, the `on_message` handler currently does:

```python
thread = await message.create_thread(
    name=f"Trade Chat — {datetime.now().strftime('%b %d')}",
    auto_archive_duration=1440,
)
```

This becomes:

```python
thread = await message.create_thread(
    name=build_thread_title(message.content, self.universe),
    auto_archive_duration=1440,
)
```

`build_thread_title` is imported from `src.chat` alongside the other helpers already imported there (`build_prompt`, `call_claude_chat`, `detect_tickers`, `format_screener_block`).

## Testing

Unit tests for `build_thread_title` covering:
- ticker + topic words present
- topic words only, no ticker
- neither present → date fallback
- long message → truncated to 100 chars
- multiple tickers → joined and capped (e.g. first 3)

No changes to Discord-side behavior beyond the `name=` argument, so no integration/mock-Discord tests are needed beyond the existing chat cog tests (if any exist — verify during planning).

## Out of scope

- Renaming existing/already-open threads.
- Using the LLM to generate the topic phrase (heuristic only, per user decision).
- Changing thread behavior for messages inside an existing thread (title is only set once, at creation).
