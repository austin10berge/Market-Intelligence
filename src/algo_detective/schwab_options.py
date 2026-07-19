"""Collects real per-contract put delta (plus bid/ask/open_interest) for
the narrow (all-time-prime) ticker universe via schwab-mcp, the nightly
pipeline's Step 8. schwab-mcp's get_option_chain tool returns its own
compact text formatting (not raw JSON) — see the module-level parser
functions' docstrings for the exact shape, captured live during design.
See docs/superpowers/specs/2026-07-19-algo-detective-schwab-delta-design.md.
"""

from __future__ import annotations

import csv
import logging
import re

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
