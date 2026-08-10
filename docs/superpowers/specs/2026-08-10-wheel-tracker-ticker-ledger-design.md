# Wheel Tracker — Ticker Ledger Design Spec

**Date:** 2026-08-10
**Status:** Approved
**Supersedes:** parts of `2026-08-08-wheel-tracker-design.md` — see "Relationship to prior design" below

---

## Problem

Two bugs found in the live wheel tracker at `dev-mi.austin10berge.com/v2`:

1. **Duplicate ticker groups.** The dashboard's "Wheel Cycles" section shows one card per
   `wt_cycles` row, and a ticker gets a new cycle row every time it starts a fresh CSP. Live data
   (`market_intelligence.db`) has, e.g., 23 cycle rows for SOFI, 7 for IOT, 4 for HOOD, 4 for DRAM —
   each rendered as a separate card for the same ticker.
2. **Missing transactions, including covered calls.** Of 270 rows in `wt_trades`, 180 (67%) have
   `cycle_id IS NULL` and never appear anywhere in the UI. This includes 21 `SELL_TO_OPEN CALL`
   trades (covered calls) plus equity buys/sells and other option trades. The cause is
   `wheel_tracker/cycles.py`'s linker: it only starts a cycle from a `SELL_TO_OPEN PUT` and then
   walks forward through a fixed phase order (csp → assigned → shares → cc → cc_close). Any trade
   that doesn't fit that exact sequence — a covered call written on shares that weren't acquired via
   assignment, a second concurrent CSP on the same underlying, a plain equity trade — is left
   unlinked and silently dropped from the dashboard.

## Decision

Retire the cycle-linking engine. Replace the "Wheel Cycles" concept with a **per-ticker ledger**:
one card per underlying ticker, listing every EQUITY/OPTION trade for that ticker with no matching
step that can fail or drop a row.

## Relationship to prior design

The 2026-08-08 design's data model (`wt_trades`, `wt_positions`, `wt_notes`), Schwab sync
(`sync.py` transaction/position/delta fetch), and alerts (`alerts.py`) are unchanged and still
accurate. This spec **replaces** that design's "Wheel-Cycle Linking (`cycles.py`)" section and the
"Wheel Cycles" panel of the "Dashboard" section, and updates the Discord integration's schema
description accordingly.

`wt_cycles` and the `cycle_id` column stay in the schema (unused) rather than being dropped via a
live migration — removing them is a separate, purely destructive follow-up if ever wanted, not part
of this change.

---

## Backend

### `store.py`

Remove: `create_cycle`, `update_cycle`, `set_trade_cycle`, `get_cycles`, `get_cycle_trades`.

Add:

```python
def get_ticker_ledger(conn: sqlite3.Connection) -> list[dict]:
    """One entry per ticker (underlying, or symbol for equity rows), each with every
    EQUITY/OPTION trade for that ticker tagged with a strategy label, plus rollup totals."""
```

Per ticker, grouping key is `underlying` if set else `symbol` (same rule the old linker used, so
existing option rows group the same way; equity rows have no `underlying` and group under their own
symbol). Only `asset_type IN ('EQUITY', 'OPTION')` rows are included — `MUTUAL_FUND` and
`COLLECTIVE_INVESTMENT` rows are unrelated to the wheel strategy and stay excluded, same as today.

Each ticker entry:

```python
{
    "underlying": "SOFI",
    "status": "ACTIVE" | "CLOSED",   # ACTIVE if any wt_positions row exists for this ticker
    "total_premium": 1234.56,        # sum of net_amount over this ticker's OPTION rows where net_amount > 0
    "realized_pnl": 980.12,          # sum of net_amount over all this ticker's trades
    "trades": [
        {..every column from wt_trades.., "strategy": "Covered Call"},
        ...
    ],  # all trades, ordered by executed_at ascending
}
```

Ticker list ordered: `ACTIVE` first, then by most recent trade `executed_at` descending.

Strategy label — pure function of `(asset_type, option_type, instruction)`, no DB lookups:

| asset_type | option_type | instruction | label |
|---|---|---|---|
| OPTION | PUT | SELL_TO_OPEN | Cash-Secured Put |
| OPTION | PUT | BUY_TO_CLOSE, EXPIRED | CSP Closed |
| OPTION | PUT | BUY_TO_OPEN | Long Put |
| OPTION | PUT | SELL_TO_CLOSE | Long Put Closed |
| OPTION | PUT | ASSIGNED | Put Assigned |
| OPTION | CALL | SELL_TO_OPEN | Covered Call |
| OPTION | CALL | BUY_TO_CLOSE, EXPIRED | Covered Call Closed |
| OPTION | CALL | BUY_TO_OPEN | Long Call |
| OPTION | CALL | SELL_TO_CLOSE | Long Call Closed |
| OPTION | CALL | ASSIGNED | Call Assigned |
| EQUITY | — | BUY, BUY_TO_OPEN | Shares Bought |
| EQUITY | — | SELL, SELL_TO_CLOSE | Shares Sold |
| (anything else) | — | — | falls back to `instruction` verbatim, so no row is ever unlabeled |

### `get_wheel_stats`

`total_cycles`/`open_cycles` (queried from `wt_cycles`) become `total_tickers`/`active_tickers`,
computed from the same ticker grouping as `get_ticker_ledger` (count of groups; active = `status ==
"ACTIVE"`).

`win_rate` is redefined from per-cycle to **per closed option leg**: group `wt_trades` rows with
`asset_type='OPTION'` by `symbol` (the exact OCC contract). A leg is "closed" if its symbol has both
an opening trade (`SELL_TO_OPEN`/`BUY_TO_OPEN`) and a closing trade (`BUY_TO_CLOSE`/`SELL_TO_CLOSE`/
`EXPIRED`/`ASSIGNED`). A closed leg is a "win" if the sum of `net_amount` for that symbol is > 0.
`win_rate = closed_and_won / closed_total`.

`premium_mtd`, `premium_ytd`, `max_short_put_delta` are unchanged (already query `wt_trades` /
`wt_positions` directly, not `wt_cycles`).

### `cycles.py`

Delete the file.

### `sync.py`

Remove the `from .cycles import link_cycles` import and the `link_cycles(conn)` call in
`run_sync()`. `check_alerts(conn)` still runs — it reads `wt_positions`, not cycles.

### `api/main.py`

Replace `GET /api/wheel/cycles` with `GET /api/wheel/tickers`:

```python
@app.get("/api/wheel/tickers")
def wheel_tickers(req: Request):
    with ...:
        return {"tickers": wt_get_ticker_ledger(conn)}
```

`wheel_stats` endpoint unchanged in shape; the dict it returns has the renamed keys described above.

### Discord bot schema description

`discord_bot/trade_system_prompt.txt` (~lines 118–139) currently tells the chat agent `wt_trades`
has a `cycle_id (FK to wt_cycles...)` and describes `wt_cycles` as the way to group trades — once
`link_cycles` stops running, that table stops updating and the bot would query stale/frozen data.
Update this section to drop the `wt_cycles`/`cycle_id` description and instead tell the agent to
group by `underlying` (or `symbol` for equity rows) directly on `wt_trades` — e.g. "to answer
'show my AAPL wheel activity', filter wt_trades where underlying='AAPL' OR symbol='AAPL'".

---

## Frontend (`src/web/v2/wheel.js`)

`renderCycles(cycles)` → `renderTickers(tickers)`:

- One card per ticker object (fixes the duplicate-card bug directly, since the backend now emits
  exactly one entry per ticker).
- Card header: ticker symbol, `ACTIVE`/`CLOSED` status badge (reusing the existing open/closed badge
  styling), `total_premium` and `realized_pnl` on the right (reusing existing `fmtMoney`/color
  logic).
- Below the header: **every** trade in `trades`, always visible (not behind a `<details>` toggle —
  hiding legs by default is part of what made the missing covered calls hard to notice). Each row
  shows: strategy label, contract/share detail (option_type + strike + expiration, or "shares" for
  equity), date, signed `net_amount` (green/red, reusing existing formatting).

Section header "Wheel Cycles" → "Wheel Tickers". Fetch call updates from `/wheel/cycles` to
`/wheel/tickers`, keyed as `tickerData.tickers`.

Stats bar (`renderStats`): label bound to `s.total_cycles`/`s.open_cycles` becomes
`s.total_tickers`/`s.active_tickers`; text changes from "N cycles · N open" to "N tickers · N
active".

`renderStats` and `renderPositions` are otherwise untouched — this task doesn't change the Open
Positions panel or the Premium MTD/YTD/Win-Rate tiles beyond the stat source rename above.

---

## Testing

- `tests/test_wheel_tracker_cycles.py` — delete (tests the removed module).
- `tests/test_wheel_tracker_store.py` — replace the `wt_cycles`-based test with coverage of
  `get_ticker_ledger`: multiple historical CSP rounds on one ticker collapse into a single ticker
  entry; a covered call with no prior CSP in the data still appears; a plain equity buy/sell
  appears; a mutual-fund row is excluded; `status` reflects `wt_positions` presence; strategy labels
  match the table above for each `(asset_type, option_type, instruction)` combination exercised.
- `tests/test_wheel_tracker_sync.py` — remove/update the three tests asserting `run_sync` calls
  `cycles.link_cycles` (`test_run_sync_calls_link_cycles_and_check_alerts`,
  `test_run_sync_link_cycles_failure_is_caught_non_fatally`, and the third `mock_link` test) since
  that call no longer exists; keep coverage that `check_alerts` still runs after sync.
- New test for the redefined `win_rate` in `get_wheel_stats`: a symbol with only an opening trade is
  not counted as closed; a symbol with open+close and positive net is a win; negative net is a loss.
- `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`
  (per `Market-Intelligence:verify` skill).
- Playwright against `dev-mi.austin10berge.com/v2`: confirm SOFI/IOT/HOOD/DRAM each render as a
  single card, and that covered call rows (previously absent) now appear under the affected tickers.

## Out of scope

- Dropping `wt_cycles` table / `cycle_id` columns from the schema (see "Relationship to prior
  design").
- Changes to the Open Positions panel, sync/alerts logic, or the Discord `!note` command.
- A UI to manually re-group or correct a ticker's trade list (not needed — grouping is now a pure,
  deterministic function of `underlying`/`symbol`, nothing to correct).
