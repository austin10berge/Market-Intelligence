# Live Options Chain Lookup for Trade Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the trade chatbot answer contract-level options questions (specific quotes like "SOFI 7/17 $21 call" and open-ended ones like "which strike should I sell a SOFI CSP at") by pre-fetching a live grid of near-money contracts across multiple expirations and injecting it into the LLM prompt, the same way screener data is injected today.

**Architecture:** A new `src/screener/options_lookup.py` module classifies chat messages for options intent and fetches a live options grid from Alpaca (reusing the already-working snapshot endpoint pattern from `src/screener/stocks.py`, not `src/algo_detective/options_chain.py`'s broken batch endpoint). `src/chat.py` gains a formatter and a new orchestration function, `gather_chat_blocks()`, that replaces the inline screener-fetch loop currently in `discord_bot/commands/chat.py`. No new services or database tables.

**Tech Stack:** Python 3.12, httpx (sync client, run via `asyncio.to_thread`), respx for HTTP mocking in tests, pytest + pytest-asyncio (`asyncio_mode = "auto"`).

## Global Constraints

- Python 3.12, no local virtualenv — run all tests via `docker compose run --rm test python3 -m pytest ...`, never bare `python`/`pytest` on the host.
- A `PostToolUse` hook auto-formats every edited `.py` file with ruff — no manual `ruff format` step needed in any task.
- Alpaca's `indicative` feed (used everywhere in this codebase) does **not** return an `openInterest` field — confirmed live during design. Liquidity is represented via bid/ask spread and daily volume only. Do not add code that reads `openInterest`.
- Confirmed live during design: `greeks.delta`, `impliedVolatility`, and `latestQuote.bp`/`ap` (bid/ask) ARE present in the snapshot response.
- Options grid window: expirations from tomorrow through **47 days out** (`MAX_DTE = 47`); strikes from `close_price × (1 - 0.18)` to `close_price × (1 + 0.02)` for puts, and `close_price × (1 - 0.02)` to `close_price × (1 + 0.12)` for calls — these are the exact same constants (`_STRIKE_RANGE_PUT`/`_STRIKE_RANGE_CALL`) already proven in `src/algo_detective/options_chain.py`.
- Build on the endpoint pattern in `src/screener/stocks.py::_fetch_alpaca_atm_iv_percent` (`GET /v1beta1/options/snapshots/{symbol}` with `expiration_date_gte/lte`, `strike_price_gte/lte`, `type`, `page_token` pagination) — **not** `src/algo_detective/options_chain.py`'s `underlying_symbols` batch call, which returns a live 400 error.
- Reuse existing private helpers from `src/screener/stocks.py` rather than re-implementing: `_build_alpaca_client()`, `_parse_occ_symbol()`, `_to_float()`, `_extract_implied_volatility()`.
- No new agentic tool-use / multi-turn LLM loop — this is a server-side prefetch, single-shot-reasoning feature (see spec's "Out of Scope" section).

---

## Task 1: `detect_options_intent()` — classify chat messages for options intent

**Files:**
- Create: `src/screener/options_lookup.py`
- Test: `tests/test_options_lookup.py`

**Interfaces:**
- Produces: `detect_options_intent(text: str) -> str | None` — returns `"call"`, `"put"`, or `None`. Used by Task 4's `gather_chat_blocks()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_options_lookup.py`:

```python
"""Unit tests for src.screener.options_lookup."""
from __future__ import annotations

from src.screener.options_lookup import detect_options_intent


class TestDetectOptionsIntent:
    def test_detects_put_language(self):
        assert detect_options_intent("should I sell a CSP on SOFI") == "put"

    def test_detects_call_language(self):
        assert detect_options_intent("what about a SOFI covered call") == "call"

    def test_detects_call_shorthand(self):
        assert detect_options_intent("SOFI 7/17 21c") == "call"

    def test_detects_put_shorthand(self):
        assert detect_options_intent("SOFI 7/17 21p") == "put"

    def test_detects_date_shorthand_alone(self):
        assert detect_options_intent("SOFI 7/17 options") == "put"

    def test_generic_options_words_default_to_put(self):
        assert detect_options_intent("which strike should I sell a SOFI CSP at") == "put"

    def test_no_trigger_on_plain_ticker_mention(self):
        assert detect_options_intent("what do you think about SOFI") is None

    def test_no_trigger_on_bare_price_talk(self):
        assert detect_options_intent("SOFI is up 3% today") is None

    def test_call_word_and_put_shorthand_prefers_put(self):
        assert detect_options_intent("SOFI call or put, 21p works") == "put"

    def test_wheel_keyword_triggers_put(self):
        assert detect_options_intent("thinking about the wheel on SOFI") == "put"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_options_lookup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.screener.options_lookup'`

- [ ] **Step 3: Write minimal implementation**

Create `src/screener/options_lookup.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_options_lookup.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/screener/options_lookup.py tests/test_options_lookup.py
git commit -m "$(cat <<'EOF'
feat(chat): add options-intent classifier for trade chat

Classifies chat messages as call-side, put-side/generic options intent,
or neither, using keyword and shorthand-pattern matching. First building
block for live options chain lookup in the trade chatbot.
EOF
)"
```

---

## Task 2: `fetch_options_grid()` — live Alpaca options snapshot grid

**Files:**
- Modify: `src/screener/options_lookup.py`
- Modify: `tests/test_options_lookup.py`

**Interfaces:**
- Consumes: `_build_alpaca_client() -> httpx.Client | None`, `_parse_occ_symbol(contract_symbol: str) -> tuple[date, str, float] | None`, `_to_float(value: object) -> float | None`, `_extract_implied_volatility(snapshot: dict) -> float | None` — all from `src.screener.stocks`.
- Produces: `fetch_options_grid(ticker: str, close_price: float, option_type: str) -> list[dict]`. `option_type` is `"call"` or `"put"`. Each row: `{"expiration": date, "dte": int, "strike": float, "bid": float, "ask": float, "mid": float, "spread_pct": float | None, "iv": float | None, "delta": float | None, "volume": int}`, sorted by `(expiration, strike)`. Returns `[]` on any failure, missing credentials, or non-positive `close_price`. Used by Task 4's `gather_chat_blocks()` and Task 3's `format_options_block()`.

- [ ] **Step 1: Write the failing tests**

First, replace the top-of-file import line in `tests/test_options_lookup.py`:

```python
from src.screener.options_lookup import detect_options_intent
```

with:

```python
from datetime import date

import httpx
import pytest
import respx

from src.config import settings
from src.screener.options_lookup import detect_options_intent, fetch_options_grid


@pytest.fixture(autouse=True)
def _alpaca_creds(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_api_key", "test-key")
    monkeypatch.setattr(settings, "alpaca_api_secret", "test-secret")
```

Then append this new test class at the end of the file:

```python
class TestFetchOptionsGrid:
    @respx.mock
    def test_parses_put_contract_row(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SOFI260717P00017000": {
                            "latestQuote": {"bp": 0.19, "ap": 0.21},
                            "greeks": {"delta": -0.18},
                            "impliedVolatility": 0.61,
                            "dailyBar": {"v": 120},
                        }
                    }
                },
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "put")
        assert len(rows) == 1
        row = rows[0]
        assert row["strike"] == 17.0
        assert row["bid"] == 0.19
        assert row["ask"] == 0.21
        assert row["mid"] == 0.20
        assert row["delta"] == -0.18
        assert row["iv"] == 61.0
        assert row["volume"] == 120
        assert row["expiration"] == date(2026, 7, 17)

    @respx.mock
    def test_filters_out_wrong_option_type(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SOFI260717C00019000": {
                            "latestQuote": {"bp": 0.30, "ap": 0.35},
                        }
                    }
                },
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "put")
        assert rows == []

    @respx.mock
    def test_drops_contracts_with_no_live_quote(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={"snapshots": {"SOFI260717P00017000": {"dailyBar": {"v": 5}}}},
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "put")
        assert rows == []

    @respx.mock
    def test_paginates_through_next_page_token(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "snapshots": {
                            "SOFI260717P00017000": {
                                "latestQuote": {"bp": 0.19, "ap": 0.21},
                            }
                        },
                        "next_page_token": "abc123",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "snapshots": {
                            "SOFI260724P00017000": {
                                "latestQuote": {"bp": 0.29, "ap": 0.31},
                            }
                        }
                    },
                ),
            ]
        )
        rows = fetch_options_grid("SOFI", 17.8, "put")
        assert len(rows) == 2
        assert rows[0]["expiration"] < rows[1]["expiration"]

    @respx.mock
    def test_network_error_returns_empty_list(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            side_effect=httpx.ConnectError("boom")
        )
        assert fetch_options_grid("SOFI", 17.8, "put") == []

    def test_missing_credentials_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(settings, "alpaca_api_key", "")
        assert fetch_options_grid("SOFI", 17.8, "put") == []

    def test_non_positive_price_returns_empty_list(self):
        assert fetch_options_grid("SOFI", 0.0, "put") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_options_lookup.py -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_options_grid'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/screener/options_lookup.py` (add these imports to the top of the file, alongside the existing `import re`):

```python
from datetime import date, timedelta

from .stocks import (
    _build_alpaca_client,
    _extract_implied_volatility,
    _parse_occ_symbol,
    _to_float,
)
```

Then append below `detect_options_intent`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_options_lookup.py -v`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
git add src/screener/options_lookup.py tests/test_options_lookup.py
git commit -m "$(cat <<'EOF'
feat(chat): add fetch_options_grid for live Alpaca options snapshots

Fetches a live grid of near-money puts/calls across the next 47 DTE
using the same working snapshot endpoint pattern already proven in
screener/stocks.py's ATM IV lookup, not algo_detective/options_chain.py's
batch endpoint (confirmed broken — returns a live 400).
EOF
)"
```

---

## Task 3: `format_options_block()` — render the grid for prompt injection

**Files:**
- Modify: `src/chat.py`
- Modify: `tests/test_chat_logic.py`

**Interfaces:**
- Consumes: rows shaped as produced by Task 2's `fetch_options_grid()`.
- Produces: `format_options_block(ticker: str, option_type: str, rows: list[dict]) -> str`. Used by Task 4's `gather_chat_blocks()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_chat_logic.py`. First, update the top-of-file imports:

```python
"""Unit tests for src.chat — ticker detection, formatting, prompt building."""
from __future__ import annotations

from datetime import date

from src.chat import (
    TICKER_SKIP_WORDS,
    build_prompt,
    build_thread_title,
    detect_tickers,
    format_options_block,
    format_screener_block,
)

UNIVERSE = {"NVDA", "AAPL", "MSFT", "GOOG", "TSM", "QCOM", "SMCI"}
```

Then append this new test class at the end of the file:

```python
class TestFormatOptionsBlock:
    def _row(self, **overrides) -> dict:
        base = {
            "expiration": date(2026, 7, 17),
            "dte": 11,
            "strike": 17.0,
            "bid": 0.19,
            "ask": 0.21,
            "mid": 0.20,
            "spread_pct": 10.0,
            "iv": 61.0,
            "delta": -0.18,
            "volume": 120,
        }
        base.update(overrides)
        return base

    def test_no_rows_returns_unavailable_message(self):
        assert format_options_block("SOFI", "put", []) == "[SOFI: no options data available]"

    def test_includes_header_with_puts_label(self):
        block = format_options_block("SOFI", "put", [self._row()])
        assert block.startswith("[SOFI — live puts chain")

    def test_includes_calls_label_and_suffix(self):
        block = format_options_block("SOFI", "call", [self._row(strike=19.0)])
        assert "19C" in block
        assert "live calls chain" in block

    def test_includes_expiration_and_dte(self):
        block = format_options_block("SOFI", "put", [self._row()])
        assert "Exp 7/17 (11 DTE)" in block

    def test_includes_bid_ask_mid(self):
        block = format_options_block("SOFI", "put", [self._row()])
        assert "0.19/0.21 (mid 0.20)" in block

    def test_includes_iv_and_delta(self):
        block = format_options_block("SOFI", "put", [self._row()])
        assert "IV 61%" in block
        assert "Δ-0.18" in block

    def test_omits_iv_and_delta_when_missing(self):
        block = format_options_block("SOFI", "put", [self._row(iv=None, delta=None)])
        assert "IV" not in block
        assert "Δ" not in block

    def test_wide_spread_marker_above_threshold(self):
        block = format_options_block("SOFI", "put", [self._row(spread_pct=25.0)])
        assert "(wide spread)" in block

    def test_no_wide_spread_marker_below_threshold(self):
        block = format_options_block("SOFI", "put", [self._row(spread_pct=5.0)])
        assert "(wide spread)" not in block

    def test_groups_multiple_expirations_on_separate_lines(self):
        rows = [self._row(), self._row(expiration=date(2026, 7, 24), dte=18, strike=17.5)]
        block = format_options_block("SOFI", "put", rows)
        exp_lines = [line for line in block.split("\n") if line.startswith("Exp")]
        assert len(exp_lines) == 2

    def test_multiple_strikes_same_expiration_joined_with_pipe(self):
        rows = [self._row(), self._row(strike=17.5, bid=0.24, ask=0.26, mid=0.25)]
        block = format_options_block("SOFI", "put", rows)
        assert " | " in block
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_options_block' from 'src.chat'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/chat.py`, directly below `format_screener_block`:

```python
def format_options_block(ticker: str, option_type: str, rows: list[dict]) -> str:
    """Render a compact live options grid for injection into the LLM prompt."""
    from datetime import date

    if not rows:
        return f"[{ticker}: no options data available]"

    suffix = "C" if option_type == "call" else "P"
    label = "calls" if option_type == "call" else "puts"

    lines = [f"[{ticker} — live {label} chain, {date.today()}]"]

    by_expiration: dict[date, list[dict]] = {}
    for row in rows:
        by_expiration.setdefault(row["expiration"], []).append(row)

    for expiration in sorted(by_expiration):
        exp_rows = by_expiration[expiration]
        dte = exp_rows[0]["dte"]
        contract_parts = []
        for row in exp_rows:
            strike_str = f"{row['strike']:g}{suffix}"
            part = f"{strike_str} {row['bid']:.2f}/{row['ask']:.2f} (mid {row['mid']:.2f})"
            if row.get("iv") is not None:
                part += f" IV {row['iv']:.0f}%"
            if row.get("delta") is not None:
                part += f" Δ{row['delta']:.2f}"
            if row.get("spread_pct") is not None and row["spread_pct"] > 20:
                part += " (wide spread)"
            contract_parts.append(part)
        lines.append(
            f"Exp {expiration.month}/{expiration.day} ({dte} DTE): "
            + " | ".join(contract_parts)
        )

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py -v`
Expected: PASS (all tests, including the existing screener/prompt tests, still pass)

- [ ] **Step 5: Commit**

```bash
git add src/chat.py tests/test_chat_logic.py
git commit -m "$(cat <<'EOF'
feat(chat): add format_options_block for live options grid rendering

Renders fetch_options_grid() rows as a compact per-expiration block,
matching the style of the existing format_screener_block(), with a
wide-spread marker so the LLM can factor liquidity into its recommendation.
EOF
)"
```

---

## Task 4: `gather_chat_blocks()` — wire it into the trade chat cog

**Files:**
- Modify: `src/chat.py`
- Modify: `tests/test_chat_logic.py`
- Modify: `discord_bot/commands/chat.py`

**Interfaces:**
- Consumes: `detect_options_intent()` and `fetch_options_grid()` (Tasks 1-2), `format_screener_block()` and `format_options_block()` (existing / Task 3), `screen_stocks(tickers: list[str], persist_history: bool) -> list[dict]` from `src.screener.stocks` (existing, unchanged signature).
- Produces: `async def gather_chat_blocks(tickers: list[str], message_content: str) -> list[str]`. This replaces the inline screener-fetch loop in `discord_bot/commands/chat.py::_handle_message` — that method now just calls this function and passes the result straight to `build_prompt()`, unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_chat_logic.py`. Update the top-of-file imports again:

```python
"""Unit tests for src.chat — ticker detection, formatting, prompt building."""
from __future__ import annotations

from datetime import date

import src.chat as chat_module
from src.chat import (
    TICKER_SKIP_WORDS,
    build_prompt,
    build_thread_title,
    detect_tickers,
    format_options_block,
    format_screener_block,
    gather_chat_blocks,
)

UNIVERSE = {"NVDA", "AAPL", "MSFT", "GOOG", "TSM", "QCOM", "SMCI"}
```

Then append this new test class at the end of the file:

```python
class TestGatherChatBlocks:
    async def test_no_tickers_returns_empty_list(self):
        result = await gather_chat_blocks([], "hello")
        assert result == []

    async def test_screener_only_when_no_options_intent(self, monkeypatch):
        monkeypatch.setattr(
            chat_module, "screen_stocks", lambda tickers, persist: [{"price": 17.8}]
        )
        called = {"count": 0}

        def _track(*args, **kwargs):
            called["count"] += 1
            return []

        monkeypatch.setattr(chat_module, "fetch_options_grid", _track)
        result = await gather_chat_blocks(["SOFI"], "what do you think about SOFI")
        assert len(result) == 1
        assert result[0].startswith("[SOFI")
        assert called["count"] == 0

    async def test_options_block_appended_when_intent_detected(self, monkeypatch):
        monkeypatch.setattr(
            chat_module, "screen_stocks", lambda tickers, persist: [{"price": 17.8}]
        )
        monkeypatch.setattr(
            chat_module,
            "fetch_options_grid",
            lambda ticker, price, option_type: [
                {
                    "expiration": date(2026, 7, 17),
                    "dte": 11,
                    "strike": 17.0,
                    "bid": 0.19,
                    "ask": 0.21,
                    "mid": 0.20,
                    "spread_pct": 10.0,
                    "iv": 61.0,
                    "delta": -0.18,
                    "volume": 120,
                }
            ],
        )
        result = await gather_chat_blocks(
            ["SOFI"], "which strike should I sell a SOFI CSP at"
        )
        assert len(result) == 2
        assert "live puts chain" in result[1]

    async def test_screener_failure_falls_back_to_unavailable_marker(self, monkeypatch):
        def _raise(tickers, persist):
            raise RuntimeError("boom")

        monkeypatch.setattr(chat_module, "screen_stocks", _raise)
        result = await gather_chat_blocks(["SOFI"], "SOFI CSP")
        assert result == ["[SOFI: data unavailable]"]

    async def test_no_options_fetch_when_price_missing(self, monkeypatch):
        monkeypatch.setattr(
            chat_module, "screen_stocks", lambda tickers, persist: [{"price": "N/A"}]
        )
        called = {"count": 0}

        def _track(*args, **kwargs):
            called["count"] += 1
            return []

        monkeypatch.setattr(chat_module, "fetch_options_grid", _track)
        await gather_chat_blocks(["SOFI"], "SOFI CSP strike")
        assert called["count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py -v`
Expected: FAIL with `ImportError: cannot import name 'gather_chat_blocks' from 'src.chat'`

- [ ] **Step 3: Write minimal implementation**

Add these imports near the top of `src/chat.py` (below the existing `import re`):

```python
from .screener.options_lookup import detect_options_intent, fetch_options_grid
from .screener.stocks import screen_stocks
```

Add this function to `src/chat.py`, after `format_options_block`:

```python
async def gather_chat_blocks(tickers: list[str], message_content: str) -> list[str]:
    """Fetch screener data, and live options grid data when relevant, per ticker.

    Returns formatted blocks ready for injection via build_prompt(). Options
    data is only fetched for tickers whose screener call succeeded and
    returned a usable numeric price — fetch_options_grid() needs the live
    price to size its strike window — and only when detect_options_intent()
    finds options-related language in the message.
    """
    if not tickers:
        return []

    blocks: list[str] = []
    prices: dict[str, float] = {}

    screener_results = await asyncio.gather(
        *[asyncio.to_thread(screen_stocks, [t], False) for t in tickers],
        return_exceptions=True,
    )
    for ticker, result in zip(tickers, screener_results):
        if isinstance(result, Exception) or not result:
            blocks.append(f"[{ticker}: data unavailable]")
            continue
        data = result[0]
        blocks.append(format_screener_block(ticker, data))
        price = data.get("price")
        if isinstance(price, int | float) and price > 0:
            prices[ticker] = float(price)

    options_intent = detect_options_intent(message_content)
    if options_intent and prices:
        grid_tickers = list(prices.keys())
        grid_results = await asyncio.gather(
            *[
                asyncio.to_thread(fetch_options_grid, t, prices[t], options_intent)
                for t in grid_tickers
            ],
            return_exceptions=True,
        )
        for ticker, rows in zip(grid_tickers, grid_results):
            if isinstance(rows, Exception):
                continue
            blocks.append(format_options_block(ticker, options_intent, rows))

    return blocks
```

Now update `discord_bot/commands/chat.py`. Replace the imports block:

```python
from src.chat import (
    build_prompt,
    build_thread_title,
    call_claude_chat,
    detect_tickers,
    format_screener_block,
)
from src.db import (
    get_stock_watchlist,
    get_trade_chat_channel_id,
    get_trade_chat_history,
    is_trade_chat_thread,
    save_trade_chat_message,
    set_trade_chat_channel_id,
)
from src.screener.stocks import screen_stocks
from src.synthesis.llm import synthesize
```

with:

```python
from src.chat import (
    build_prompt,
    build_thread_title,
    call_claude_chat,
    detect_tickers,
    gather_chat_blocks,
)
from src.db import (
    get_stock_watchlist,
    get_trade_chat_channel_id,
    get_trade_chat_history,
    is_trade_chat_thread,
    save_trade_chat_message,
    set_trade_chat_channel_id,
)
from src.synthesis.llm import synthesize
```

Then replace this block inside `_handle_message`:

```python
        async with thread.typing():
            tickers = detect_tickers(message.content, self.universe)

            screener_blocks: list[str] = []
            if tickers:
                results = await asyncio.gather(
                    *[
                        asyncio.to_thread(screen_stocks, [t], False)
                        for t in tickers
                    ],
                    return_exceptions=True,
                )
                for ticker, result in zip(tickers, results):
                    if isinstance(result, Exception) or not result:
                        screener_blocks.append(f"[{ticker}: data unavailable]")
                    else:
                        screener_blocks.append(format_screener_block(ticker, result[0]))

            history = get_trade_chat_history(thread_id)
```

with:

```python
        async with thread.typing():
            tickers = detect_tickers(message.content, self.universe)
            screener_blocks = await gather_chat_blocks(tickers, message.content)

            history = get_trade_chat_history(thread_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_chat_logic.py -v`
Expected: PASS (all tests)

Then run the full suite to confirm nothing else broke:

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/chat.py discord_bot/commands/chat.py tests/test_chat_logic.py
git commit -m "$(cat <<'EOF'
feat(chat): wire live options grid into trade chat message handling

gather_chat_blocks() replaces the inline screener-fetch loop in
_handle_message — same screener behavior as before, plus a live options
grid block appended whenever the message shows options intent and the
ticker's screener call returned a usable price.
EOF
)"
```

---

## Manual Verification (after all tasks)

The chat bot's `claude` CLI call and Discord wiring aren't covered by unit tests (per the existing `docs/handoffs/2026-07-05-trade-chatbot-troubleshooting.md`, the bot currently falls back to Gemini in prod). To confirm this feature works end-to-end once deployed:

1. Deploy per `CLAUDE.md`'s worktree/dev-dashboard instructions or the existing prod deploy process.
2. In the configured trade chat channel, send a message like `"which strike should I sell a SOFI CSP at"` and confirm the bot's response references specific strikes/expirations/premiums (not just generic screener data).
3. Send a narrower message like `"SOFI 7/17 21p"` and confirm the response quotes that specific contract's live bid/ask.
4. Send a plain message like `"what do you think about SOFI"` and confirm no options grid is fetched (check logs for absence of an options snapshot request) — screener-only behavior should be unchanged.
