# Algo Detective Automated Feature & Label Pipeline — Design Spec

**Date:** 2026-07-19

## Background

The `algo_detective` subsystem (`src/algo_detective/`) reverse-engineers a Reddit/blog trader's ("GarbageTimePro", posts as mLabs Trading, `blog.mlabstrading.com`) CSP-wheel scanner criteria by comparing technical/fundamental features on dates he traded a ticker (`is_prime=1`, ground truth) against dates he didn't (`is_prime=0`, control) across a large ticker universe.

While building a signal P&L backtest tool for these criteria (`docs/superpowers/plans/2026-07-18-algo-detective-signal-backtest.md`), we discovered:

1. **`detective_features`** (technicals/fundamentals + `is_prime` label, per ticker+date) is populated *only* by manually running one-off `sessionNN.py` research scripts. No automated process ever calls its writer, `upsert_feature_rows_bulk()`. Coverage today: 2025-09-09 → 2026-06-16, 72 scan dates, 1704 tickers total, but only 84 ever labeled `is_prime=1` (327 prime rows).
2. **`detective_options`** (real per-ticker IV/PCR, used as the preferred signal-backtest IV source over the RV20 proxy) has been frozen at 2025-12-08 for 7+ months. Root cause: `main.py`'s nightly "Step 5: algo-detective options snapshot" calls `get_all_features()` (a bare `SELECT * FROM detective_features`, no `ensure_tables()` guard) *before* checking for a prime-ticker whitelist — and on the pipeline's actual runtime host, that table apparently doesn't exist. The failure is silently swallowed by a non-fatal `except` in `main.py`, so it's been crash-looping nightly with zero alerting (confirmed via Loki: `no such table: detective_features`, at least 2026-07-10 → 2026-07-18, likely longer).
3. **The `is_prime` label itself has never been automatable** via the previously-known source (manually reading Reddit posts and hand-transcribing into `data/detective/prime_tickers.csv`) — until we found that `blog.mlabstrading.com` publishes structured weekly recap posts (`/posts/results_boring_puts_YYYY_MM_DD`) containing an actual HTML table of his opening trades (ticker, open date, strike, fill price, P&L, etc.) — real structured ground truth, not free text.

Net effect: nothing in `detective_features`/`detective_options` grows on its own today, which is why any out-of-sample/walk-forward validation of a candidate scanner gate (see the signal-backtest tool above) is currently working with a statistically thin, frozen dataset.

## Goal

Make both halves of `detective_features` grow automatically, going forward, without manual intervention:
- **Labels** (`is_prime=1`): scrape mLabs' weekly recap posts for his actual trades.
- **Features** (both prime and control rows): compute technicals/fundamentals for the tracked universe automatically, reusing the existing `compute_features()`/feature-computation logic rather than rebuilding it.

Plus: fix the concrete Step 5 crash-loop bug blocking `detective_options` collection, and backfill both labels and control-universe features across the full historical mLabs recap archive (not just going forward), so the signal-backtest tool has enough spread of history for a meaningful walk-forward split.

## Non-Goals

- Scraping mLabs' *watchlist* posts (`boring_puts_watchlist_*`) for auxiliary fields (delta, IV, `pop_pct`, `mlabs_score`, etc.). `compute_features()` already independently derives the technical fields; those extra columns in the current CSV were a manual cross-check aid, not a hard requirement. Out of scope for this plan.
- Building alerting/monitoring for step failures. Step 5's bug went unnoticed for 7 months specifically because `WARNING`-level per-container logs have no aggregation. This plan keeps the same non-fatal-log-and-continue convention every other pipeline step uses; better visibility is a real gap but a separate concern.
- A human review/approval queue for scraped labels. Recap-post data is treated as authoritative (his own audited trade log) — see "Error Handling" for how parsing failures are handled instead of adding a review step.
- Historical backfill of *control*-universe features for dates outside what the mLabs recap backfill surfaces. Only dates that end up with at least one `is_prime=1` row get a historical control-universe pass; we are not back-computing the full universe for every trading day since 2025.

## Architecture

Two new steps added to `main.py`'s existing nightly pipeline, after the current Step 5 (which gets a one-line fix), plus a two-phase one-time backfill CLI:

```
Step 5 (existing, fixed): ensure_tables() called before get_all_features()
Step 6 (new): mLabs label sync — scrape → parse → upsert is_prime=1 rows
Step 7 (new): control universe feature sync — compute + upsert is_prime=0 rows for today
```

Step 6 always runs before Step 7 in the same nightly pass. Step 7 excludes anything Step 6 already labeled `is_prime=1` today, so a freshly-discovered prime ticker is never overwritten back to a control row in the same run. Both steps follow the existing convention: wrapped in a non-fatal try/except, logged, pipeline continues either way.

Step 6 is nightly but checkpoint-based — mLabs posts a recap roughly weekly, so most nightly runs find nothing new and no-op cheaply (one HTTP request to the post index, no table scrape).

## Components

### `src/algo_detective/mlabs_scraper.py` (new)

Pure HTTP + HTML parsing, no DB access — same "clean, independently testable" shape as `features.py::compute_features()`.

- `fetch_post_index() -> list[str]` — fetches `https://blog.mlabstrading.com/posts`, returns every `results_boring_puts_*` slug found.
- `fetch_recap_trades(slug: str) -> list[dict]` — fetches `https://blog.mlabstrading.com/posts/{slug}`, parses the "This Week's Opening Trades" HTML table, returns `[{"ticker": str, "open_date": "YYYY-MM-DD"}, ...]`. Only `ticker` + `open_date` are extracted (see Non-Goals) — the table also has strike/fill/exit/P&L/ROC columns, ignored for now.

Uses `httpx` + `lxml` (both already project dependencies — no new deps needed).

**Implementation note (not yet verified against raw HTML):** the design above is based on an AI-summarized fetch of the two example URLs the user provided, not raw HTML inspection. The first implementation task must fetch the actual HTML (`httpx.get(...)`, not an LLM-summarizing tool) and save 1-2 real pages as test fixtures before writing the parser, to get exact selectors right and confirm the page is server-rendered (not a JS-only SPA — the evidence so far, a plain fetch returning full table content and a multi-month post listing, suggests it is, but this needs confirming against raw response bytes, not a markdown-converted summary).

### `src/algo_detective/label_sync.py` (new)

- `sync_new_labels() -> int` — Step 6's orchestrator. Reads `detective_scraped_posts` for already-processed slugs, diffs against `fetch_post_index()`, and for each new slug: parses trades, resolves OHLCV per ticker (look up the ticker in that date's already-tracked `universe_daily_ohlcv` data first — via `universe.load_ohlcv_batch_for_date`, checking whether the specific ticker is present in the returned batch — falling back to `backtester.data_provider.get_historical_data()`'s on-demand fetch+cache for any ticker not present there, i.e. outside the tracked S&P/Nasdaq/NYSE-large-cap universe — GTPro has traded sub-$25B names before), calls `features.compute_features()`, upserts via `store.upsert_feature_rows_bulk(..., is_prime=1)`. Records the slug in `detective_scraped_posts` **only on successful parse** (see Error Handling). Returns count of new prime rows written.

### `src/algo_detective/control_sync.py` (new)

- `sync_control_universe(date: str) -> int` — Step 7's orchestrator (and reused by backfill Phase 2). Pulls `universe.get_control_tickers(date, exclude=todays_primes)`, skips any `(date, ticker)` already in `store.get_computed_pairs()`, computes + upserts the rest as `is_prime=0`. `todays_primes` = `SELECT ticker FROM detective_features WHERE date=? AND is_prime=1`.

**Refactor note:** `build.py::run_build()`'s existing per-date loop already does "prime ∪ control tickers, skip already-computed, fetch fundamentals/OHLCV/macro, compute, upsert" — exactly this logic, just CSV-driven. Extract that loop body into a shared helper (e.g. `build.py::compute_and_store_for_date(date, prime_tickers, computed_pairs) -> int`) that both the existing CSV-driven `run_build()` and the new `label_sync`/`control_sync` modules call, instead of duplicating feature-computation orchestration. This is a targeted improvement to code this plan already touches, not a speculative refactor.

### New table: `detective_scraped_posts`

```sql
CREATE TABLE IF NOT EXISTS detective_scraped_posts (
    slug         TEXT PRIMARY KEY,
    scraped_at   TEXT NOT NULL,
    trades_found INTEGER NOT NULL
);
```

Added via the existing `_DDL` string + `ensure_tables()` pattern in `store.py`.

### `src/algo_detective/backfill_mlabs.py` (new, CLI)

Two-phase, idempotent, safely re-runnable:

```
Phase 1 (prime):   for every results_boring_puts_* slug (not just new ones):
                     same parse → compute → upsert(is_prime=1) → checkpoint as label_sync
                   collect the distinct set of dates touched
Phase 2 (control): for each distinct date from Phase 1:
                     sync_control_universe(date)
```

Phase 2 reuses `control_sync.sync_control_universe()` unchanged — it already takes a `date` parameter, so backfill just loops it over historical dates instead of "today."

### `main.py` changes

- Fix: call `algo_detective.store.ensure_tables()` once, before the existing Step 5 body.
- Add Step 6 (`label_sync.sync_new_labels()`) and Step 7 (`control_sync.sync_control_universe(today)`), each in the same non-fatal try/except style as Step 5, Step 6 before Step 7.

## Data Flow

**Backfill (one-time, run manually via CLI — not wired into the nightly pipeline):**
```
fetch_post_index() → for each slug:
  fetch_recap_trades(slug) → [{ticker, open_date}, ...]
  for each pair: resolve OHLCV → compute_features() → upsert(is_prime=1)
  record slug in detective_scraped_posts
→ collect distinct dates touched
→ for each date: sync_control_universe(date)
```

**Nightly steady state:**
```
Step 6: known = SELECT slug FROM detective_scraped_posts
        new = fetch_post_index() - known
        for slug in new: parse → compute → upsert(is_prime=1) → checkpoint
Step 7: sync_control_universe(today)
```

## Error Handling

- **Parser breaks on an mLabs HTML structure change**: caught per-post inside `label_sync`, logged at `WARNING` with the slug and exception. The slug is **not** recorded in `detective_scraped_posts`, so it's retried automatically every night — no backoff, no dead-lettering. Matches the codebase's existing simplicity convention; nightly retries of a cheap HTTP fetch cost nothing.
- **Site unreachable / network error**: same non-fatal, logged, retried-next-night treatment as any other fetcher in the pipeline (e.g. `data_provider.get_historical_data`'s yfinance calls).
- **Ticker's OHLCV unresolvable** (delisted, foreign listing, etc.): `compute_features()` already returns `None` for this case (per `features.py`) — skip that specific `(ticker, date)` row, log which one and why, continue with the rest of the post/date.
- **Step 5's specific bug**: fixed by the `ensure_tables()` call described above — this is the one concrete, currently-broken thing this plan fixes outright, not just works around.

## Testing

- **Parser unit tests** against saved HTML fixtures (one for a recap-trades table, one for the post-index listing) — no live network calls, following the existing `respx`-mocked pattern already used in `test_algo_detective_options_chain.py`.
- **Checkpoint idempotency**: running `sync_new_labels()` twice in a row doesn't reprocess an already-checkpointed slug or duplicate rows.
- **Step 6 → Step 7 ordering**: a prime label written by Step 6 survives an immediately-following Step 7 run untouched (regression test for the exact "silent label downgrade" risk identified during design).
- **Control sync**: skips `(date, ticker)` pairs already in `get_computed_pairs()`; computes and upserts missing ones as `is_prime=0`.
- **Backfill CLI**: smoke test against fixtures (not live scraping), verifying both phases run and Phase 2 only touches dates Phase 1 actually surfaced.
- **Full suite regression**: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -q` must stay at the current baseline (471 passed / 6 pre-existing unrelated failures) plus whatever new tests this plan adds — no new failures.

## Global Constraints

- Python 3.12, no local virtualenv — tests via `docker compose run --rm test python3 -m pytest tests/...`.
- `httpx` and `lxml` are already project dependencies — no new dependencies needed for scraping/parsing.
- A `PostToolUse` hook auto-runs ruff on every edited `.py` file.
- All new modules use `from __future__ import annotations`, per existing repo convention.
- Follow the existing non-fatal-try/except-log-and-continue convention for pipeline steps (`main.py` Steps 1-5) rather than introducing a different failure-handling style for Steps 6-7.
- No new Docker services/cron entries — everything rides the existing nightly `pipeline` container run (see Option B decision below).

## Rejected Alternatives

- **Single combined step** doing scraping + bulk feature computation together: rejected for mixing two different concerns (HTML parsing vs. numeric computation) in one unit, harder to test/reason about independently.
- **Fully separate cron-scheduled services** (like `prewarm`/`market-data-refresh` today), decoupled from the main pipeline: rejected as unnecessary operational surface (new profile-gated service, new cron entry) for something the checkpoint-based nightly Step 6 already handles cheaply without a dedicated schedule.
