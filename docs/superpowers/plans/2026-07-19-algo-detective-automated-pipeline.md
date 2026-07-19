# Algo Detective Automated Feature & Label Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `detective_features` (technicals/fundamentals + `is_prime` ground-truth labels) grow automatically every night — scraping mLabs' weekly recap posts for real trade labels and computing features for the full control universe — instead of only via manual research-script runs, and fix the 7-month-old silent bug that's frozen `detective_options` (real IV data) collection.

**Architecture:** Two new nightly pipeline steps (`main.py` Step 6: mLabs label sync, Step 7: control-universe feature sync) plus a fix to the existing Step 5 crash-loop, a shared feature-computation helper extracted from the existing `build.py::run_build()` (reused by both the new steps and the unchanged CSV-driven manual path), and a two-phase backfill CLI. Scraping is `httpx` + `lxml` against real HTML — no browser automation, no new dependencies.

**Tech Stack:** Python 3.12, httpx, lxml, SQLite (existing `market_intelligence.db`), pytest with `respx` for HTTP mocking.

**Spec:** `docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md`

## Global Constraints

- Python 3.12, no local virtualenv — run tests via `docker compose run --rm test python3 -m pytest tests/...`. Bare `python -m ...` on the host fails (no deps installed there).
- `httpx>=0.27` and `lxml>=5.0` are already project dependencies — do not add `cssselect`, `beautifulsoup4`, or any other new dependency. Use `lxml.html` + XPath (not `.cssselect()`, which needs the `cssselect` package we don't have).
- A `PostToolUse` hook auto-runs ruff on every edited `.py` file — no manual format step.
- All new modules use `from __future__ import annotations`, per existing repo convention.
- Follow the existing non-fatal try/except-log-and-continue convention for pipeline steps (`main.py` Steps 1-5) — Steps 6 and 7 must not raise out of `main.py`.
- Automated recap scraping only reaches back to `results_boring_puts_2026_01_05` — every recap post from 2025-09-01 through 2025-12-29 has no `trades-table` HTML element (PDF-only format), which must be treated as a valid zero-result outcome (checkpointed, not retried), not a parse error.
- Only `Type = "CSP"` rows count as prime signals. `CC` (covered call) and any other trade type must be filtered out — they represent management of existing share positions, not fresh scanner-driven entries.
- Only `ticker` + `open_date` are extracted from recap posts — no delta/IV/score columns (those don't exist in this table; `compute_features()` derives technicals independently).

---

## File Structure

```
src/algo_detective/
├── store.py            (MODIFY — detective_scraped_posts DDL, get_scraped_slugs(),
│                         record_scraped_post())
├── build.py             (MODIFY — extract compute_and_store_for_date() helper,
│                         refactor run_build() to use it; behavior unchanged)
├── mlabs_scraper.py      (NEW — fetch_post_index(), fetch_recap_trades())
├── control_sync.py        (NEW — sync_control_universe(date))
├── label_sync.py           (NEW — sync_new_labels())
└── backfill_mlabs.py         (NEW — two-phase backfill CLI)

src/
└── main.py             (MODIFY — Step 5 ensure_tables() fix, new Steps 6 & 7)

tests/
├── test_algo_detective_store.py              (MODIFY — add checkpoint table tests)
├── test_algo_detective_mlabs_scraper.py       (NEW — Task 2)
├── test_algo_detective_build.py               (NEW — Task 3)
├── test_algo_detective_control_sync.py          (NEW — Task 4)
├── test_algo_detective_label_sync.py             (NEW — Task 5)
├── test_main_pipeline_algo_detective_steps.py     (NEW — Task 6)
└── test_algo_detective_backfill_mlabs.py            (NEW — Task 7)
```

---

### Task 1: `detective_scraped_posts` checkpoint table

**Files:**
- Modify: `src/algo_detective/store.py`
- Modify: `tests/test_algo_detective_store.py`

**Interfaces:**
- Produces: `get_scraped_slugs() -> set[str]`; `record_scraped_post(slug: str, trades_found: int) -> None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_algo_detective_store.py`, extending the existing import block (currently ends with `get_computed_options_pairs,`) to also import `get_scraped_slugs, record_scraped_post`:

```python
from src.algo_detective.store import (
    ensure_tables,
    get_computed_pairs,
    upsert_feature_rows_bulk,
    upsert_macro_row,
    get_all_features,
    get_macro_for_date,
    get_feature_counts,
    upsert_options_rows,
    get_options_index,
    get_computed_options_pairs,
    get_scraped_slugs,
    record_scraped_post,
)
```

Append to the end of the file:

```python
class TestScrapedPostsCheckpoint:
    def test_empty_when_nothing_recorded(self):
        ensure_tables()
        assert get_scraped_slugs() == set()

    def test_records_and_returns_slug(self):
        ensure_tables()
        record_scraped_post("results_boring_puts_2026_01_05", trades_found=3)
        assert get_scraped_slugs() == {"results_boring_puts_2026_01_05"}

    def test_records_zero_trades_slug(self):
        ensure_tables()
        record_scraped_post("results_boring_puts_2025_12_29", trades_found=0)
        assert get_scraped_slugs() == {"results_boring_puts_2025_12_29"}

    def test_recording_same_slug_twice_does_not_duplicate(self):
        ensure_tables()
        record_scraped_post("results_boring_puts_2026_02_02", trades_found=5)
        record_scraped_post("results_boring_puts_2026_02_02", trades_found=5)
        assert get_scraped_slugs() == {"results_boring_puts_2026_02_02"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py::TestScrapedPostsCheckpoint -v`
Expected: FAIL — `ImportError: cannot import name 'get_scraped_slugs'`

- [ ] **Step 3: Add the table DDL and functions**

In `src/algo_detective/store.py`, the `_DDL` string currently ends with the `detective_macro` table definition followed by a closing `"""` (around line 101). Insert a new table definition right before that closing `"""`:

```python
CREATE TABLE IF NOT EXISTS detective_scraped_posts (
    slug            TEXT PRIMARY KEY,
    scraped_at      TEXT NOT NULL,
    trades_found    INTEGER NOT NULL
);
"""
```

(This replaces the bare `"""` that currently closes `_DDL` — the new `CREATE TABLE` block goes immediately after `detective_macro`'s closing `);` and before the closing `"""`.)

Add these two functions to `src/algo_detective/store.py`, after `get_computed_options_pairs` (or any other existing function — placement among the module's other functions doesn't matter, just keep them together):

```python
def get_scraped_slugs() -> set[str]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT slug FROM detective_scraped_posts").fetchall()
        return {r["slug"] for r in rows}
    finally:
        conn.close()


def record_scraped_post(slug: str, trades_found: int) -> None:
    from datetime import datetime, timezone

    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO detective_scraped_posts (slug, scraped_at, trades_found) "
            "VALUES (?, ?, ?) ON CONFLICT(slug) DO UPDATE SET "
            "scraped_at = excluded.scraped_at, trades_found = excluded.trades_found",
            (slug, datetime.now(timezone.utc).isoformat(), trades_found),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py -v`
Expected: PASS (all tests in the file, including the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/store.py tests/test_algo_detective_store.py
git commit -m "feat(algo-detective): add detective_scraped_posts checkpoint table"
```

---

### Task 2: mLabs scraper — post index + recap trade parsing

**Files:**
- Create: `src/algo_detective/mlabs_scraper.py`
- Test: `tests/test_algo_detective_mlabs_scraper.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure HTTP + parsing module).
- Produces: `fetch_post_index() -> list[str]`; `fetch_recap_trades(slug: str) -> list[dict]` — each item `{"ticker": str, "open_date": "YYYY-MM-DD"}`.

**Real HTML structure (verified 2026-07-19 by fetching raw pages, not AI-summarized):**

The recap page's trades table:
```html
<table class="trades-table"><thead><tr>
<th>Type</th><th>Open</th><th>Exp</th><th>Close</th><th>Ticker</th>
<th>Strike</th><th>Qty</th><th>Fill</th><th>Exit</th><th>Fee</th><th>Cap</th><th>P/L$</th><th>ROC</th>
</tr></thead><tbody>
<tr><td>CSP</td><td>7/15</td><td>7/17</td><td>7/17</td><td><strong>NVO</strong></td><td>49</td><td>1</td><td>0.16</td><td>0.00</td><td>1.04</td><td>4.9k</td><td>14.96</td><td class="positive">0.31%</td></tr>
</tbody></table>
```
Column order (0-indexed `<td>` position within each `<tr>`): `Type=0, Open=1, Exp=2, Close=3, Ticker=4, Strike=5, Qty=6, Fill=7, Exit=8, Fee=9, Cap=10, P/L$=11, ROC=12`. The `Open` date has no year (`"7/15"` = month/day only). Pre-2026-01-05 posts have no `<table class="trades-table">` element at all (PDF-only format) — this must produce an empty list, not an error.

The posts index page:
```html
<a href="/posts/results_boring_puts_2026_07_13">...</a>
<a href="/posts/results_boring_puts_2026_07_06">...</a>
```
A single fetch of `https://blog.mlabstrading.com/posts` returns all 43 recap slugs found so far (verified — no pagination markers, no "load more").

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_mlabs_scraper.py`:

```python
"""Tests for src/algo_detective/mlabs_scraper.py — parses mLabs Trading's
weekly recap posts (blog.mlabstrading.com) into (ticker, open_date) pairs
for is_prime labeling. HTML snippets below are trimmed excerpts of real
pages fetched 2026-07-19, not synthetic mockups.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

import httpx
import respx

from src.algo_detective.mlabs_scraper import fetch_post_index, fetch_recap_trades

_INDEX_HTML = """
<html><body>
<a href="/posts/results_boring_puts_2026_07_13">Results 7/13</a>
<a href="/posts/results_boring_puts_2026_07_06">Results 7/6</a>
<a href="/posts/boring_puts_watchlist_2026_07_14">Watchlist 7/14</a>
<a href="/posts/results_boring_puts_2025_09_01">Results 9/1/25</a>
</body></html>
"""

_SINGLE_TRADE_HTML = """
<html><body>
<h3>This Week's Opening Trades</h3>
<table class="trades-table"><thead><tr>
<th>Type</th><th>Open</th><th>Exp</th><th>Close</th><th>Ticker</th>
<th>Strike</th><th>Qty</th><th>Fill</th><th>Exit</th><th>Fee</th><th>Cap</th><th>P/L$</th><th>ROC</th>
</tr></thead><tbody>
<tr><td>CSP</td><td>7/15</td><td>7/17</td><td>7/17</td><td><strong>NVO</strong></td>
<td>49</td><td>1</td><td>0.16</td><td>0.00</td><td>1.04</td><td>4.9k</td><td>14.96</td>
<td class="positive">0.31%</td></tr>
</tbody></table>
</body></html>
"""

_MULTI_TRADE_HTML = """
<html><body>
<table class="trades-table"><thead><tr>
<th>Type</th><th>Open</th><th>Exp</th><th>Close</th><th>Ticker</th>
<th>Strike</th><th>Qty</th><th>Fill</th><th>Exit</th><th>Fee</th><th>Cap</th><th>P/L$</th><th>ROC</th>
</tr></thead><tbody>
<tr><td>CSP</td><td>2/2</td><td>2/20</td><td></td><td><strong>AEO</strong></td>
<td>22</td><td>3</td><td>0.40</td><td>0.00</td><td>1.36</td><td>6.6k</td><td>118.64</td><td>1.80%</td></tr>
<tr><td>CSP</td><td>2/3</td><td>2/6</td><td>2/5</td><td><strong>UAL</strong></td>
<td>106</td><td>1</td><td>0.71</td><td>1.74</td><td>1.34</td><td>10.6k</td><td>-104.34</td><td>-0.98%</td></tr>
<tr><td>CC</td><td>2/6</td><td>2/13</td><td></td><td><strong>NVDA</strong></td>
<td>192.5</td><td>1</td><td>0.38</td><td>0.00</td><td>0.67</td><td>19.15k</td><td>37.33</td><td>0.19%</td></tr>
</tbody></table>
</body></html>
"""

_NO_TABLE_HTML = """
<html><body>
<h2>Detailed Trading Log</h2>
<p><a href="/trade_logs/MLABS%20Trading.pdf">Download Trading Log (PDF)</a></p>
<ul><li>Cash Secured Puts (CSP) on DELL, NVDA*, UAL, GOOG*</li></ul>
</body></html>
"""


class TestFetchPostIndex:
    @respx.mock
    def test_returns_only_results_slugs_not_watchlist(self):
        respx.get("https://blog.mlabstrading.com/posts").mock(
            return_value=httpx.Response(200, text=_INDEX_HTML)
        )
        slugs = fetch_post_index()
        assert slugs == [
            "results_boring_puts_2025_09_01",
            "results_boring_puts_2026_07_06",
            "results_boring_puts_2026_07_13",
        ]


class TestFetchRecapTrades:
    @respx.mock
    def test_parses_single_csp_trade(self):
        respx.get("https://blog.mlabstrading.com/posts/results_boring_puts_2026_07_13").mock(
            return_value=httpx.Response(200, text=_SINGLE_TRADE_HTML)
        )
        trades = fetch_recap_trades("results_boring_puts_2026_07_13")
        assert trades == [{"ticker": "NVO", "open_date": "2026-07-15"}]

    @respx.mock
    def test_filters_out_non_csp_rows_and_keeps_column_order(self):
        respx.get("https://blog.mlabstrading.com/posts/results_boring_puts_2026_02_02").mock(
            return_value=httpx.Response(200, text=_MULTI_TRADE_HTML)
        )
        trades = fetch_recap_trades("results_boring_puts_2026_02_02")
        # NVDA (Type=CC) must be excluded; AEO and UAL (Type=CSP) kept
        assert trades == [
            {"ticker": "AEO", "open_date": "2026-02-02"},
            {"ticker": "UAL", "open_date": "2026-02-03"},
        ]

    @respx.mock
    def test_returns_empty_list_when_no_table_present(self):
        respx.get("https://blog.mlabstrading.com/posts/results_boring_puts_2025_09_01").mock(
            return_value=httpx.Response(200, text=_NO_TABLE_HTML)
        )
        trades = fetch_recap_trades("results_boring_puts_2025_09_01")
        assert trades == []

    @respx.mock
    def test_open_date_year_rolls_over_at_december_to_january_boundary(self):
        html = _SINGLE_TRADE_HTML.replace(">7/15<", ">1/2<")
        respx.get("https://blog.mlabstrading.com/posts/results_boring_puts_2025_12_29").mock(
            return_value=httpx.Response(200, text=html)
        )
        trades = fetch_recap_trades("results_boring_puts_2025_12_29")
        assert trades == [{"ticker": "NVO", "open_date": "2026-01-02"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_mlabs_scraper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo_detective.mlabs_scraper'`

- [ ] **Step 3: Implement the scraper**

Create `src/algo_detective/mlabs_scraper.py`:

```python
"""Scrapes mLabs Trading's weekly recap posts (blog.mlabstrading.com) for
his actual CSP trades — the structured ground truth behind is_prime
labeling, replacing the previous manual Reddit-transcription process.

Only the structured HTML trades-table format is supported (posts from
results_boring_puts_2026_01_05 onward). Earlier posts link to a PDF trade
log instead and have no trades-table element — fetch_recap_trades()
returns an empty list for those, which is a valid, expected outcome, not
an error. See
docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

import logging
import re

import httpx
import lxml.html

logger = logging.getLogger(__name__)

_BASE_URL = "https://blog.mlabstrading.com"
_SLUG_RE = re.compile(r"/posts/(results_boring_puts_\d{4}_\d{2}_\d{2})")


def fetch_post_index() -> list[str]:
    """Return every results_boring_puts_* slug found on the posts index,
    sorted ascending (oldest first)."""
    response = httpx.get(f"{_BASE_URL}/posts", timeout=30.0)
    response.raise_for_status()
    tree = lxml.html.fromstring(response.text)
    hrefs = tree.xpath('//a[contains(@href, "/posts/results_boring_puts_")]/@href')
    slugs = {m.group(1) for href in hrefs if (m := _SLUG_RE.search(href))}
    return sorted(slugs)


def _resolve_open_date(slug: str, month_day: str) -> str:
    """Combine a recap slug's year with a bare 'M/D' open-date cell,
    handling the December-to-January week rollover."""
    slug_year = int(slug.split("_")[-3])
    slug_month = int(slug.split("_")[-2])
    month_str, day_str = month_day.split("/")
    month, day = int(month_str), int(day_str)
    year = slug_year + 1 if month < slug_month - 1 else slug_year
    return f"{year:04d}-{month:02d}-{day:02d}"


def fetch_recap_trades(slug: str) -> list[dict]:
    """Fetch one recap post and return its CSP opening trades as
    [{"ticker": str, "open_date": "YYYY-MM-DD"}, ...].

    Returns an empty list (not an error) when the post has no
    trades-table element (PDF-era posts before 2026-01-05).
    """
    response = httpx.get(f"{_BASE_URL}/posts/{slug}", timeout=30.0)
    response.raise_for_status()
    tree = lxml.html.fromstring(response.text)

    tables = tree.xpath('//table[@class="trades-table"]')
    if not tables:
        return []

    trades = []
    for row in tables[0].xpath(".//tbody/tr"):
        cells = [td.text_content().strip() for td in row.xpath("./td")]
        if len(cells) < 5:
            continue
        trade_type, open_cell, ticker = cells[0], cells[1], cells[4]
        if trade_type != "CSP":
            continue
        trades.append({"ticker": ticker, "open_date": _resolve_open_date(slug, open_cell)})

    return trades
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_mlabs_scraper.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/mlabs_scraper.py tests/test_algo_detective_mlabs_scraper.py
git commit -m "feat(algo-detective): add mLabs recap post scraper"
```

---

### Task 3: Extract shared feature-computation helper in `build.py`

**Files:**
- Modify: `src/algo_detective/build.py`
- Test: `tests/test_algo_detective_build.py` (new — `build.py` currently has no test coverage)

**Interfaces:**
- Consumes: `get_fundamentals_for_tickers` from `..market_data.store`; `compute_features` from `.features`; `compute_macro_for_date` from `.macro_context`; `load_ohlcv_batch_for_date` from `.universe`; `upsert_feature_rows_bulk`, `upsert_macro_row` from `.store`.
- Produces: `compute_and_store_for_date(date: str, ticker_flags: list[tuple[str, int]], computed_pairs: set[tuple[str, str]], ohlcv_fallback_fn: Callable[[str], "pd.DataFrame | None"] | None = None) -> list[dict]` — returns the rows that were computed and upserted (each a full `detective_features` row dict, including `is_prime`), skipping any `(date, ticker)` already in `computed_pairs`.

**Why this task exists:** `run_build()`'s existing per-date loop already does exactly "prime ∪ control tickers, skip already-computed, fetch fundamentals/OHLCV/macro, compute, upsert" — this task extracts that loop body into a reusable function so Tasks 4 and 5 (the new automated control/label sync) call the same tested logic instead of duplicating it. `run_build()`'s own behavior (including its CSV-specific cross-validation step, which stays in `run_build()` and is *not* part of the extracted helper) must be unchanged after this refactor.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_build.py`:

```python
"""Tests for compute_and_store_for_date in src/algo_detective/build.py —
the shared feature-computation helper extracted from run_build()'s
per-date loop, reused by control_sync.py and label_sync.py (Tasks 4-5).
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.algo_detective.build import compute_and_store_for_date


def _make_ohlcv(periods: int = 220) -> pd.DataFrame:
    closes = [100.0 + i * 0.1 for i in range(periods)]
    dates = pd.date_range(end="2026-02-02", periods=periods, freq="B")
    return pd.DataFrame(
        {
            "Open": closes, "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes], "Close": closes,
            "Volume": [1_000_000] * periods,
        },
        index=dates,
    )


@pytest.fixture
def _patched_dependencies():
    ohlcv = _make_ohlcv()
    with patch("src.algo_detective.build.get_fundamentals_for_tickers") as mock_fund, \
         patch("src.algo_detective.build.load_ohlcv_batch_for_date") as mock_ohlcv, \
         patch("src.algo_detective.build.compute_macro_for_date") as mock_macro, \
         patch("src.algo_detective.build.upsert_macro_row"), \
         patch("src.algo_detective.build.upsert_feature_rows_bulk") as mock_upsert:
        mock_fund.return_value = [
            {"symbol": "AEO", "sector": "Consumer Cyclical", "market_cap_b": 3.0},
            {"symbol": "SPY", "sector": None, "market_cap_b": 400.0},
        ]
        mock_ohlcv.return_value = {"AEO": ohlcv.copy(), "SPY": ohlcv.copy()}
        mock_macro.return_value = None
        mock_upsert.side_effect = lambda rows: len(rows)
        yield


class TestComputeAndStoreForDate:
    def test_computes_and_upserts_rows_for_each_ticker(self, _patched_dependencies):
        rows = compute_and_store_for_date(
            "2026-02-02", [("AEO", 1), ("SPY", 0)], computed_pairs=set(),
        )
        assert {r["ticker"] for r in rows} == {"AEO", "SPY"}
        assert next(r for r in rows if r["ticker"] == "AEO")["is_prime"] == 1
        assert next(r for r in rows if r["ticker"] == "SPY")["is_prime"] == 0

    def test_skips_pairs_already_in_computed_pairs(self, _patched_dependencies):
        rows = compute_and_store_for_date(
            "2026-02-02",
            [("AEO", 1), ("SPY", 0)],
            computed_pairs={("2026-02-02", "SPY")},
        )
        assert {r["ticker"] for r in rows} == {"AEO"}

    def test_skips_ticker_with_no_ohlcv_in_batch_and_no_fallback(self, _patched_dependencies):
        rows = compute_and_store_for_date(
            "2026-02-02", [("UNKNOWN", 1)], computed_pairs=set(),
        )
        assert rows == []

    def test_uses_fallback_when_ticker_missing_from_batch(self, _patched_dependencies):
        fallback_df = _make_ohlcv()
        rows = compute_and_store_for_date(
            "2026-02-02",
            [("SMALLCAP", 1)],
            computed_pairs=set(),
            ohlcv_fallback_fn=lambda ticker: fallback_df.copy(),
        )
        assert {r["ticker"] for r in rows} == {"SMALLCAP"}

    def test_returns_empty_list_when_nothing_to_compute(self, _patched_dependencies):
        rows = compute_and_store_for_date("2026-02-02", [], computed_pairs=set())
        assert rows == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_build.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_and_store_for_date'`

- [ ] **Step 3: Extract the helper and refactor `run_build()`**

In `src/algo_detective/build.py`, replace the body of `run_build()` from the line `for date in dates:` through the `logger.info("Date %s: inserted %d rows", date, count)` line (i.e. the entire per-date loop, currently lines ~41-108) with a call to the new extracted helper, and add the helper function itself. The full replacement:

```python
def compute_and_store_for_date(
    date: str,
    ticker_flags: list[tuple[str, int]],
    computed_pairs: set[tuple[str, str]],
    ohlcv_fallback_fn: "Callable[[str], pd.DataFrame | None] | None" = None,
) -> list[dict]:
    """Compute features and upsert detective_features rows for the given
    (ticker, is_prime) pairs on one date, skipping any pair already in
    computed_pairs. If a ticker has no OHLCV in the tracked universe batch
    and ohlcv_fallback_fn is given, it's called as a last resort (used by
    label_sync.py for prime tickers outside the tracked universe).

    Returns the rows that were computed and upserted — callers needing
    per-row post-processing (e.g. run_build()'s CSV cross-validation) read
    them from the return value rather than recomputing.
    """
    to_compute = [(t, f) for t, f in ticker_flags if (date, t) not in computed_pairs]
    if not to_compute:
        return []

    all_syms = [t for t, _ in to_compute]
    fund_rows = get_fundamentals_for_tickers(all_syms)
    sector_map = {r["symbol"]: r.get("sector") for r in fund_rows}
    fund_map = {
        r["symbol"]: {
            "market_cap_b": r.get("market_cap_b"),
            "beta": r.get("beta"),
            "forward_pe": r.get("forward_pe"),
            "peg_ratio": r.get("peg_ratio"),
            "revenue_growth": r.get("revenue_growth"),
            "earnings_growth": r.get("earnings_growth"),
            "debt_to_equity": r.get("debt_to_equity"),
            "dividend_yield": r.get("dividend_yield"),
            "fcf": r.get("fcf"),
        }
        for r in fund_rows
    }

    macro = compute_macro_for_date(date)
    if macro:
        upsert_macro_row(macro)

    ohlcv_map = load_ohlcv_batch_for_date(all_syms, date)
    now = datetime.now(timezone.utc).isoformat()
    rows_to_insert: list[dict] = []

    for ticker, is_prime in to_compute:
        df = ohlcv_map.get(ticker)
        if (df is None or df.empty) and ohlcv_fallback_fn is not None:
            df = ohlcv_fallback_fn(ticker)
        if df is None or df.empty:
            logger.warning("No OHLCV for %s on %s", ticker, date)
            continue

        feats = compute_features(ticker, date, df, sector=sector_map.get(ticker))
        if feats is None:
            logger.debug("Insufficient history for %s on %s", ticker, date)
            continue

        rows_to_insert.append({
            "date": date, "ticker": ticker, "is_prime": is_prime,
            **feats,
            **fund_map.get(ticker, {}),
            "computed_at": now,
        })

    upsert_feature_rows_bulk(rows_to_insert)
    return rows_to_insert


def run_build(csv_path: Path = _CSV_PATH) -> None:
    ensure_tables()

    records = load_prime_tickers(csv_path)
    logger.info("Loaded %d prime records", len(records))

    computed_pairs = get_computed_pairs()
    dates = get_unique_dates(records)
    logger.info("Processing %d unique dates", len(dates))

    for date in dates:
        prime_tickers = get_prime_tickers_for_date(records, date)
        prime_set = set(prime_tickers)
        control_tickers = get_control_tickers(date, exclude=prime_set)

        ticker_flags = [(t, 1) for t in prime_tickers] + [(t, 0) for t in control_tickers]
        rows = compute_and_store_for_date(date, ticker_flags, computed_pairs)

        for row in rows:
            if row["is_prime"]:
                _cross_validate(row["ticker"], date, row, records)

        logger.info(
            "Date %s: computed %d of %d requested tickers",
            date, len(rows), len(ticker_flags),
        )

    counts = get_feature_counts()
    logger.info(
        "Build complete — total: %d  prime: %d  control: %d  macro_dates: %d",
        counts["total"],
        counts["prime"],
        counts["control"],
        counts["macro_dates"],
    )
```

Add `from typing import Callable` to the imports at the top of `src/algo_detective/build.py` (alongside the existing `from datetime import datetime, timezone`).

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_build.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Verify `run_build()`'s behavior is unchanged**

`build.py` has no prior automated test coverage of `run_build()` itself, so verify manually against real data:

Run: `docker compose --profile pipeline run --rm pipeline python3 -m src.algo_detective.build`
Expected: Completes without error; the final "Build complete — total: N prime: N control: N macro_dates: N" log line reports the same counts as a run before this refactor (spot-check against `get_feature_counts()` — the total/prime/control counts should match what was already in `detective_features` before this task, since every `(date, ticker)` pair was already computed and this run should skip all of them, reporting "computed 0 of N requested tickers" for every date).

- [ ] **Step 6: Commit**

```bash
git add src/algo_detective/build.py tests/test_algo_detective_build.py
git commit -m "refactor(algo-detective): extract compute_and_store_for_date from run_build"
```

---

### Task 4: Control-universe feature sync

**Files:**
- Create: `src/algo_detective/control_sync.py`
- Test: `tests/test_algo_detective_control_sync.py`

**Interfaces:**
- Consumes: `compute_and_store_for_date` (Task 3); `get_control_tickers` from `.universe`; `get_computed_pairs` from `.store`.
- Produces: `sync_control_universe(date: str) -> int` — computes and upserts `is_prime=0` rows for the tracked universe on `date`, excluding tickers already labeled `is_prime=1` that date. Returns count of rows written.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_control_sync.py`:

```python
"""Tests for src/algo_detective/control_sync.py — Step 7 of the nightly
pipeline (and reused by backfill Phase 2). Computes control-universe
(is_prime=0) features for a date, never overwriting a same-day is_prime=1
label written earlier by label_sync.py (Task 5).
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

from src.algo_detective.control_sync import sync_control_universe


class TestSyncControlUniverse:
    @patch("src.algo_detective.control_sync.compute_and_store_for_date")
    @patch("src.algo_detective.control_sync.get_computed_pairs")
    @patch("src.algo_detective.control_sync.get_control_tickers")
    @patch("src.algo_detective.control_sync._get_todays_primes")
    def test_excludes_todays_primes_from_control_tickers(
        self, mock_primes, mock_control, mock_pairs, mock_compute,
    ):
        mock_primes.return_value = {"AEO"}
        mock_control.return_value = ["SPY", "MSFT"]
        mock_pairs.return_value = set()
        mock_compute.return_value = [{"ticker": "SPY"}, {"ticker": "MSFT"}]

        count = sync_control_universe("2026-02-02")

        mock_control.assert_called_once_with("2026-02-02", exclude={"AEO"})
        assert count == 2

    @patch("src.algo_detective.control_sync.compute_and_store_for_date")
    @patch("src.algo_detective.control_sync.get_computed_pairs")
    @patch("src.algo_detective.control_sync.get_control_tickers")
    @patch("src.algo_detective.control_sync._get_todays_primes")
    def test_all_control_tickers_flagged_is_prime_zero(
        self, mock_primes, mock_control, mock_pairs, mock_compute,
    ):
        mock_primes.return_value = set()
        mock_control.return_value = ["SPY"]
        mock_pairs.return_value = set()
        mock_compute.return_value = []

        sync_control_universe("2026-02-02")

        called_ticker_flags = mock_compute.call_args.args[1]
        assert called_ticker_flags == [("SPY", 0)]

    @patch("src.algo_detective.control_sync.compute_and_store_for_date")
    @patch("src.algo_detective.control_sync.get_computed_pairs")
    @patch("src.algo_detective.control_sync.get_control_tickers")
    @patch("src.algo_detective.control_sync._get_todays_primes")
    def test_returns_zero_when_nothing_computed(
        self, mock_primes, mock_control, mock_pairs, mock_compute,
    ):
        mock_primes.return_value = set()
        mock_control.return_value = []
        mock_pairs.return_value = set()
        mock_compute.return_value = []

        assert sync_control_universe("2026-02-02") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_control_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo_detective.control_sync'`

- [ ] **Step 3: Implement `sync_control_universe`**

Create `src/algo_detective/control_sync.py`:

```python
"""Step 7 of the nightly algo_detective pipeline: computes technical/
fundamental features for the tracked control universe on a given date,
storing them as is_prime=0. Reused by backfill_mlabs.py's Phase 2 to
backfill control-universe features for every historical date the mLabs
recap backfill (Task 5) surfaces a prime label for.

Must run after label_sync.py's sync_new_labels() in the same pipeline
pass — excludes today's already-labeled prime tickers so a freshly
discovered is_prime=1 row is never downgraded back to a control row.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

import logging

from .build import compute_and_store_for_date
from .store import _get_connection, get_computed_pairs
from .universe import get_control_tickers

logger = logging.getLogger(__name__)


def _get_todays_primes(date: str) -> set[str]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker FROM detective_features WHERE date = ? AND is_prime = 1",
            (date,),
        ).fetchall()
        return {r["ticker"] for r in rows}
    finally:
        conn.close()


def sync_control_universe(date: str) -> int:
    """Compute + upsert is_prime=0 rows for the tracked universe on date,
    excluding tickers already labeled is_prime=1 that date. Returns the
    number of rows written."""
    todays_primes = _get_todays_primes(date)
    control_tickers = get_control_tickers(date, exclude=todays_primes)
    computed_pairs = get_computed_pairs()

    ticker_flags = [(t, 0) for t in control_tickers]
    rows = compute_and_store_for_date(date, ticker_flags, computed_pairs)
    logger.info("Control sync %s: %d rows written (%d requested)", date, len(rows), len(ticker_flags))
    return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_control_sync.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/control_sync.py tests/test_algo_detective_control_sync.py
git commit -m "feat(algo-detective): add control-universe feature sync"
```

---

### Task 5: mLabs label sync

**Files:**
- Create: `src/algo_detective/label_sync.py`
- Test: `tests/test_algo_detective_label_sync.py`

**Interfaces:**
- Consumes: `fetch_post_index`, `fetch_recap_trades` (Task 2); `compute_and_store_for_date` (Task 3, which already handles the tracked-universe OHLCV batch lookup internally); `get_scraped_slugs`, `record_scraped_post` (Task 1); `get_computed_pairs` from `.store`; `get_historical_data` from `..backtester.data_provider` (OHLCV fallback for tickers outside the tracked universe, passed to `compute_and_store_for_date` as `ohlcv_fallback_fn`).
- Produces: `sync_new_labels() -> int` — Step 6 of the nightly pipeline. Returns count of new prime rows written.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_label_sync.py`:

```python
"""Tests for src/algo_detective/label_sync.py — Step 6 of the nightly
pipeline. Discovers new mLabs recap posts, parses them into (ticker, date)
pairs, computes features, and upserts is_prime=1 rows.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

from src.algo_detective.label_sync import sync_new_labels


class TestSyncNewLabels:
    @patch("src.algo_detective.label_sync.record_scraped_post")
    @patch("src.algo_detective.label_sync.compute_and_store_for_date")
    @patch("src.algo_detective.label_sync.get_computed_pairs")
    @patch("src.algo_detective.label_sync.fetch_recap_trades")
    @patch("src.algo_detective.label_sync.fetch_post_index")
    @patch("src.algo_detective.label_sync.get_scraped_slugs")
    def test_skips_already_processed_slugs(
        self, mock_known, mock_index, mock_trades, mock_pairs, mock_compute, mock_record,
    ):
        mock_known.return_value = {"results_boring_puts_2026_07_06"}
        mock_index.return_value = [
            "results_boring_puts_2026_07_06", "results_boring_puts_2026_07_13",
        ]
        mock_trades.return_value = [{"ticker": "NVO", "open_date": "2026-07-15"}]
        mock_pairs.return_value = set()
        mock_compute.return_value = [{"ticker": "NVO", "date": "2026-07-15", "is_prime": 1}]

        sync_new_labels()

        mock_trades.assert_called_once_with("results_boring_puts_2026_07_13")

    @patch("src.algo_detective.label_sync.record_scraped_post")
    @patch("src.algo_detective.label_sync.compute_and_store_for_date")
    @patch("src.algo_detective.label_sync.get_computed_pairs")
    @patch("src.algo_detective.label_sync.fetch_recap_trades")
    @patch("src.algo_detective.label_sync.fetch_post_index")
    @patch("src.algo_detective.label_sync.get_scraped_slugs")
    def test_groups_trades_by_date_and_computes_prime_flag(
        self, mock_known, mock_index, mock_trades, mock_pairs, mock_compute, mock_record,
    ):
        mock_known.return_value = set()
        mock_index.return_value = ["results_boring_puts_2026_02_02"]
        mock_trades.return_value = [
            {"ticker": "AEO", "open_date": "2026-02-02"},
            {"ticker": "UAL", "open_date": "2026-02-03"},
        ]
        mock_pairs.return_value = set()
        mock_compute.side_effect = lambda date, flags, pairs, ohlcv_fallback_fn=None: [
            {"ticker": t, "date": date, "is_prime": f} for t, f in flags
        ]

        count = sync_new_labels()

        calls = {c.args[0]: c.args[1] for c in mock_compute.call_args_list}
        assert calls == {
            "2026-02-02": [("AEO", 1)],
            "2026-02-03": [("UAL", 1)],
        }
        assert count == 2

    @patch("src.algo_detective.label_sync.record_scraped_post")
    @patch("src.algo_detective.label_sync.compute_and_store_for_date")
    @patch("src.algo_detective.label_sync.get_computed_pairs")
    @patch("src.algo_detective.label_sync.fetch_recap_trades")
    @patch("src.algo_detective.label_sync.fetch_post_index")
    @patch("src.algo_detective.label_sync.get_scraped_slugs")
    def test_checkpoints_slug_with_trade_count_after_processing(
        self, mock_known, mock_index, mock_trades, mock_pairs, mock_compute, mock_record,
    ):
        mock_known.return_value = set()
        mock_index.return_value = ["results_boring_puts_2025_09_01"]
        mock_trades.return_value = []  # PDF-era post, no table
        mock_pairs.return_value = set()
        mock_compute.return_value = []

        sync_new_labels()

        mock_record.assert_called_once_with("results_boring_puts_2025_09_01", trades_found=0)

    @patch("src.algo_detective.label_sync.record_scraped_post")
    @patch("src.algo_detective.label_sync.compute_and_store_for_date")
    @patch("src.algo_detective.label_sync.get_computed_pairs")
    @patch("src.algo_detective.label_sync.fetch_recap_trades")
    @patch("src.algo_detective.label_sync.fetch_post_index")
    @patch("src.algo_detective.label_sync.get_scraped_slugs")
    def test_does_not_checkpoint_slug_when_parsing_raises(
        self, mock_known, mock_index, mock_trades, mock_pairs, mock_compute, mock_record,
    ):
        mock_known.return_value = set()
        mock_index.return_value = ["results_boring_puts_2026_07_13"]
        mock_trades.side_effect = RuntimeError("mLabs changed their HTML structure")
        mock_pairs.return_value = set()

        count = sync_new_labels()

        mock_record.assert_not_called()
        assert count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_label_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo_detective.label_sync'`

- [ ] **Step 3: Implement `sync_new_labels`**

Create `src/algo_detective/label_sync.py`:

```python
"""Step 6 of the nightly algo_detective pipeline: discovers new mLabs
recap posts, parses them into (ticker, open_date) pairs (real trades =
authoritative is_prime=1 ground truth, replacing manual Reddit
transcription), computes features, and upserts.

Must run before control_sync.py's sync_control_universe() in the same
pipeline pass, so a freshly discovered prime label is never overwritten
back to a control row. See
docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from ..backtester.data_provider import get_historical_data
from .build import compute_and_store_for_date
from .mlabs_scraper import fetch_post_index, fetch_recap_trades
from .store import get_computed_pairs, get_scraped_slugs, record_scraped_post

logger = logging.getLogger(__name__)


def _ohlcv_fallback(ticker: str):
    """Last-resort OHLCV source for a prime ticker outside the tracked
    universe — reuses the backtester's on-demand fetch+cache."""
    df = get_historical_data(symbol=ticker)
    return df if not df.empty else None


def sync_new_labels() -> int:
    """Scrape any mLabs recap post not yet in the checkpoint table, and
    upsert its CSP trades as is_prime=1 rows. Returns count of rows
    written across all newly-processed posts."""
    known = get_scraped_slugs()
    new_slugs = [s for s in fetch_post_index() if s not in known]

    total_written = 0
    for slug in new_slugs:
        try:
            trades = fetch_recap_trades(slug)
        except Exception:
            logger.warning("Failed to parse recap post %s, will retry next run", slug, exc_info=True)
            continue

        by_date: dict[str, list[str]] = defaultdict(list)
        for trade in trades:
            by_date[trade["open_date"]].append(trade["ticker"])

        computed_pairs = get_computed_pairs()
        for date, tickers in by_date.items():
            # de-dupe tickers within the same date (e.g. two lots of the same name)
            ticker_flags = [(t, 1) for t in sorted(set(tickers))]
            rows = compute_and_store_for_date(
                date, ticker_flags, computed_pairs, ohlcv_fallback_fn=_ohlcv_fallback,
            )
            total_written += len(rows)

        record_scraped_post(slug, trades_found=len(trades))
        logger.info("Processed %s: %d CSP trades found", slug, len(trades))

    return total_written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_label_sync.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/label_sync.py tests/test_algo_detective_label_sync.py
git commit -m "feat(algo-detective): add mLabs label sync"
```

---

### Task 6: Wire into `main.py` — fix Step 5, add Steps 6 & 7

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main_pipeline_algo_detective_steps.py`

**Interfaces:**
- Consumes: `ensure_tables` from `algo_detective.store`; `sync_new_labels` (Task 5); `sync_control_universe` (Task 4).

- [ ] **Step 1: Write the failing test**

Create `tests/test_main_pipeline_algo_detective_steps.py`:

```python
"""Tests for the algo_detective steps (5, 6, 7) wired into src/main.py's
nightly pipeline: Step 5's ensure_tables() fix, and the new Step 6 (mLabs
label sync) / Step 7 (control universe sync), in that order, both
non-fatal on failure.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_step5_calls_ensure_tables_before_get_all_features():
    call_order = []
    with patch(
        "src.algo_detective.store.ensure_tables",
        side_effect=lambda: call_order.append("ensure_tables"),
    ) as mock_ensure, \
         patch(
            "src.algo_detective.store.get_all_features",
            side_effect=lambda: call_order.append("get_all_features") or [],
        ) as mock_get, \
         patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0), \
         patch("src.algo_detective.label_sync.sync_new_labels", return_value=0), \
         patch("src.algo_detective.control_sync.sync_control_universe", return_value=0):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())

        mock_ensure.assert_called_once()
        mock_get.assert_called_once()
        assert call_order == ["ensure_tables", "get_all_features"]


@pytest.mark.asyncio
async def test_step6_runs_before_step7():
    call_order = []
    with patch("src.algo_detective.store.ensure_tables"), \
         patch("src.algo_detective.store.get_all_features", return_value=[]), \
         patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0), \
         patch(
            "src.algo_detective.label_sync.sync_new_labels",
            side_effect=lambda: call_order.append("label_sync") or 0,
        ), \
         patch(
            "src.algo_detective.control_sync.sync_control_universe",
            side_effect=lambda d: call_order.append("control_sync") or 0,
        ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())

        assert call_order == ["label_sync", "control_sync"]


@pytest.mark.asyncio
async def test_step6_failure_does_not_block_step7():
    with patch("src.algo_detective.store.ensure_tables"), \
         patch("src.algo_detective.store.get_all_features", return_value=[]), \
         patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0), \
         patch("src.algo_detective.label_sync.sync_new_labels", side_effect=RuntimeError("boom")), \
         patch("src.algo_detective.control_sync.sync_control_universe", return_value=0) as mock_control:
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_control.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_main_pipeline_algo_detective_steps.py -v`
Expected: FAIL — `ImportError: cannot import name '_run_algo_detective_steps'`

- [ ] **Step 3: Refactor `main.py`'s Step 5 into a helper, fix the bug, add Steps 6 & 7**

In `src/main.py`, replace the existing Step 5 block (currently starting `# ── Step 5: Algo-detective options snapshot ──────────────` and ending after the `logger.warning("Algo-detective options snapshot failed (non-fatal): %s", _exc)` line, per the code shown in the Background section of the spec) with a call to a new extracted async helper, and define that helper plus Steps 6 and 7:

```python
        await _run_algo_detective_steps(today)

        logger.info("✅ Pipeline complete!")
```

(This replaces the old inline Step 5 block — the `logger.info("✅ Pipeline complete!")` line already exists immediately after it and stays as-is.)

Add the new helper function to `src/main.py`, near the other step-level helpers (or directly above `run_pipeline`/wherever the existing Step 5 code lived):

```python
async def _run_algo_detective_steps(today) -> None:
    """Steps 5-7 of the nightly pipeline: sync mLabs trade labels, sync
    control-universe features, then refresh live options IV snapshots for
    the (now up to date) prime-ticker whitelist. All three are non-fatal —
    a failure in one logs and lets the rest of the pipeline continue."""
    from .algo_detective.store import ensure_tables

    ensure_tables()

    logger.info("Step 6/7: Syncing mLabs trade labels...")
    try:
        from .algo_detective.label_sync import sync_new_labels

        written = await asyncio.to_thread(sync_new_labels)
        logger.info("Label sync: %d new prime rows written", written)
    except Exception as _exc:
        logger.warning("Algo-detective label sync failed (non-fatal): %s", _exc)

    logger.info("Step 7/7: Syncing control-universe features...")
    try:
        from .algo_detective.control_sync import sync_control_universe

        written = await asyncio.to_thread(sync_control_universe, today.isoformat())
        logger.info("Control sync: %d rows written", written)
    except Exception as _exc:
        logger.warning("Algo-detective control sync failed (non-fatal): %s", _exc)

    logger.info("Step 5/7: Collecting algo-detective options snapshot...")
    try:
        from .algo_detective.options_chain import fetch_snapshot_pcr
        from .algo_detective.store import get_all_features as _get_detective_features

        _features = await asyncio.to_thread(_get_detective_features)
        _prime = sorted({f["ticker"] for f in _features if f["is_prime"] == 1})
        if _prime:
            stored = await asyncio.to_thread(fetch_snapshot_pcr, _prime, today.isoformat())
            logger.info("Options snapshot: %d rows stored for %d prime tickers", stored, len(_prime))
    except Exception as _exc:
        logger.warning("Algo-detective options snapshot failed (non-fatal): %s", _exc)
```

Note the step numbering in the log lines is reordered (label sync and control sync now run first, options snapshot last, since the options step depends on the prime-ticker whitelist that label sync just updated) but kept as "Step N/7" text purely for operator log-reading continuity with the existing convention — it is not used programmatically anywhere.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_main_pipeline_algo_detective_steps.py -v`
Expected: PASS (3 passed)

Also re-run the pre-existing pipeline tests for regressions:
Run: `docker compose run --rm test python3 -m pytest tests/test_trading_calendar.py -v`
Expected: same result as baseline (these 3 tests were already failing before this task, per the pre-existing `no attribute 'sys'` issue unrelated to this change — confirm the count/identity of failures is unchanged, not newly broken by this edit).

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_main_pipeline_algo_detective_steps.py
git commit -m "fix(main): fix Step 5 crash-loop, wire in mLabs label sync and control sync"
```

---

### Task 7: Two-phase backfill CLI

**Files:**
- Create: `src/algo_detective/backfill_mlabs.py`
- Test: `tests/test_algo_detective_backfill_mlabs.py`

**Interfaces:**
- Consumes: `fetch_post_index`, `fetch_recap_trades` (Task 2); `compute_and_store_for_date` (Task 3); `sync_control_universe` (Task 4); `_ohlcv_fallback` (Task 5, reused so backfill handles tickers outside the tracked universe the same way the nightly sync does); `get_scraped_slugs`, `record_scraped_post`, `get_computed_pairs` (Tasks 1, 3).
- Produces: `run_backfill() -> dict` with keys `prime_rows_written, dates_backfilled, control_rows_written`; a `python -m src.algo_detective.backfill_mlabs` CLI entry point.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_backfill_mlabs.py`:

```python
"""Tests for the two-phase mLabs backfill CLI in
src/algo_detective/backfill_mlabs.py. Phase 1 scrapes every historical
results_boring_puts_* post (idempotent — safe to re-run) into is_prime=1
rows; Phase 2 runs a historical control-universe sync for every distinct
date Phase 1 touched.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

from src.algo_detective.backfill_mlabs import run_backfill


class TestRunBackfill:
    @patch("src.algo_detective.backfill_mlabs.sync_control_universe")
    @patch("src.algo_detective.backfill_mlabs.record_scraped_post")
    @patch("src.algo_detective.backfill_mlabs.compute_and_store_for_date")
    @patch("src.algo_detective.backfill_mlabs.get_computed_pairs")
    @patch("src.algo_detective.backfill_mlabs.fetch_recap_trades")
    @patch("src.algo_detective.backfill_mlabs.fetch_post_index")
    def test_phase1_processes_every_slug_regardless_of_checkpoint(
        self, mock_index, mock_trades, mock_pairs, mock_compute, mock_record, mock_control,
    ):
        mock_index.return_value = [
            "results_boring_puts_2025_09_01", "results_boring_puts_2026_01_05",
        ]
        mock_trades.side_effect = [
            [],  # PDF-era post
            [{"ticker": "NVO", "open_date": "2026-01-05"}],
        ]
        mock_pairs.return_value = set()
        mock_compute.return_value = [{"ticker": "NVO", "date": "2026-01-05", "is_prime": 1}]
        mock_control.return_value = 40

        result = run_backfill()

        assert mock_trades.call_count == 2
        assert mock_record.call_count == 2
        assert result["prime_rows_written"] == 1

    @patch("src.algo_detective.backfill_mlabs.sync_control_universe")
    @patch("src.algo_detective.backfill_mlabs.record_scraped_post")
    @patch("src.algo_detective.backfill_mlabs.compute_and_store_for_date")
    @patch("src.algo_detective.backfill_mlabs.get_computed_pairs")
    @patch("src.algo_detective.backfill_mlabs.fetch_recap_trades")
    @patch("src.algo_detective.backfill_mlabs.fetch_post_index")
    def test_phase2_runs_control_sync_only_for_dates_phase1_touched(
        self, mock_index, mock_trades, mock_pairs, mock_compute, mock_record, mock_control,
    ):
        mock_index.return_value = ["results_boring_puts_2026_01_05"]
        mock_trades.return_value = [
            {"ticker": "NVO", "open_date": "2026-01-05"},
            {"ticker": "AAPL", "open_date": "2026-01-07"},
        ]
        mock_pairs.return_value = set()
        mock_compute.side_effect = lambda date, flags, pairs, ohlcv_fallback_fn=None: [
            {"ticker": t, "date": date, "is_prime": f} for t, f in flags
        ]
        mock_control.return_value = 0

        run_backfill()

        control_dates = {c.args[0] for c in mock_control.call_args_list}
        assert control_dates == {"2026-01-05", "2026-01-07"}

    @patch("src.algo_detective.backfill_mlabs.sync_control_universe")
    @patch("src.algo_detective.backfill_mlabs.record_scraped_post")
    @patch("src.algo_detective.backfill_mlabs.compute_and_store_for_date")
    @patch("src.algo_detective.backfill_mlabs.get_computed_pairs")
    @patch("src.algo_detective.backfill_mlabs.fetch_recap_trades")
    @patch("src.algo_detective.backfill_mlabs.fetch_post_index")
    def test_returns_summary_dict(
        self, mock_index, mock_trades, mock_pairs, mock_compute, mock_record, mock_control,
    ):
        mock_index.return_value = []
        mock_control.return_value = 0

        result = run_backfill()

        assert set(result) == {"prime_rows_written", "dates_backfilled", "control_rows_written"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_backfill_mlabs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo_detective.backfill_mlabs'`

- [ ] **Step 3: Implement the backfill CLI**

Create `src/algo_detective/backfill_mlabs.py`:

```python
"""One-time (but safely re-runnable) backfill of mLabs recap trade labels
and control-universe features across the full scrapeable history.

Phase 1 scrapes every results_boring_puts_* post found on the index
(regardless of the checkpoint table — unlike the nightly sync_new_labels,
this re-processes everything, which is safe since upserts are
idempotent) into is_prime=1 rows, collecting the distinct dates touched.
Phase 2 runs a historical control-universe sync for each of those dates,
so every prime date also gets a full control-universe comparison set.

Run: docker compose --profile pipeline run --rm pipeline python3 -m src.algo_detective.backfill_mlabs
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

import logging
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

from .build import compute_and_store_for_date
from .control_sync import sync_control_universe
from .label_sync import _ohlcv_fallback
from .mlabs_scraper import fetch_post_index, fetch_recap_trades
from .store import get_computed_pairs, record_scraped_post


def run_backfill() -> dict:
    """Backfill every scrapeable mLabs recap post (Phase 1) and the
    control universe for every date it touches (Phase 2). Returns
    {"prime_rows_written": int, "dates_backfilled": int, "control_rows_written": int}."""
    slugs = fetch_post_index()
    logger.info("Phase 1: backfilling %d recap posts", len(slugs))

    prime_rows_written = 0
    dates_touched: set[str] = set()

    for slug in slugs:
        try:
            trades = fetch_recap_trades(slug)
        except Exception:
            logger.warning("Failed to parse %s during backfill, skipping", slug, exc_info=True)
            continue

        by_date: dict[str, list[str]] = defaultdict(list)
        for trade in trades:
            by_date[trade["open_date"]].append(trade["ticker"])

        computed_pairs = get_computed_pairs()
        for date, tickers in by_date.items():
            ticker_flags = [(t, 1) for t in sorted(set(tickers))]
            rows = compute_and_store_for_date(
                date, ticker_flags, computed_pairs, ohlcv_fallback_fn=_ohlcv_fallback,
            )
            prime_rows_written += len(rows)
            dates_touched.add(date)

        record_scraped_post(slug, trades_found=len(trades))

    logger.info(
        "Phase 1 complete: %d prime rows written across %d distinct dates",
        prime_rows_written, len(dates_touched),
    )

    logger.info("Phase 2: backfilling control universe for %d dates", len(dates_touched))
    control_rows_written = 0
    for date in sorted(dates_touched):
        control_rows_written += sync_control_universe(date)

    logger.info("Phase 2 complete: %d control rows written", control_rows_written)

    return {
        "prime_rows_written": prime_rows_written,
        "dates_backfilled": len(dates_touched),
        "control_rows_written": control_rows_written,
    }


if __name__ == "__main__":
    summary = run_backfill()
    print(f"\nBackfill complete: {summary}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_backfill_mlabs.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/backfill_mlabs.py tests/test_algo_detective_backfill_mlabs.py
git commit -m "feat(algo-detective): add two-phase mLabs backfill CLI"
```

---

## Post-Implementation

Run the full existing suite once more to confirm no regressions anywhere else in the repo:

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -v`
Expected: PASS on all pre-existing tests plus the new tests from Tasks 1-7; the same pre-existing, unrelated failures as before this plan (`test_algo_detective_options_chain`, `test_csp_scanner_integration` x2, `test_trading_calendar` x3) — no new failures.

Run the actual backfill against real data (requires the dev stack up, see `CLAUDE.md`) — this is a real, one-time data-mutating operation against production research data, run it deliberately, not as part of automated testing:

```bash
docker compose --profile pipeline run --rm pipeline python3 -m src.algo_detective.backfill_mlabs
```

Then confirm `detective_options` (via Step 5, now unblocked) starts collecting again on the next scheduled nightly pipeline run — check via Loki the following day:

```bash
curl -s -G "http://10.0.1.25:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={service_name=~"market-intelligence-pipeline.*"} |= "Options snapshot:"' \
  --data-urlencode "start=$(($(date +%s)-86400))000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  --data-urlencode "limit=20"
```
Expected: a log line like `Options snapshot: N rows stored for M prime tickers` with no accompanying `no such table` warning.
