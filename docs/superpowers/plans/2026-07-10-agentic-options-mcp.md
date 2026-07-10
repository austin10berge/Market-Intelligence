# Agentic Options/Stock Data Tool Calls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the trade chat bot's `claude -p` call access to Alpaca's official MCP server (read-only market-data toolsets only) so it can fetch options/stock data mid-response for questions the existing regex prefetch doesn't cover.

**Architecture:** `discord_bot/alpaca-mcp.json` registers Alpaca's MCP server, scoped via `ALPACA_TOOLSETS` to `options-data`, `assets`, and `stock-data`. `src/chat.py::call_claude_chat` is extended to pass `--mcp-config`, `--strict-mcp-config`, and an expanded `--allowedTools` list (WebSearch plus every individual `mcp__alpaca__*` tool name — `--allowedTools` has no MCP wildcard support) to the existing `claude -p` subprocess call. The existing prefetch (`detect_options_intent`, `fetch_options_grid`, `gather_chat_blocks`) is untouched.

**Tech Stack:** Python 3.12, `alpaca-mcp-server` (PyPI, v2.x), Claude Code CLI (already vendored into the discord-bot Docker image), pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-07-09-agentic-options-mcp-design.md`

## Global Constraints

- `ALPACA_TOOLSETS` must be exactly `options-data,assets,stock-data` — no `trading`, `positions`, `account`, `watchlists`, `crypto-data`, `news`, `fixed-income-data`, `index-data`, `corporate-actions`, or `locates`.
- `ALPACA_PAPER_TRADE` set to `"true"` in the MCP server env (defense-in-depth; `trading` toolset is disabled regardless).
- Env var name bridge: the MCP server requires `ALPACA_SECRET_KEY`; the repo's existing secret is `ALPACA_API_SECRET` (see `.env.example`) — map, don't rename.
- Per-server MCP tool-call timeout: `30000` ms, nested inside the existing 120s timeout on `call_claude_chat`.
- No new secrets — reuse `ALPACA_API_KEY` / `ALPACA_API_SECRET`, already in `.env` and already loaded into the discord-bot container via `env_file: .env` in `docker-compose.yml`.
- `alpaca-mcp-server` is added as a plain entry in `pyproject.toml`'s flat `dependencies` list — no new extras group, matching the repo's existing pattern (no per-service dependency splitting exists today).
- `--allowedTools` does not support `mcp__servername__*` wildcards — every enabled tool name must be listed individually.

---

### Task 1: Alpaca MCP server config + dependency

**Files:**
- Create: `discord_bot/alpaca-mcp.json`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `discord_bot/alpaca-mcp.json` — a file consumed by Task 2's `_MCP_CONFIG_PATH` constant in `src/chat.py`.

- [ ] **Step 1: Create the MCP server config**

Create `discord_bot/alpaca-mcp.json`:

```json
{
  "mcpServers": {
    "alpaca": {
      "command": "alpaca-mcp-server",
      "env": {
        "ALPACA_API_KEY": "${ALPACA_API_KEY}",
        "ALPACA_SECRET_KEY": "${ALPACA_API_SECRET}",
        "ALPACA_PAPER_TRADE": "true",
        "ALPACA_TOOLSETS": "options-data,assets,stock-data"
      },
      "timeout": 30000
    }
  }
}
```

- [ ] **Step 2: Add the dependency**

In `pyproject.toml`, add `"alpaca-mcp-server"` as the last entry in the `dependencies` list (after `"holidays>=0.55"`):

```toml
dependencies = [
    "httpx>=0.27",
    "aiohttp>=3.9.0",
    "discord.py>=2.3.0",
    "yfinance>=0.2.40,<1.0",
    "google-genai>=1.0",
    "python-dotenv>=1.0",
    "pydantic>=2.7",
    "pydantic-settings>=2.2",
    "fastapi>=0.110.0",
    "uvicorn>=0.29.0",
    "redis[asyncio]>=5.0",
    "pandas>=2.0,<3.0",
    "pandas-ta>=0.3.14b0",
    "scipy>=1.11",
    "scikit-learn>=1.5",
    "lightgbm>=4.0",
    "lxml>=5.0",
    "requests>=2.31",
    "holidays>=0.55",
    "alpaca-mcp-server",
]
```

- [ ] **Step 3: Rebuild the discord-bot image**

Run: `docker compose build discord-bot`
Expected: build succeeds; pip install step shows `alpaca-mcp-server` and its dependencies being installed.

- [ ] **Step 4: Verify the console script is on PATH**

Run: `docker compose run --rm discord-bot which alpaca-mcp-server`
Expected: prints a path (e.g. `/usr/local/bin/alpaca-mcp-server`), exit code 0. If this fails with a non-zero exit or empty output, the package didn't register a console script under this name — check `pip show -f alpaca-mcp-server` output inside the container for the actual entry point and update `command` in `discord_bot/alpaca-mcp.json` (Step 1) accordingly before proceeding.

- [ ] **Step 5: Commit**

```bash
git add discord_bot/alpaca-mcp.json pyproject.toml
git commit -m "feat(discord-bot): add Alpaca MCP server config scoped to market-data toolsets"
```

---

### Task 2: Wire `call_claude_chat` to the Alpaca MCP server

**Files:**
- Modify: `src/chat.py:1-10` (imports), `src/chat.py:333-341` (`call_claude_chat`)
- Test: `tests/test_chat_logic.py`

**Interfaces:**
- Consumes: `discord_bot/alpaca-mcp.json` (Task 1).
- Produces: `call_claude_chat(prompt: str, timeout: int = 120) -> str | None` — signature unchanged, callers in `discord_bot/commands/chat.py` need no changes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_chat_logic.py`. First add `import asyncio` near the top of the file (after `from datetime import date`):

```python
import asyncio
from datetime import date
```

Then add this new test class at the end of the file:

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

        allowed_idx = argv.index("--allowedTools")
        allowed = argv[allowed_idx + 1:]
        assert "WebSearch" in allowed
        assert "mcp__alpaca__get_option_chain" in allowed
        assert "mcp__alpaca__get_option_snapshot" in allowed
        assert "mcp__alpaca__get_stock_snapshot" in allowed
        assert "mcp__alpaca__get_option_contracts" in allowed
        assert len(allowed) == 25  # WebSearch + 24 Alpaca tools
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py::TestCallClaudeChatMcpWiring -v`
Expected: FAIL — `ValueError: '--strict-mcp-config' is not in list` (or similar), since the current `call_claude_chat` only passes `--tools WebSearch --allowedTools WebSearch`.

- [ ] **Step 3: Add the Path import**

In `src/chat.py`, change lines 5-7 from:

```python
import asyncio
import logging
import re
```

to:

```python
import asyncio
import logging
from pathlib import Path
import re
```

- [ ] **Step 4: Add the MCP config path and allowed-tools constants**

In `src/chat.py`, immediately before the `call_claude_chat` function definition (currently line 333), add:

```python
_MCP_CONFIG_PATH = Path(__file__).parent.parent / "discord_bot" / "alpaca-mcp.json"

# Must stay in sync with ALPACA_TOOLSETS=options-data,assets,stock-data in
# discord_bot/alpaca-mcp.json — --allowedTools has no MCP wildcard support
# (mcp__alpaca__* is not accepted), so every enabled tool is listed by name.
_ALPACA_ALLOWED_TOOLS: tuple[str, ...] = (
    # options-data toolset
    "mcp__alpaca__get_option_chain",
    "mcp__alpaca__get_option_snapshot",
    "mcp__alpaca__get_option_latest_quote",
    "mcp__alpaca__get_option_latest_trade",
    "mcp__alpaca__get_option_bars",
    "mcp__alpaca__get_option_trades",
    "mcp__alpaca__get_option_exchange_codes",
    # assets toolset
    "mcp__alpaca__get_option_contracts",
    "mcp__alpaca__get_option_contract",
    "mcp__alpaca__get_all_assets",
    "mcp__alpaca__get_asset",
    "mcp__alpaca__get_calendar",
    "mcp__alpaca__get_clock",
    "mcp__alpaca__get_corporate_action_announcements",
    "mcp__alpaca__get_corporate_action_announcement",
    # stock-data toolset
    "mcp__alpaca__get_stock_bars",
    "mcp__alpaca__get_stock_quotes",
    "mcp__alpaca__get_stock_trades",
    "mcp__alpaca__get_stock_latest_bar",
    "mcp__alpaca__get_stock_latest_quote",
    "mcp__alpaca__get_stock_latest_trade",
    "mcp__alpaca__get_stock_snapshot",
    "mcp__alpaca__get_most_active_stocks",
    "mcp__alpaca__get_market_movers",
)
```

- [ ] **Step 5: Update the subprocess call**

In `src/chat.py`, change (current lines 336-341):

```python
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", "--tools", "WebSearch", "--allowedTools", "WebSearch",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
```

to:

```python
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--mcp-config", str(_MCP_CONFIG_PATH),
            "--strict-mcp-config",
            "--tools", "WebSearch",
            "--allowedTools", "WebSearch", *_ALPACA_ALLOWED_TOOLS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py::TestCallClaudeChatMcpWiring -v`
Expected: PASS

- [ ] **Step 7: Run the full test suite to check for regressions**

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`
Expected: all tests pass (no changes to `gather_chat_blocks`, prompt building, or ticker detection).

- [ ] **Step 8: Lint**

Run: `~/.local/bin/ruff check src/chat.py tests/test_chat_logic.py`
Expected: no errors. (A `PostToolUse` hook auto-formats edited `.py` files, so `ruff format` shouldn't be needed, but run it if `ruff check` flags formatting.)

- [ ] **Step 9: Commit**

```bash
git add src/chat.py tests/test_chat_logic.py
git commit -m "feat(chat): wire call_claude_chat to Alpaca MCP server for on-demand data fetch"
```

---

### Task 3: End-to-end verification against the live Discord thread

**Files:** none (verification only)

**Interfaces:**
- Consumes: Task 1's `discord_bot/alpaca-mcp.json` and Task 2's updated `call_claude_chat`.

This task requires manually interacting with the real private Discord server — it cannot be automated by a subagent, since Claude has no Discord access. The user must perform the Discord steps themselves.

- [ ] **Step 1: Bring up the local stack with the rebuilt image**

Run: `docker compose up --build`
Expected: `api`, `dashboard`, `discord-bot`, and `redis` services start; discord-bot logs show it connecting to Discord without errors.

- [ ] **Step 2: Confirm the MCP server actually starts inside the running container**

Run: `docker compose logs discord-bot | grep -i "alpaca\|mcp"`
Expected: no error/crash lines referencing `alpaca-mcp-server` or MCP connection failures. (The MCP server launches lazily on the first `claude -p` call rather than at container startup, so this may show nothing until Step 3 triggers a message — that's fine, re-run after Step 3 if this is empty.)

- [ ] **Step 3: Ask a question inside the prefetch's existing scope (regression check)**

In the real private Discord trade chat channel/thread, send a message like `SOFI CSP, what strike looks good?`
Expected: bot replies with a recommendation referencing live premium/delta data, same as before this change — confirms the existing prefetch path still works unmodified.

- [ ] **Step 4: Ask a follow-up outside the prefetch's scope**

In the same thread, send a follow-up like `what about something further out, like a 90 DTE covered call on SOFI?`
Expected: the prefetch grid only covers up to 47 DTE (per `docs/superpowers/specs/2026-07-06-live-options-chain-lookup-design.md`), so this expiration window has no prefetched data. A correct response includes specific strike/premium/Greeks data for a ~90 DTE SOFI expiration — this can only come from the bot calling an Alpaca MCP tool live, since the prefetch never covers that window. If the bot instead says it has no data that far out (the old, pre-tool-call behavior), the tool call didn't fire — check `docker compose logs discord-bot` around the time of the message for stderr from the `claude` subprocess for permission-denial or MCP-launch errors.

- [ ] **Step 5: Confirm scope stayed read-only**

Re-check the response from Step 4 and the container logs — confirm nothing indicates an order was placed, a position was touched, or account/watchlist data was modified. There should be no way for this to happen given the `ALPACA_TOOLSETS` scoping from Task 1, but this step is the concrete check that the scoping actually held in practice, not just on paper.

- [ ] **Step 6: Confirm graceful degradation when the MCP server can't start**

This checks the open question noted in the design spec's Error Handling section: does `call_claude_chat` still return a usable response if the Alpaca MCP server fails to launch, or does the whole `claude -p` call fail?

Temporarily break the MCP server's credentials to force a launch failure:

Run: `docker compose run --rm -e ALPACA_API_KEY=invalid -e ALPACA_API_SECRET=invalid discord-bot python3 -c "
import asyncio
from src.chat import call_claude_chat

async def main():
    result = await call_claude_chat('Say hello in one word.')
    print('RESULT:', repr(result))

asyncio.run(main())
"`

Expected one of two outcomes:
- **Graceful degradation:** prints `RESULT: 'Hello'` (or similar) — `claude -p` completed using `WebSearch` even though the Alpaca MCP tools were unavailable. No code change needed; note this behavior in the spec/plan for future reference.
- **Hard failure:** prints `RESULT: None` — the invalid MCP server config took down the whole call. This is already handled at the caller level: `_handle_message` in `discord_bot/commands/chat.py` falls back to `synthesize()` (Gemini) whenever `call_claude_chat` returns `None`, so the bot still responds, just without the WebSearch tool either for that turn. No code change needed here either, since this matches the spec's stated fallback behavior — just confirms it's the actual failure mode so it's documented rather than assumed.

Either outcome is acceptable per the spec; this step exists to confirm which one actually happens, not to require a specific one.

- [ ] **Step 7: Report results**

No commit — this is a verification task. If Steps 3-6 pass, the feature is confirmed working end-to-end.
