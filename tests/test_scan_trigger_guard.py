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
