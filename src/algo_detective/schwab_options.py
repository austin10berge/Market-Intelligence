"""Collects real per-contract put delta (plus bid/ask/open_interest) for
the narrow (all-time-prime) ticker universe via schwab-mcp, the nightly
pipeline's Step 8. schwab-mcp's get_option_chain tool returns its own
compact text formatting (not raw JSON) — see the module-level parser
functions' docstrings for the exact shape, captured live during design.
See docs/superpowers/specs/2026-07-19-algo-detective-schwab-delta-design.md.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import re
from datetime import date, timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .options_chain import _next_fridays
from .store import upsert_options_rows

logger = logging.getLogger(__name__)

_STRIKE_HEADER_RE = re.compile(
    r'^\s*"([\d.]+)"\[\d+,?\]\{([^}]+)\}:\s*\n\s*(.+)$',
    re.MULTILINE,
)


def _parse_put_chain(raw_text: str) -> list[dict]:
    """Parse schwab-mcp's compact get_option_chain text output into a list
    of put contract dicts: {strike, bid, ask, delta, open_interest}.

    The compact format pairs a per-strike header line
    '"STRIKE"[N,]{field1,field2,...}:' with a CSV value line directly below
    it — schwab-mcp's own display formatting, not raw Schwab JSON. Field
    order is read from each header rather than hardcoded, since it's
    regenerated per response.
    """
    contracts = []
    for match in _STRIKE_HEADER_RE.finditer(raw_text):
        strike_str, field_names_csv, value_line = match.groups()
        field_names = [f.strip() for f in field_names_csv.split(",")]
        values = next(csv.reader([value_line.strip()]))
        field_map = dict(zip(field_names, values))
        try:
            contracts.append(
                {
                    "strike": float(strike_str),
                    "bid": float(field_map["bid"]),
                    "ask": float(field_map["ask"]),
                    "delta": float(field_map["delta"]),
                    "open_interest": int(field_map["openInterest"]),
                }
            )
        except (KeyError, ValueError):
            logger.warning("Skipping unparseable contract row: %r", value_line)
            continue
    return contracts


def _select_target_delta_contract(contracts: list[dict], target_delta: float = 0.20) -> dict | None:
    """Return the put contract whose delta magnitude is closest to
    target_delta (mLabs' stated CSP range is 0.15-0.30 puts; 0.20 is the
    midpoint). Returns None if contracts is empty."""
    if not contracts:
        return None
    return min(contracts, key=lambda c: abs(abs(c["delta"]) - target_delta))


async def _fetch_chain_via_mcp_async(ticker: str, from_date_str: str, to_date_str: str) -> str:
    from ..config import settings

    async with streamable_http_client(settings.schwab_mcp_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_option_chain",
                {
                    "symbol": ticker,
                    "contract_type": "PUT",
                    "from_date": from_date_str,
                    "to_date": to_date_str,
                },
            )
            if result.is_error:
                raise RuntimeError(
                    f"schwab-mcp get_option_chain error for {ticker}: {result.content}"
                )
            return result.content[0].text


def _fetch_chain_via_mcp(ticker: str, from_date_str: str, to_date_str: str) -> str:
    """Sync boundary around the async MCP tool call — bridges this
    module's asyncio.to_thread-based calling convention (see main.py) to
    the MCP SDK's async-only client API. This is the network boundary;
    tests patch this function directly rather than simulating the MCP
    session handshake (no automated test safely exercises the real live
    schwab-mcp service — see the design spec's Testing section)."""
    return asyncio.run(_fetch_chain_via_mcp_async(ticker, from_date_str, to_date_str))


def fetch_delta_snapshot(tickers: list[str], scan_date_str: str) -> int:
    """Step 8 of the nightly pipeline: fetch real put delta/bid/ask/
    open_interest for tickers via Schwab (schwab-mcp), upsert into
    detective_options. Returns the number of rows written."""
    scan_date = date.fromisoformat(scan_date_str)
    fridays = _next_fridays(scan_date + timedelta(days=1), n=2)
    from_date_str, to_date_str = fridays[0].isoformat(), fridays[-1].isoformat()

    rows = []
    for ticker in tickers:
        try:
            raw = _fetch_chain_via_mcp(ticker, from_date_str, to_date_str)
            contracts = _parse_put_chain(raw)
        except Exception:
            logger.warning(
                "Failed to fetch option chain for %s on %s", ticker, scan_date_str, exc_info=True
            )
            continue

        selected = _select_target_delta_contract(contracts)
        if selected is None:
            logger.warning("No usable put contracts for %s on %s", ticker, scan_date_str)
            continue

        rows.append(
            {
                "date": scan_date_str,
                "ticker": ticker,
                "delta": selected["delta"],
                "bid": selected["bid"],
                "ask": selected["ask"],
                "open_interest": selected["open_interest"],
            }
        )

    if not rows:
        return 0
    written = upsert_options_rows(rows)
    logger.info("Delta snapshot %s: %d/%d tickers written", scan_date_str, written, len(tickers))
    return written
