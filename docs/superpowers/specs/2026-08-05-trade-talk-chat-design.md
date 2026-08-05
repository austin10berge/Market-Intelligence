# trade-talk Skill Design

**Date:** 2026-08-05  
**Status:** Approved

## Problem

Austin wants a way to drop into a Claude Code session and chat about trading — review a ticker's price action, check today's scanner candidates, or think through a CSP setup — with his full methodology (the same system prompt powering the Discord Trade Talk bot) already loaded as context. Today that requires copy-pasting or manually reading the file.

## Goal

A `/trade-talk` skill that, when invoked, reads the single source-of-truth methodology file and fetches current market posture up front — then lets Austin ask about anything: tickers, candidates, watchlists, or pure strategy discussion, pulling live data only when the question calls for it.

## Scope

**In:** reading methodology file, market posture prefetch, on-demand scanner/watchlist/ticker data, computing TA from Schwab OHLCV bars.  
**Out:** editing the methodology file (that's `/trade-prompt`), backtesting (that's `/backtest`), order execution.

---

## Architecture

### Trigger

- Slash command: `/trade-talk`
- Auto-trigger description: "Use when discussing trading, reviewing a ticker, checking CSP/LEAPS candidates, or evaluating a trade setup against Austin's methodology."

### Startup sequence (every invocation)

1. **Read** `discord_bot/trade_system_prompt.txt` — the full methodology file. Do not duplicate its content into the skill; just read it fresh each time so any edits via `/trade-prompt` are instantly reflected.
2. **Fetch market posture** via `GET https://market.austin10berge.com/api/market-posture` (30s timeout). Announce posture label and composite score at the top of the first reply — gives instant regime context before Austin even asks.

### On-demand data (fetched only when the question calls for it)

| Question type | Tool / endpoint |
|---|---|
| Today's CSP candidates / scanner | `GET .../api/screener/csp` (30s timeout, 1 retry — known to be intermittently slow) |
| LEAPS candidates | `GET .../api/screener/leaps` |
| CSP watchlist | `GET .../api/watchlist` |
| Stock watchlist | `GET .../api/watchlist/stock` |
| Per-ticker price action, RSI, Bollinger, trend | Schwab MCP: `get_advanced_price_history` + `get_quotes` → compute indicators from raw OHLCV bars |

Use `https://market.austin10berge.com` for all MI API calls (prod). The internal `http://api:8000` hostname is only valid inside the Docker network.

### Behavior guardrails (same as the Discord bot)

- Never state a specific number — price, RSI, IV, %-move, BB width — unless it came from an actual tool call **in the current turn**.
- If a fetch fails, say so plainly. Never invent plausible-sounding data.
- If no ticker is named but "show me candidates" or similar is asked, pull from the scanner rather than inventing tickers.

### Relationship to other skills

| Skill | Responsibility |
|---|---|
| `trade-talk` (this) | Read methodology, chat, pull live data |
| `trade-prompt` | Edit `discord_bot/trade_system_prompt.txt` |
| `backtest` | Backtest a strategy/gate/threshold |

These never overlap: `trade-talk` opens the methodology file read-only; only `trade-prompt` has write authority over it.

---

## File to create

`Market-Intelligence/.claude/skills/trade-talk/SKILL.md`

The skill file should:
- State the trigger and auto-trigger description
- Include the startup checklist (read file → fetch posture → summarize posture in first reply)
- Include the on-demand data table with exact endpoint URLs and timeout/retry notes
- Include the guardrails section
- Include a short cross-reference to `trade-prompt` and `backtest` so Austin knows where to go for those actions

---

## Out of scope / future ideas

- A richer per-ticker view combining the CSP scanner's computed signals (bb_width_pct, rv20, adr20, etc.) with Schwab price history in one block — would require a new `/api/screener/ticker/{ticker}` endpoint, not covered here.
- Session memory across conversations for "last ticker we looked at" — not needed yet.
