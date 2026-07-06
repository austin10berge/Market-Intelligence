"""Live options chain lookup for the trade chatbot — intent detection and
Alpaca snapshot grid fetching."""

from __future__ import annotations

import re
from datetime import date, timedelta

from .stocks import (
    _build_alpaca_client,
    _extract_implied_volatility,
    _parse_occ_symbol,
    _to_float,
)

_CALL_WORDS = frozenset({"call", "calls", "cc", "covered call", "covered calls"})
_PUT_WORDS = frozenset({"put", "puts", "csp", "csps"})
_GENERIC_WORDS = frozenset({
    "wheel", "strike", "strikes", "premium", "expiration", "expiring",
    "dte", "sell", "option", "options",
})

_CALL_SHORTHAND_RE = re.compile(r"\b\d{1,4}(?:\.\d+)?c\b", re.IGNORECASE)
_PUT_SHORTHAND_RE = re.compile(r"\b\d{1,4}(?:\.\d+)?p\b", re.IGNORECASE)
_DATE_SHORTHAND_RE = re.compile(r"\b\d{1,2}/\d{1,2}\b")


def _has_word_match(text: str, words: frozenset[str]) -> bool:
    """Check if text contains any of the words/phrases with word-boundary matching.

    Uses regex with \b word boundaries to avoid false positives from substring
    matches (e.g., "put" in "computer" or "cc" in "according").
    """
    for word in words:
        # Split multi-word phrases and rejoin with whitespace boundaries
        parts = word.split()
        if len(parts) == 1:
            # Single word: word boundaries on both sides
            pattern = r"\b" + re.escape(word) + r"\b"
        else:
            # Multi-word phrase: word boundary at start/end, one-or-more spaces between
            pattern = r"\b" + r"\s+".join(re.escape(p) for p in parts) + r"\b"

        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False


def detect_options_intent(text: str) -> str | None:
    """Classify a chat message as call-side, put-side/generic options intent, or neither.

    Returns "call" only when call-side language is present with no put-side
    signal. Returns "put" for put-side language, generic options language
    (strike/premium/wheel/etc.), or bare date/strike shorthand ("7/17",
    "21c", "21p") — this matches the CSP/wheel-focused default in
    trade_system_prompt.txt. Returns None for messages with no options
    intent at all (e.g. a bare ticker mention).
    """
    lowered = text.lower()

    has_call_word = _has_word_match(lowered, _CALL_WORDS)
    has_put_word = _has_word_match(lowered, _PUT_WORDS)
    has_generic_word = _has_word_match(lowered, _GENERIC_WORDS)
    has_call_shorthand = bool(_CALL_SHORTHAND_RE.search(text))
    has_put_shorthand = bool(_PUT_SHORTHAND_RE.search(text))
    has_date_shorthand = bool(_DATE_SHORTHAND_RE.search(text))

    any_intent = (
        has_call_word or has_put_word or has_generic_word
        or has_call_shorthand or has_put_shorthand or has_date_shorthand
    )
    if not any_intent:
        return None

    if (has_call_word or has_call_shorthand) and not (has_put_word or has_put_shorthand):
        return "call"

    return "put"


MAX_DTE = 47
STRIKE_RANGE_PUT_LOW = 0.18    # scan puts from close × (1 - 0.18)
STRIKE_RANGE_PUT_HIGH = 0.02   # to close × (1 + 0.02)
STRIKE_RANGE_CALL_LOW = 0.02   # scan calls from close × (1 - 0.02)
STRIKE_RANGE_CALL_HIGH = 0.12  # to close × (1 + 0.12)
_GRID_PAGE_LIMIT = 200


def fetch_options_grid(ticker: str, close_price: float, option_type: str) -> list[dict]:
    """Fetch a live grid of near-money option contracts for `ticker`.

    Pulls all `option_type` ("put" or "call") contracts expiring within the
    next MAX_DTE days, within the strike window used elsewhere in this
    codebase for CSP/covered-call scanning. Returns one row per contract,
    sorted by expiration then strike. Returns [] on any failure, missing
    credentials, or a non-positive close_price.
    """
    if close_price <= 0:
        return []

    client = _build_alpaca_client()
    if client is None:
        return []

    today = date.today()
    if option_type == "call":
        strike_lo = close_price * (1 - STRIKE_RANGE_CALL_LOW)
        strike_hi = close_price * (1 + STRIKE_RANGE_CALL_HIGH)
        occ_type = "C"
    else:
        strike_lo = close_price * (1 - STRIKE_RANGE_PUT_LOW)
        strike_hi = close_price * (1 + STRIKE_RANGE_PUT_HIGH)
        occ_type = "P"

    params = {
        "feed": "indicative",
        "limit": _GRID_PAGE_LIMIT,
        "type": option_type,
        "expiration_date_gte": (today + timedelta(days=1)).isoformat(),
        "expiration_date_lte": (today + timedelta(days=MAX_DTE)).isoformat(),
        "strike_price_gte": round(strike_lo, 2),
        "strike_price_lte": round(strike_hi, 2),
    }

    rows: list[dict] = []
    next_page_token: str | None = None

    try:
        while True:
            request_params = dict(params)
            if next_page_token:
                request_params["page_token"] = next_page_token

            response = client.get(
                f"/v1beta1/options/snapshots/{ticker}", params=request_params
            )
            response.raise_for_status()
            payload = response.json()
            snapshots = payload.get("snapshots", {})

            for contract_symbol, snapshot in snapshots.items():
                row = _parse_snapshot_row(contract_symbol, snapshot or {}, today, occ_type)
                if row is not None:
                    rows.append(row)

            next_page_token = payload.get("next_page_token")
            if not next_page_token:
                break
    except Exception:
        return []
    finally:
        client.close()

    rows.sort(key=lambda r: (r["expiration"], r["strike"]))
    return rows


def _parse_snapshot_row(
    contract_symbol: str, snapshot: dict, today: date, expected_type: str
) -> dict | None:
    parsed = _parse_occ_symbol(contract_symbol)
    if parsed is None:
        return None
    expiration, option_type, strike = parsed
    if option_type != expected_type:
        return None

    latest_quote = snapshot.get("latestQuote") or {}
    bid = _to_float(latest_quote.get("bp"))
    ask = _to_float(latest_quote.get("ap"))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None

    mid = (bid + ask) / 2
    spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else None

    greeks = snapshot.get("greeks") or {}
    delta = _to_float(greeks.get("delta"))
    iv = _extract_implied_volatility(snapshot)
    volume = int(_to_float((snapshot.get("dailyBar") or {}).get("v")) or 0)

    return {
        "expiration": expiration,
        "dte": max((expiration - today).days, 0),
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": round(mid, 2),
        "spread_pct": round(spread_pct, 1) if spread_pct is not None else None,
        "iv": round(iv, 1) if iv is not None else None,
        "delta": round(delta, 3) if delta is not None else None,
        "volume": volume,
    }
