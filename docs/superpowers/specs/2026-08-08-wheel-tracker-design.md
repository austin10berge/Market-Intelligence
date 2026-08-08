# Wheel Tracker — Design Spec

**Date:** 2026-08-08
**Status:** Approved

---

## Overview

Add a wheel-strategy trade tracking system to Market Intelligence. The system auto-syncs all trades
from Schwab nightly, links related trades into wheel cycles (CSP → assignment → shares → CC →
called away), and surfaces them on the dashboard and via the Discord trade-chat bot. Includes NTFY
alerts for near-expiration and elevated assignment risk.

---

## Architecture

New subpackage `src/wheel_tracker/` alongside `src/algo_detective/`. No new Docker service, no new
cron — everything integrates into the existing nightly pipeline.

```
src/wheel_tracker/
├── __init__.py
├── sync.py      — Schwab transaction + position pull via MCP (streamablehttp_client)
├── cycles.py    — Wheel-cycle auto-linking logic
├── alerts.py    — DTE / assignment-risk alert generation
└── store.py     — DB reads/writes for wt_* tables
```

Pipeline integration: new Step 5 in `src/main.py` calls `wheel_tracker.sync.run_sync()`, which
internally calls `cycles.link_cycles()` then `alerts.check_alerts()`. Wrapped in try/except — a
Schwab auth failure never kills the rest of the pipeline.

Four new SQLite tables added to `src/db.py _ensure_tables()` in `market_intelligence.db`.

---

## Data Model

### `wt_trades`

Append-only. One row per Schwab transaction. Never deleted or modified after import.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `schwab_transaction_id` | TEXT UNIQUE | Idempotent sync key |
| `account_id` | TEXT | Schwab account hash |
| `executed_at` | TEXT | ISO 8601 timestamp |
| `settled_date` | TEXT | |
| `asset_type` | TEXT | `EQUITY` \| `OPTION` |
| `symbol` | TEXT | OCC symbol for options; ticker for equity |
| `underlying` | TEXT | Option underlying ticker (null for equity) |
| `option_type` | TEXT | `PUT` \| `CALL` \| null |
| `strike` | REAL | null for equity |
| `expiration` | TEXT | ISO date; null for equity |
| `instruction` | TEXT | `SELL_TO_OPEN`, `BUY_TO_CLOSE`, `ASSIGNED`, `EXPIRED`, `BUY`, `SELL`, … |
| `quantity` | REAL | |
| `price` | REAL | Per share / per contract (×100 for notional) |
| `commission` | REAL | |
| `net_amount` | REAL | Signed cash impact: positive = cash received |
| `cycle_id` | INTEGER FK | Null until `cycles.link_cycles()` runs |
| `imported_at` | TEXT | |

### `wt_positions`

Replaced each pipeline run for each account. Reflects live open positions.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `account_id` | TEXT | |
| `symbol` | TEXT | |
| `underlying` | TEXT | null for equity |
| `asset_type` | TEXT | `EQUITY` \| `OPTION` |
| `option_type` | TEXT | `PUT` \| `CALL` \| null |
| `strike` | REAL | |
| `expiration` | TEXT | |
| `dte` | INTEGER | Computed at sync time from expiration vs. today |
| `quantity` | REAL | Negative = short |
| `average_price` | REAL | |
| `current_price` | REAL | |
| `market_value` | REAL | |
| `unrealized_pnl` | REAL | |
| `delta` | REAL | Fetched from option chain for short option positions; null for equity |
| `cycle_id` | INTEGER FK | |
| `last_dte_alerted` | TEXT | Date of last DTE alert sent for this position |
| `last_delta_alerted` | TEXT | Date of last delta alert sent |
| `refreshed_at` | TEXT | |

### `wt_cycles`

One row per wheel cycle or standalone trade group.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `underlying` | TEXT | Ticker (e.g. `AAPL`) |
| `status` | TEXT | `OPEN` \| `CLOSED` |
| `opened_at` | TEXT | Date of first trade in cycle |
| `closed_at` | TEXT | Date of final close; null if open |
| `total_premium` | REAL | Sum of `net_amount` for all option legs (positive = net collected) |
| `realized_pnl` | REAL | Total P/L across all legs; null if open |
| `auto_detected` | INTEGER | 1 = system-linked; 0 = user-confirmed or corrected |

### `wt_notes`

Append-only. Notes from Discord or the dashboard attached to a trade or cycle.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `trade_id` | INTEGER FK | Optional — `wt_trades.id` |
| `cycle_id` | INTEGER FK | Optional — `wt_cycles.id` |
| `source` | TEXT | `discord` \| `dashboard` |
| `content` | TEXT | |
| `created_at` | TEXT | |

---

## Schwab Sync (`sync.py`)

Uses `streamablehttp_client` + `ClientSession` — identical pattern to `algo_detective/schwab_options.py`.

**Transaction sync:**
- Reads `max(executed_at)` from `wt_trades` for the account.
- First run (no rows): pulls maximum available history from `get_transactions`.
- Subsequent runs: pulls since last `executed_at` (incremental).
- Upserts on `schwab_transaction_id` — re-running is safe.

**Position snapshot:**
- Calls `get_account(positions=True)` for each account.
- Upserts on `(account_id, symbol)` — preserves `last_dte_alerted` and `last_delta_alerted` from
  the existing row so alert de-dup state survives across pipeline runs.
- Rows for positions no longer held (closed between runs) are deleted after the upsert pass.

**Delta fetch:**
- For each open short option position in the fresh snapshot, calls `get_option_chain` on that underlying.
- Only fetches the expiration matching the position — bounded by number of open short positions.
- DTE is pure date arithmetic; no API call needed.

---

## Wheel-Cycle Linking (`cycles.py`)

Runs after sync on all unlinked trades (where `cycle_id IS NULL`), ordered by `executed_at`.

**Detection algorithm:**

1. Find a `SELL_TO_OPEN PUT` trade → candidate cycle start.
2. Walk forward on that `(account_id, underlying)`:
   - `BUY_TO_CLOSE` or `EXPIRED` on the same OCC symbol → cycle closes (CSP expired worthless or bought back).
   - `ASSIGNED` on the short put → enter shares-held phase: link the corresponding equity `BUY` transaction.
3. In shares-held phase, continue forward:
   - `SELL_TO_OPEN CALL` on same underlying with matching quantity → link to cycle.
   - `BUY_TO_CLOSE` or `EXPIRED` on the CC → loop back to (3) for next CC.
   - `ASSIGNED` from CC, or `SELL` of equity → cycle closes.
4. Write a `wt_cycles` row; set `auto_detected=1`; update `cycle_id` on all linked trades.
5. Any unlinked trades after the pass (e.g. standalone equity buys/sells) are left with `cycle_id = NULL` — they appear in Trade History but not in a cycle.

Users can reassign `cycle_id` via Discord note or dashboard edit to correct mis-linkages.

---

## Dashboard (`src/web/`)

New "Wheel" tab. Three panels, all backed by new FastAPI endpoints.

### Open Positions — `GET /wheel/positions`
Table sorted by DTE ascending:

| Symbol | Type | Strike | Expiration | DTE | Qty | Avg Cost | Unrealized P/L | Delta | Cycle |

### Wheel Cycles — `GET /wheel/cycles`
Open cycles first, then closed. Each row shows: underlying, status, premium collected, realized P/L
(when closed), open/close date. Expandable to show all trade legs with individual P/L contribution.

### Stats Bar — `GET /wheel/stats`
- Premium collected MTD / YTD
- CSP win rate: (expired worthless + bought back at profit) ÷ total closed CSPs
- Current max short-put delta (assignment risk indicator)

---

## Discord Integration

Two additions to the trade-chat bot.

**Notes command:** `!note <trade_id|cycle_id> <text>` — handled in the bot's command dispatcher
(not via Claude agent). Writes directly to `wt_notes` with `source='discord'`.

**Natural-language queries:** Add the `wt_*` table schema and purpose to the Claude agent system
prompt in `chat.py`. The existing agent already has DB access; this lets it answer questions like
"show my open CSPs" or "what's my total premium collected on TSLA this year" by querying `wt_*`
tables directly.

---

## Alerts (`alerts.py`)

Runs at end of `sync.run_sync()`. Sends via existing `notify/ntfy.py`.

| Alert | Trigger | Message | De-dup |
|---|---|---|---|
| Expiration this week | Open short option, DTE ≤ 7 | `"Expiring soon: {symbol} | DTE {N} | {option_type} ${strike}"` | Once per `(symbol, expiration)` — stored in `last_dte_alerted` |
| Assignment risk elevated | Open short put, delta ≥ 0.30 (absolute) | `"Assignment risk: {symbol} | Δ {delta:.2f} | ${strike} put exp {expiration}"` | Once per calendar day per position — stored in `last_delta_alerted` |

De-dup state lives in `wt_positions` — already refreshed each run, so the columns are always
current.

---

## Out of Scope

- Margin / buying-power tracking (not available from Schwab transaction history)
- LEAPS or spreads (not part of wheel strategy; can be added later)
- Order execution (existing design is read-only)
- Dashboard "edit cycle" UI (note via Discord covers manual correction for now)
