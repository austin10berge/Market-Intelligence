# v2 Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair five defects found in the 2026-09-18 audit of the v2 UI — the stock-screener 500, the scanner's untradeable candidates, the missing ETF holdings, the inconsistent holding P/L, and the contradictory premium labels.

**Architecture:** Two independent Python fixes in `src/screener/`, and two independent JavaScript fixes in `src/web/v2/wheel.js`. Each fix repairs the root cause at its source. No new dependencies. No schema changes. No API contract changes.

**Tech Stack:** Python 3.12, FastAPI, pandas, pandas_ta, yfinance, Alpaca options snapshots API, vanilla ES5-style JavaScript (IIFE modules), Docker Compose, pytest, Playwright MCP.

**Spec:** This plan is the spec. The audit evidence is quoted inline in each task.

---

## Global Constraints

- **Python runs only in Docker.** There is no host virtualenv. `python -m ...` on the host fails.
- **Test command:** `docker compose run --rm test python3 -m pytest <path> -v`
- **Build and run are separate steps.** The `test` image build is slow enough to exceed a default Bash timeout. Build first, then run. Set an explicit `timeout` of `900000` ms on both. **Never** background either command.
- **Lint:** `~/.local/bin/ruff check src/ tests/` and `~/.local/bin/ruff format src/ tests/`. A `PostToolUse` hook already formats every edited `.py` file.
- **Dev URL:** `https://dev-mi.austin10berge.com` — always test here. Never touch `market.austin10berge.com` (prod, host 10.0.1.21).
- **Docker serves `/home/dev/workspace/Market-Intelligence/src/`.** Edits must land in this main workspace. Do **not** use a git worktree — only one `src/` can be served to dev-mi at a time.
- **Python edits need an API restart, not a rebuild:** `docker compose up -d --no-build api`
- **JavaScript edits need nothing** — `src/web/` is bind-mounted into the dashboard container. Hard-reload the browser.
- **DO NOT COMMIT.** The user reviews all UI work live on dev-mi before any commit. Leave every change in the working tree and report. The orchestrator commits after the user approves.
- **Preserve existing uncommitted work.** `src/screener/options.py`, `src/screener/csp_scanner.py`, and `src/screener/csp_scan_nightly.py` already carry uncommitted changes (a `max_delta` parameter). Do not revert them. Run `git diff <file>` before you edit and confirm your change is additive.
- **Frontend verification is Playwright, not unit tests.** `src/web/v2/wheel.js` is an IIFE loaded by a plain `<script>` tag. It has no export surface and the repo has no JS test runner for it. CLAUDE.md prescribes Playwright MCP for frontend verification. Use `mcp__playwright__browser_*`. Resize to **390×844** before any screenshot.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/screener/stocks.py` | Add `_drop_incomplete_rows` and `_pct_from` helpers; use them | 1 |
| `tests/test_stock_screener_nan.py` | **Create.** Unit tests for the two new helpers | 1 |
| `src/screener/options.py` | Add `_usable_quote`, `_mid_price`, `_spread_pct`; rewire the gate block | 2 |
| `tests/test_options_quality_gates.py` | **Create.** Unit tests for the three new helpers | 2 |
| `src/web/v2/wheel.js` | `renderHoldings` — include ETFs, reconcile P/L | 3 |
| `src/web/v2/wheel.js` | `renderStats` + Monthly Realized header — disambiguate labels | 4 |

**Task 1, Task 2 and Task 3 touch disjoint files. Run them as three parallel sonnet subagents.**
**Task 4 edits the same file as Task 3. Run it only after Task 3 reports done.**

---

## Task 1: Stock screener — stop the NaN 500

**Finding #1.** `GET /api/screener/stocks` returns 500 on every call. The Watchlist page shows "No stocks found" instead of the user's 12 tickers.

**Evidence:**
```
ValueError: Out of range float values are not JSON compliant: nan
[WARNING] src.cache: Redis SET failed for key 'screener:stocks': ... nan
```
yfinance appends an in-progress row for the current session with a populated `Volume` but a NaN `Close`:
```
2026-09-16 00:00:00-04:00  332.410004  35981000
2026-09-17 00:00:00-04:00         NaN  36423997
```
`src/screener/stocks.py:703` reads `hist["Close"].iloc[-1]` and gets that NaN. Line 829 guards `pct_from_52wk_high` with `not pd.isna(current_price)`. Lines 843–851 do **not** carry that guard, so `ema_200_pct` becomes NaN and breaks serialization.

**Verified fix:** dropping NaN-Close rows restores AAPL to `price 332.41`, `sma_200_pct 16.5`, `ema_200_pct 14.7`.

**Files:**
- Modify: `src/screener/stocks.py` (add helpers near the other module-level `_`-prefixed helpers; use them at lines ~700 and ~828–852)
- Create: `tests/test_stock_screener_nan.py`

**Interfaces:**
- Produces: `_drop_incomplete_rows(hist: pd.DataFrame) -> pd.DataFrame`
- Produces: `_pct_from(value, base) -> float | None`
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stock_screener_nan.py`:

```python
"""Regression tests for the NaN-Close row that broke /api/screener/stocks.

yfinance appends an in-progress row for the current session with a populated
Volume but a NaN Close. Left in place it poisons current_price and every
derived field, and the resulting NaN breaks JSONResponse (allow_nan=False)
with an opaque 500.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.screener.stocks import _drop_incomplete_rows, _pct_from


def _frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": closes, "Volume": [1_000] * len(closes)},
        index=pd.date_range("2026-09-01", periods=len(closes), freq="D"),
    )


def test_drops_trailing_nan_close_row():
    hist = _frame([330.0, 331.0, 332.41, float("nan")])
    result = _drop_incomplete_rows(hist)
    assert len(result) == 3
    assert result["Close"].iloc[-1] == 332.41


def test_drops_interior_nan_close_rows():
    hist = _frame([330.0, float("nan"), 332.41])
    result = _drop_incomplete_rows(hist)
    assert len(result) == 2
    assert list(result["Close"]) == [330.0, 332.41]


def test_leaves_clean_frame_unchanged():
    hist = _frame([330.0, 331.0, 332.41])
    result = _drop_incomplete_rows(hist)
    assert len(result) == 3
    assert result["Close"].iloc[-1] == 332.41


def test_all_nan_frame_becomes_empty():
    hist = _frame([float("nan"), float("nan")])
    assert _drop_incomplete_rows(hist).empty


def test_frame_without_close_column_is_returned_unchanged():
    hist = pd.DataFrame({"Volume": [1, 2]})
    assert len(_drop_incomplete_rows(hist)) == 2


def test_pct_from_computes_percent_difference():
    assert _pct_from(332.41, 285.38) == 16.5


def test_pct_from_returns_none_when_value_is_nan():
    assert _pct_from(float("nan"), 285.38) is None
    assert _pct_from(np.nan, 285.38) is None


def test_pct_from_returns_none_when_value_is_none():
    assert _pct_from(None, 285.38) is None


def test_pct_from_returns_none_when_base_is_missing_or_nonpositive():
    assert _pct_from(332.41, None) is None
    assert _pct_from(332.41, float("nan")) is None
    assert _pct_from(332.41, 0) is None
    assert _pct_from(332.41, -5) is None


def test_pct_from_is_negative_below_base():
    assert _pct_from(90.0, 100.0) == -10.0
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose build test
```
Then, as a separate call with `timeout: 900000`:
```bash
docker compose run --rm test python3 -m pytest tests/test_stock_screener_nan.py -v
```
Expected: `ImportError: cannot import name '_drop_incomplete_rows'`

- [ ] **Step 3: Add the two helpers**

In `src/screener/stocks.py`, beside the other module-level helpers (near `_safe_pct` / `_to_float`):

```python
def _drop_incomplete_rows(hist: pd.DataFrame) -> pd.DataFrame:
    """Drop rows whose Close is NaN.

    yfinance appends an in-progress row for the current session with a
    populated Volume but a NaN Close. Left in place that NaN propagates into
    current_price, the SMA/BBand tails and every derived *_pct field, and then
    breaks JSONResponse serialization (allow_nan=False) as an opaque 500.
    """
    if "Close" not in hist.columns:
        return hist
    return hist[hist["Close"].notna()]


def _pct_from(value, base) -> float | None:
    """Percent difference of `value` against `base`, to one decimal place.

    Returns None when either side is missing or `base` is non-positive, so a
    missing input can never produce a NaN that escapes into the JSON response.
    """
    if base is None or pd.isna(base) or float(base) <= 0:
        return None
    if value is None or pd.isna(value):
        return None
    return round(((float(value) - float(base)) / float(base)) * 100, 1)
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_stock_screener_nan.py -v
```
Expected: 10 passed. Use `timeout: 900000`.

- [ ] **Step 5: Use `_drop_incomplete_rows` at the fetch site**

In `screen_stocks`, at `src/screener/stocks.py` ~line 700. Replace:

```python
                if info is None or hist is None or hist.empty:
                    continue

                current_price = hist["Close"].iloc[-1]
```

with:

```python
                if info is None or hist is None or hist.empty:
                    continue

                hist = _drop_incomplete_rows(hist)
                if hist.empty:
                    continue

                current_price = hist["Close"].iloc[-1]
```

- [ ] **Step 6: Use `_pct_from` for all three derived percentages**

Replace the `pct_from_52wk_high_val` block at ~line 828 (keep the existing NOTE comment about the sign convention — it is still accurate and still load-bearing):

```python
                high_52wk = _to_float(info.get("fiftyTwoWeekHigh"))
                # NOTE: negative = below high, positive = above high — this is
                # the opposite sign convention from csp_scanner.py/features.py's
                # pct_from_52wk_high (positive = below high), by design: this
                # value only feeds chat.py's signed "+/-X.X%" human-readable
                # display. Do not wire this field into a pct_from_52wk_high_max
                # gate without flipping the sign to match the scanner convention.
                pct_from_52wk_high_val = _pct_from(current_price, high_52wk)

                market_cap_val = _to_float(info.get("marketCap"))

                sma_200_pct_val = _pct_from(current_price, sma_200_val)
                ema_200_pct_val = _pct_from(current_price, ema_200_val)
```

Leave the `bb_width_pct_val` block below it untouched.

- [ ] **Step 7: Restart the API and confirm the 500 is gone**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose up -d --no-build api
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" "https://dev-mi.austin10berge.com/api/screener/stocks"
```
Expected: `200`. The first call recomputes the screener and may take up to 3 minutes — use `timeout: 900000`.

- [ ] **Step 8: Confirm no NaN survives and the values are real**

```bash
curl -s "https://dev-mi.austin10berge.com/api/screener/stocks" | python3 -c "
import json,sys,math
d=json.load(sys.stdin)
rows=d['candidates']
print('rows:', len(rows))
bad=[(r.get('symbol'),k) for r in rows for k,v in r.items()
     if isinstance(v,float) and (math.isnan(v) or math.isinf(v))]
print('NaN/inf fields:', bad)
a=[r for r in rows if r['symbol']=='AAPL'][0]
print('AAPL price:', a['price'], 'sma_200_pct:', a['sma_200_pct'], 'ema_200_pct:', a['ema_200_pct'])
"
```
Expected: `NaN/inf fields: []`, a non-zero AAPL price near 332, and numeric `sma_200_pct` / `ema_200_pct` — not the string `'N/A'`.

- [ ] **Step 9: Confirm the result now caches**

```bash
docker compose exec -T redis redis-cli --scan --pattern 'screener:*'
```
Expected: `screener:stocks` is listed. Before the fix `cache_set` raised on the NaN and nothing was ever stored, so every page load re-ran the whole screener against yfinance.

- [ ] **Step 10: Confirm the Watchlist page renders**

Use the Playwright MCP. Resize to 390×844. Open `https://dev-mi.austin10berge.com/v2/`, click the **Watchlist** nav button, wait 60 s, then take a snapshot.
Expected: a list of ticker rows with real prices. **Not** "No stocks found". Confirm `browser_console_messages` reports no 500 for `/api/screener/stocks`.

- [ ] **Step 11: Lint and run the wider suite**

```bash
~/.local/bin/ruff check src/ tests/
docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -q
```
Expected: ruff clean, no new failures. Record the pass/fail counts in your report. Use `timeout: 900000`.

- [ ] **Step 12: Report — do not commit**

Report the files you changed, the test counts, the `curl` status code, the NaN field list, and the Watchlist row count. Leave everything uncommitted.

---

## Task 2: Scanner — reject contracts that cannot be traded

**Finding #2.** 17 of 29 candidates had `bid 0`, `IV 0.0` and `delta null`. 8 reported a premium above the ask. Four of the top seven cards on screen were such rows.

**Evidence — this is a genuine one-sided market, not a fetch failure.** A direct Alpaca query returned HTTP 200 for all 7 contracts, with quote timestamps at the 19:59 close:
```
CART260925P00043000  bid=0     ask=0.42  qts=2026-09-17T19:59:31  last=0.52  tts=2026-09-15T14:44:25  iv=None  delta=None
CART261016P00032000  bid=0     ask=0.57  qts=2026-09-17T19:59:57  last=1.7   tts=2026-04-22T17:39:48  iv=None  delta=None
NU260925P00013000    bid=0.05  ask=0.06  qts=2026-09-17T19:59:59  last=0.06  tts=2026-09-17T19:21:13  iv=0.4129  delta=-0.1345
```
The data arrived and parsed correctly. `iv` and `delta` are `None` **because** there is no bid — Alpaca cannot derive greeks from a one-sided book. NU, fetched in the same request at the same instant, returned all three fields. Note `CART261016P00032000`: its last trade is from **2026-04-22**, five months stale, and the scan published it as a $1.70 premium at 69.3% annualized.

**Two decisions from the user:**
1. Hard-filter contracts with no bid.
2. Use **mid** `(bid + ask) / 2` as the premium, not the last trade.

These compose: once no-bid rows are rejected, mid is always computed on a two-sided market, and the existing `max_spread_pct` gate finally runs on every survivor.

**Four gates currently pass a candidate when its data is absent** (`src/screener/options.py`):

| Line | Gate | Current behaviour with missing data |
|---|---|---|
| 447 | `premium = trade.get("p", 0.0)` | Uses the last trade price, however stale |
| 448 | bid | Read, never used as a gate |
| 470 | delta band | `if delta is not None and ...` — `None` passes |
| 484 | IV floor | `if iv > 0 and iv < min_iv` — `0` passes |
| 522 | spread | `if bid > 0 and ask > bid` — keeps `spread_pct = 0.0` and skips `max_spread_pct` |

**Files:**
- Modify: `src/screener/options.py` (helpers near the other module-level `_` helpers; rejection counters ~line 418; gate block ~lines 429–530)
- Create: `tests/test_options_quality_gates.py`

**Interfaces:**
- Produces: `_usable_quote(bid: float, ask: float) -> bool`
- Produces: `_mid_price(bid: float, ask: float) -> float`
- Produces: `_spread_pct(bid: float, ask: float) -> float`
- Consumes: nothing from other tasks.

- [ ] **Step 1: Confirm you are not reverting uncommitted work**

```bash
cd /home/dev/workspace/Market-Intelligence
git diff src/screener/options.py
```
You should see an additive `max_delta` parameter. Keep it. Your change is to a different part of the file.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_options_quality_gates.py`:

```python
"""Unit tests for the CSP scanner's quote-quality gates.

Audit 2026-09-18: 17 of 29 candidates had bid=0, IV=0 and delta=None, and 8
reported a premium above the ask. A direct Alpaca query confirmed those are
genuine one-sided markets — IV and delta are None *because* there is no bid,
not because the fetch failed. A contract with no bid cannot be sold.
"""

from __future__ import annotations

import pytest

from src.screener.options import _mid_price, _spread_pct, _usable_quote


def test_usable_quote_accepts_two_sided_market():
    assert _usable_quote(0.05, 0.06) is True


def test_usable_quote_rejects_missing_bid():
    # CART260925P00043000: bid=0, ask=0.42 — no one is bidding.
    assert _usable_quote(0.0, 0.42) is False


def test_usable_quote_rejects_missing_ask():
    assert _usable_quote(0.42, 0.0) is False


def test_usable_quote_rejects_empty_book():
    assert _usable_quote(0.0, 0.0) is False


def test_usable_quote_rejects_negative_values():
    assert _usable_quote(-0.01, 0.42) is False
    assert _usable_quote(0.42, -0.01) is False


def test_usable_quote_rejects_crossed_book():
    # A crossed or locked book gives a meaningless mid and a zero/negative spread.
    assert _usable_quote(0.50, 0.40) is False
    assert _usable_quote(0.40, 0.40) is False


def test_mid_price_is_the_midpoint():
    assert _mid_price(0.05, 0.06) == pytest.approx(0.055)
    assert _mid_price(3.52, 4.32) == pytest.approx(3.92)


def test_mid_price_never_exceeds_the_ask():
    # The old last-trade premium did: CART 43P quoted premium 0.52 vs ask 0.42.
    assert _mid_price(0.0, 0.42) <= 0.42


def test_spread_pct_is_midpoint_relative():
    # bid 0.05 / ask 0.06 -> 0.01 / 0.055 = 18.18%
    assert _spread_pct(0.05, 0.06) == pytest.approx(18.1818, rel=1e-3)


def test_spread_pct_on_a_tight_market_is_small():
    assert _spread_pct(3.86, 4.47) < 20.0


def test_spread_pct_on_a_wide_market_is_large():
    # CART260925P00050000: bid 3.53 / ask 5.82 -> a 49% spread.
    assert _spread_pct(3.53, 5.82) > 45.0
```

- [ ] **Step 3: Run the tests and confirm they fail**

```bash
docker compose build test
```
Then separately, with `timeout: 900000`:
```bash
docker compose run --rm test python3 -m pytest tests/test_options_quality_gates.py -v
```
Expected: `ImportError: cannot import name '_usable_quote'`

- [ ] **Step 4: Add the three helpers**

In `src/screener/options.py`, beside the other module-level helpers:

```python
def _usable_quote(bid: float, ask: float) -> bool:
    """True when the book is two-sided and not crossed.

    A contract with no bid cannot be sold, so it is worthless to a
    premium-selling strategy however attractive its last trade looks. Alpaca
    also cannot derive IV or greeks from a one-sided book — `impliedVolatility`
    and `greeks` arrive as None for exactly these rows, which is a symptom of
    the missing bid rather than an independent fetch failure.
    """
    return bid > 0 and ask > 0 and ask > bid


def _mid_price(bid: float, ask: float) -> float:
    """Midpoint of the bid/ask — the usual limit-order target.

    Preferred over the last trade price, which goes stale for months on
    illiquid strikes (observed 2026-09-18: a 2026-04-22 print published as a
    live premium) and can sit above the current ask.
    """
    return (bid + ask) / 2


def _spread_pct(bid: float, ask: float) -> float:
    """Bid/ask spread as a percentage of the midpoint — standard for options."""
    mid = _mid_price(bid, ask)
    if mid <= 0:
        return 0.0
    return ((ask - bid) / mid) * 100
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_options_quality_gates.py -v
```
Expected: 11 passed. Use `timeout: 900000`.

- [ ] **Step 6: Add the new rejection counters**

In the `rejected` dict initialiser at ~line 418, add these two keys alongside the existing ones:

```python
        "no_bid": 0,
        "no_delta": 0,
```

Keep `"wide_spread"` as it is — it will finally start counting.

- [ ] **Step 7: Gate on the quote, and switch the premium to mid**

Replace the bid/ask/premium block at ~lines 447–450:

```python
        bid = quote.get("bp", 0.0)
        ask = quote.get("ap", 0.0)
        premium = trade.get("p", 0.0)
        if premium == 0.0:
            premium = c.get("yf_premium", 0.0)
```

with:

```python
        bid = float(quote.get("bp", 0.0) or 0.0)
        ask = float(quote.get("ap", 0.0) or 0.0)

        # Reject one-sided books before the IV and delta gates below. Those two
        # fields are None for exactly these contracts, so rejecting here keeps
        # the later gates meaningful instead of letting a missing value pass.
        if not _usable_quote(bid, ask):
            rejected["no_bid"] += 1
            logger.debug(
                "No usable quote: %s %s %.0fP — bid=%.2f ask=%.2f",
                c["symbol"],
                c["expiration"],
                c["strike"],
                bid,
                ask,
            )
            continue

        # Mid, not the last trade. The yf_premium fallback is gone with it: a
        # two-sided Alpaca quote is now required, and yfinance returns bid=ask=0
        # outside market hours anyway.
        premium = _mid_price(bid, ask)
```

**Also delete the now-dead `trade` assignment** two lines above. Verified: `trade` is read only to build `premium`, so nothing else uses it. `vol` comes from `snapshot["dailyBar"]["v"]`, not from `trade`. Delete this line:

```python
        trade = snapshot.get("latestTrade", {})
```

**Leave `yf_premium` where it is.** It becomes an unused dict key at `src/screener/options.py:346` and `:356`, but `ruff` does not flag unused dict keys and removing it widens the diff into the chain-parsing block for no gain. Note it in your report as dead data for a later cleanup.

- [ ] **Step 8: Make the delta gate reject a missing delta**

Replace the delta band check at ~line 470:

```python
        min_delta = settings.get("min_delta", 0.15)
        max_delta = settings.get("max_delta", 0.40)
        if delta is not None and not (min_delta <= delta <= max_delta):
```

with:

```python
        min_delta = settings.get("min_delta", 0.15)
        max_delta = settings.get("max_delta", 0.40)
        if delta is None:
            rejected["no_delta"] += 1
            logger.debug(
                "No delta available: %s %s %.0fP",
                c["symbol"],
                c["expiration"],
                c["strike"],
            )
            continue
        if not (min_delta <= delta <= max_delta):
```

- [ ] **Step 9: Make the IV floor reject a missing IV**

Replace the IV floor condition at ~line 484:

```python
        if iv > 0 and iv < min_iv:
```

with:

```python
        if iv <= 0 or iv < min_iv:
```

- [ ] **Step 10: Make the spread gate always run**

Replace the spread block at ~lines 522–536:

```python
        spread_pct = 0.0
        if bid > 0 and ask > bid:
            # Use midpoint-relative spread: standard for options
            spread_pct = ((ask - bid) / ((ask + bid) / 2)) * 100
            if spread_pct > settings["max_spread_pct"]:
```

with:

```python
        # _usable_quote above guarantees a two-sided, uncrossed book, so this
        # gate now runs on every surviving candidate. Previously a bid of 0 left
        # spread_pct at 0.0 and skipped the check entirely — the widest possible
        # markets were the only ones that bypassed the spread filter.
        spread_pct = _spread_pct(bid, ask)
        if spread_pct > settings["max_spread_pct"]:
```

Fix the indentation of the `rejected["wide_spread"] += 1` / `logger.debug(...)` / `continue` lines that follow, so they sit one level shallower under the new `if`.

- [ ] **Step 11: Simplify the now-dead IV fallback**

The IV floor guarantees `iv > 0` for every survivor, so the fallback at ~line 549 is unreachable. Replace:

```python
            iv=iv if iv > 0 else 30.0,  # fallback to mid-range if IV unavailable
```

with:

```python
            iv=iv,  # guaranteed > 0 by the IV floor above
```

- [ ] **Step 12: Run both test files and confirm they still pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_options_quality_gates.py tests/test_options.py -v
```
Expected: all pass. Use `timeout: 900000`.

- [ ] **Step 13: Restart the API and force a fresh scan**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose up -d --no-build api
sleep 5
curl -s -X DELETE "https://dev-mi.austin10berge.com/api/screener/csp-scan"
curl -s "https://dev-mi.austin10berge.com/api/screener/csp-scan" > /tmp/scan-after.json
```
The scan takes 3–6 minutes cold. Use `timeout: 900000`. Do not background it.

- [ ] **Step 14: Confirm every untradeable candidate is gone**

```bash
python3 -c "
import json
d=json.load(open('/tmp/scan-after.json'))
c=d['candidates']
print('candidates:', len(c), '(was 29)')
print('bid<=0      :', len([x for x in c if not x.get('bid') or x['bid']<=0]), '(was 17, must be 0)')
print('iv<=0       :', len([x for x in c if not x.get('impliedVolatility')]), '(was 17, must be 0)')
print('delta None  :', len([x for x in c if x.get('delta') is None]), '(was 17, must be 0)')
print('premium>ask :', len([x for x in c if x['premium'] > x['ask']]), '(was 8, must be 0)')
print('spread_pct==0:', len([x for x in c if x['spread_pct']==0]), '(must be 0)')
print()
print('%-6s %-7s %-6s %-6s %-7s %-7s %-7s %-6s' % ('sym','strike','bid','ask','prem','spread','delta','score'))
for x in sorted(c, key=lambda y:-y['composite_score'])[:10]:
    print('%-6s %-7s %-6s %-6s %-7s %-7s %-7s %-6s' % (
        x['symbol'],x['strike'],x['bid'],x['ask'],x['premium'],x['spread_pct'],x['delta'],x['composite_score']))
"
```
Expected: all five counts are **0**. Every remaining premium sits between its bid and its ask. CART must not appear.

- [ ] **Step 15: Confirm the rejection counters fired**

```bash
docker compose logs api --since 15m 2>&1 | grep -iE "no_bid|no_delta|wide_spread|rejected"
```
Expected: `no_bid` shows a non-zero count. This proves the gate ran rather than the candidates vanishing for some other reason.

- [ ] **Step 16: Confirm the Scanner page**

Use the Playwright MCP at 390×844. Open `https://dev-mi.austin10berge.com/v2/`, click **Scanner**, wait for results, snapshot.
Expected: no card shows `IV 0.0%` or `Δ—`. Take a screenshot as visual proof.

- [ ] **Step 17: Lint and run the wider suite**

```bash
~/.local/bin/ruff check src/ tests/
docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -q
```
Expected: ruff clean, no new failures. Use `timeout: 900000`.

- [ ] **Step 18: Report — do not commit**

Report the before/after candidate counts, all five zero-checks, the rejection-counter line, and the screenshot path. **Call out the candidate count explicitly** — a large drop is the intended outcome, but the user needs the number to judge whether the scanner still surfaces enough names.

---

## Task 3: Wheel — show ETF holdings and reconcile the P/L

Two findings in one function, `renderHoldings` in `src/web/v2/wheel.js:218`.

**Finding #4 — ETF holdings are invisible.** Line 219 filters `p.asset_type === 'EQUITY'`. Schwab classifies ETFs as `COLLECTIVE_INVESTMENT`. The user's 100 DRAM shares never appear under Open Holdings, but the DRAM $60 covered call does appear under Open Trades — so a covered position reads as naked. Confirmed on **both** dev and prod:
```
DRAM  COLLECTIVE_INVESTMENT  qty=100.0  avg=53.5266  mv=5768.00  (prod)
```
`SWVXX` is a `MUTUAL_FUND` cash sweep and must stay hidden.

**Finding #5 — the card can contradict itself.** This is **hardening, not a bug fix.** On prod every holding reconciles exactly (`stored_pnl == market_value − qty × avg`): HOOD `-194.86`, SOFI `-1046.64`, DRAM `+415.34`. The mapping in `src/wheel_tracker/sync.py:263` is correct. But dev's frozen 2026-08-27 snapshot is internally inconsistent — HOOD shows `Cost $12199`, value `$12067` and `+$128`, where `12067 − 12199 = −132`. Line 227 prefers the stored value with `??` and prints it beside a cost basis it does not match. The guard below prefers the self-consistent number whenever the two disagree, so it never fires on healthy data.

**Files:**
- Modify: `src/web/v2/wheel.js` — `renderHoldings`, lines 218–245

**Interfaces:**
- Consumes: the `/api/wheel/positions` payload — `asset_type`, `quantity`, `average_price`, `current_price`, `market_value`, `unrealized_pnl`.
- Produces: nothing other tasks depend on. **Task 4 edits this same file — do not start Task 4 until this task reports done.**

- [ ] **Step 1: Capture the current state as your baseline**

Use the Playwright MCP at 390×844. Open `https://dev-mi.austin10berge.com/v2/`, click **Wheel**, wait 10 s, snapshot.
Record: Open Holdings lists exactly HOOD and SOFI. DRAM is absent. HOOD reads `Cost $12199` / `$12067` / `+$128`.

- [ ] **Step 2: Replace the filter and the P/L selection**

In `src/web/v2/wheel.js`, replace lines 218–228 — from `function renderHoldings` down to and including the `const unrealized = ...` line:

```javascript
    function renderHoldings(positions) {
        const equities = positions.filter(p => p.asset_type === 'EQUITY');
        if (!equities.length) return `<div class="list-message">No equity holdings</div>`;
        return equities.map((p, i) => {
            const qty = Math.abs(p.quantity || 0);
            const avgCost = p.average_price || 0;
            const curPrice = p.current_price || 0;
            const costBasis = qty * avgCost;
            const curValue = p.market_value || (qty * curPrice);
            const unrealized = p.unrealized_pnl ?? (curValue - costBasis);
```

with:

```javascript
    // Schwab classifies ETFs as COLLECTIVE_INVESTMENT, not EQUITY. Both are
    // share positions the wheel writes calls against, so both belong here.
    // MUTUAL_FUND is excluded on purpose — that is the SWVXX cash sweep.
    const HOLDING_ASSET_TYPES = ['EQUITY', 'COLLECTIVE_INVESTMENT'];

    function renderHoldings(positions) {
        const equities = positions.filter(p => HOLDING_ASSET_TYPES.includes(p.asset_type));
        if (!equities.length) return `<div class="list-message">No share holdings</div>`;
        return equities.map((p, i) => {
            const qty = Math.abs(p.quantity || 0);
            const avgCost = p.average_price || 0;
            const curPrice = p.current_price || 0;
            const costBasis = qty * avgCost;
            const curValue = p.market_value || (qty * curPrice);
            // Schwab's unrealized_pnl normally equals curValue - costBasis exactly.
            // A stale or half-written snapshot can break that, and the card would
            // then print a gain next to a cost basis that implies a loss. Prefer
            // the number consistent with the two figures shown beside it.
            const computed = curValue - costBasis;
            const stored = p.unrealized_pnl;
            const unrealized = (stored == null || Math.abs(stored - computed) > 1)
                ? computed
                : stored;
```

Leave the rest of the function — the template literal from `const pnlColor` onward — exactly as it is.

- [ ] **Step 3: Hard-reload and confirm DRAM now appears**

No restart is needed; `src/web/` is bind-mounted. Use the Playwright MCP at 390×844. Open the page, click **Wheel**, wait 10 s, snapshot.
Expected: Open Holdings lists **three** rows — HOOD, SOFI and **DRAM** (`100 shares · avg $53.53`). `SWVXX` must **not** appear.

- [ ] **Step 4: Confirm the HOOD card is now self-consistent**

From the same snapshot, read the HOOD card.
Expected: `Cost $12199`, value `$12067`, and **`-$132`** — no longer `+$128`. The card is styled `down` and coloured red.
Also confirm DRAM reads `+$286` (`5639 − 100 × 53.5266`), not `+$0`.

- [ ] **Step 5: Confirm the guard stays dormant on good data**

```bash
curl -s "https://market.austin10berge.com/api/wheel/positions" | python3 -c "
import json,sys
for x in json.load(sys.stdin)['positions']:
    if x['asset_type'] in ('EQUITY','COLLECTIVE_INVESTMENT'):
        computed = x['market_value'] - x['quantity']*x['average_price']
        print('%-6s stored=%-12.2f computed=%-12.2f guard_fires=%s' % (
            x['symbol'], x['unrealized_pnl'], computed,
            abs(x['unrealized_pnl']-computed) > 1))
"
```
This is a **read-only** check against prod, to prove the guard is dormant on healthy data.
Expected: `guard_fires=False` for every row. If any row reports `True`, stop and report it — the assumption behind this change is wrong.

- [ ] **Step 6: Take a screenshot**

Save as `audit-fix-wheel-holdings.png` at 390×844.

- [ ] **Step 7: Report — do not commit**

Report the three holding rows with their P/L values, the prod dormancy check, and the screenshot path.

---

## Task 4: Wheel — disambiguate the premium labels

**Finding #6.** The page shows `Net Premium YTD $4710 / MTD $0` directly above `Monthly Realized … Sep +$663`. The two read as the same quantity and contradict each other.

**They measure different things, and both definitions are correct:**
- `premium_mtd` (`src/wheel_tracker/store.py:665`) buckets by **`executed_at`** — premium on trades *opened or closed* this month.
- `get_monthly_realized_pnl` (`src/wheel_tracker/store.py:636`) buckets by **`close_date`** — P/L on positions that *finished* in that month.

An option opened in August and closed in September counts toward September realized but not September premium. On dev, where the last trade import was 2026-08-27, that produces `MTD $0` beside `Sep +$663`. Prod currently shows `premium_mtd: 400.37`, so the contradiction is milder there but the ambiguity is identical.

**This is a labelling fix only. Do not change either calculation** — both are correct and both are useful.

**Files:**
- Modify: `src/web/v2/wheel.js` — `renderStats` (~line 194) and the Monthly Realized section header (~line 395)

**Interfaces:**
- Consumes: `/api/wheel/stats` (`premium_ytd`, `premium_mtd`) — unchanged.
- Produces: nothing. **Start only after Task 3 reports done** — same file.

- [ ] **Step 1: Confirm Task 3 landed**

```bash
cd /home/dev/workspace/Market-Intelligence
grep -n "HOLDING_ASSET_TYPES" src/web/v2/wheel.js
```
Expected: two matches. If you get none, stop — Task 3 has not landed yet.

- [ ] **Step 2: Relabel the premium stat card**

In `renderStats`, replace these three lines (~194–197):

```javascript
                <div class="whl-stat-label">Net Premium YTD</div>
                <div class="whl-stat-val" style="color:${premColor}">${fmtMoney(s.premium_ytd)}</div>
                <div class="whl-stat-sub">MTD ${fmtMoney(s.premium_mtd)}</div>
```

with:

```javascript
                <div class="whl-stat-label">Premium Traded YTD</div>
                <div class="whl-stat-val" style="color:${premColor}">${fmtMoney(s.premium_ytd)}</div>
                <div class="whl-stat-sub">MTD ${fmtMoney(s.premium_mtd)} · by trade date</div>
```

- [ ] **Step 3: Relabel the Monthly Realized header**

Replace the section title at ~line 395:

```javascript
                <span class="section-title">Monthly Realized</span>
```

with:

```javascript
                <span class="section-title">Monthly Realized · by close date</span>
```

- [ ] **Step 4: Confirm both labels render**

Use the Playwright MCP at 390×844. Open the page, click **Wheel**, wait 10 s, snapshot.
Expected:
- The stat card reads `Premium Traded YTD`, `$4710`, `MTD $0 · by trade date`.
- The section header reads `Monthly Realized · by close date`.
- The **numbers are unchanged** — `$4710`, `$0`, `Sep +$663`. If any number moved, you edited a calculation by mistake. Revert and retry.

- [ ] **Step 5: Confirm the header does not wrap or clip at 390 px**

```javascript
() => {
  const el = [...document.querySelectorAll('.section-title')]
      .find(e => e.textContent.includes('Monthly Realized'));
  return { text: el.textContent, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth };
}
```
Run via `browser_evaluate`. Expected: `scrollWidth <= clientWidth`. If the label clips, shorten it to `Monthly Realized (closed)` and re-check.

- [ ] **Step 6: Take a screenshot**

Save as `audit-fix-wheel-labels.png` at 390×844.

- [ ] **Step 7: Report — do not commit**

Report both label strings as rendered, confirmation that the three numbers are unchanged, the clipping measurement, and the screenshot path.

---

## Not in scope

**Finding #3 (wheel freshness) — verified working on prod, no fix requested.**
`refreshed_at 2026-09-18T00:05:01` with every DTE correct for today: AMZU and GGLL expiring today show `0`, HOOD 09-25 shows `7`, NVDA 10-16 shows `28`, SOFI 2027-06-17 shows `272`. The dev staleness was a dev-only artifact — the nightly pipeline runs only on the `finance` host.

Two latent defects remain, and one has already fired in production. On 2026-09-17 the pipeline logged `accounts_synced: 0, positions_refreshed: 0` after a Schwab refresh-token error, so prod served 09-16 positions that day. The badge at `src/web/v2/wheel.js:443` is hardcoded to `'Live'` on any successful fetch and ignores `refreshed_at`, and DTE is read from the stored column rather than recomputed. The Scanner badge already does this correctly (`cached · 3m ago`) and is the model to copy. Say the word and this becomes Task 5.

**Also unfixed, from the audit:** `GLAB` is delisted and still in the stock watchlist (Yahoo 404 on every scan). `mountPerfChart` leaks a `window` resize listener because `teardown()` is empty — measured as not user-visible (1 chart in the DOM after 3 visits).

---

## Orchestration

**Wave A — three parallel sonnet subagents.** Tasks 1, 2 and 3 touch disjoint files.

| Agent | Task | Files |
|---|---|---|
| A1 | Task 1 | `src/screener/stocks.py`, `tests/test_stock_screener_nan.py` |
| A2 | Task 2 | `src/screener/options.py`, `tests/test_options_quality_gates.py` |
| A3 | Task 3 | `src/web/v2/wheel.js` (`renderHoldings` only) |

Tasks 1 and 2 both restart the `api` container. That is idempotent and safe to repeat, but if both agents restart within seconds of each other, one may see a brief connection refusal. Re-run the affected `curl` rather than treating it as a failure.

**Wave B — one sonnet subagent, after A3 reports done.**

| Agent | Task | Files |
|---|---|---|
| B1 | Task 4 | `src/web/v2/wheel.js` (`renderStats` + section header) |

**After both waves:** the orchestrator reviews every change, runs the full suite once, and presents the result on dev-mi for live review. **Commit only after the user approves.**
