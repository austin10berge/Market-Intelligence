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
