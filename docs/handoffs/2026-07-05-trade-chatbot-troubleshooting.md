# Trade Chatbot — Troubleshooting Handoff

**Date:** 2026-07-05  
**Branch:** `task-1-screener-fields`  
**Prod host:** firefly / 10.0.1.21 (`~/market-intelligence`)  
**Dev host:** this machine (dev bot is **stopped** — do not restart it while prod bot is live)

---

## What's Built and Working

- Discord bot creates a new thread per top-level message in the configured channel (`/trade-setup` sets the channel)
- Live screener data (RSI, BB, EMA200, vol ratio, IV, etc.) is auto-fetched for any ticker mentioned and injected into the prompt
- Conversation history is stored in SQLite (`trade_chat_history`) and loaded on every message
- System prompt (`discord_bot/trade_system_prompt.txt`) contains Austin's trading DNA + V42 criteria + persona instructions

## The Core Problem

**`claude` CLI is not installed in the Docker container.** The chatbot was designed to call `claude -p` as a subprocess (same pattern as `src/synthesis/llm.py`). That binary is only on the host machine, not inside Docker. Every LLM call hits `FileNotFoundError` and falls back to Gemini via `synthesize()`.

```
# Prod log evidence:
[WARNING] src.chat: chat: 'claude' binary not found
```

The Gemini responses are noticeably lower quality — less personality, less specific to Austin's methodology.

## Bugs Fixed This Session (all on branch, latest commit `58717bd`)

| Fix | Commit |
|-----|--------|
| Double reply (thread starter message triggered second `_handle_message`) | `2138987` |
| Ticker detection only worked for 7-ticker personal watchlist | `dfa38a1` |
| Gemini fallback ignored conversation history (no context on thread replies) | `58717bd` |

**`58717bd` was pushed but NOT yet deployed to prod.** Deploy with:
```bash
git pull origin task-1-screener-fields
docker compose up -d --no-build discord-bot
```

## What Needs to Be Fixed Next

### Option A — Mount the host `claude` binary into the container (simplest)

The `claude` CLI lives at `/usr/local/bin/claude` or `~/.local/bin/claude` on the host. Mount it into the container via `docker-compose.yml` volumes, and also mount the Claude auth config (`~/.claude/`).

```yaml
# docker-compose.yml — discord-bot service
volumes:
  - /usr/local/bin/claude:/usr/local/bin/claude:ro
  - /root/.claude:/root/.claude:ro   # or /home/dev/.claude:/root/.claude:ro
```

Risk: auth tokens expire; container user may differ from host user owning the config.

### Option B — Call Anthropic API directly (most robust)

Replace `call_claude_chat()` in `src/chat.py` with a direct `anthropic` Python SDK call. Uses proper multi-turn messages API instead of text-prompt hacking.

- Check if `anthropic` is already in `pyproject.toml` (likely yes, used elsewhere)
- Use `claude-sonnet-4-6` model
- Pass `system=` + `messages=[{role, content}]` array (already have history in right shape from `get_trade_chat_history()`)
- Needs `ANTHROPIC_API_KEY` env var — user said they don't have one separately, but check if it's already set for other services

### Option C — Keep Gemini, accept the quality difference

`58717bd` fixed the missing history. Gemini now has full context. Quality is lower but functional.

## Key Files

| File | Purpose |
|------|---------|
| `src/chat.py` | `call_claude_chat()`, `detect_tickers()`, `build_prompt()`, `format_screener_block()` |
| `discord_bot/commands/chat.py` | Discord cog — `on_message`, `_handle_message`, `/trade-setup` |
| `discord_bot/trade_system_prompt.txt` | Static system prompt (trading DNA + V42 + persona) |
| `src/db.py` | `get/save_trade_chat_*` functions, `is_trade_chat_thread()` |
| `src/synthesis/llm.py` | `synthesize()` — Gemini fallback used for ALL chat responses currently |

## Prod State

```bash
# Verify claude CLI presence (should be empty = not installed):
docker compose exec discord-bot which claude

# Check channel is configured:
docker compose exec discord-bot python3 -c "
from src.db import get_trade_chat_channel_id
print(get_trade_chat_channel_id())
"

# Check recent conversation history:
docker compose exec discord-bot python3 -c "
import sqlite3
conn = sqlite3.connect('/app/discord_bot/data/market_intelligence.db')
for r in conn.execute('SELECT id, role, content[:200], timestamp FROM trade_chat_history ORDER BY id DESC LIMIT 6').fetchall():
    print(r)
"
```
