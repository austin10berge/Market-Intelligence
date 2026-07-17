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
