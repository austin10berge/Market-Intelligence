# Schwab MCP → Trade Chat Bot — Design Spec

**Date:** 2026-07-15
**Status:** Approved

---

## Overview

Wire the bot's `claude -p` call in `src/chat.py::call_claude_chat` to the **Schwab MCP server** (`schwab-mcp`, [github.com/jkoelker/schwab-mcp](https://github.com/jkoelker/schwab-mcp), read-only credentials already set up on this host — see `[[reference_schwab_mcp]]`), giving the trade chat bot access to real portfolio data: positions, balances, order history, transaction history, and live quotes/option chains from the operator's actual Schwab account.

This is distinct from the existing Alpaca MCP integration ([2026-07-09-agentic-options-mcp-design.md](2026-07-09-agentic-options-mcp-design.md)): Alpaca runs in paper-trade mode and only ever provided market/options data, never real account state. Schwab is the first source of real portfolio data (actual positions, actual P&L, actual buying power) reaching the bot.

**Threat model note:** same as the Alpaca integration — the trade chat Discord is private, single-user (the operator). The standing multi-user-untrusted-input hardening rule doesn't apply to the *agentic tool-use* surface here; the operator has accepted that residual risk for this bot already. The isolated `.claude` config mount and hardcoded `--allowedTools` are kept regardless, since they cost nothing.

**Order execution is not exposed, and cannot be smuggled in even via prompt injection:** the installed `schwab-mcp` build never registers `place_previewed_order`, `place_order`, or `cancel_order` at the source level (they only exist when `--jesus-take-the-wheel` or Discord-approval flags are passed at server startup, neither of which this design uses). Order-preview tools (`preview_*`) are also deliberately excluded from the allowlist below, even though they can't execute anything — no reason to hand a chat-input-driven LLM call the ability to construct order payloads at all.

---

## Architecture — informed by the Alpaca stdio failure

The original Alpaca design ([2026-07-09] "Post-implementation correction") spawned `alpaca-mcp-server` fresh per `claude -p` call over stdio, and this **silently never worked**: `claude -p` dispatches its first request without waiting for `--mcp-config` stdio servers to finish connecting, and a freshly-spawned Python/FastMCP process (~5s boot) reliably lost the race against the model's ~1.3s first-turn response. The fix was making `alpaca-mcp-server` a persistent, always-on `streamable-http` service.

`schwab-mcp` is stdio-only at the CLI level (confirmed from source — no `--transport` flag; `anyio.run(server.run, backend="asyncio")` hardcodes FastMCP's stdio transport) and has a comparable-or-heavier import cost (`discord.py`, `httpx`, `schwab-py`, `mcp`, plus its own package). Spawning it fresh per call would hit the identical failure mode. So this design applies the same fix Alpaca needed, from the start: **wrap `schwab-mcp` in a stdio-to-HTTP bridge** ([`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy), Python/pip-installable — fits the existing `base` image without adding a Node runtime) running as its own persistent `docker-compose` service, so `discord-bot` always talks to an already-connected HTTP endpoint, never spawns a process itself.

```
discord_bot/
├── schwab-mcp.json          (NEW — MCP server config, committed, points at the bridge over HTTP)
└── commands/chat.py          (unchanged)

src/chat.py                   (MODIFY — call_claude_chat gains a second --mcp-config + expanded tool flags)

docker-compose.yml            (MODIFY — new `schwab-mcp` service: mcp-proxy wrapping `schwab-mcp server`)

pyproject.toml                (MODIFY — add schwab-mcp + mcp-proxy dependencies)

discord_bot/trade_system_prompt.txt  (MODIFY — tell the model it has live Schwab account access)
```

No change to Discord-side routing, the existing prefetch (`detect_tickers`/`gather_chat_blocks`), or the Alpaca integration — this is purely additive, a second MCP server alongside the existing one.

---

## Components

### 1. `schwab-mcp` service (`docker-compose.yml`, new)

Built from the `base` target (same as `alpaca-mcp`), running `mcp-proxy` in front of `schwab-mcp server`:

```yaml
schwab-mcp:
  build:
    context: .
    target: base
  container_name: market-intelligence-schwab-mcp
  volumes:
    # Read-only credentials — only this service's container ever sees them;
    # discord-bot reaches Schwab data exclusively via the HTTP MCP endpoint below,
    # never via filesystem access to the token file.
    - ~/.local/share/schwab-mcp:/root/.local/share/schwab-mcp:ro
  command: >
    mcp-proxy --port 8002 --host 0.0.0.0
    -- schwab-mcp server --no-technical-tools
  expose:
    - "8002"  # Internal only — discord-bot reaches it via the Docker network
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "curl", "-s", "-o", "/dev/null", "http://localhost:8002/mcp"]
    interval: 15s
    timeout: 5s
    retries: 3
    start_period: 10s
```

- **`--no-technical-tools`**: passed to `schwab-mcp server` for defense-in-depth scope minimization at the source, matching the tool scope agreed below — exact set of tools this disables to be confirmed during implementation (not observed in the tool list captured during earlier ad-hoc verification, so it may not add much beyond what's already excluded — verify and adjust this flag if it turns out to remove tools we actually want).
- **Exact `mcp-proxy` flag syntax** (the `--` separator between proxy flags and the wrapped command, default endpoint path for streamable-http mode) **to be confirmed against the installed CLI version during implementation** — same hedge as the original Alpaca spec's flag syntax, for the same reason (don't want to hand-guess a bridge tool's CLI contract in the design doc).
- **`~/.local/share/schwab-mcp` read-only mount**: the same host directory already used by the operator's own `schwab-mcp` CLI setup (`[[reference_schwab_mcp]]`) — no duplicate credential file, no new secret to manage. Read-only so a compromised container can't tamper with the token file.
- `discord-bot` gains `schwab-mcp` as a `depends_on: condition: service_healthy` dependency, same pattern as `alpaca-mcp`.

### 2. `discord_bot/schwab-mcp.json` (new)

```json
{
  "mcpServers": {
    "schwab": {
      "type": "http",
      "url": "http://schwab-mcp:8002/mcp",
      "timeout": 30000
    }
  }
}
```

### 3. Tool Scope

Allowlisted (16 tools, all read-only at the source — no write/execute tool exists in this build regardless):

- **Positions/balances:** `get_accounts`, `get_account`
- **Orders/transactions:** `get_orders`, `get_order`, `get_transactions`, `get_transaction`
- **Quotes/chains:** `get_quotes`, `get_option_chain`, `get_advanced_option_chain`, `get_option_expiration_chain`, `get_advanced_price_history`, `get_movers`, `get_market_hours`, `get_instruments`, `create_option_symbol`, `get_datetime`

Explicitly **not** enabled: any `preview_*` order-builder tool (see threat-model note above — excluded by choice, not because they're dangerous, just unnecessary surface).

### 4. `pyproject.toml`

Add to the flat `dependencies` list (matches existing pattern — `api`/`pipeline` images carry the unused packages, same as `alpaca-mcp-server` today):
- `schwab-mcp @ git+https://github.com/jkoelker/schwab-mcp.git`
- `mcp-proxy`

### 5. `src/chat.py::call_claude_chat`

Add a second `--mcp-config` entry (the flag accepts multiple space-separated files — confirmed via `claude -p --help`) and extend `--allowedTools`:

```python
_SCHWAB_MCP_CONFIG_PATH = Path(__file__).parent.parent / "discord_bot" / "schwab-mcp.json"

_SCHWAB_ALLOWED_TOOLS: tuple[str, ...] = (
    "mcp__schwab__get_accounts",
    "mcp__schwab__get_account",
    "mcp__schwab__get_orders",
    "mcp__schwab__get_order",
    "mcp__schwab__get_transactions",
    "mcp__schwab__get_transaction",
    "mcp__schwab__get_quotes",
    "mcp__schwab__get_option_chain",
    "mcp__schwab__get_advanced_option_chain",
    "mcp__schwab__get_option_expiration_chain",
    "mcp__schwab__get_advanced_price_history",
    "mcp__schwab__get_movers",
    "mcp__schwab__get_market_hours",
    "mcp__schwab__get_instruments",
    "mcp__schwab__create_option_symbol",
    "mcp__schwab__get_datetime",
)

proc = await asyncio.create_subprocess_exec(
    "claude", "-p",
    "--mcp-config", str(_MCP_CONFIG_PATH), str(_SCHWAB_MCP_CONFIG_PATH),
    "--strict-mcp-config",
    "--tools", "WebSearch",
    "--allowedTools", "WebSearch", *_ALPACA_ALLOWED_TOOLS, *_SCHWAB_ALLOWED_TOOLS,
    ...
)
```

### 6. `discord_bot/trade_system_prompt.txt`

Short addition telling the model it has live Schwab account access and the required call sequence: account-specific tools (`get_account`, `get_orders`, `get_transactions`) need an `account_hash`, obtained by calling `get_accounts` first (there is exactly one account). Purely LLM-driven tool use — no Python-side intent detection or prefetch, same as how Alpaca tools are already exposed today.

### 7. Secrets

None new. The token/credentials files already exist on the host (`[[reference_schwab_mcp]]`); this design only mounts them read-only into the new service.

---

## Data Flow

Per message inside a trade chat thread (`_handle_message`, unchanged):

1. Existing prefetch runs as today (`detect_tickers` → `gather_chat_blocks`) — untouched, still Alpaca/internal-screener-backed.
2. `call_claude_chat()` invokes `claude -p` with both MCP configs (Alpaca + Schwab) and the combined `--allowedTools` list.
3. If the model needs real account data ("what are my positions", "what did I pay for HOOD", "any open orders on SOFI") it calls the relevant `mcp__schwab__*` tool against the already-running bridge and incorporates the result — same one-shot-call-with-internal-tool-round-trip shape as Alpaca.
4. Response persisted and sent to Discord, unchanged.

---

## Error Handling

Mirrors the Alpaca integration's established behavior:
- **`schwab-mcp` bridge unreachable/not yet healthy:** `claude -p` treats the tools as unavailable rather than failing the call; `depends_on: condition: service_healthy` prevents `discord-bot` from starting before the bridge is up. If the whole call fails for an unrelated reason, the existing Gemini fallback in `_handle_message` still covers it.
- **Stale/expired Schwab token:** tool calls fail server-side; degrades the same way as any other unreachable MCP tool. Refreshing it is a host-level operation outside this design — see `[[feedback_bounded_execution_for_unfamiliar_cli]]` for the standing rule never to re-run `schwab-mcp auth` without a `timeout` wrapper.
- **Slow/hung tool call:** bounded by `timeout: 30000` in `schwab-mcp.json`, nested inside the existing 120s `call_claude_chat` timeout.
- **No rate limiting added** — same rationale as Alpaca (single-user, private use; Schwab's own API limits are the backstop).

---

## Testing

- No changes to the existing prefetch test coverage.
- Manual verification once built: `docker compose build discord-bot schwab-mcp && docker compose up -d schwab-mcp discord-bot`, confirm `schwab-mcp`'s healthcheck passes, then send a message in the trade-chat channel ("what are my current positions") and confirm the bot calls `get_accounts` and returns real position data (cross-check against the balances/positions already verified directly via the `schwab` MCP server on this host).
- Apply the same `--debug-file` reproduction method used to catch the original Alpaca stdio bug if the tool doesn't appear to connect — confirm the bridge is actually connected (not just "container healthy") before the first model turn.

---

## Deployment — prod needs the credential files too

The live bot the operator actually talks to runs on **prod** (firefly, 10.0.1.21), not this dev host — confirmed by the discord-bot prod-rebuild history in `[[project_discord_bot_permissions]]`. The `~/.local/share/schwab-mcp/{credentials.yaml,token.yaml}` this design mounts read-only only exist on the dev host today (`[[reference_schwab_mcp]]`). Everything else in this design (image contents, compose service definitions) reaches prod automatically via the normal `git pull` + `docker compose build` deploy flow, but the credential files do not — they're host-local and gitignored by design (same as the discord-bot's isolated `.claude` config).

**Claude has no SSH/prod access** (per repo `CLAUDE.md`), so this is a manual one-time step for the operator, not something automated here: copy the same two files from dev to prod (`scp ~/.local/share/schwab-mcp/{credentials.yaml,token.yaml} prod:~/.local/share/schwab-mcp/`) rather than re-running `schwab-mcp auth` on prod — the OAuth login flow needs a real browser to complete the callback and is expected to hang/fail headless (see `[[feedback_bounded_execution_for_unfamiliar_cli]]`), while the token itself is a portable refresh token tied to the Schwab account/app registration, not to the dev machine. This is called out explicitly in the implementation plan as a manual pre-deploy step, not glossed over as "just works."

---

## Out of Scope (v1)

- Order preview or placement tools (excluded by design — see Threat Model note).
- Rate limiting / per-message cost caps.
- Any UI/dashboard surface for Schwab data — this is chat-only.
- Multi-account support (the operator has exactly one Schwab account today).
