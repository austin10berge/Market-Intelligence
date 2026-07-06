"""Live options chain lookup for the trade chatbot — intent detection and
Alpaca snapshot grid fetching."""

from __future__ import annotations

import re

_CALL_WORDS = frozenset({"call", "calls", "cc", "covered call", "covered calls"})
_PUT_WORDS = frozenset({"put", "puts", "csp", "csps"})
_GENERIC_WORDS = frozenset({
    "wheel", "strike", "strikes", "premium", "expiration", "expiring",
    "dte", "sell", "option", "options",
})

_CALL_SHORTHAND_RE = re.compile(r"\b\d{1,4}(?:\.\d+)?c\b", re.IGNORECASE)
_PUT_SHORTHAND_RE = re.compile(r"\b\d{1,4}(?:\.\d+)?p\b", re.IGNORECASE)
_DATE_SHORTHAND_RE = re.compile(r"\b\d{1,2}/\d{1,2}\b")


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

    has_call_word = any(word in lowered for word in _CALL_WORDS)
    has_put_word = any(word in lowered for word in _PUT_WORDS)
    has_generic_word = any(word in lowered for word in _GENERIC_WORDS)
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
