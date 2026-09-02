"""Tests for GET /api/earnings-calendar endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.models import Signal, SignalSource


@pytest.fixture
def upcoming_signal():
    return Signal(
        source=SignalSource.EARNINGS_CALENDAR,
        value=0.0,
        metadata={
            "upcoming": [
                {"symbol": "CRM", "name": "Salesforce Inc.", "report_date": "2026-09-05", "estimate": "2.44"},
            ],
            "count": 1,
            "lookahead_days": 21,
        },
        summary="Earnings Calendar: 1 report(s) in next 21 days",
    )


@pytest.fixture
def empty_signal():
    return Signal(
        source=SignalSource.EARNINGS_CALENDAR,
        value=0.0,
        metadata={"upcoming": [], "count": 0, "lookahead_days": 21},
        summary="Earnings Calendar: no reports in the next 21 days",
    )


async def test_returns_upcoming_earnings(upcoming_signal):
    with patch(
        "src.api.main.EarningsCalendarFetcher.fetch",
        new_callable=AsyncMock,
        return_value=upcoming_signal,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/earnings-calendar")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["lookahead_days"] == 21
    assert data["upcoming"][0]["symbol"] == "CRM"


async def test_returns_empty_when_none_upcoming(empty_signal):
    with patch(
        "src.api.main.EarningsCalendarFetcher.fetch",
        new_callable=AsyncMock,
        return_value=empty_signal,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/earnings-calendar")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["upcoming"] == []


async def test_returns_empty_on_fetch_failure():
    with patch(
        "src.api.main.EarningsCalendarFetcher.fetch",
        new_callable=AsyncMock,
        return_value=None,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/earnings-calendar")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["upcoming"] == []
    assert "error" in data
