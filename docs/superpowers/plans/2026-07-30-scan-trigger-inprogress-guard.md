# Scan Trigger In-Progress Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop two concurrent `/api/scan/trigger` calls (e.g. a user double-tapping Discord's `/scan` command) from both running the full pipeline — today nothing prevents double LLM cost, double external fetches, and duplicate DB rows for the same date.

**Architecture:** Add a module-level in-process flag in `src/api/main.py` tracking whether a pipeline run triggered via `/api/scan/trigger` is currently in flight. If a second trigger request arrives while one is running, return immediately with a distinct "already running" status instead of queuing a second `run_pipeline()` call. Update the Discord bot's `/scan` command handler to show a different message when it gets that response, instead of always claiming "Scan Queued".

**Tech Stack:** Python, FastAPI, asyncio, discord.py, pytest, pytest-asyncio.

## Global Constraints

- Single uvicorn process, no `--workers` flag (`Dockerfile:32`) — an in-process flag is sufficient, no distributed lock needed.
- Do not guard against the *nightly cron* pipeline run overlapping with a Discord-triggered run — that's out of scope for this plan; only concurrent `/api/scan/trigger` calls need to be deduplicated.
- Preserve the existing 401 unauthorized behavior (bad/missing `x-bot-token`) — the in-progress check happens after auth, not before.

---

## File Structure

- Modify: `src/api/main.py` — add the in-progress flag and the guard in `trigger_scan`.
- Modify: `discord_bot/commands/scan.py` — handle the "already running" response distinctly from "queued".
- Test: new file `tests/test_scan_trigger_guard.py` — covers the API-side guard.
- Test (Discord bot side): check whether `discord_bot/` has its own test directory/suite (e.g. `discord_bot/tests/`) — if so, add a test there following its conventions; if the bot has no existing test suite, skip automated testing for the bot-side change and note it as a manual-verification item (do not invent a new test framework for a single small change).

## Interfaces

- Module-level state in `src/api/main.py`: `_scan_in_progress: bool` (or an `asyncio.Lock` — see Task 1 for the choice).
- `/api/scan/trigger` response on a duplicate request: `{"status": "already_running", "message": "A scan is already in progress. Results will post to Discord shortly."}` with HTTP 200 (not an error — this is an expected, handled condition, not a failure).

---

### Task 1: Add the in-progress guard to `/api/scan/trigger`

**Files:**
- Modify: `src/api/main.py:177-204` (`_run_and_post_to_discord`, `trigger_scan`)
- Test: `tests/test_scan_trigger_guard.py` (new file)

**Interfaces:**
- Produces: `trigger_scan` now returns `{"status": "already_running", ...}` (HTTP 200) instead of queuing a second background task, when a scan is already in flight.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scan_trigger_guard.py`. Check `src/api/main.py`'s existing tests (search for a `tests/test_api_*.py` covering `/api/scan/trigger`) for the request/auth-header construction convention; if `trigger_scan` is tested by calling the route function directly with a mocked `Request` and `BackgroundTasks`, follow that pattern:

```python
"""Tests for the /api/scan/trigger in-progress guard."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks


def _make_request(token: str) -> MagicMock:
    req = MagicMock()
    req.headers = {"x-bot-token": token, "x-bot-callback-url": ""}
    return req


@pytest.mark.asyncio
async def test_trigger_scan_rejects_second_call_while_first_in_progress(monkeypatch):
    from src.api import main as api_main

    monkeypatch.setattr(api_main.settings, "discord_bot_secret", "test-secret")

    # Make the background pipeline run "hang" so the second request arrives
    # while the first is still in flight.
    release = asyncio.Event()

    async def slow_pipeline_run(channel_id, discord_bot_url):
        await release.wait()

    monkeypatch.setattr(api_main, "_run_and_post_to_discord", slow_pipeline_run)

    bg_tasks_1 = BackgroundTasks()
    body = api_main.ScanTriggerRequest(channel_id="123", requested_by="alice")

    first = await api_main.trigger_scan(_make_request("test-secret"), body, bg_tasks_1)
    assert first["status"] == "queued"

    # Fire the background task the way FastAPI would, but don't await completion —
    # simulate it being "in flight" when the second request comes in.
    task = asyncio.create_task(bg_tasks_1.tasks[0].func(*bg_tasks_1.tasks[0].args, **bg_tasks_1.tasks[0].kwargs))
    await asyncio.sleep(0)  # let it start

    bg_tasks_2 = BackgroundTasks()
    second = await api_main.trigger_scan(_make_request("test-secret"), body, bg_tasks_2)
    assert second["status"] == "already_running"
    assert len(bg_tasks_2.tasks) == 0  # no second background task queued

    release.set()
    await task
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scan_trigger_guard.py -v`
Expected: FAIL — `second["status"] == "queued"` (no guard exists yet), or the test errors if `BackgroundTasks().tasks` access doesn't match the installed FastAPI version's internals. If the internals don't match, adapt the test to instead call `_run_and_post_to_discord` directly to simulate "in flight" (see the alternate approach in Step 3's note) rather than relying on `BackgroundTasks` internals.

- [ ] **Step 3: Implement the guard**

In `src/api/main.py`, near the top of the "Discord Bot Endpoints" section (around line 170, before `ScanTriggerRequest`), add:

```python
# In-process guard against overlapping /api/scan/trigger runs — single uvicorn
# process (no --workers), so a plain module-level flag is sufficient. Prevents
# double LLM cost / double external fetches / duplicate digest rows when a
# user double-taps Discord's /scan command.
_scan_in_progress = False
```

Modify `_run_and_post_to_discord` to clear the flag in a `finally` block:

```python
async def _run_and_post_to_discord(channel_id: str, discord_bot_url: str) -> None:
    """Background task: run pipeline, POST results back to the Discord bot."""
    global _scan_in_progress
    if not discord_bot_url:
        discord_bot_url = os.getenv("DISCORD_BOT_CALLBACK_URL", "http://discord-bot:9000")
    try:
        result = await run_pipeline(output_mode="on-demand")
        await invalidate_market_posture()
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{discord_bot_url}/callback",
                json={"channel_id": channel_id, "result": result},
                headers={"x-bot-secret": settings.discord_bot_secret},
            )
    except Exception as e:
        logger.error(f"Discord scan callback failed: {e}")
    finally:
        _scan_in_progress = False
```

Modify `trigger_scan`:

```python
@app.post("/api/scan/trigger")
async def trigger_scan(req: Request, body: ScanTriggerRequest, background_tasks: BackgroundTasks):
    """Trigger a market sentiment scan from the Discord bot."""
    global _scan_in_progress
    token = req.headers.get("x-bot-token")
    if not token or token != settings.discord_bot_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if _scan_in_progress:
        return {
            "status": "already_running",
            "message": "A scan is already in progress. Results will post to Discord shortly.",
        }

    _scan_in_progress = True
    discord_bot_url = req.headers.get("x-bot-callback-url", "")
    background_tasks.add_task(_run_and_post_to_discord, body.channel_id, discord_bot_url)
    return {"status": "queued", "message": "Scan started. Results will post to Discord shortly."}
```

Note: setting `_scan_in_progress = True` happens synchronously in the route handler before the background task is scheduled, so there is no race window between two requests checking-then-setting — FastAPI route handlers for a given request run to completion before the event loop yields to another coroutine at an `await` point, and there is no `await` between the check and the set here.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scan_trigger_guard.py -v`
Expected: PASS. If Step 2's test needed adapting away from `BackgroundTasks` internals, use this simpler equivalent instead:

```python
@pytest.mark.asyncio
async def test_trigger_scan_rejects_second_call_while_first_in_progress(monkeypatch):
    from src.api import main as api_main

    monkeypatch.setattr(api_main.settings, "discord_bot_secret", "test-secret")
    api_main._scan_in_progress = True  # simulate a run already in flight
    try:
        body = api_main.ScanTriggerRequest(channel_id="123", requested_by="alice")
        result = await api_main.trigger_scan(_make_request("test-secret"), body, BackgroundTasks())
        assert result["status"] == "already_running"
    finally:
        api_main._scan_in_progress = False


@pytest.mark.asyncio
async def test_trigger_scan_queues_when_not_in_progress(monkeypatch):
    from src.api import main as api_main

    monkeypatch.setattr(api_main.settings, "discord_bot_secret", "test-secret")
    api_main._scan_in_progress = False
    bg = BackgroundTasks()
    body = api_main.ScanTriggerRequest(channel_id="123", requested_by="alice")
    result = await api_main.trigger_scan(_make_request("test-secret"), body, bg)
    assert result["status"] == "queued"
    assert api_main._scan_in_progress is True
    api_main._scan_in_progress = False  # cleanup — normally cleared by _run_and_post_to_discord
```

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api/main.py tests/test_scan_trigger_guard.py
git commit -m "fix(api): guard /api/scan/trigger against overlapping pipeline runs"
```

---

### Task 2: Make the Discord bot show a distinct message for "already running"

**Files:**
- Modify: `discord_bot/commands/scan.py:27-63` (`ScanCommands.scan`)

**Interfaces:**
- Consumes: `{"status": "already_running", "message": "..."}` response shape from Task 1.

- [ ] **Step 1: Implement the response-status branch**

In `discord_bot/commands/scan.py`, the `scan` command currently does `resp.raise_for_status()` then always shows a "Scan Queued" embed. Change it to branch on the response body's `status` field:

```python
    @app_commands.command(name="scan", description="Trigger a full market sentiment scan")
    async def scan(self, interaction: discord.Interaction) -> None:
        """Triggers the pipeline and posts results back when complete."""
        await interaction.response.defer(thinking=True)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{API_BASE}/api/scan/trigger",
                    json={
                        "channel_id": str(interaction.channel_id),
                        "requested_by": str(interaction.user),
                    },
                    headers={
                        "x-bot-token": BOT_SECRET,
                        "x-bot-callback-url": f"http://discord-bot:{CALLBACK_PORT}",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(
                embed=_error_embed(f"API returned {e.response.status_code}: {e.response.text}")
            )
            return
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(str(e)))
            return

        if payload.get("status") == "already_running":
            embed = discord.Embed(
                title="⏳ Scan Already Running",
                description=(
                    "A scan is already in progress — hang tight, results will "
                    "post to this channel shortly."
                ),
                color=discord.Color.orange(),
            )
        else:
            embed = discord.Embed(
                title="⏳ Scan Queued",
                description=(
                    f"Full pipeline is running. Results will appear in this channel "
                    f"in ~30–60 seconds.\n\n*Requested by {interaction.user.mention}*"
                ),
                color=discord.Color.blue(),
            )
        await interaction.followup.send(embed=embed)
```

- [ ] **Step 2: Manual verification (no existing bot test suite to extend)**

Check `discord_bot/` for a `tests/` directory or any `test_*.py` file. If none exists, this change is small and low-risk enough not to warrant introducing a new test harness — instead, verify manually:

Run: `python -c "import ast; ast.parse(open('discord_bot/commands/scan.py').read())"`
Expected: No syntax errors (basic sanity check since there's no test suite to run).

If a test suite *does* exist under `discord_bot/`, add a test there mirroring its conventions (mock the `httpx.AsyncClient.post` response to return `{"status": "already_running", "message": "..."}` and assert the embed title is "⏳ Scan Already Running").

- [ ] **Step 3: Commit**

```bash
git add discord_bot/commands/scan.py
git commit -m "feat(discord-bot): show distinct message when a scan is already running"
```
