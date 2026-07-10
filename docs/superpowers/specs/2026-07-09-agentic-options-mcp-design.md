# Agentic Options/Stock Data Tool Calls for Trade Chat — Design Spec

**Date:** 2026-07-09
**Status:** Approved

---

## Overview

Extend the trade chatbot so it can fetch additional live market data on its own accord mid-response, instead of being limited to whatever the server-side prefetch grabbed for the current message. This supplements — does not replace — the existing deterministic prefetch described in [2026-07-06-live-options-chain-lookup-design.md](2026-07-06-live-options-chain-lookup-design.md): `detect_options_intent()` → `fetch_options_grid()` still runs on every message and remains the fast, deterministic path for the common case. The new capability covers what the prefetch can't anticipate — e.g. a thread follow-up like "what about the 8/21s" or "check the same setup on PLTR" where the user didn't mention that ticker/expiration in the message the prefetch scanned.

This is made possible by wiring the bot's `claude -p` call in `src/chat.py::call_claude_chat` to **Alpaca's official MCP server** (`alpaca-mcp-server`, [github.com/alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server)) rather than a hand-rolled tool. Alpaca is already the data source behind the existing prefetch (`src/screener/options_lookup.py`, `src/screener/stocks.py`), so this keeps the bot on a single, consistent, officially-maintained data provider instead of introducing a second one.

**Threat model note:** the trade chat Discord is private with a single user (the operator) with access. The standing rule from prior discord-bot hardening work — isolate config, hardcode minimal tool grants, no bot-writable settings.json — was written for a threat model of untrusted multi-user chat input reaching a tool-enabled prompt. That threat model doesn't apply here; the operator explicitly accepted the residual risk of agentic tool use for this single-user bot. The existing isolated-config pattern for the discord-bot service is kept regardless, since it costs nothing and remains good practice independent of the injection question.

**Post-implementation correction (2026-07-10):** the original design below specified `stdio` transport, with `claude -p` spawning a fresh `alpaca-mcp-server` process per invocation. Live verification found this never actually worked: `claude -p` dispatches its first API request without waiting for `--mcp-config` servers to finish connecting ("running fully async, nonblocking" per `--debug` output), and a fresh `alpaca-mcp-server` process takes ~5s to boot (Python + FastMCP + alpaca-py imports) — well past the ~1.3s the model needs to produce a first-turn response. The tool was reliably never available by the time it mattered; the model always answered as if it only had `WebSearch`. Root-caused via direct `claude -p --debug-file` reproduction, confirmed via a control run where the connection incidentally completed in time (`Successfully connected... in 4992ms`, matching capabilities `hasTools:true`) and still lost the race against a ~2.7s total session. Fix: `alpaca-mcp-server` now runs as its own persistent `streamable-http` service (`alpaca-mcp` in `docker-compose.yml`, always-on, already connected), and `alpaca-mcp.json` points at it over HTTP instead of spawning it. HTTP connection time to an already-running server measured at ~104ms — confirmed via the same reproduction method, including an end-to-end tool call returning real live SOFI option data. The "Components" and "Error Handling" sections below are updated to reflect this; the "Architecture" and code-sample sections retain their original stdio-era wording where it doesn't affect correctness, since the CLI flags in `call_claude_chat` (`--mcp-config`, `--strict-mcp-config`, `--allowedTools`) are unchanged — only `alpaca-mcp.json`'s contents and the addition of the `alpaca-mcp` service changed.

---

## Architecture

```
discord_bot/
├── alpaca-mcp.json          (NEW — MCP server config, committed, secrets via ${VAR} expansion)
└── commands/chat.py          (unchanged)

src/chat.py                   (MODIFY — call_claude_chat gains --mcp-config + expanded tool flags)

pyproject.toml                (MODIFY — add alpaca-mcp-server dependency)
```

No change to Discord-side thread routing or message handling — `_handle_message` in `discord_bot/commands/chat.py` already invokes the full chat pipeline (prefetch → prompt build → `call_claude_chat`) on every message inside a known trade thread, not just the first.

No change to `src/screener/options_lookup.py` or `src/screener/stocks.py` — the existing prefetch functions and their direct Alpaca REST calls are untouched.

---

## Components

### 1. `alpaca-mcp` service (`docker-compose.yml`, new) + `discord_bot/alpaca-mcp.json` (new)

**Corrected from the original stdio design** — see the post-implementation note above. `alpaca-mcp-server` now runs as its own always-on service, built from the `base` Docker target (already has the package installed via `pyproject.toml`), started with `--transport streamable-http --host 0.0.0.0 --port 8001`:

```yaml
alpaca-mcp:
  build:
    context: .
    target: base
  env_file: .env
  environment:
    ALPACA_SECRET_KEY: ${ALPACA_API_SECRET}
    ALPACA_PAPER_TRADE: "true"
    ALPACA_TOOLSETS: "options-data,assets,stock-data"
  command: alpaca-mcp-server --transport streamable-http --host 0.0.0.0 --port 8001
  expose:
    - "8001"
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "curl", "-s", "-o", "/dev/null", "http://localhost:8001/mcp"]
    interval: 15s
    timeout: 5s
    retries: 3
    start_period: 10s
```

`discord-bot` gains `alpaca-mcp` as a `depends_on: condition: service_healthy` dependency. `discord_bot/alpaca-mcp.json` (committed; no secrets — the service holds them, not this file) now just points at the running service over HTTP:

```json
{
  "mcpServers": {
    "alpaca": {
      "type": "http",
      "url": "http://alpaca-mcp:8001/mcp",
      "timeout": 30000
    }
  }
}
```

- **Env var name bridge:** the repo's existing secret is named `ALPACA_API_SECRET` (see `.env.example`); the Alpaca MCP server expects `ALPACA_SECRET_KEY`. Mapped in the service's `environment:` block (Docker Compose's own `${VAR}` substitution from the project's `.env` file — the same pattern already used by `youtube-summarizer`'s `DISCORD_WEBHOOK_URL` in this compose file), not by renaming the repo-wide variable.
- **`ALPACA_PAPER_TRADE=true`:** set for defense-in-depth even though the `trading` toolset (order placement) is not enabled — see Toolset Scope below.
- **`ALPACA_TOOLSETS=options-data,assets,stock-data`:** restricts the server to read-only market-data tools. Excludes `trading`, `positions`, `account`, `watchlists`, `crypto-data`, `news`, `fixed-income-data`, `index-data`, `corporate-actions`, and locate-related toolsets.
- **Healthcheck has no `-f`:** the MCP streamable-http endpoint returns 406 on a bare GET without the protocol's `Accept` header, even when the server is fully healthy — confirmed during implementation. The healthcheck only needs to confirm the port accepts connections, not that a specific status code comes back.
- **`timeout: 30000`** in `alpaca-mcp.json`: per-tool-call wall-clock limit, so one slow Alpaca call can't consume the full 120s budget on `call_claude_chat`. No longer doubles as a connection-startup allowance now that the server is already running.

### 2. Toolset Scope

Enabled toolsets and their tools, per Alpaca MCP server v2:

- **`options-data`:** `get_option_chain`, `get_option_snapshot`, `get_option_latest_quote`, `get_option_latest_trade`, `get_option_bars`, `get_option_trades`, `get_option_exchange_codes`
- **`assets`:** `get_option_contracts`, `get_option_contract`, `get_all_assets`, `get_asset`, `get_calendar`, `get_clock`, `get_corporate_action_announcements`, `get_corporate_action_announcement`
- **`stock-data`:** `get_stock_bars`, `get_stock_quotes`, `get_stock_trades`, `get_stock_latest_bar`, `get_stock_latest_quote`, `get_stock_latest_trade`, `get_stock_snapshot`, `get_most_active_stocks`, `get_market_movers`

Explicitly **not** enabled: `trading` (order placement — stocks/crypto/options, single or multi-leg), `positions` (includes `close_all_positions`, `exercise_options_position`), `account` (balances, config), `watchlists` (create/update/delete), `crypto-data`, `news`, `fixed-income-data`, `index-data`, `corporate-actions` (market-data variant), and `locates`. These are excluded regardless of the waived injection concern — they let the bot place orders, liquidate positions, or spend money rather than just answer questions, which is a separate concern from prompt-injection risk.

### 3. `pyproject.toml`

Add `alpaca-mcp-server` to the flat `dependencies` list (matches the repo's existing pattern — no per-service dependency splitting exists today; `api` and `pipeline` images will carry the unused package, consistent with how `base` already installs one shared dependency set for all targets).

### 4. `src/chat.py::call_claude_chat`

Extend the `claude -p` subprocess invocation to add `--mcp-config discord_bot/alpaca-mcp.json` and widen the tool flags to include the Alpaca MCP tools alongside the existing `WebSearch`:

```python
proc = await asyncio.create_subprocess_exec(
    "claude", "-p",
    "--mcp-config", "discord_bot/alpaca-mcp.json",
    "--tools", "WebSearch,mcp__alpaca__*",
    "--allowedTools", "WebSearch,mcp__alpaca__*",
    ...
)
```

Exact flag syntax (comma-separated list vs. repeated flags vs. wildcard support for `mcp__alpaca__*`) to be confirmed against the installed CLI version during implementation; `ALPACA_TOOLSETS` already gates which tools exist server-side, so a wildcard allow-pattern here is not a scope expansion beyond what's configured in `alpaca-mcp.json`.

### 5. Secrets

No new secrets. Reuses `ALPACA_API_KEY` / `ALPACA_API_SECRET`, already present in `.env` and already loaded into the discord-bot container via `env_file: .env` in `docker-compose.yml`.

---

## Data Flow

Per message inside a trade chat thread (`_handle_message`, unchanged):

1. `detect_tickers()` + `gather_chat_blocks()` run the existing prefetch (screener data always; options grid when `detect_options_intent()` fires) → `screener_blocks`.
2. `build_prompt()` assembles system prompt + history + current message + `screener_blocks`, as today.
3. `call_claude_chat()` invokes `claude -p` — now with Alpaca MCP tools and WebSearch available.
4. If the model determines it needs data not present in `screener_blocks` (different ticker, different expiration/strike window, historical bars, etc.), it calls an Alpaca MCP tool, receives a structured result, and incorporates it into the same response — no change to the one-shot nature of the call from the bot's perspective; the tool round-trip happens inside the single `claude -p` invocation.
5. Final text response returned, persisted to `trade_chat_history` via `save_trade_chat_message`, and sent to Discord — identical to today's flow.

---

## Error Handling

- **MCP server unreachable** (service down, `docker compose` dependency not yet healthy, etc.): `claude -p` degrades by treating the Alpaca tools as unavailable rather than failing the entire call — confirmed during implementation (this is also exactly the failure mode that made the original stdio design silently non-functional: from the model's perspective, "MCP server not ready yet" and "MCP server down" look identical). `WebSearch` isn't dependent on the Alpaca server and still works. If the whole call fails hard for an unrelated reason, the existing Gemini fallback in `_handle_message` (triggered whenever `call_claude_chat` returns `None`) still covers total failure — also confirmed during implementation, separately, when the discord-bot's isolated Claude Code session had no valid credentials (`Not logged in`) and every `claude -p` call failed outright.
- **`discord-bot` starts before `alpaca-mcp` is ready:** guarded by `depends_on: alpaca-mcp: condition: service_healthy` in `docker-compose.yml` — Compose won't start `discord-bot` until the healthcheck passes.
- **Slow/hung tool call:** bounded by the per-server `timeout: 30000` in `alpaca-mcp.json`, nested inside the existing 120s timeout on the overall `call_claude_chat` call.
- **No rate limiting or cost caps added.** Single-user private use; the prefetch remains bounded at one Alpaca call per ticker per message as before, but the model could in principle make several tool calls in one turn. Alpaca's own API rate limits are the natural backstop. Not addressed further in v1 — revisit if it becomes a problem in practice.

---

## Testing

- No new tests for the prefetch path (`detect_options_intent`, `fetch_options_grid`, `format_options_block`) — unchanged.
- Smoke test that `alpaca-mcp.json` launches `alpaca-mcp-server` cleanly with the configured environment and exposes exactly the tools implied by `ALPACA_TOOLSETS` (manual or scripted check, not part of the pytest suite — this is a subprocess/MCP-protocol concern, not application logic).
- Manual verification in the dev Discord trade chat thread: ask a follow-up question outside the prefetch's scope (different ticker or expiration not in the current message) and confirm the model calls an Alpaca tool and answers correctly.

---

## Out of Scope (v1)

- Removing or modifying the existing regex-based prefetch (`detect_options_intent`, `fetch_options_grid`) — it stays as the fast-path default.
- Enabling `trading`, `positions`, `account`, `watchlists`, `crypto-data`, `news`, `fixed-income-data`, `index-data`, `corporate-actions`, or `locates` toolsets.
- Rate limiting or per-message/per-day cost caps on tool calls.
- Multi-turn agentic loops beyond what a single `claude -p` invocation's built-in tool-use supports.
