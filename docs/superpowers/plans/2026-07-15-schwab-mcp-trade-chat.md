# Schwab MCP → Trade Chat Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Market Intelligence trade-chat Discord bot read-only access to the operator's real Schwab account (positions, balances, orders, transactions, quotes/option chains) via `schwab-mcp`, following the design in `docs/superpowers/specs/2026-07-15-schwab-mcp-trade-chat-design.md`.

**Architecture:** `schwab-mcp` is stdio-only, so it runs behind `mcp-proxy` (confirmed working locally: `mcp-proxy --port N --host H -- schwab-mcp server` serves a real streamable-HTTP MCP endpoint at `/mcp`) as its own persistent `docker-compose` service — the same fix the Alpaca MCP integration needed after its original per-call stdio spawn silently never connected in time. `discord-bot`'s `claude -p` call gets a second `--mcp-config` entry pointing at this bridge over HTTP, plus an expanded `--allowedTools` list.

**Tech Stack:** Python 3.12, `schwab-mcp` (git dependency), `mcp-proxy` (PyPI), Docker Compose, pytest + pytest-asyncio (existing suite).

## Global Constraints

- Read-only only: no `preview_*`, `place_*`, or `cancel_*` tool is ever added to the allowlist — per spec Threat Model note.
- `schwab-mcp` must never be spawned per-call over stdio by `claude -p` — always reached via the persistent `mcp-proxy` bridge service (per spec Architecture section and the Alpaca precedent it's based on).
- Credential files (`~/.local/share/schwab-mcp/{credentials.yaml,token.yaml}`) are mounted read-only, and only into the `schwab-mcp` bridge service's container — never into `discord-bot` itself.
- Claude has no SSH/prod access (repo `CLAUDE.md`) — any prod-side step must be exact copy-pasteable commands for the operator, never assumed-done.
- Follow existing code style: `ruff` auto-formats on save (per `CLAUDE.md`), no manual formatting step needed.

---

### Task 1: Add `schwab-mcp` + `mcp-proxy` dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `Dockerfile:6` (base stage `apt-get install` line)

**Interfaces:**
- Produces: `schwab-mcp` and `mcp-proxy` console scripts available on `PATH` in any image built from the `base` target (used by Task 2's new service).

- [ ] **Step 1: Add the two dependencies to `pyproject.toml`**

In `pyproject.toml`, in the `[project]` → `dependencies` list, add two lines right after the existing `"alpaca-mcp-server>=2.1,<3",` line:

```toml
    "alpaca-mcp-server>=2.1,<3",
    "schwab-mcp @ git+https://github.com/jkoelker/schwab-mcp.git",
    "mcp-proxy>=0.12,<1",
```

- [ ] **Step 2: Add `git` to the base image's apt packages**

`schwab-mcp` isn't published to PyPI — pip needs the `git` binary to resolve the `git+https://...` dependency at build time. In `Dockerfile`, change:

```dockerfile
# install curl for healthchecks and ca-certificates for outbound HTTPS
RUN apt-get update && apt-get install -y curl ca-certificates libgomp1 && rm -rf /var/lib/apt/lists/*
```

to:

```dockerfile
# install curl for healthchecks, ca-certificates for outbound HTTPS, and git
# (pip needs it to install schwab-mcp directly from GitHub — not on PyPI)
RUN apt-get update && apt-get install -y curl ca-certificates libgomp1 git && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Verify the image builds and both binaries are on PATH**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose build api
docker compose run --rm --entrypoint sh api -c "which schwab-mcp mcp-proxy && schwab-mcp --help"
```

Expected: build succeeds with no pip/git errors, `which` prints both paths (e.g. `/usr/local/bin/schwab-mcp`, `/usr/local/bin/mcp-proxy`), and `schwab-mcp --help` prints its usage (`auth`, `save-credentials`, `server` subcommands).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml Dockerfile
git commit -m "build: add schwab-mcp and mcp-proxy dependencies"
```

---

### Task 2: Add the `schwab-mcp` bridge service to `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml` (insert new service after the existing `alpaca-mcp` block, ending around line 87; add a `depends_on` entry to `discord-bot`)

**Interfaces:**
- Consumes: the `base` image built in Task 1 (already has `schwab-mcp` + `mcp-proxy` on `PATH`); host directory `~/.local/share/schwab-mcp` (must already exist on this host — confirmed present, see `[[reference_schwab_mcp]]`).
- Produces: an HTTP MCP endpoint reachable at `http://schwab-mcp:8002/mcp` from other containers on the compose network, health-gated so dependents don't start before it's ready.

- [ ] **Step 1: Insert the new service**

In `docker-compose.yml`, immediately after the existing `alpaca-mcp` service block (right before the `# ── Discord bot ──` comment), insert:

```yaml
  # ── Schwab MCP bridge (persistent HTTP; backs the trade chat bot's access to
  # real account data — positions, orders, transactions, balances). schwab-mcp
  # only supports stdio transport (no --transport flag, confirmed from its
  # source), so mcp-proxy wraps it and re-exposes it over streamable-HTTP —
  # the same fix alpaca-mcp above needed after its original per-call stdio
  # spawn silently lost the race against claude -p's first request. Verified
  # locally: mcp-proxy --port N -- schwab-mcp server serves a working /mcp
  # endpoint; bare GET returns 406 (healthy) same as alpaca-mcp's endpoint. ──
  schwab-mcp:
    build:
      context: .
      target: base
    container_name: market-intelligence-schwab-mcp
    volumes:
      # Read-only — only this service's container ever sees the token file;
      # discord-bot reaches Schwab data exclusively via the HTTP endpoint
      # below, never via filesystem access to the credential.
      - ~/.local/share/schwab-mcp:/root/.local/share/schwab-mcp:ro
    command: mcp-proxy --port 8002 --host 0.0.0.0 -- schwab-mcp server --no-technical-tools
    expose:
      - "8002"  # Internal only — discord-bot reaches it via the Docker network
    restart: unless-stopped
    healthcheck:
      # No -f: the MCP endpoint 406s a bare GET (wrong Accept header) even when
      # healthy — this only needs to confirm the server is accepting connections.
      test: ["CMD", "curl", "-s", "-o", "/dev/null", "http://localhost:8002/mcp"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s

```

- [ ] **Step 2: Add `schwab-mcp` as a dependency of `discord-bot`**

In the `discord-bot` service's `depends_on:` block, add the new service alongside the existing ones:

```yaml
    depends_on:
      api:
        condition: service_healthy
      alpaca-mcp:
        condition: service_healthy
      schwab-mcp:
        condition: service_healthy
```

- [ ] **Step 3: Bring the service up and verify it's healthy**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose up -d --build schwab-mcp
docker compose ps schwab-mcp   # wait for STATUS to show "(healthy)"
docker compose logs schwab-mcp --tail 30
```

Expected: `docker compose ps` shows `market-intelligence-schwab-mcp` as `Up ... (healthy)` within ~15-20s. Logs show `Configured default server: schwab-mcp server`, `Serving MCP Servers via SSE: http://0.0.0.0:8002/sse`, and `Uvicorn running on http://0.0.0.0:8002`, with no traceback.

- [ ] **Step 4: Verify a real tool call round-trips through the bridge**

```bash
docker compose exec schwab-mcp curl -s http://localhost:8002/mcp \
  -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" \
  -X POST -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
```

Expected: a JSON-RPC response containing `"serverInfo":{"name":"schwab-mcp", ...}` — confirms the container can see the mounted credentials and schwab-mcp started cleanly (not just that mcp-proxy itself is up).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(discord-bot): add persistent schwab-mcp bridge service"
```

---

### Task 3: Wire Schwab MCP into `call_claude_chat`

**Files:**
- Create: `discord_bot/schwab-mcp.json`
- Modify: `src/chat.py:334-407` (add config path, allowed-tools tuple, extend the subprocess call)
- Test: `tests/test_chat_logic.py` (extend `TestCallClaudeChatMcpWiring`)

**Interfaces:**
- Consumes: `mcp__schwab__*` tool names as registered by the bridge from Task 2 (matches the 16-tool scope in the spec).
- Produces: `_SCHWAB_MCP_CONFIG_PATH: Path`, `_SCHWAB_ALLOWED_TOOLS: tuple[str, ...]` — new module-level names in `src/chat.py`, used only within this file.

- [ ] **Step 1: Create `discord_bot/schwab-mcp.json`**

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

- [ ] **Step 2: Write the failing test**

In `tests/test_chat_logic.py`, replace the existing `test_invokes_claude_with_mcp_config_and_alpaca_tools` test (inside `TestCallClaudeChatMcpWiring`) with this extended version:

```python
    async def test_invokes_claude_with_mcp_config_and_alpaca_tools(self, monkeypatch):
        captured = {}

        class _FakeProcess:
            returncode = 0

            async def communicate(self, input=None):
                return b"ok", b""

        async def _fake_create_subprocess_exec(*args, **kwargs):
            captured["args"] = args
            return _FakeProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

        result = await chat_module.call_claude_chat("some prompt")

        assert result == "ok"
        argv = captured["args"]
        assert argv[0] == "claude"
        assert "-p" in argv
        assert "--strict-mcp-config" in argv

        mcp_config_idx = argv.index("--mcp-config")
        assert argv[mcp_config_idx + 1].replace("\\", "/").endswith("discord_bot/alpaca-mcp.json")
        assert argv[mcp_config_idx + 2].replace("\\", "/").endswith("discord_bot/schwab-mcp.json")

        allowed_idx = argv.index("--allowedTools")
        allowed = argv[allowed_idx + 1:]
        assert "WebSearch" in allowed
        assert "mcp__alpaca__get_option_chain" in allowed
        assert "mcp__alpaca__get_option_snapshot" in allowed
        assert "mcp__alpaca__get_stock_snapshot" in allowed
        assert "mcp__alpaca__get_option_contracts" in allowed
        assert "mcp__schwab__get_accounts" in allowed
        assert "mcp__schwab__get_orders" in allowed
        assert "mcp__schwab__get_quotes" in allowed
        assert not any(t.startswith("mcp__schwab__preview_") for t in allowed)
        assert len(allowed) == 41  # WebSearch + 24 Alpaca tools + 16 Schwab tools
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
docker compose run --rm test python3 -m pytest tests/test_chat_logic.py::TestCallClaudeChatMcpWiring -v
```

Expected: FAIL — `argv[mcp_config_idx + 2]` raises `IndexError` (only one `--mcp-config` value exists yet), and/or the `len(allowed) == 41` assertion fails against the current `25`.

- [ ] **Step 4: Implement**

In `src/chat.py`, after the existing `_ALPACA_ALLOWED_TOOLS` tuple definition (ends at line 367) and before `async def call_claude_chat`, add:

```python
_SCHWAB_MCP_CONFIG_PATH = Path(__file__).parent.parent / "discord_bot" / "schwab-mcp.json"

# Read-only account/market-data tools only — no preview_*/place_*/cancel_*
# tool is ever listed here, regardless of what the schwab-mcp server exposes.
# See docs/superpowers/specs/2026-07-15-schwab-mcp-trade-chat-design.md.
_SCHWAB_ALLOWED_TOOLS: tuple[str, ...] = (
    # positions/balances
    "mcp__schwab__get_accounts",
    "mcp__schwab__get_account",
    # orders/transactions
    "mcp__schwab__get_orders",
    "mcp__schwab__get_order",
    "mcp__schwab__get_transactions",
    "mcp__schwab__get_transaction",
    # quotes/chains
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
```

Then update the `call_claude_chat` subprocess invocation:

```python
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--mcp-config", str(_MCP_CONFIG_PATH), str(_SCHWAB_MCP_CONFIG_PATH),
            "--strict-mcp-config",
            "--tools", "WebSearch",
            "--allowedTools", "WebSearch", *_ALPACA_ALLOWED_TOOLS, *_SCHWAB_ALLOWED_TOOLS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker compose run --rm test python3 -m pytest tests/test_chat_logic.py::TestCallClaudeChatMcpWiring -v
```

Expected: PASS.

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -v
```

Expected: all tests pass, including the rest of `TestCallClaudeChatMcpWiring` and everything in `tests/test_trade_chat_db.py`.

- [ ] **Step 7: Commit**

```bash
git add discord_bot/schwab-mcp.json src/chat.py tests/test_chat_logic.py
git commit -m "feat(chat): wire Schwab MCP tools into call_claude_chat"
```

---

### Task 4: Tell the model about its Schwab access in the system prompt

**Files:**
- Modify: `discord_bot/trade_system_prompt.txt:100-104`

**Interfaces:**
- Consumes: nothing new — this is prose read by `_load_system_prompt()` in `discord_bot/commands/chat.py`, unchanged.

- [ ] **Step 1: Add a paragraph before the final closing paragraph**

In `discord_bot/trade_system_prompt.txt`, insert a new paragraph immediately before the final paragraph (the one starting `You are a direct, opinionated trading partner.`):

```
You also have direct read-only access to Austin's real Schwab account via MCP tools. Call get_accounts first to get the account_hash (there is exactly one account) — then use it with get_account, get_orders, or get_transactions to answer questions about his actual positions, balances, open orders, or trade/transaction history. When a question is specifically about his own holdings or account state (not just market data on a ticker), prefer this real account data over the injected screener data.
```

So the file's tail reads:

```
...LEAPS capped ~10-20%, bulk stays long-term holds. Build an emergency fund/non-market income before scaling into options. Avoid "feeding the furnace" (repeatedly averaging into one losing position through a sustained downturn with no cap).

**Technical analysis**
...no intraday since he doesn't day trade.

---

You also have direct read-only access to Austin's real Schwab account via MCP tools. Call get_accounts first to get the account_hash (there is exactly one account) — then use it with get_account, get_orders, or get_transactions to answer questions about his actual positions, balances, open orders, or trade/transaction history. When a question is specifically about his own holdings or account state (not just market data on a ticker), prefer this real account data over the injected screener data.

You are a direct, opinionated trading partner. You can discuss TA (trend, momentum, VCP, Bollinger Bands, RSI, MA structure), fundamentals, macro, company outlook, bullish/bearish reads, trade ideas, and CSP/wheel setup evaluation. When live screener data is injected into the conversation, use those numbers to reason concretely. Ask clarifying questions when it matters (timeframe? CSP or just watching?). Give a directional lean — don't hedge excessively. The V42 criteria are one lens, not the whole picture.
```

- [ ] **Step 2: Verify it loads cleanly**

```bash
docker compose exec discord-bot python3 -c "
from pathlib import Path
p = Path('/app/discord_bot/trade_system_prompt.txt')
text = p.read_text()
assert 'Schwab account via MCP tools' in text
print('OK, length:', len(text))
"
```

Expected: prints `OK, length: <number>` with no exception. (If `discord-bot` isn't running yet, run this against the host file directly instead: `python3 -c "..." ` with the host path `discord_bot/trade_system_prompt.txt`.)

- [ ] **Step 3: Commit**

```bash
git add discord_bot/trade_system_prompt.txt
git commit -m "docs(trade-chat): tell the model about its Schwab account access"
```

---

### Task 5: End-to-end verification (dev)

**Files:** none (verification only)

**Interfaces:** none — this task exercises the full stack built in Tasks 1-4.

- [ ] **Step 1: Rebuild and bring up the full dev stack**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose up -d --build redis api alpaca-mcp schwab-mcp discord-bot
docker compose ps
```

Expected: `alpaca-mcp` and `schwab-mcp` both show `(healthy)`; `discord-bot` shows `Up` (it depends on both being healthy first, per Task 2's `depends_on`).

- [ ] **Step 2: Reproduce the exact `claude -p` call chat.py makes, with debug output**

This directly targets the failure mode that broke the original Alpaca integration (MCP server not connected before the model's first turn) — run it from inside the `discord-bot` container so paths and the isolated `.claude` config match production behavior exactly:

```bash
docker compose exec discord-bot sh -c '
echo "What are my current Schwab positions? Just list them." | claude -p \
  --mcp-config discord_bot/alpaca-mcp.json discord_bot/schwab-mcp.json \
  --strict-mcp-config \
  --tools WebSearch \
  --allowedTools WebSearch mcp__schwab__get_accounts mcp__schwab__get_account \
  --debug-file /tmp/schwab-debug.log
'
docker compose exec discord-bot grep -i "schwab\|Successfully connected\|hasTools" /tmp/schwab-debug.log | tail -30
```

Expected: the response text references real position data (e.g. actual ticker symbols from the account, not a refusal or "I don't have access"). The debug log shows the `schwab` MCP server connecting successfully before or during the first turn (`Successfully connected... hasTools:true` or equivalent), not losing the race the way the original Alpaca stdio design did.

- [ ] **Step 3: Record the result**

If Step 2 shows the model correctly calling `get_accounts` and returning real data, this task is done — no code changes, this is a verification checkpoint. If it fails the same way the original Alpaca design did (tool unavailable despite the container being healthy), stop and re-open Task 2/3 rather than proceeding to Task 6 — do not ship a silently-broken integration.

---

### Task 6: Prod rollout (manual, operator-run)

**Files:** none in this repo — operational steps only, per the spec's Deployment section. Claude has no SSH/prod access, so these are exact commands for the operator to run, not something to automate here.

- [ ] **Step 1: Copy the Schwab credential files to prod**

From this dev host:

```bash
scp ~/.local/share/schwab-mcp/credentials.yaml ~/.local/share/schwab-mcp/token.yaml \
  <prod-user>@10.0.1.21:~/.local/share/schwab-mcp/
```

(Create the destination directory first if it doesn't exist: `ssh <prod-user>@10.0.1.21 mkdir -p ~/.local/share/schwab-mcp`.) Do **not** run `schwab-mcp auth` on prod — it needs a real browser to complete the OAuth callback and is expected to hang headless (see `[[feedback_bounded_execution_for_unfamiliar_cli]]`). The copied token is a portable refresh token tied to the Schwab account/app registration, not to the dev machine.

- [ ] **Step 2: Deploy the code changes to prod**

```bash
ssh <prod-user>@10.0.1.21
cd <path-to-Market-Intelligence-on-prod>
git pull
docker compose build schwab-mcp discord-bot
docker compose up -d schwab-mcp discord-bot
docker compose ps schwab-mcp discord-bot
```

Expected: both show healthy/up, same as the dev verification in Task 5.

- [ ] **Step 3: Smoke-test in the real trade-chat Discord channel**

Send a message in the actual trade-chat thread, e.g. "what are my current positions" — confirm the bot's reply reflects real Schwab account data.
