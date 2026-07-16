# Trade-Chat MCP Tool for Market Intelligence Data

## Problem

The trade-chat bot (`discord_bot/commands/chat.py` → `src/chat.py`) already injects live
per-ticker screener data and options grids when a message names a ticker or sounds
options-related, and has read-only Alpaca + Schwab MCP tools for external market data and
account state. It has no way to answer questions about Austin's own Market Intelligence
watchlists or scanner output ("what's on my CSP watchlist", "what's the scanner showing
today", "any LEAPS candidates") — that data only exists via the FastAPI backend
(`src/api/main.py`), which the bot never calls.

## Goals

- Give the trade-chat bot's model a way to pull watchlist and scanner data from the MI API
  as an on-demand tool call (the model decides when to use it, like `get_movers` today) —
  not an always-injected block, and not raw shell access.
- Keep the same minimal-blast-radius tool-grant pattern already established for this bot
  (`[[feedback_bot_tool_permissions]]`): narrow, typed, hardcoded in `--allowedTools`, no
  standing broad capability (no `Bash`, no `WebFetch`).
- Add no new infrastructure: no new Docker service, no new port, no new dependency, no
  `docker-compose.yml` change.

## Context this design relies on

- This is a private, single-user Discord server — the untrusted-input threat model that
  originally justified isolating this bot's config (`[[project_discord_bot_permissions]]`)
  is about a shared/multi-user server; it doesn't change the tool-scoping decision here
  (narrow MCP tool is still simpler and more reliable than scoped `Bash(curl)`), but it's
  why a stdio-spawned, non-persistent server is acceptable without extra hardening.
- `mcp` and `fastmcp` Python packages are already installed in the `discord-bot` image as
  transitive dependencies of `alpaca-mcp-server` / `schwab-mcp` / `mcp-proxy` (confirmed via
  `docker exec market-intelligence-discord-bot pip show mcp`) — no `pyproject.toml` change
  needed.
- `discord_bot/` is already fully `COPY`'d into the image by the shared `base` Dockerfile
  stage — no Dockerfile change needed for a new file placed there.
- The `api` service is already always-on inside the same Docker network at `http://api:8000`
  and already implements market-hours-aware Redis caching for the screener endpoints — the
  new tool should call that instead of recomputing screener logic itself.

## Design

### New file: `discord_bot/mi_mcp_server.py`

A FastMCP **stdio** server (spawned by `claude -p` per chat turn via `--mcp-config`, not an
always-on process — matches the transient-subprocess lifecycle of a normal tool call). Each
tool is a thin `httpx` GET against the already-running `api` service and returns the parsed
JSON body directly, so the model gets structured data rather than a raw/opaque blob.

Five tools, one per endpoint:

| Tool | Calls | Returns |
|---|---|---|
| `get_csp_watchlist()` | `GET /api/watchlist` | CSP screener ticker list |
| `get_stock_watchlist()` | `GET /api/watchlist/stock` | Stock screener ticker list |
| `get_csp_candidates()` | `GET /api/screener/csp` | Curated CSP candidates (current scanner output) |
| `get_leaps_candidates()` | `GET /api/screener/leaps` | Curated LEAPS candidates |
| `get_market_posture()` | `GET /api/market-posture` | Latest digest: composite score, posture, signals |

No arguments on any tool (all five source endpoints are argument-free reads). Each function
raises on non-2xx via `response.raise_for_status()` — FastMCP surfaces the exception to the
model as a tool-call error, which is sufficient; no bespoke retry/error-formatting logic.

### New file: `discord_bot/mi-mcp.json`

Same shape as the existing `alpaca-mcp.json`/`schwab-mcp.json`, but stdio instead of http:

```json
{
  "mcpServers": {
    "mi": {
      "type": "stdio",
      "command": "python3",
      "args": ["mi_mcp_server.py"]
    }
  }
}
```

### Edit: `src/chat.py`

- Add a `_MI_MCP_CONFIG_PATH` constant (mirrors `_MCP_CONFIG_PATH` / `_SCHWAB_MCP_CONFIG_PATH`).
- Add a `_MI_ALLOWED_TOOLS` tuple listing the five `mcp__mi__*` tool names (mirrors
  `_ALPACA_ALLOWED_TOOLS` / `_SCHWAB_ALLOWED_TOOLS`).
- In `call_claude_chat`, add `_MI_MCP_CONFIG_PATH` to the `--mcp-config` argument list and
  `*_MI_ALLOWED_TOOLS` to `--allowedTools`. `ToolSearch` is already granted and required for
  the model to discover any MCP tool schema (established in
  `[[project_schwab_mcp_discord_bot]]`) — no change needed there.

### Edit: `discord_bot/trade_system_prompt.txt`

Add a short paragraph (near the existing Schwab-tools paragraph) telling the model:
- These tools exist and what each returns.
- Prefer them over the injected per-ticker screener block when the question is about the
  watchlist/scanner/regime as a whole rather than a single already-named ticker.
- Same anti-fabrication rule already in place applies: don't state watchlist/candidate
  contents unless they came from an actual tool call this turn.

### Not in scope

- `/api/screener/csp-scan` (the broad configurable scan) and `/api/insider` — not part of
  the original ask ("watchlist and scanner"); can be added the same way later if wanted.
- No changes to `docker-compose.yml`, `Dockerfile`, or `pyproject.toml`.
- No caching layer inside `mi_mcp_server.py` — it rides the API's existing cache.

## Testing plan

- Unit-style: `mi_mcp_server.py` is a stdio server, so it can't usefully be run standalone —
  instead, `docker exec` into the running `discord-bot` container and call the five tool
  functions directly from a `python3 -c` one-liner (they're plain `async def`s, no MCP
  plumbing required to invoke them), confirming each returns the same JSON shape as
  `curl http://localhost:8000/api/...` from the dev host.
- End-to-end: in the real (dev) trade-chat Discord thread, ask a watchlist question ("what's
  on my CSP watchlist right now?") and a scanner question ("any CSP candidates worth
  looking at today?") and confirm the reply's data matches `GET /api/watchlist` /
  `GET /api/screener/csp` output, and that a transcript under
  `/root/.claude/projects/-app-discord-bot/*.jsonl` (or local-dev equivalent) shows an
  actual `mcp__mi__*` tool_use record for that turn, not a fabricated answer.
- Confirm `ruff check` / `ruff format` pass on the two edited/new Python files (auto-runs via
  the `PostToolUse` hook per `CLAUDE.md`, but re-verify explicitly since this is a new file).

## Rollout

Dev-only for this task — this is a local dev-workspace bot, no prod rollout step identified
in earlier memory beyond the existing pending Schwab-MCP prod rollout
(`[[project_schwab_mcp_discord_bot]]`), which this doesn't change.
