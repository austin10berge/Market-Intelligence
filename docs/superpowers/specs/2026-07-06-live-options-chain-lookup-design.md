# Live Options Chain Lookup for Trade Chat — Design Spec

**Date:** 2026-07-06
**Status:** Approved

---

## Overview

Extend the trade chatbot (`discord_bot/commands/chat.py`, `src/chat.py`) so it can answer contract-level options questions — both specific quotes ("SOFI 7/17 $21 call") and open-ended strike/expiration selection ("which strike should I sell a SOFI CSP at?"). The bot does not decide what to fetch via agentic tool-use; instead, when a message shows options intent, the server pre-fetches a wide grid of live contracts (multiple strikes across multiple expirations) and hands it to the LLM as data, the same way screener data is injected today. The LLM does the actual comparison/recommendation in one shot, using the delta/DTE/premium criteria already in `trade_system_prompt.txt`.

This mirrors the existing architecture exactly: `detect_tickers()` → fetch → format into a block → inject into `build_prompt()`. No new services, no tool-calling loop, no change to how `claude -p` is invoked.

**Confirmed live** (tested against Alpaca's `/v1beta1/options/snapshots/{symbol}` endpoint during design, feed=indicative): each contract snapshot includes `greeks.delta`, `impliedVolatility`, and `latestQuote.bp/ap` (bid/ask). No `openInterest` field is present under this feed — liquidity must be inferred from bid/ask spread and daily volume instead.

Also confirmed: `src/algo_detective/options_chain.py`'s batch snapshot call (`underlying_symbols` query param on the collection endpoint) returns a live 400 error. This spec builds on the endpoint pattern already working in `src/screener/stocks.py::_fetch_alpaca_atm_iv_percent` (path-based `/v1beta1/options/snapshots/{symbol}` with `expiration_date_gte/lte`, `strike_price_gte/lte`, `type`), not `options_chain.py`'s.

---

## Architecture

```
src/screener/
├── stocks.py                (unchanged)
└── options_lookup.py         (NEW — intent detection + live grid fetch)

src/chat.py                   (MODIFY — add format_options_block())

discord_bot/commands/chat.py  (MODIFY — gather options grid alongside screener fetch)
```

No new database tables, no new services. One new outbound Alpaca call per ticker per message, only when options intent is detected.

---

## Components

### 1. `detect_options_intent(text: str) -> str | None` — `src/screener/options_lookup.py`

Regex/keyword classifier, same style as `detect_tickers()`. Returns:
- `"call"` if the message contains call-side language (`call`, `CC`, `covered call`)
- `"put"` if it contains put-side or generic options language (`put`, `CSP`, `csp`, `wheel`, `strike`, `premium`, `sell`, `DTE`, `expiration`, a date-like token such as `7/17` or `21c`/`21p`)
- `None` if neither — a bare ticker mention ("what do you think about SOFI") does not trigger a fetch.

Defaults to `"put"` when intent is present but ambiguous, matching the CSP/wheel focus of the system prompt.

### 2. `fetch_options_grid(ticker: str, close_price: float, option_type: str) -> list[dict]` — `src/screener/options_lookup.py`

One Alpaca request per ticker:

```
GET /v1beta1/options/snapshots/{ticker}
    feed=indicative
    expiration_date_gte=<tomorrow>
    expiration_date_lte=<+47 days>
    strike_price_gte/lte=<close_price ± 15% (puts) or ± 12% (calls)>
    type=put|call
    limit=200 (+ page_token loop, same pattern as _fetch_alpaca_atm_iv_percent)
```

Parses each returned OCC contract into a row:

```python
{
    "expiration": date,
    "dte": int,
    "strike": float,
    "bid": float | None,
    "ask": float | None,
    "mid": float | None,
    "spread_pct": float | None,
    "iv": float | None,
    "delta": float | None,
    "volume": int,
}
```

Rows with no `latestQuote` (no live market) are dropped. Sorted by expiration, then strike.

### 3. `format_options_block(ticker: str, option_type: str, rows: list[dict]) -> str` — `src/chat.py`

Renders one compact block per ticker, grouped by expiration:

```
[SOFI — live puts chain, 2026-07-06]
Exp 7/17 (11 DTE): 16.5P 0.19/0.21 (mid 0.20) IV 61% Δ-0.18 | 17P 0.24/0.26 IV 63% Δ-0.22 | 17.5P 0.31/0.33 IV 64% Δ-0.27
Exp 7/24 (18 DTE): ...
Exp 8/21 (46 DTE): ...
```

If a contract's spread is wide relative to mid (e.g. spread > 20% of mid), append a `(wide spread)` marker inline so the LLM can factor in liquidity when recommending a strike.

### 4. Wiring — `discord_bot/commands/chat.py::_handle_message`

Alongside the existing per-ticker `screen_stocks` gather:

```python
for t in tickers:
    intent = detect_options_intent(message.content)
    if intent:
        tasks.append(asyncio.to_thread(fetch_options_grid, t, close_price, intent))
```

Both screener and options results land in the same `screener_blocks` list passed to `build_prompt()` — no change needed to prompt assembly itself.

---

## Error Handling

- **Not optionable / no chain:** empty `rows` → block reads `[{ticker}: no options data available]`, same convention as the existing screener fallback.
- **Alpaca timeout/failure:** caught inside `fetch_options_grid`, returns `[]`; wrapped in the existing `asyncio.gather(..., return_exceptions=True)` so one failed fetch never blocks the chat response.
- **No open interest field:** liquidity is represented via spread% and daily volume only; not treated as a bug, just a documented feed limitation.
- **Question outside the 47-DTE window** (e.g. "60 DTE covered call"): grid simply returns no rows for that expiration; the LLM says it has no data that far out rather than the code dynamically re-querying a wider window. Explicit v1 limitation — revisit only if it comes up in practice.
- **Ambiguous option type:** defaults to puts (see intent detector); no error, just a default.

---

## Testing

- `detect_options_intent`: table-driven cases — put language, call language, plain ticker mention (no trigger), date/strike-shorthand patterns (`7/17`, `21c`).
- `fetch_options_grid`: mocked `httpx` responses shaped like the live payload captured during design (including `greeks.delta`, `impliedVolatility`, `latestQuote`); assert correct DTE/strike filtering, pagination via `page_token`, and rows dropped when `latestQuote` is missing.
- `format_options_block`: fixed input rows → exact rendered string, including the wide-spread marker.
- `tests/test_chat_logic.py`: extend to cover the new gather branch (options block appended when intent fires, absent when it doesn't).

---

## Out of Scope (v1)

- True agentic tool-use (LLM deciding fetch parameters itself via multiple round-trips) — considered and deliberately deferred; the prefetch approach above gives the same UX with lower latency and a smaller safety surface for a bot fed untrusted chat input.
- Dynamic widening of the DTE/strike window based on follow-up questions.
- Open interest / true liquidity depth (not available on the indicative feed).
- Fixing `algo_detective/options_chain.py`'s broken batch snapshot call — noted as a pre-existing issue, not addressed here.
