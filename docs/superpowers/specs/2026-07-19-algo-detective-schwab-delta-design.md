# Algo Detective — Real Delta Collection via Schwab — Design Spec

**Date:** 2026-07-19

## Background

The `algo_detective` subsystem's automated nightly pipeline (`docs/superpowers/plans/2026-07-19-algo-detective-automated-pipeline.md`) grows `detective_features` (technicals/fundamentals + `is_prime` ground-truth labels) automatically. The natural next step is an automated search over feature thresholds ("gates") to systematically replicate mLabs' CSP entry criteria, extending 35 sessions of manual KS-statistic gate tuning that arrived at the current `V42` criteria.

Delta is one of mLabs' three explicitly stated screening parameters (per prior reverse-engineering sessions: *"Delta: 0.15–0.30 (puts)"*, alongside DTE and IV), but it does not exist anywhere in the current feature set. `detective_options` (per-ticker options snapshot, `src/algo_detective/options_chain.py`) fetches `best_iv`, `best_volume`, `pcr_vol` from Alpaca's indicative snapshots feed — `pcr_oi` is permanently `None` (Alpaca's indicative feed has no open-interest field), and delta was never captured at all. Without delta data, no gate search can consider it as a discriminator.

A live comparison against Schwab's option-chain API (`mcp__schwab__get_option_chain`, backed by the already-running `schwab-mcp` service) confirmed it returns real per-contract `delta`, `gamma`, `theta`, `vega`, `openInterest`, `bid`/`ask` — data Alpaca's feed structurally cannot provide (no Greeks, no OI, ever).

Historical backfill was evaluated and rejected: none of Alpaca, yFinance, or Schwab expose historical options Greeks (all three are live-snapshot-only feeds). A Black-Scholes approximation using `rv20` (realized vol, already a stored feature) as an implied-vol proxy was tested empirically against 10 real Schwab contracts and found unreliable — mean absolute delta error 0.077, max 0.148, against a target band only 0.15 wide, driven by a *systematic* one-directional bias (implied vol runs 1.3–1.6x realized vol on every name tested, the well-documented volatility risk premium), not random noise. This would actively mislead a gate search rather than merely add noise, so it's excluded from scope.

## Goal

Collect real per-contract delta (plus opportunistically `bid`, `ask`, `open_interest`, filling the existing OI gap) nightly via Schwab, for the tracked prime universe, so a future gate-search project can consider delta as a discriminating feature.

## Non-Goals

- **Historical delta backfill.** Rejected above — no reliable source exists. Coverage starts from whenever this ships and grows forward, same shape as `best_iv` today.
- **Replacing Alpaca's existing `best_iv`/`pcr_vol` collection.** This is additive — Schwab data lands in new columns on the same `detective_options` row via the existing `COALESCE`-based partial upsert, not a replacement of the Alpaca-based snapshot step.
- **Control-universe (full ~1,700+ ticker) delta collection.** Scoped to the narrow universe only (every ticker that has ever been `is_prime=1` — the same set Step 5's existing options-snapshot step already computes). Schwab's chain endpoint is per-symbol with no bulk call, so the full tracked universe is out of scope on rate-limit/runtime grounds; this can be revisited later if the narrow universe proves insufficient for gate search.
- **The gate-search project itself.** A separate, later design — this is purely the data-collection prerequisite for it.

## Architecture

One new step in `main.py`'s existing `_run_algo_detective_steps` helper, alongside the existing Alpaca-based options-snapshot step:

```
... (existing Steps 6/7/5 — label sync, control sync, Alpaca options snapshot) ...
New step: Schwab delta snapshot — fetch real delta/bid/ask/OI for the narrow
          (all-time-prime) ticker universe, non-fatal like every other step.
```

## Components

### `src/algo_detective/schwab_options.py` (new)

- `_fetch_chain_via_mcp(ticker: str, from_date: date, to_date: date) -> dict` — the MCP-call boundary. Connects to the already-running `schwab-mcp` service over the Docker network via the official `mcp` Python SDK's streamable-HTTP client (`http://schwab-mcp:8002/mcp`), calls its `get_option_chain` tool (`contract_type=PUT`), returns the raw chain payload. This is the one function tests patch — no test simulates the MCP handshake itself.
- `_select_target_delta_contract(chain: dict, target_delta: float = 0.20) -> dict | None` — given a chain payload (possibly spanning multiple expirations), returns the single put contract whose delta magnitude is closest to `target_delta`, with its `delta`, `bid`, `ask`, `open_interest`. Returns `None` if the chain has no usable contracts (e.g. no bids).
- `fetch_delta_snapshot(tickers: list[str], scan_date_str: str) -> int` — orchestrator, same shape as `options_chain.py::fetch_snapshot_pcr`. For each ticker: fetch the chain spanning the next 2 Friday expirations (same convention `options_chain.py` already uses for `best_iv`), select the target-delta contract, collect rows, upsert via `store.upsert_options_rows` (existing function, already does `COALESCE`-based per-column merge on `(date, ticker)` — no changes needed there beyond the schema extension below). Small sleep between tickers, matching `options_chain.py`'s existing `_REQUEST_SLEEP` convention. Returns count of rows written.

### `src/algo_detective/store.py` (modify)

Extend the existing `_OPTIONS_COLUMNS` idempotent-migration list (the mechanism that already added `pcr_vol`/`pcr_oi` via `ensure_tables()`'s `ALTER TABLE ... ADD COLUMN` loop) with:
```python
("delta", "REAL"),
("bid", "REAL"),
("ask", "REAL"),
("open_interest", "INTEGER"),
```

### `src/main.py` (modify)

Add one more independently non-fatal try/except block to the existing `_run_algo_detective_steps(today)` helper, calling `schwab_options.fetch_delta_snapshot(narrow_universe_tickers, today.isoformat())` — reusing the same `_prime` ticker set the existing options-snapshot step already computes from `get_all_features()`.

### `docker-compose.yml` (modify)

Add `depends_on: schwab-mcp: condition: service_healthy` to the `pipeline` service (mirroring the existing `discord-bot` → `schwab-mcp` dependency), since the pipeline now needs that service running.

## Data Flow

1. Nightly pipeline reaches the new step with the same narrow-universe ticker list Step 5 already has in hand.
2. For each ticker: one `get_option_chain` MCP call → one contract selected (closest to 0.20 delta, next-2-Fridays window) → one row of `{delta, bid, ask, open_interest}`.
3. Rows upserted into `detective_options` via the existing `COALESCE`-merge path — a row already populated with Alpaca's `best_iv`/`pcr_vol` for that `(date, ticker)` gains the new columns; a row that doesn't exist yet is created with just the new columns populated (existing behavior, unchanged).

## Error Handling

Matches the existing convention used by every other pipeline step: the whole `fetch_delta_snapshot` call is wrapped in one non-fatal try/except in `main.py`. Within it, a single ticker's chain-fetch failure (network error, no usable contracts, MCP call failure) is caught per-ticker and logged, not fatal to the rest of the batch — mirrors `label_sync.py`'s per-slug isolation pattern.

## Testing

- Unit tests for `_select_target_delta_contract` against canned chain payloads (real shape captured live during design, see Background) — correct nearest-to-target selection across multiple expirations/strikes, `None` on an empty/unusable chain.
- Unit tests for `fetch_delta_snapshot` with `_fetch_chain_via_mcp` patched — correct upsert row shape, correct per-ticker error isolation (one ticker raising doesn't block the rest).
- Schema migration test extending the existing `_OPTIONS_COLUMNS` test pattern in `test_algo_detective_store.py`.
- Pipeline-wiring test extending `test_main_pipeline_algo_detective_steps.py`'s existing pattern (non-fatal isolation of the new step).
- **Manual verification** (no automated test safely exercises the real live `schwab-mcp` service): a live dev-stack run confirming real `delta`/`bid`/`ask`/`open_interest` values land in `detective_options` for a handful of narrow-universe tickers.

## Global Constraints

- Python 3.12, no local virtualenv — tests via `docker compose run --rm test python3 -m pytest tests/...`.
- No new HTTP-fetch dependency for options data — MCP client uses the official `mcp` Python SDK (new dependency, but replaces what would otherwise be hand-rolled JSON-RPC/SSE handling).
- A `PostToolUse` hook auto-runs ruff on every edited `.py` file — prior tasks in this codebase found it doesn't always fully resolve every finding (e.g. `ruff format`-fixable E501/E402 survived a commit in the labels/features plan); explicitly run `ruff check`/`ruff format --check` before committing rather than assuming the hook caught everything.
- Follow the existing non-fatal try/except-log-and-continue convention for every pipeline step.
- Target delta for contract selection: 0.20 (midpoint of mLabs' stated 0.15–0.30 range) — adjustable later without a schema change.

## Rejected Alternatives

- **Historical delta via Black-Scholes + rv20 proxy**: empirically tested (see Background), mean abs error 0.077 / max 0.148 against a 0.15-wide target band, systematically biased by the volatility risk premium (implied vol consistently 1.3–1.6x realized vol). Would mislead rather than merely add noise to a gate search — rejected.
- **Direct `schwab-py` client with its own OAuth token in the pipeline container**: would share `~/.local/share/schwab-mcp/token.yaml` with `schwab-mcp`'s own concurrent refresh cycle — a real write-race risk (the repo has a documented prior incident from a similar mount-permission issue on this exact file). Rejected in favor of treating `schwab-mcp` as a proper internal service and connecting as a second MCP client.
- **Full tracked control universe (~1,700+ tickers) instead of narrow universe**: Schwab's chain endpoint has no bulk/multi-symbol call, making per-night fetch volume ~23x larger — rate-limit and runtime risk not justified until the narrow universe proves insufficient for gate search.
