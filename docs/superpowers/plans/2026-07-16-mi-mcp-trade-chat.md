# Trade-Chat MI Watchlist/Scanner MCP Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the trade-chat bot's model an on-demand tool to query Austin's own Market Intelligence watchlists and scanner output (CSP watchlist, stock watchlist, CSP candidates, LEAPS candidates, market posture) — the same way it already calls Alpaca/Schwab tools.

**Architecture:** A stdio FastMCP server (`discord_bot/mi_mcp_server.py`), spawned per chat turn by `claude -p` (not an always-on service), wraps five thin async functions in `src/mi_client.py` that each call one existing FastAPI endpoint on the already-running `api` service over Docker-internal DNS (`http://api:8000`) and return the parsed JSON. No screener logic is duplicated — the API's existing Redis cache is reused as-is.

**Tech Stack:** Python 3.12, `httpx` (async client), `fastmcp` 3.4.4 (already installed transitively via `alpaca-mcp-server`/`schwab-mcp`/`mcp-proxy` — confirmed via `docker exec market-intelligence-discord-bot pip show mcp`), `pytest` + `pytest-asyncio` (auto mode) + `respx` for tests.

## Global Constraints

- No new dependency in `pyproject.toml` — `mcp`/`fastmcp` are already present.
- No `Dockerfile` or `docker-compose.yml` changes — `discord_bot/` is already fully `COPY`'d into the image.
- Tool grant stays narrow and hardcoded in `src/chat.py`'s `--allowedTools` list (no `Bash`, no `WebFetch`), matching the existing Alpaca/Schwab pattern (`[[feedback_bot_tool_permissions]]`).
- Follow the existing repo split: business/network logic lives in `src/` and is unit-tested there; `discord_bot/` files stay thin wrappers with no logic of their own (mirrors `discord_bot/commands/chat.py` delegating to `src/chat.py`) and are verified manually, not via pytest.
- Real API response shapes (confirmed via `curl http://localhost:8000/api/...` against the running dev stack): watchlist endpoints return `{"watchlist": [...]}`; screener endpoints return `{"candidates": [...]}`; use these exact key names in tests, not placeholders.

---

### Task 1: `src/mi_client.py` — thin async API client + tests

**Files:**
- Create: `src/mi_client.py`
- Test: `tests/test_mi_client.py`

**Interfaces:**
- Produces: `API_BASE: str` (module constant, `"http://api:8000"`), and five async functions with signature `async def <name>() -> dict`: `get_csp_watchlist`, `get_stock_watchlist`, `get_csp_candidates`, `get_leaps_candidates`, `get_market_posture`. Each raises `httpx.HTTPStatusError` on a non-2xx response. Task 2 imports all five plus `API_BASE` is not needed there.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mi_client.py`:

```python
"""Unit tests for src.mi_client — thin API wrapper for MI's own watchlist/scanner endpoints."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.mi_client import (
    API_BASE,
    get_csp_candidates,
    get_csp_watchlist,
    get_leaps_candidates,
    get_market_posture,
    get_stock_watchlist,
)


class TestGetCspWatchlist:
    @respx.mock
    async def test_returns_parsed_json(self):
        respx.get(f"{API_BASE}/api/watchlist").mock(
            return_value=httpx.Response(200, json={"watchlist": ["NVDA", "AAPL"]})
        )
        result = await get_csp_watchlist()
        assert result == {"watchlist": ["NVDA", "AAPL"]}

    @respx.mock
    async def test_raises_on_error_status(self):
        respx.get(f"{API_BASE}/api/watchlist").mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            await get_csp_watchlist()


class TestGetStockWatchlist:
    @respx.mock
    async def test_returns_parsed_json(self):
        respx.get(f"{API_BASE}/api/watchlist/stock").mock(
            return_value=httpx.Response(200, json={"watchlist": ["SOFI"]})
        )
        result = await get_stock_watchlist()
        assert result == {"watchlist": ["SOFI"]}


class TestGetCspCandidates:
    @respx.mock
    async def test_returns_parsed_json(self):
        respx.get(f"{API_BASE}/api/screener/csp").mock(
            return_value=httpx.Response(
                200, json={"candidates": [{"symbol": "AMD", "strike": 460.0}]}
            )
        )
        result = await get_csp_candidates()
        assert result == {"candidates": [{"symbol": "AMD", "strike": 460.0}]}


class TestGetLeapsCandidates:
    @respx.mock
    async def test_returns_parsed_json(self):
        respx.get(f"{API_BASE}/api/screener/leaps").mock(
            return_value=httpx.Response(
                200, json={"candidates": [{"symbol": "MSFT", "strike": 350.0}]}
            )
        )
        result = await get_leaps_candidates()
        assert result == {"candidates": [{"symbol": "MSFT", "strike": 350.0}]}


class TestGetMarketPosture:
    @respx.mock
    async def test_returns_parsed_json(self):
        respx.get(f"{API_BASE}/api/market-posture").mock(
            return_value=httpx.Response(
                200, json={"composite_score": 0.3, "posture": "Neutral"}
            )
        )
        result = await get_market_posture()
        assert result == {"composite_score": 0.3, "posture": "Neutral"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_mi_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.mi_client'`

- [ ] **Step 3: Write the implementation**

Create `src/mi_client.py`:

```python
"""Thin async client for the Market Intelligence API's own watchlist/scanner
endpoints. Used by discord_bot/mi_mcp_server.py to expose these as MCP tools
so the trade-chat bot's model can query Austin's own watchlists and scanner
output on demand, the same way it already queries Alpaca/Schwab."""

from __future__ import annotations

import httpx

API_BASE = "http://api:8000"
_TIMEOUT = 15.0


async def _get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(f"{API_BASE}{path}")
        response.raise_for_status()
        return response.json()


async def get_csp_watchlist() -> dict:
    """Return Austin's CSP (cash-secured put) screener watchlist tickers."""
    return await _get("/api/watchlist")


async def get_stock_watchlist() -> dict:
    """Return Austin's stock screener watchlist tickers."""
    return await _get("/api/watchlist/stock")


async def get_csp_candidates() -> dict:
    """Return today's curated CSP candidates from the live scanner."""
    return await _get("/api/screener/csp")


async def get_leaps_candidates() -> dict:
    """Return today's curated LEAPS candidates from the live scanner."""
    return await _get("/api/screener/leaps")


async def get_market_posture() -> dict:
    """Return the latest market posture digest: composite score, posture label, and signals."""
    return await _get("/api/market-posture")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_mi_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mi_client.py tests/test_mi_client.py
git commit -m "feat(trade-chat): add thin MI API client for watchlist/scanner data"
```

---

### Task 2: `discord_bot/mi_mcp_server.py` + `discord_bot/mi-mcp.json`

**Files:**
- Create: `discord_bot/mi_mcp_server.py`
- Create: `discord_bot/mi-mcp.json`

**Interfaces:**
- Consumes: the five functions from `src/mi_client.py` (Task 1) by name — `get_csp_watchlist`, `get_stock_watchlist`, `get_csp_candidates`, `get_leaps_candidates`, `get_market_posture`.
- Produces: an MCP stdio server exposing tools named `get_csp_watchlist`, `get_stock_watchlist`, `get_csp_candidates`, `get_leaps_candidates`, `get_market_posture` (Task 3 references these as `mcp__mi__get_csp_watchlist`, etc. — the `mi` prefix comes from the server name key in `mi-mcp.json`, not from this file).

This file has no dedicated pytest suite — it is a thin wrapper with no logic of its own (mirrors `discord_bot/commands/chat.py`, which also has no direct unit tests; the logic it delegates to is tested in `src/`). It is verified manually in Step 3 below and end-to-end in Task 5.

- [ ] **Step 1: Write the server**

Create `discord_bot/mi_mcp_server.py`:

```python
"""Stdio MCP server exposing Market Intelligence watchlist/scanner data as
tools for the trade-chat bot. Spawned per chat turn by `claude -p` via
--mcp-config (see src/chat.py) — not an always-on process. All logic lives
in src/mi_client.py; this file only registers those functions as MCP tools."""

from __future__ import annotations

from fastmcp import FastMCP

from src.mi_client import (
    get_csp_candidates,
    get_csp_watchlist,
    get_leaps_candidates,
    get_market_posture,
    get_stock_watchlist,
)

mcp = FastMCP("mi")

mcp.tool(get_csp_watchlist)
mcp.tool(get_stock_watchlist)
mcp.tool(get_csp_candidates)
mcp.tool(get_leaps_candidates)
mcp.tool(get_market_posture)

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
```

- [ ] **Step 2: Write the MCP config**

Create `discord_bot/mi-mcp.json`:

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

- [ ] **Step 3: Manually verify the server registers all five tools with correct descriptions**

The `discord-bot` container already has `PYTHONPATH=/app` set and runs with cwd `/app/discord_bot` (see `Dockerfile`), matching how `mi_mcp_server.py` will actually run — but the file doesn't exist in the running container's image yet since it hasn't been rebuilt. Verify the module loads correctly and registers tools by mounting the new file into the running container and importing it directly:

Run:
```bash
docker cp discord_bot/mi_mcp_server.py market-intelligence-discord-bot:/app/discord_bot/mi_mcp_server.py
docker cp src/mi_client.py market-intelligence-discord-bot:/app/src/mi_client.py
docker exec -w /app/discord_bot market-intelligence-discord-bot python3 -c "
import asyncio
from mi_mcp_server import mcp
from src.mi_client import get_csp_watchlist

async def main():
    tools = await mcp._list_tools()
    for t in tools:
        print(t.name, '-', t.description)
    print('---live call---')
    print(await get_csp_watchlist())

asyncio.run(main())
"
```
Expected output: 5 lines, one per tool, each with its docstring as the description, then a real JSON dict from the live `api` service:
```
get_csp_watchlist - Return Austin's CSP (cash-secured put) screener watchlist tickers.
get_stock_watchlist - Return Austin's stock screener watchlist tickers.
get_csp_candidates - Return today's curated CSP candidates from the live scanner.
get_leaps_candidates - Return today's curated LEAPS candidates from the live scanner.
get_market_posture - Return the latest market posture digest: composite score, posture label, and signals.
---live call---
{'watchlist': ['AAPL', 'MSFT', 'GOOGL', ...]}
```
Compare the live-call output against `curl -s http://localhost:8000/api/watchlist` run from the dev host in the same moment — they should match.

This is a throwaway verification against the live container's existing image (which already has `fastmcp`/`httpx` installed) — no image rebuild needed for this check. The real image will get these files permanently via the normal `docker compose build discord-bot` in Task 5.

- [ ] **Step 4: Commit**

```bash
git add discord_bot/mi_mcp_server.py discord_bot/mi-mcp.json
git commit -m "feat(trade-chat): add stdio MCP server for MI watchlist/scanner tools"
```

---

### Task 3: Wire the new server into `src/chat.py`

**Files:**
- Modify: `src/chat.py:334-421` (the `_MCP_CONFIG_PATH`/`_SCHWAB_MCP_CONFIG_PATH` constants, `_ALPACA_ALLOWED_TOOLS`/`_SCHWAB_ALLOWED_TOOLS` tuples, and `call_claude_chat`)
- Modify: `tests/test_chat_logic.py:306-351` (`TestCallClaudeChatMcpWiring`)

**Interfaces:**
- Consumes: `discord_bot/mi-mcp.json` (Task 2) by path; the five `mcp__mi__*` tool names.
- Produces: `call_claude_chat` now passes three `--mcp-config` paths and 47 `--allowedTools` entries (up from 42) — later tasks don't depend on this count directly, but Task 5's end-to-end check does depend on the tools actually being callable.

- [ ] **Step 1: Update the existing wiring test to expect the new server (write the failing assertions first)**

In `tests/test_chat_logic.py`, replace the body of `TestCallClaudeChatMcpWiring.test_invokes_claude_with_mcp_config_and_alpaca_tools`:

```python
class TestCallClaudeChatMcpWiring:
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
        assert argv[mcp_config_idx + 3].replace("\\", "/").endswith("discord_bot/mi-mcp.json")

        allowed_idx = argv.index("--allowedTools")
        allowed = argv[allowed_idx + 1:]
        assert "WebSearch" in allowed
        assert "ToolSearch" in allowed
        assert "mcp__alpaca__get_option_chain" in allowed
        assert "mcp__alpaca__get_option_snapshot" in allowed
        assert "mcp__alpaca__get_stock_snapshot" in allowed
        assert "mcp__alpaca__get_option_contracts" in allowed
        assert "mcp__schwab__get_accounts" in allowed
        assert "mcp__schwab__get_orders" in allowed
        assert "mcp__schwab__get_quotes" in allowed
        assert not any(t.startswith("mcp__schwab__preview_") for t in allowed)
        assert "mcp__mi__get_csp_watchlist" in allowed
        assert "mcp__mi__get_stock_watchlist" in allowed
        assert "mcp__mi__get_csp_candidates" in allowed
        assert "mcp__mi__get_leaps_candidates" in allowed
        assert "mcp__mi__get_market_posture" in allowed
        assert len(allowed) == 47  # WebSearch + ToolSearch + 24 Alpaca + 16 Schwab + 5 MI tools

        tools_idx = argv.index("--tools")
        tools = argv[tools_idx + 1:allowed_idx]
        assert tools == ("WebSearch", "ToolSearch")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py::TestCallClaudeChatMcpWiring -v`
Expected: FAIL — `argv[mcp_config_idx + 3]` raises `IndexError` (only 2 `--mcp-config` paths currently), or the `mcp__mi__*` assertions fail with `AssertionError`.

- [ ] **Step 3: Update `src/chat.py`**

Add the new config path constant right after `_SCHWAB_MCP_CONFIG_PATH` (around line 369):

```python
_MI_MCP_CONFIG_PATH = Path(__file__).parent.parent / "discord_bot" / "mi-mcp.json"

# Austin's own Market Intelligence watchlist/scanner data — see src/mi_client.py
# for the underlying HTTP calls and discord_bot/mi_mcp_server.py for the MCP
# wrapper. Read-only, no arguments, no auth (internal Docker network only).
_MI_ALLOWED_TOOLS: tuple[str, ...] = (
    "mcp__mi__get_csp_watchlist",
    "mcp__mi__get_stock_watchlist",
    "mcp__mi__get_csp_candidates",
    "mcp__mi__get_leaps_candidates",
    "mcp__mi__get_market_posture",
)
```

Update `call_claude_chat`'s subprocess invocation to add the new config path and tool names:

```python
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--mcp-config", str(_MCP_CONFIG_PATH), str(_SCHWAB_MCP_CONFIG_PATH), str(_MI_MCP_CONFIG_PATH),
            "--strict-mcp-config",
            # ToolSearch is required for the model to discover MCP tool schemas
            # at all — without it, mcp__alpaca__*/mcp__schwab__* tools connect
            # successfully (server-side "hasTools:true") but the model has no
            # way to learn they exist, and silently never calls them. Found via
            # live reproduction (2026-07-15): identical prompts reliably failed
            # to call any Schwab tool without ToolSearch (3/3) and reliably
            # succeeded with it (2/2, correct real data both times).
            "--tools", "WebSearch", "ToolSearch",
            "--allowedTools", "WebSearch", "ToolSearch", *_ALPACA_ALLOWED_TOOLS, *_SCHWAB_ALLOWED_TOOLS, *_MI_ALLOWED_TOOLS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py -v`
Expected: PASS (all tests in the file, including the updated `TestCallClaudeChatMcpWiring` test)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -v`
Expected: PASS (no regressions elsewhere)

- [ ] **Step 6: Commit**

```bash
git add src/chat.py tests/test_chat_logic.py
git commit -m "feat(trade-chat): wire MI watchlist/scanner MCP tools into call_claude_chat"
```

---

### Task 4: Update `discord_bot/trade_system_prompt.txt`

**Files:**
- Modify: `discord_bot/trade_system_prompt.txt` (insert a new paragraph after the existing Schwab-tools paragraph, before the final "You are a direct, opinionated trading partner" paragraph)

**Interfaces:**
- Consumes: nothing from earlier tasks except the tool names being documented (`get_csp_watchlist`, `get_stock_watchlist`, `get_csp_candidates`, `get_leaps_candidates`, `get_market_posture`) — must match Task 1/3 exactly.
- Produces: nothing consumed by later tasks — this is the terminal task before end-to-end verification.

- [ ] **Step 1: Insert the new paragraph**

In `discord_bot/trade_system_prompt.txt`, after the paragraph beginning "You also have direct read-only access to Austin's real Schwab account via MCP tools..." (currently the last paragraph before "You are a direct, opinionated trading partner..."), insert:

```
You also have tools for Austin's own Market Intelligence watchlists and scanner: get_csp_watchlist (his CSP screener watchlist), get_stock_watchlist (his stock screener watchlist), get_csp_candidates (today's curated CSP candidates from the live scanner, ranked by composite_score), get_leaps_candidates (today's curated LEAPS candidates), and get_market_posture (latest composite score, posture label, and signal breakdown — macro/regime context). Use these when a question is about "my watchlist," "the scanner," "candidates," or overall market regime/posture rather than a single already-named ticker. As with all live data: don't state watchlist or candidate contents unless they came from an actual tool call this turn — if a tool call fails or isn't available, say so plainly instead of guessing.
```

- [ ] **Step 2: Verify the paragraph was inserted correctly**

Run: `grep -c "get_csp_watchlist" discord_bot/trade_system_prompt.txt`
Expected: `1`

- [ ] **Step 3: Commit**

```bash
git add discord_bot/trade_system_prompt.txt
git commit -m "docs(trade-chat): document MI watchlist/scanner tools in system prompt"
```

---

### Task 5: Rebuild, deploy to dev, and verify end-to-end

**Files:** none (operational verification only)

**Interfaces:** none — this task only exercises the full stack built by Tasks 1-4.

- [ ] **Step 1: Lint check**

Run: `~/.local/bin/ruff check src/mi_client.py discord_bot/mi_mcp_server.py src/chat.py tests/test_mi_client.py tests/test_chat_logic.py`
Expected: no errors (the `PostToolUse` hook should have already auto-formatted each file on edit, so this should be a no-op confirmation)

- [ ] **Step 2: Rebuild and restart the discord-bot service on dev**

Run:
```bash
docker compose build discord-bot
docker compose up -d discord-bot
```
Expected: build succeeds, container restarts healthy (`docker compose ps discord-bot` shows `Up`)

- [ ] **Step 3: Ask a watchlist question in the real dev trade-chat Discord thread**

Send a message in the configured trade-chat channel: `what's on my CSP watchlist right now?`

Expected: the bot's reply lists tickers matching `curl http://localhost:8000/api/watchlist` output at the same moment.

- [ ] **Step 4: Ask a scanner question in the same thread**

Send: `any CSP candidates worth a look today?`

Expected: the bot's reply references real tickers/strikes from `curl http://localhost:8000/api/screener/csp` — not fabricated numbers.

- [ ] **Step 5: Confirm the reply came from an actual tool call, not a fabrication**

Run (per `[[project_trade_chat_fabrication]]`'s established verification method):
```bash
docker exec market-intelligence-discord-bot find /root/.claude/projects -name "*.jsonl" -newermt "-5 minutes"
```
Then inspect the newest matching file for a `tool_use` record naming `mcp__mi__get_csp_watchlist` or `mcp__mi__get_csp_candidates` in the relevant turn (e.g. `docker exec market-intelligence-discord-bot grep -o "mcp__mi__[a-z_]*" <path>`).
Expected: at least one `mcp__mi__*` tool_use record present for each of the two test messages sent in Steps 3-4.

- [ ] **Step 6: Note completion**

No commit needed for this task (operational verification only) — implementation is complete once Steps 1-5 pass.
