# Pre-Existing Test Failures — Investigation Handoff

**Date:** 2026-07-30
**Branch:** `task-1-screener-fields` (all 5 perf/correctness fixes from this session are merged in — see `docs/superpowers/plans/2026-07-30-*.md` for what changed)
**Status:** Not started — this doc is a handoff for the next session, no fix attempted yet
**How to reproduce:** `docker compose build test && docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -q` (this repo has no local venv — bare `python -m pytest` fails, missing deps; also note `docker compose run --rm test` does **not** rebuild on source changes, always `docker compose build test` first after editing)

## Background

While implementing 5 unrelated perf/correctness fixes today (CSP-scan technical-indicator dedup, screener cache-stampede locking, IV-backfill circuit breaker, scan-trigger in-progress guard, and yfinance fetcher concurrency/blocking fixes — all now merged into `task-1-screener-fields`), the same **6 test failures** showed up on every single one of the 5 feature branches, independently confirmed against the unmodified base commit (`9e0b886`, the tip of `task-1-screener-fields` before today's work). They are pre-existing and unrelated to anything merged today — flagging them now rather than letting them keep silently failing.

Current full-suite result on `task-1-screener-fields` (with today's merges): **6 failed, 592 passed**.

These are 4 distinct root causes across 6 test cases. Root causes below were verified by reading the actual code and test source — not guessed.

---

## 1. `test_trading_calendar.py` — 3 failures — stale monkeypatch target

**Tests:**
- `test_main_skips_run_pipeline_on_non_trading_day[argv_mode0]`
- `test_main_skips_run_pipeline_on_non_trading_day[argv_mode1]`
- `test_main_runs_on_demand_even_on_non_trading_day`

**Error:** `AttributeError: module 'src.main' has no attribute 'sys'`

**Root cause:** The tests do `monkeypatch.setattr(main_module.sys, "argv", [...])` (`tests/test_trading_calendar.py:31,43`), assuming `src/main.py` has a module-level `import sys` it uses to read argv. It doesn't — `src/main.py:422-437`'s `main()` calls `argparse.ArgumentParser().parse_args()` with no explicit `args=` list, which pulls from the real process `sys.argv` via argparse's own internal `import sys`, not any `src.main.sys` reference. `src/main.py` only imports `argparse, asyncio, json, logging, datetime, . db, .cache, .config, .fetchers.*` — no bare `import sys`.

**Likely fix (not yet done — pick one, don't just guess, check which the rest of the test file/codebase conventions favor first):**
- Have the test monkeypatch the real `sys.argv` directly (`monkeypatch.setattr("sys.argv", [...])`) instead of `main_module.sys.argv` — simplest, no production code change.
- Or add `import sys` to `src/main.py` and change `main()` to `parser.parse_args(sys.argv[1:])` explicitly — makes the module testable the way the test currently assumes, but touches production code for a test-only concern.

---

## 2. `test_algo_detective_options_chain.py::TestFetchSnapshotPcr::test_stores_rows_for_each_ticker` — mock URL mismatch

**Error:** `assert None == 3.0` (the PCR value is never stored) — with a captured warning: `Alpaca snapshots failed for AAPL: RESPX: <Request('GET', 'https://data.alpaca.markets/v1beta1/options/snapshots/AAPL?feed=indicative&limit=1000')> not mocked!`

**Root cause:** The test mocks `respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots")` — no ticker path segment. The real code (`src/algo_detective/options_chain.py`, wherever it builds this request) requests `.../v1beta1/options/snapshots/{TICKER}?feed=indicative&limit=1000` — ticker as a path segment, plus query params. The mock's URL doesn't match, respx refuses the request, the fetch fails per-ticker, and `fetch_snapshot_pcr` silently continues (returns `stored == 2`, i.e. it "succeeded" at the wrong thing — storing failure/skip rows, not real PCR data), so the later `index[("2026-06-18", "AAPL")]["pcr_vol"]` lookup finds nothing.

**Likely fix:** Update the respx mock in the test to match the real URL shape (per-ticker path segment + query params), OR this could indicate the endpoint format changed at some point and other tests/call sites should be checked for the same drift. Read `src/algo_detective/options_chain.py`'s actual request-building code first — don't assume the test is simply wrong without confirming which side is "correct" per the live Alpaca API.

---

## 3 & 4. `test_csp_scanner_integration.py` — 2 failures — both trace back to the same class of bug: **`apply_fundamental_filter`'s per-ticker live-yfinance fallback for store misses**

### 3. `TestApplyFundamentalFilterUsesStore::test_ticker_not_in_store_is_skipped`

**Error:** `AssertionError: Expected 'Ticker' to not have been called. Called 1 times. Calls: [call('UNKNOWN_XYZ'), ...]`

**Root cause:** `apply_fundamental_filter` (`src/screener/csp_scanner.py:445-479`) does this when the local store has ≥50 rows (i.e. is considered "populated," not empty):
```python
store_tickers   = [t for t in tickers if t in store_lookup]
missing_tickers = [t for t in tickers if t not in store_lookup]
...
if missing_tickers:
    miss_pass, miss_rows = _fundamental_filter_from_yfinance(missing_tickers, params)  # live per-ticker yfinance call
```
This is a **deliberate per-ticker fallback**, not a bug in the sense of unintended behavior — but the test's docstring says "A ticker absent from the store is simply skipped (no fallback to yfinance)," which directly contradicts what the code does. One of these is wrong:
- Either the test predates this fallback being added (stale expectation — the fallback was intentionally added later for coverage and the test needs updating), or
- The fallback is an unwanted regression and tickers missing from the store really should just be skipped (silently reducing the universe) rather than triggering a live per-ticker yfinance call on the scan's hot path — this is architecturally the same class of problem as the "CSP scan recomputes technicals twice" issue fixed earlier today (see `docs/superpowers/plans/2026-07-30-csp-scan-dedupe-technicals.md`), so if this fallback is happening broadly (not just for one-off unknown tickers), it could be a live, unflagged performance issue worth scoping.

**Recommend:** `git log -p --follow src/screener/csp_scanner.py` (or `git blame`) around `apply_fundamental_filter`/`_fundamental_filter_from_yfinance` to find when/why the per-ticker fallback was added, and check whether Austin actually wants it. Don't just "fix the test to match the code" without confirming the code's behavior is intentional.

### 4. `TestRunCspScanDataSource::test_no_http_requests_during_scan`

**Error:** Same `Ticker` mock, but called 21 times for real tickers — `AAPL, AMD, AMZN, DIA, GOOGL, IWM, JPM, MA, META, MSFT, NFLX, NVDA, QQQ, SOFI, SPY, TSLA, V, XLE, XLF, XLK, XLV`.

**Root cause — confirmed, this one's a genuine test-isolation gap:** `run_csp_scan` (`src/screener/csp_scanner.py:1063-1071`) does:
```python
universe = fetch_universe()          # mocked in this test → returns synthetic _TEST_TICKERS (T000..T059)
watchlist = get_watchlist()          # NOT mocked, NOT isolated
watchlist_extras = [t for t in watchlist if t not in set(universe)]
if watchlist_extras:
    universe = sorted(set(universe) | set(watchlist_extras))
```
`get_watchlist()` lives in `src/db.py` and reads from `settings.db_path`. But `tests/test_csp_scanner_integration.py`'s autouse fixture (`tests/test_csp_scanner_integration.py:26-30`) only patches `src.market_data.store.settings.db_path` — a **different module's** settings reference. `src.db`'s own `settings.db_path` is never patched in this test file, so `get_watchlist()` falls through to its hardcoded default watchlist (`src/db.py` — the exact 21 tickers seen in the failure, in the exact same order) instead of any test fixture. Those real tickers then get merged into the scan universe, aren't present in the (correctly isolated) local fundamentals store, and trigger the same per-ticker yfinance fallback described in bug #3 above — which is what the test is actually trying to assert never happens.

**Fix (this one's unambiguous, unlike #3):** The `_patch_db_path` autouse fixture in `tests/test_csp_scanner_integration.py` needs to also patch `src.db.settings.db_path` (or patch `src.db.get_watchlist` directly to return a controlled list) so `get_watchlist()` is properly test-isolated like every other store access in this file already is. This is a one-line-ish test fix, not a production code change — worth doing first since it's low-risk and unblocks accurately testing bug #3 in isolation too.

---

## Suggested order of attack for the next session

1. **Bug #4 first** (`get_watchlist()` not isolated) — it's the clearest root cause, purely a test fixture gap, and fixing it may be a prerequisite for cleanly investigating bug #3 (right now bug #3's symptom is entangled with #4's leakage in the full-suite run, though `test_ticker_not_in_store_is_skipped` fails independently too since it calls `apply_fundamental_filter` directly).
2. **Bug #3** — needs a product decision (is the per-ticker yfinance fallback wanted or not?) before writing the fix — check git history / ask Austin if git history doesn't make it obvious.
3. **Bug #1** (`test_trading_calendar.py` sys monkeypatch) — mechanical, low-risk, pick one of the two fix approaches above.
4. **Bug #2** (Alpaca snapshot mock URL) — needs a look at `src/algo_detective/options_chain.py`'s actual request code to confirm the correct URL shape before updating the mock.

None of these block anything merged today — they're independent, pre-existing gaps.
