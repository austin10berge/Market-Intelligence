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
