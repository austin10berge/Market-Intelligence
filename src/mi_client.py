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
