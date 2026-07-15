# Overview Tab: LLM/VIX Reliability Fixes + GEX/Breadth Coloring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three reliability bugs on the Market Intelligence Overview tab (LLM summary fallback text, VIX "Unavailable", flaky SaaS/Semis-Memory theme drops) and add genuine bullish/bearish coloring to the GEX and Breadth widgets.

**Architecture:** Two of the three bugs (VIX + themes) share one root cause — unretried `yf.download()` batch calls silently dropping tickers under Yahoo rate-limiting — and get one shared fix (a retry helper, plus chunking for the larger themes batch). The LLM bug is a separate root cause (nightly pipeline's primary provider, Claude CLI, fails ~100% of the time; its Gemini fallback has no retry for transient errors) fixed with a scoped retry in the synthesis layer. GEX/Breadth coloring is a pure frontend change, verified via Playwright against the dev deployment (no JS test harness exists for `src/web/v2/app.js`).

**Tech Stack:** Python 3.12 (FastAPI backend, `yfinance`, `google-genai`), vanilla JS frontend (`src/web/v2/`), pytest + pytest-asyncio + respx for backend tests, Playwright MCP for frontend verification, Docker Compose for running tests/services.

## Global Constraints

- Python deps only run inside Docker — no bare `python -m ...` on the host (`CLAUDE.md`).
- Run tests via `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`.
- Frontend (`src/web/v2/`) is JS-rendered — verify changes with Playwright MCP against `https://dev-mi.austin10berge.com`, not curl.
- Docker serves from the main workspace `src/` directly (not a worktree) — edits here are live-testable after a container restart, no `docker-compose.local.yml` override needed.
- No SSH/prod access — prod (`market.austin10berge.com`, host `10.0.1.21`) config changes must be handed to the user as exact commands, never applied directly.
- `~/.local/bin/ruff` auto-formats edited `.py` files via a `PostToolUse` hook — no manual format step needed.
- Do not retry on non-transient errors (auth/quota) in the LLM retry logic — only genuinely transient failure classes.
- Preserve existing public shapes: `_fetch_themes()` must still return `{"singles": {...}, "baskets": {...}}`; `has_partial_failure()` and the `/api/market-overview` response schema are unchanged.

---

### Task 1: Retry helper for yfinance batch downloads (VIX + Sectors)

**Files:**
- Modify: `src/fetchers/market_overview.py:66-165` (insert helper after `_gex_trend`, before `_fetch_sectors`; update `_fetch_sectors` and `_fetch_vix` to use it)
- Test: `tests/test_market_overview.py` (add new tests to existing file)

**Interfaces:**
- Produces: `async def _download_with_retry(*args, **kwargs) -> pd.DataFrame` in `src.fetchers.market_overview` — thin retry wrapper around `yf.download`, same call signature, raises the last exception if all attempts fail. Consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_market_overview.py`, in a new section after the `TestVix` class (around line 349, right before the `# ── Breadth ──` divider):

```python
# ── Download retry helper ───────────────────────────────────────────────────

class TestDownloadWithRetry:
    @patch("src.fetchers.market_overview.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview.yf.download")
    async def test_succeeds_on_first_attempt(self, mock_dl, mock_sleep):
        mock_dl.return_value = "ok"
        result = await _download_with_retry("XLK", period="30d")
        assert result == "ok"
        mock_sleep.assert_not_called()

    @patch("src.fetchers.market_overview.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview.yf.download")
    async def test_recovers_after_one_transient_failure(self, mock_dl, mock_sleep):
        mock_dl.side_effect = [Exception("rate limited"), "ok"]
        result = await _download_with_retry("^VIX ^VIX3M", period="10d")
        assert result == "ok"
        assert mock_dl.call_count == 2
        mock_sleep.assert_called_once()

    @patch("src.fetchers.market_overview.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview.yf.download")
    async def test_raises_last_exception_after_exhausting_retries(self, mock_dl, mock_sleep):
        mock_dl.side_effect = [
            Exception("fail 1"), Exception("fail 2"), Exception("fail 3"),
        ]
        with pytest.raises(Exception, match="fail 3"):
            await _download_with_retry("^VIX ^VIX3M", period="10d")
        assert mock_dl.call_count == 3
```

Update the import block at the top of `tests/test_market_overview.py` to include the new name:

```python
from src.fetchers.market_overview import (
    SECTOR_ETFS,
    _download_with_retry,
    _fetch_breadth,
    _fetch_gex,
    _fetch_sectors,
    _fetch_vix,
    _gex_bucket,
    _gex_trend,
    fetch_market_overview,
    has_partial_failure,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_market_overview.py::TestDownloadWithRetry -v`
Expected: FAIL — `ImportError: cannot import name '_download_with_retry'`

- [ ] **Step 3: Implement `_download_with_retry` and wire it into `_fetch_sectors` / `_fetch_vix`**

In `src/fetchers/market_overview.py`, insert immediately after `_gex_trend` (after line 75, before the blank lines preceding `async def _fetch_sectors`):

```python
_YF_RETRIES = 2
_YF_RETRY_BACKOFF_S = 1.5


async def _download_with_retry(*args, **kwargs):
    """Retry a yf.download call a couple of times before giving up.

    Yahoo Finance intermittently drops tickers or errors out entirely under
    rate limiting; a short retry with backoff self-heals most of these without
    adding meaningful latency to the request.
    """
    last_exc: Exception | None = None
    for attempt in range(_YF_RETRIES + 1):
        try:
            return await asyncio.to_thread(yf.download, *args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < _YF_RETRIES:
                logger.warning(
                    "market_overview: yf.download failed (attempt %d/%d), retrying: %s",
                    attempt + 1, _YF_RETRIES + 1, exc,
                )
                await asyncio.sleep(_YF_RETRY_BACKOFF_S * (attempt + 1))
    raise last_exc
```

Replace the body of `_fetch_sectors` (the `raw = await asyncio.to_thread(...)` call, originally lines 80-87):

```python
    raw = await _download_with_retry(
        " ".join(tickers),
        period="30d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )
```

Replace the body of `_fetch_vix` (the `raw = await asyncio.to_thread(...)` call, originally lines 127-134):

```python
    raw = await _download_with_retry(
        "^VIX ^VIX3M",
        period="10d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_market_overview.py -v`
Expected: PASS — all tests, including the 3 new `TestDownloadWithRetry` tests and every pre-existing test in the file (confirms `_fetch_sectors`/`_fetch_vix` behavior is unchanged on the success path).

- [ ] **Step 5: Commit**

```bash
git add src/fetchers/market_overview.py tests/test_market_overview.py
git commit -m "fix(market-overview): retry yfinance batch downloads for VIX and sectors

Yahoo Finance intermittently drops tickers or errors out under rate
limiting; VIX has been failing outright across multiple days in prod
logs with no retry. Adds a shared 2-retry/backoff wrapper around
yf.download, reused by sectors and VIX fetches."
```

---

### Task 2: Chunk the thematic-ETF batch download

**Files:**
- Modify: `src/fetchers/market_overview.py:294-347` (`_fetch_themes`)
- Test: `tests/test_market_overview.py` (new `TestThemes` and `TestChunkTickers` sections)

**Interfaces:**
- Consumes: `_download_with_retry(*args, **kwargs)` from Task 1.
- Produces: `_chunk_tickers(items: list[str], size: int) -> list[list[str]]` — pure helper, no other task depends on it, but tested directly for the merge-trailing-singleton edge case.
- `_fetch_themes()` return shape is unchanged: `{"singles": {label: {"ticker": str, "pct_1d": float|None, "pct_1w": float|None, "pct_1m": float|None}}, "baskets": {label: {"tickers": {...}, "avg_1d": ..., "avg_1w": ..., "avg_1m": ...}}}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_market_overview.py`, after the new `TestDownloadWithRetry` section from Task 1:

```python
# ── Theme chunking ───────────────────────────────────────────────────────────

class TestChunkTickers:
    def test_even_split(self):
        items = [f"T{i}" for i in range(12)]
        chunks = _chunk_tickers(items, size=6)
        assert chunks == [items[0:6], items[6:12]]

    def test_remainder_larger_than_one_kept_separate(self):
        items = [f"T{i}" for i in range(15)]  # 6, 6, 3
        chunks = _chunk_tickers(items, size=6)
        assert [len(c) for c in chunks] == [6, 6, 3]

    def test_trailing_singleton_merged_into_prior_chunk(self):
        items = [f"T{i}" for i in range(13)]  # naive split: 6, 6, 1
        chunks = _chunk_tickers(items, size=6)
        # yfinance returns a flat (non-grouped) DataFrame for single-ticker
        # downloads, which _extract can't parse — no chunk may have size 1.
        assert [len(c) for c in chunks] == [6, 7]
        assert sum(len(c) for c in chunks) == 13

    def test_single_item_total_stays_one_chunk(self):
        chunks = _chunk_tickers(["ONLY"], size=6)
        assert chunks == [["ONLY"]]


# ── Themes ────────────────────────────────────────────────────────────────────

class TestThemes:
    @patch("src.fetchers.market_overview.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview.yf.download")
    async def test_all_themes_present_when_all_chunks_succeed(self, mock_dl, mock_sleep):
        singles = {"SaaS": "IGV", "Semis/Memory": "SMH"}
        baskets = {"Hyperscalers": ["AMZN", "MSFT"]}
        with patch.dict(
            "src.fetchers.market_overview.SINGLE_TICKER_THEMES", singles, clear=True
        ), patch.dict(
            "src.fetchers.market_overview.BASKET_THEMES", baskets, clear=True
        ):
            mock_dl.return_value = _make_yf_df(["IGV", "SMH", "AMZN", "MSFT"], n_days=30)
            result = await _fetch_themes()
        assert set(result["singles"].keys()) == {"SaaS", "Semis/Memory"}
        assert set(result["baskets"].keys()) == {"Hyperscalers"}

    @patch("src.fetchers.market_overview.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.fetchers.market_overview.yf.download")
    async def test_one_failed_chunk_does_not_drop_other_chunks(self, mock_dl, mock_sleep):
        # 8 single-ticker themes, chunk size 6 → two chunks: [6 tickers], [2 tickers]
        singles = {f"Theme{i}": f"TK{i}" for i in range(8)}
        with patch.dict(
            "src.fetchers.market_overview.SINGLE_TICKER_THEMES", singles, clear=True
        ), patch.dict(
            "src.fetchers.market_overview.BASKET_THEMES", {}, clear=True
        ):
            chunk1_ticker_str = " ".join(f"TK{i}" for i in range(6))
            chunk2_tickers = [f"TK{i}" for i in range(6, 8)]
            chunk2_ticker_str = " ".join(chunk2_tickers)

            # asyncio.gather runs both chunks' retry loops concurrently, so a
            # plain list-based side_effect would have a nondeterministic
            # consumption order across the two chunks. Route by the ticker
            # string argument instead so each chunk's outcome is deterministic
            # regardless of scheduling order.
            def _routed_side_effect(tickers_arg, **kwargs):
                if tickers_arg == chunk1_ticker_str:
                    raise Exception("chunk 1 rate limited")
                assert tickers_arg == chunk2_ticker_str
                return _make_yf_df(chunk2_tickers, n_days=30)

            mock_dl.side_effect = _routed_side_effect
            result = await _fetch_themes()
        # Chunk 1 (Theme0..Theme5) exhausted retries and failed entirely;
        # chunk 2 (Theme6, Theme7) succeeded and must still be present.
        assert set(result["singles"].keys()) == {"Theme6", "Theme7"}
```

Update the `tests/test_market_overview.py` import block again to add the new names:

```python
from src.fetchers.market_overview import (
    SECTOR_ETFS,
    _chunk_tickers,
    _download_with_retry,
    _fetch_breadth,
    _fetch_gex,
    _fetch_sectors,
    _fetch_themes,
    _fetch_vix,
    _gex_bucket,
    _gex_trend,
    fetch_market_overview,
    has_partial_failure,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_market_overview.py::TestChunkTickers tests/test_market_overview.py::TestThemes -v`
Expected: FAIL — `ImportError: cannot import name '_chunk_tickers'` (and `_fetch_themes` not yet imported in that block either).

- [ ] **Step 3: Implement chunking in `_fetch_themes`**

In `src/fetchers/market_overview.py`, insert this constant and helper immediately before `async def _fetch_themes()` (originally line 294):

```python
_THEME_CHUNK_SIZE = 6


def _chunk_tickers(items: list[str], size: int) -> list[list[str]]:
    """Split into chunks of `size`, merging a trailing remainder of exactly 1
    into the prior chunk.

    yfinance returns a flat, non-ticker-grouped DataFrame for single-ticker
    downloads (group_by='ticker' only takes effect with 2+ tickers), which
    the per-ticker extraction below can't parse — so no chunk may end up
    with exactly one ticker.
    """
    chunks = [items[i:i + size] for i in range(0, len(items), size)]
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        chunks[-2].extend(chunks.pop())
    return chunks
```

Replace the entire body of `_fetch_themes` (originally lines 294-347) with:

```python
async def _fetch_themes() -> dict:
    all_tickers = list(SINGLE_TICKER_THEMES.values()) + [
        t for tickers in BASKET_THEMES.values() for t in tickers
    ]
    chunks = _chunk_tickers(all_tickers, _THEME_CHUNK_SIZE)
    chunk_results = await asyncio.gather(
        *[
            _download_with_retry(
                " ".join(chunk), period="30d", group_by="ticker",
                progress=False, auto_adjust=True,
            )
            for chunk in chunks
        ],
        return_exceptions=True,
    )

    closes: dict[str, object] = {}
    for chunk, result in zip(chunks, chunk_results):
        if isinstance(result, Exception):
            logger.warning("Thematic ETF: chunk %s failed: %s", chunk, result)
            continue
        for ticker in chunk:
            try:
                series = result[ticker]["Close"].dropna()
            except (KeyError, TypeError):
                continue
            if not series.empty:
                closes[ticker] = series

    def _extract(ticker: str) -> dict | None:
        series = closes.get(ticker)
        if series is None:
            return None
        return {
            "pct_1d": _pct_change(series, 1),
            "pct_1w": _pct_change(series, 5),
            "pct_1m": _pct_change(series, 21),
        }

    singles: dict[str, dict] = {}
    for label, ticker in SINGLE_TICKER_THEMES.items():
        data = _extract(ticker)
        if data is not None:
            singles[label] = {"ticker": ticker, **data}

    baskets: dict[str, dict] = {}
    for label, tickers in BASKET_THEMES.items():
        ticker_data: dict[str, dict] = {}
        for t in tickers:
            data = _extract(t)
            if data is not None:
                ticker_data[t] = data
        if not ticker_data:
            continue

        def _avg(key: str, td: dict = ticker_data) -> float | None:
            vals = [v[key] for v in td.values() if v.get(key) is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        baskets[label] = {
            "tickers": ticker_data,
            "avg_1d": _avg("pct_1d"),
            "avg_1w": _avg("pct_1w"),
            "avg_1m": _avg("pct_1m"),
        }

    return {"singles": singles, "baskets": baskets}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_market_overview.py -v`
Expected: PASS — all tests in the file, including the new `TestChunkTickers` and `TestThemes` sections.

- [ ] **Step 5: Commit**

```bash
git add src/fetchers/market_overview.py tests/test_market_overview.py
git commit -m "fix(market-overview): chunk thematic ETF batch downloads

The 21-ticker single-shot yf.download for themes has been silently
dropping a different random subset of tickers on every call (confirmed
live — SaaS and Semis/Memory each vanished on separate requests),
consistent with Yahoo rate-limiting under large batches. Splitting into
~6-ticker chunks with the Task 1 retry wrapper bounds a single flaky
call to a small slice instead of the whole theme set."
```

---

### Task 3: Retry Gemini synthesis on transient failures + document `LLM_PROVIDER`

**Files:**
- Modify: `src/synthesis/llm.py:86-112` (`_call_gemini`)
- Modify: `.env.example` (document `LLM_PROVIDER`)
- Test: `tests/test_llm_synthesis.py` (new file)

**Interfaces:**
- No change to `_call_gemini(system_prompt: str, user_prompt: str) -> str | None` signature — same inputs/outputs, `synthesize()` in the same file is unaffected.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_synthesis.py`:

```python
"""Unit tests for src.synthesis.llm Gemini retry behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import errors as genai_errors

from src.synthesis.llm import _call_gemini


def _make_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


def _server_error() -> genai_errors.ServerError:
    return genai_errors.ServerError(
        code=503,
        response_json={"error": {"message": "high demand", "status": "UNAVAILABLE"}},
    )


def _client_error() -> genai_errors.ClientError:
    return genai_errors.ClientError(
        code=429,
        response_json={"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}},
    )


@patch("src.synthesis.llm.settings")
@patch("src.synthesis.llm.asyncio.sleep", new_callable=AsyncMock)
class TestCallGemini:
    async def test_succeeds_on_first_attempt(self, mock_sleep, mock_settings):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.return_value = (
                _make_response("hello world")
            )
            result = await _call_gemini("system", "user")
        assert result == "hello world"
        mock_sleep.assert_not_called()

    async def test_recovers_after_one_transient_server_error(self, mock_sleep, mock_settings):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = [
                _server_error(),
                _make_response("recovered"),
            ]
            result = await _call_gemini("system", "user")
        assert result == "recovered"
        mock_sleep.assert_called_once()

    async def test_gives_up_after_exhausting_retries_on_persistent_server_error(
        self, mock_sleep, mock_settings
    ):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = _server_error()
            result = await _call_gemini("system", "user")
        assert result is None
        assert mock_sleep.call_count == 2  # 3 total attempts, 2 backoffs

    async def test_does_not_retry_on_client_error(self, mock_sleep, mock_settings):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = _client_error()
            result = await _call_gemini("system", "user")
        assert result is None
        mock_sleep.assert_not_called()
        assert mock_client_cls.return_value.models.generate_content.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_llm_synthesis.py -v`
Expected: FAIL — retry-specific assertions fail (e.g. `test_recovers_after_one_transient_server_error` fails because `_call_gemini` currently returns `None` on the first exception with no retry).

- [ ] **Step 3: Implement the retry**

Replace the body of `_call_gemini` in `src/synthesis/llm.py` (originally lines 86-112):

```python
_GEMINI_RETRIES = 2
_GEMINI_RETRY_BACKOFF_S = 2.0


async def _call_gemini(system_prompt: str, user_prompt: str) -> str | None:
    """Call Gemini API for synthesis, retrying transient failures.

    Server-side errors (5xx, e.g. "high demand") and other unexpected
    exceptions (network blips) are retried a couple of times with backoff.
    Client errors (4xx — bad auth, exceeded quota) are not retried since
    they won't resolve within the request lifetime.
    """
    from google import genai
    from google.genai import errors as genai_errors

    client = genai.Client(api_key=settings.gemini_api_key)

    for attempt in range(_GEMINI_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                ),
            )
            text = response.text
            if text:
                logger.info(f"LLM: Gemini returned {len(text)} chars")
                return text.strip()

            logger.warning("LLM: Gemini returned empty response")
            return None

        except genai_errors.ClientError as exc:
            logger.exception("LLM: Gemini call failed with a client error (not retrying): %s", exc)
            return None

        except Exception as exc:
            if attempt < _GEMINI_RETRIES:
                logger.warning(
                    "LLM: Gemini call failed (attempt %d/%d), retrying: %s",
                    attempt + 1, _GEMINI_RETRIES + 1, exc,
                )
                await asyncio.sleep(_GEMINI_RETRY_BACKOFF_S * (attempt + 1))
                continue
            logger.exception("LLM: Gemini call failed after %d attempts: %s", _GEMINI_RETRIES + 1, exc)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_llm_synthesis.py -v`
Expected: PASS — all 4 tests.

Then run the full suite to confirm nothing else regressed:

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`
Expected: PASS

- [ ] **Step 5: Document `LLM_PROVIDER` in `.env.example`**

Read `.env.example` first to find the exact line (`GEMINI_API_KEY=your-gemini-api-key-here`), then add directly below it:

```
GEMINI_API_KEY=your-gemini-api-key-here
LLM_PROVIDER=gemini
```

- [ ] **Step 6: Commit**

```bash
git add src/synthesis/llm.py tests/test_llm_synthesis.py .env.example
git commit -m "fix(synthesis): retry Gemini on transient errors, document LLM_PROVIDER

Prod's Claude CLI primary provider has failed on every sampled night in
Loki logs (exit code 1 or 120s timeout), silently falling back to
Gemini every run. When Gemini also hit a transient error (503 'high
demand', a DNS blip) with no retry, the digest cached the static 'LLM
unavailable' fallback for the rest of the day. Adds a 2-retry backoff
for transient failures only (not auth/quota errors). LLM_PROVIDER was
never documented in .env.example, which is likely how prod ended up
pinned to the always-failing 'claude' path unnoticed."
```

---

### Task 4: GEX and Breadth bullish/bearish coloring

**Files:**
- Modify: `src/web/v2/app.js:1373-1396` (`renderGex`, `renderBreadth`)
- Modify: `src/web/v2/index.html:695, 710` (CSS for `.gex-value` and `.breadth-ad`)

**Interfaces:**
- No new functions — modifies existing `renderGex(gex)` and `renderBreadth(breadth)`, called from `fetchMarketOverview()` and `renderOverviewView()` exactly as today.

- [ ] **Step 1: Add GEX color classes to CSS**

In `src/web/v2/index.html`, replace line 695:

```
        .gex-value { font-family: 'IBM Plex Mono', monospace; font-size: 22px; font-weight: 700; margin-bottom: 4px; }
```

with:

```
        .gex-value { font-family: 'IBM Plex Mono', monospace; font-size: 22px; font-weight: 700; margin-bottom: 4px; }
        .gex-value.positive { color: var(--tv-green); }
        .gex-value.negative { color: var(--tv-red); }
```

- [ ] **Step 2: Add Breadth A/D color classes to CSS**

In the same file, replace line 710:

```
        .breadth-ad { font-size: 12px; color: var(--tv-muted); }
```

with:

```
        .breadth-ad { font-size: 12px; color: var(--tv-muted); }
        .breadth-ad.positive { color: var(--tv-green); }
        .breadth-ad.negative { color: var(--tv-red); }
```

- [ ] **Step 3: Color the GEX value by sign**

In `src/web/v2/app.js`, replace `renderGex` (originally lines 1373-1381):

```javascript
function renderGex(gex) {
    const el = document.getElementById('gex-content');
    if (!el) return;
    if (!gex || gex.value_b == null) { el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }
    const arrow = gex.trend === 'Rising' ? '↑' : gex.trend === 'Falling' ? '↓' : '→';
    const sign = gex.value_b >= 0 ? 'positive' : 'negative';
    el.innerHTML = `
        <div class="gex-value ${sign}">$${gex.value_b.toFixed(1)}B</div>
        <div class="gex-label">${escHtml(gex.label)}</div>
        <div class="gex-avg">20d avg: $${gex.rolling_20d_avg_b.toFixed(1)}B &nbsp; ${arrow} ${escHtml(gex.trend)}</div>`;
}
```

- [ ] **Step 4: Color the Breadth A/D line by signal agreement**

In `src/web/v2/app.js`, replace `renderBreadth` (originally lines 1384-1396):

```javascript
function renderBreadth(breadth) {
    const el = document.getElementById('breadth-content');
    if (!el) return;
    if (!breadth) { el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }
    const pct = breadth.pct_above_200ma ?? 0;
    const maColor = pct >= 60 ? 'green' : pct >= 40 ? 'yellow' : 'red';
    const ratio = breadth.ad_ratio;
    const adAgreement = (pct > 50 && ratio != null && ratio > 1) ? 'positive'
        : (pct < 50 && ratio != null && ratio < 1) ? 'negative'
        : '';
    el.innerHTML = `
        <div class="breadth-row">
            <span class="breadth-label">200d MA</span>
            <div class="breadth-bar-track"><div class="breadth-bar-fill ${maColor}" style="width:${pct.toFixed(1)}%"></div></div>
            <span class="breadth-value ${maColor}">${pct.toFixed(0)}%</span>
        </div>
        <div class="breadth-ad ${adAgreement}">A/D &nbsp; ${breadth.advancing}↑ / ${breadth.declining}↓ &nbsp; ratio ${ratio != null ? ratio.toFixed(2) : '—'}</div>`;
}
```

- [ ] **Step 5: No restart needed — confirm the dashboard container is serving the live edit**

`src/web/v2/` is served by the `dashboard` container (nginx), which `docker-compose.local.yml`'s `x-worktree-dashboard` bind-mounts directly from this workspace (`/home/dev/workspace/Market-Intelligence/src/web/v2:/usr/share/nginx/html/v2`) — confirmed the running dev stack uses this overlay (`dashboard`'s `MARKET_INTELLIGENCE_API_URL` is set to `https://dev-mi.austin10berge.com/api`, which only comes from `docker-compose.local.yml`). Edits to `app.js`/`index.html` are visible immediately; just hard-refresh the browser to bypass any client-side cache before verifying in Step 6. No container restart or rebuild required for this task.

- [ ] **Step 6: Verify with Playwright against dev**

- Navigate to `https://dev-mi.austin10berge.com/v2/`, click the **Overview** tab.
- Take a `browser_snapshot` and confirm:
  - GEX value text renders with a visibly different color when `value_b` is negative vs. positive (cross-check the sign against a fresh `curl https://dev-mi.austin10berge.com/api/market-overview` response's `gex.value_b`).
  - Breadth's A/D line renders green when `pct_above_200ma > 50 AND ad_ratio > 1`, red when both are below their midline, and default/muted otherwise — cross-check against the same API response's `breadth.pct_above_200ma` and `breadth.ad_ratio`.
- Confirm no console errors via `browser_console_messages` (level: error).

- [ ] **Step 7: Commit**

```bash
git add src/web/v2/app.js src/web/v2/index.html
git commit -m "feat(overview): color GEX and Breadth widgets by genuine bullish/bearish signal

GEX colored by sign (negative = dealers amplify moves = bearish/red;
positive = dealer hedging dampens moves = bullish/green) — the
existing Low/Moderate/High/Extreme label already conveys intensity, so
the trend arrow stays neutral since direction alone isn't a reliable
signal. Breadth's existing 200MA bar thresholds are untouched; the
previously-uncolored A/D ratio line is now colored only when both
breadth signals (pct above 200MA, advance/decline ratio) agree on
direction — left neutral when they disagree."
```

---

### Task 5: Full verification and prod handoff

**Files:** none (verification + handoff only)

- [ ] **Step 1: Run the full backend test suite**

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`
Expected: PASS, no regressions from Tasks 1-3.

- [ ] **Step 2: Lint**

Run: `~/.local/bin/ruff check src/ tests/`
Expected: no new violations in `src/fetchers/market_overview.py`, `src/synthesis/llm.py`, `tests/test_market_overview.py`, `tests/test_llm_synthesis.py`.

- [ ] **Step 3: Restart the dev API container to pick up Tasks 1-3's Python changes**

`uvicorn` runs without `--reload` (see `Dockerfile`), so bind-mounted source changes aren't picked up by the running process automatically — restart is required:

Run: `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build api`
Expected: container recreated, `docker compose ps api` shows `running (healthy)` within ~10s (healthcheck hits `/api/health`).

- [ ] **Step 4: Playwright-verify VIX and themes on dev**

- Navigate to `https://dev-mi.austin10berge.com/v2/`, Overview tab.
- Reload 3 times (or wait for the natural cache refresh) and confirm via `browser_snapshot`:
  - VIX card shows a spot value, not "Unavailable", on each load.
  - Switching to the Themes toggle on the Sectors card shows both "SaaS" and "Semis/Memory" among the listed themes.
- Cross-check with `curl https://dev-mi.austin10berge.com/api/market-overview` — confirm `vix` is non-null and `themes.singles` includes both `"SaaS"` and `"Semis/Memory"` keys.

- [ ] **Step 5: Prepare the prod handoff**

Since there is no SSH/prod access from this environment, produce the following for the user to run manually on the prod host (`10.0.1.21`):

1. Edit prod's `.env` (path alongside the prod `docker-compose.yml`, typically `/root/market-intelligence/.env` per the cron paths documented in `docker-compose.yml`): change
   ```
   LLM_PROVIDER=claude
   ```
   to
   ```
   LLM_PROVIDER=gemini
   ```
   (If the line doesn't exist yet, add it — it was previously undocumented.)

2. Rebuild/restart the affected prod services so the code changes (Tasks 1-4) and the `.env` change take effect. Unlike dev, prod runs plain `docker-compose.yml` with no bind-mount overlay — both `api` (Tasks 1-3 Python changes) and `dashboard` (Task 4's `src/web/v2/` changes, baked into the image at build time) need a rebuild:
   ```bash
   docker compose -f docker-compose.yml build api dashboard pipeline
   docker compose -f docker-compose.yml up -d api dashboard
   ```
   (`pipeline` is invoked fresh each night via cron with `docker compose run --rm pipeline`, so it doesn't need an explicit restart — just the rebuilt image.)

3. Confirm after the next nightly pipeline run (or an on-demand run) that `market.austin10berge.com/api/market-posture`'s `llm_summary` is a real synthesized summary, not the "LLM unavailable" fallback string.
4. Confirm `market.austin10berge.com`'s Overview tab shows VIX populated, SaaS/Semis-Memory in the Themes toggle, and colored GEX/Breadth widgets.

- [ ] **Step 6: Report deviations**

If any test, lint check, or Playwright verification fails, stop and report the specific failure rather than proceeding — do not mark this task complete with unresolved failures.
