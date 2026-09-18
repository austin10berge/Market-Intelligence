# Trade Chatbot — Design Spec

**Date:** 2026-07-03  
**Status:** Approved

---

## Overview

A conversational trading partner living inside the existing Market Intelligence Discord bot. The user types naturally in a designated channel; the bot creates a thread per conversation, auto-fetches live screener data for any tickers mentioned, and responds as a knowledgeable partner who knows Austin's trading methodology and the reverse-engineered mLabs/V42 strategy.

This is a broad discussion partner — technical analysis, company outlooks, bullish/bearish reads, trade ideas, CSP/wheel setup evaluation — not a narrow criteria checker.

---

## Architecture

No new services or infrastructure. The chatbot is a new cog added to the existing `discord_bot/` that runs inside the already-deployed `discord-bot` Docker container.

```
discord_bot/
├── bot.py                  (add chat.py to setup_hook)
├── commands/
│   ├── chat.py             (NEW — channel listener + /trade-setup + conversation logic)
│   ├── scan.py
│   ├── insider.py
│   └── callback_server.py
└── utils/
    └── trade_chat.py       (NEW — prompt builder + claude -p caller + ticker detection)

src/screener/
└── stocks.py               (MODIFY — add RSI, volume ratio, EMA200, % from 52wk high, market cap)
```

Two new SQLite tables (added to `src/db.py`):

```sql
CREATE TABLE IF NOT EXISTS trade_chat_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- key='channel_id' stores the designated channel's Discord ID

CREATE TABLE IF NOT EXISTS trade_chat_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  TEXT NOT NULL,
    role       TEXT NOT NULL,   -- 'user' or 'assistant'
    content    TEXT NOT NULL,
    timestamp  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_chat_history_thread ON trade_chat_history(thread_id);
```

---

## Components

### 1. `/trade-setup` slash command

Run once in the target channel from mobile or desktop. The bot saves that channel's ID to `trade_chat_config` and confirms with a reply. On startup, the bot reads this value and begins listening to that channel.

### 2. Channel listener

The bot listens to `on_message` events. When a message arrives in the configured channel (not from a bot):

- If the message is a top-level message in the configured channel (not inside a thread): create a new Discord thread named `"Trade Chat — {date}"`, then process the message inside that thread.
- If the message is inside a thread whose `thread_id` exists in `trade_chat_history` (i.e., a previously created trade thread): process it there. This is how the bot distinguishes its own trade threads from any other threads in the channel.

This means each top-level message in the channel spawns its own thread. Conversations stay isolated.

### 3. Per-message pipeline (`trade_chat.py`)

For every message processed:

1. **Detect tickers** — two pass approach:
   - Match `$TICKER` pattern (explicit, zero false positives)
   - Match bare UPPERCASE tokens (2–5 chars) against a cached set of known tickers pulled from the screener universe in the DB. Excludes common non-ticker uppercase words (RSI, IV, ADR, EMA, SMA, ATM, etc.).

2. **Fetch screener data** — for each detected ticker, call `screen_stocks([ticker], persist_history=False)`. Run fetches concurrently via `asyncio.gather`. If a fetch fails or the ticker is unknown, inject `[TICKER: data unavailable]` and continue.

3. **Build prompt** — assemble:
   - System prompt (static, loaded once at startup)
   - Conversation history for this thread (from SQLite, formatted as alternating User/Assistant blocks)
   - Current user message with screener data blocks injected inline (see Data Injection Format below)

4. **Call `claude -p`** — subprocess call, same pattern as `synthesis/llm.py`. 120s timeout. If it fails, fall back to Gemini via `synthesize()`.

5. **Post response** — send Claude's reply to the thread.

6. **Persist** — append both the user message (with injected data) and the assistant response to `trade_chat_history`.

### 4. Conversation history

Loaded from SQLite at the start of each message processing. No fixed truncation window initially — the full thread history is included. If prompts grow very long (>50 exchanges), a rolling window of the last 30 exchanges can be added later.

---

## System Prompt

The system prompt is a static string loaded at bot startup from a file: `discord_bot/trade_system_prompt.txt`. It has three sections:

**Section 1 — Trading DNA (~500 tokens)**  
Distilled from Austin's Obsidian methodology notes. Captures: trading philosophy, risk tolerance, preferred setups, conviction thresholds, sector preferences, wheel/CSP execution rules, position sizing instincts, what makes him pass or fade a name. This is a one-time manual step: paste Obsidian notes into Claude, ask it to compress into a tight trading partner brief.

**Section 2 — mLabs/V42 strategy context (~400 tokens)**  
The reverse-engineered filter set as structured reference, presented as "one useful lens Austin applies, especially for CSP screening." Includes:
- 4-tier hierarchy: regime → fundamentals → technicals → options contracts
- V42 gate values (bb_width_pct_max=21, forward_pe_max=45, volume_ratio_max=1.15, rv20_max=0.45, adr20_pct_max=4.0, tech_rsi_max=60, etc.)
- Sector-specific overrides (fin_rsi_max=70, hc_rsi_max=60, fin_market_cap_b_min=100)
- Known structural characteristics: VCP pattern, wheel execution, "boring names" bias, EOD scanning
- Known dead-ends: FCX (high rv20+pfh), AAL/AA (sub-25B mcap), QCOM below EMA200

**Section 3 — Persona instructions (~150 tokens)**  
Direct and opinionated trading partner. Can discuss: TA (trend, momentum, VCP, Bollinger, RSI, MA structure), fundamentals, macro, company outlook, bullish/bearish reads, trade ideas, CSP/wheel setups. When screener data is injected, uses the numbers to reason concretely. Asks clarifying questions when context matters ("what's your timeframe?" / "are you thinking CSP or just watching?"). Does not hedge excessively — gives a directional lean. The mLabs criteria are one lens, not the whole answer.

---

## Data Injection Format

Injected inline after the user's message, before sending to Claude:

```
[NVDA — live data, {date}]
Price: $138.42 (+0.8% 1d, +2.1% 1w, -4.3% 1m)
RSI: 56.2 | BB width: 13.1% (upper: $142.10 / lower: $134.90)
SMA200: $128.40 (+8.0% above) | EMA200: $130.10 (+6.4% above)
vs 52wk high: -8.4% | Volume ratio: 0.97 | ADR20: 3.2%
IV (ATM): 34% | IV percentile: 42% | IV/RV ratio: 1.18 | RV20: 28.8%
Sector: Technology | Market cap: $3.38T | Beta: 1.72
Valuation: PE=48.2 | Fwd PE=29.1 | PEG=1.8
Fundamentals: EPS growth=+22% | Rev growth=+12% | FCF=$60.8B | D/E=0.42
V42 gates: PASS ✓  (or list failing gates if any)
```

Fields omitted if unavailable (shown as N/A in raw data, omitted from injection to save tokens).

---

## Screener Additions (`screen_stocks()`)

Fields to add to the candidate dict returned by `screen_stocks()`:

| Field | Source | Notes |
|---|---|---|
| `rsi` | `pandas_ta.rsi()` on price history, period=14 | Already using pandas_ta for BB |
| `sma_50` | `pandas_ta.sma()` on price history, period=50 | For MA structure discussion |
| `ema_200` | `pandas_ta.ema()` on price history, period=200 | V42 uses EMA200 as gate |
| `volume_ratio` | `current_volume / avg_volume_20d` | 20-day avg volume from price history |
| `pct_from_52wk_high` | `(price - 52wk_high) / 52wk_high * 100` | From yfinance `fiftyTwoWeekHigh` |
| `market_cap` | `yfinance info.get("marketCap")` | Raw value in dollars |
| `sma_200_pct` | `(price - sma_200) / sma_200 * 100` | Already have sma_200, compute pct |
| `ema_200_pct` | `(price - ema_200) / ema_200 * 100` | Derived from new ema_200 |
| `bb_width_pct` | `(bb_upper - bb_lower) / bb_mid * 100` | Already have raw BB values |

All additions use data already fetched (yfinance history + info). No new API calls required.

---

## Error Handling

- **Screener fetch fails for a ticker**: inject `[TICKER: data unavailable]`, continue with conversation
- **`claude -p` fails or times out**: fall back to `synthesize()` (Gemini), same as pipeline does today
- **No channel configured**: `/trade-setup` not yet run — bot does nothing and logs a warning
- **Thread creation fails**: bot replies in the channel directly instead of creating a thread
- **Unknown ticker detected**: skip silently (filtered out by universe check)

---

## One-Time Setup Steps (before first use)

1. **Distill system prompt** — paste Obsidian notes into Claude, ask for a ~500 token trading partner brief, save to `discord_bot/trade_system_prompt.txt`
2. **Run `/trade-setup`** in the desired Discord channel from mobile or desktop
3. **Deploy** — `docker compose up --build` picks up the new cog automatically

---

## Out of Scope

- Order execution or brokerage integration
- Automated trade alerts (existing pipeline handles this)
- Web UI / non-Discord interface
- Fine-tuned or locally-hosted models
- Retention policy / conversation pruning (can add later)
