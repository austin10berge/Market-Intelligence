# Wheel Tracker Ticker Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the wheel tracker's fragile cycle-linking engine (which drops ~67% of trades that don't fit its exact CSP→assignment→CC sequence) with a per-ticker ledger that shows every EQUITY/OPTION trade for a ticker, tagged with a strategy label, with exactly one card per ticker.

**Architecture:** A new `get_ticker_ledger()` store function groups `wt_trades` rows by `underlying` (falling back to `symbol` for equity rows) with no matching/linking step that can fail. A pure `_strategy_label()` function tags every row from its `(asset_type, option_type, instruction)` tuple. `get_wheel_stats()`'s win-rate calc moves from cycle-based to per-option-leg (grouped by exact contract `symbol`). The cycle engine (`cycles.py`, `wt_cycles` reads/writes) is deleted; the `wt_cycles` table/`cycle_id` columns stay in the schema, unused. Frontend (`wheel.js`) and the Discord bot's schema description are updated to match.

**Tech Stack:** Python 3.12 / SQLite (`src/wheel_tracker/store.py`, `src/wheel_tracker/sync.py`), FastAPI (`src/api/main.py`), vanilla JS (`src/web/v2/wheel.js`), pytest (Docker-run test suite).

## Global Constraints

- Python tests run via `docker compose run --rm test python3 -m pytest ...` — no bare `python -m` on the host (no local venv).
- `~/.local/bin/ruff` auto-formats every edited `.py` file via a PostToolUse hook — no manual format step.
- Do not drop the `wt_cycles` table or `cycle_id` columns from the schema — leave them in place, unused (per approved spec).
- Do not touch `wt_positions`, `alerts.py`, the Open Positions panel, or the Discord `!note` command — out of scope.
- Frontend changes must be verified against `https://dev-mi.austin10berge.com/v2/` with Playwright MCP (`mcp__playwright__*`), not curl — JS-rendered page.
- Restart the dev API container (`docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build api`) to pick up backend changes before any Playwright check — `docker-compose.local.yml` already mounts `src/` live, but the process needs a restart to reload Python.

---

### Task 1: Strategy label helper + ticker ledger store function

**Files:**
- Modify: `src/wheel_tracker/store.py` (add `_strategy_label`, `get_ticker_ledger` near the end, in the "API query helpers" section after `get_cycle_trades`)
- Test: `tests/test_wheel_tracker_store.py` (add tests after `test_upsert_position_preserves_alert_columns`, before `test_create_and_update_cycle`)

**Interfaces:**
- Produces: `_strategy_label(asset_type: str, option_type: str | None, instruction: str) -> str` — pure function, no DB access.
- Produces: `get_ticker_ledger(conn: sqlite3.Connection) -> list[dict]` — each dict: `{"underlying": str, "status": "ACTIVE"|"CLOSED", "total_premium": float, "realized_pnl": float, "trades": list[dict]}`. Each trade dict is every column of its `wt_trades` row plus `"strategy": str`. Tickers ordered ACTIVE first, then by most recent trade `executed_at` descending.
- Consumes: existing `wt_trades` and `wt_positions` tables (already populated by `sync.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_wheel_tracker_store.py`, right after `test_upsert_position_preserves_alert_columns` (before `test_create_and_update_cycle`):

```python
def test_strategy_label_covers_every_wheel_leg():
    from src.wheel_tracker.store import _strategy_label

    assert _strategy_label("OPTION", "PUT", "SELL_TO_OPEN") == "Cash-Secured Put"
    assert _strategy_label("OPTION", "PUT", "BUY_TO_CLOSE") == "CSP Closed"
    assert _strategy_label("OPTION", "PUT", "EXPIRED") == "CSP Closed"
    assert _strategy_label("OPTION", "PUT", "BUY_TO_OPEN") == "Long Put"
    assert _strategy_label("OPTION", "PUT", "SELL_TO_CLOSE") == "Long Put Closed"
    assert _strategy_label("OPTION", "PUT", "ASSIGNED") == "Put Assigned"
    assert _strategy_label("OPTION", "CALL", "SELL_TO_OPEN") == "Covered Call"
    assert _strategy_label("OPTION", "CALL", "BUY_TO_CLOSE") == "Covered Call Closed"
    assert _strategy_label("OPTION", "CALL", "EXPIRED") == "Covered Call Closed"
    assert _strategy_label("OPTION", "CALL", "BUY_TO_OPEN") == "Long Call"
    assert _strategy_label("OPTION", "CALL", "SELL_TO_CLOSE") == "Long Call Closed"
    assert _strategy_label("OPTION", "CALL", "ASSIGNED") == "Call Assigned"
    assert _strategy_label("EQUITY", None, "BUY") == "Shares Bought"
    assert _strategy_label("EQUITY", None, "BUY_TO_OPEN") == "Shares Bought"
    assert _strategy_label("EQUITY", None, "SELL") == "Shares Sold"
    assert _strategy_label("EQUITY", None, "SELL_TO_CLOSE") == "Shares Sold"


def test_strategy_label_falls_back_to_instruction_for_unknown_combo():
    from src.wheel_tracker.store import _strategy_label

    assert _strategy_label("EQUITY", None, "SPLIT") == "SPLIT"
    assert _strategy_label("OPTION", "CALL", "UNKNOWN") == "UNKNOWN"


def test_get_ticker_ledger_groups_multiple_rounds_into_one_entry():
    """Historically the cycle-linker made a new wt_cycles row per CSP round —
    two CSP rounds on the same ticker must collapse into a single ledger entry."""
    from src.wheel_tracker.store import upsert_trade, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="r1a", symbol="SOFI  250117P00015000",
        underlying="SOFI", instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00",
        net_amount=50.0,
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="r1b", symbol="SOFI  250117P00015000",
        underlying="SOFI", instruction="EXPIRED", executed_at="2025-01-17T21:00:00",
        net_amount=0.0,
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="r2a", symbol="SOFI  250321P00016000",
        underlying="SOFI", instruction="SELL_TO_OPEN", executed_at="2025-02-01T10:00:00",
        net_amount=60.0,
    ))

    tickers = get_ticker_ledger(conn)
    sofi_entries = [t for t in tickers if t["underlying"] == "SOFI"]
    assert len(sofi_entries) == 1
    assert len(sofi_entries[0]["trades"]) == 3
    assert sofi_entries[0]["total_premium"] == pytest.approx(110.0)


def test_get_ticker_ledger_includes_covered_call_with_no_prior_csp():
    """The old linker required a CSP to start a cycle; a CC written on shares
    that were never wheeled through a CSP must still appear."""
    from src.wheel_tracker.store import upsert_trade, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="cc1", asset_type="OPTION", symbol="MSFT  250620C00450000",
        underlying="MSFT", option_type="CALL", instruction="SELL_TO_OPEN",
        executed_at="2025-01-05T10:00:00", net_amount=120.0,
    ))

    tickers = get_ticker_ledger(conn)
    msft = next(t for t in tickers if t["underlying"] == "MSFT")
    assert len(msft["trades"]) == 1
    assert msft["trades"][0]["strategy"] == "Covered Call"


def test_get_ticker_ledger_includes_plain_equity_trade():
    from src.wheel_tracker.store import upsert_trade, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="eq1", asset_type="EQUITY", symbol="NVDA",
        underlying=None, option_type=None, strike=None, expiration=None,
        instruction="BUY", executed_at="2025-01-02T10:00:00", net_amount=-500.0,
    ))

    tickers = get_ticker_ledger(conn)
    nvda = next(t for t in tickers if t["underlying"] == "NVDA")
    assert nvda["trades"][0]["strategy"] == "Shares Bought"
    assert nvda["realized_pnl"] == pytest.approx(-500.0)


def test_get_ticker_ledger_excludes_mutual_fund_rows():
    from src.wheel_tracker.store import upsert_trade, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="mf1", asset_type="MUTUAL_FUND", symbol="SWVXX",
        underlying=None, option_type=None, strike=None, expiration=None,
        instruction="BUY_TO_OPEN", executed_at="2025-01-02T10:00:00", net_amount=-1000.0,
    ))

    tickers = get_ticker_ledger(conn)
    assert not any(t["underlying"] == "SWVXX" for t in tickers)


def test_get_ticker_ledger_status_reflects_open_position():
    from src.wheel_tracker.store import upsert_trade, upsert_position, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="hood1", symbol="HOOD  250117P00030000",
        underlying="HOOD", instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00",
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="crwv1", symbol="CRWV  250117P00050000",
        underlying="CRWV", instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00",
    ))
    upsert_position(conn, dict(
        account_id="ACC1", symbol="CRWV  250117P00050000", underlying="CRWV",
        asset_type="OPTION", option_type="PUT", strike=50.0, expiration="2025-01-17",
        dte=10, quantity=-1.0, average_price=1.0, current_price=0.5, market_value=-50.0,
        unrealized_pnl=50.0, delta=-0.2, refreshed_at="2025-01-07T17:00:00",
    ))

    tickers = {t["underlying"]: t for t in get_ticker_ledger(conn)}
    assert tickers["CRWV"]["status"] == "ACTIVE"
    assert tickers["HOOD"]["status"] == "CLOSED"


def test_get_ticker_ledger_orders_active_first_then_recency():
    from src.wheel_tracker.store import upsert_trade, upsert_position, get_ticker_ledger

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="old1", symbol="IOT   250117P00020000",
        underlying="IOT", instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00",
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="new1", symbol="DRAM  250321P00040000",
        underlying="DRAM", instruction="SELL_TO_OPEN", executed_at="2025-03-01T10:00:00",
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="active1", symbol="WMT   250321P00100000",
        underlying="WMT", instruction="SELL_TO_OPEN", executed_at="2025-01-15T10:00:00",
    ))
    upsert_position(conn, dict(
        account_id="ACC1", symbol="WMT   250321P00100000", underlying="WMT",
        asset_type="OPTION", option_type="PUT", strike=100.0, expiration="2025-03-21",
        dte=10, quantity=-1.0, average_price=1.0, current_price=0.5, market_value=-50.0,
        unrealized_pnl=50.0, delta=-0.2, refreshed_at="2025-01-07T17:00:00",
    ))

    order = [t["underlying"] for t in get_ticker_ledger(conn)]
    assert order == ["WMT", "DRAM", "IOT"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_store.py -k "strategy_label or ticker_ledger" -v`
Expected: FAIL with `ImportError: cannot import name '_strategy_label'` (and `get_ticker_ledger`).

- [ ] **Step 3: Implement `_strategy_label` and `get_ticker_ledger`**

Add to `src/wheel_tracker/store.py`, immediately after `get_cycle_trades` (before `get_wheel_stats`):

```python
_OPTION_STRATEGY_LABELS = {
    ("PUT", "SELL_TO_OPEN"): "Cash-Secured Put",
    ("PUT", "BUY_TO_CLOSE"): "CSP Closed",
    ("PUT", "EXPIRED"): "CSP Closed",
    ("PUT", "BUY_TO_OPEN"): "Long Put",
    ("PUT", "SELL_TO_CLOSE"): "Long Put Closed",
    ("PUT", "ASSIGNED"): "Put Assigned",
    ("CALL", "SELL_TO_OPEN"): "Covered Call",
    ("CALL", "BUY_TO_CLOSE"): "Covered Call Closed",
    ("CALL", "EXPIRED"): "Covered Call Closed",
    ("CALL", "BUY_TO_OPEN"): "Long Call",
    ("CALL", "SELL_TO_CLOSE"): "Long Call Closed",
    ("CALL", "ASSIGNED"): "Call Assigned",
}
_EQUITY_STRATEGY_LABELS = {
    "BUY": "Shares Bought",
    "BUY_TO_OPEN": "Shares Bought",
    "SELL": "Shares Sold",
    "SELL_TO_CLOSE": "Shares Sold",
}


def _strategy_label(asset_type: str, option_type: str | None, instruction: str) -> str:
    """Pure mapping from a trade's (asset_type, option_type, instruction) to a
    human strategy label. Falls back to the raw instruction so no row is ever
    unlabeled — new/unseen instruction values still render something useful."""
    if asset_type == "OPTION":
        return _OPTION_STRATEGY_LABELS.get((option_type, instruction), instruction)
    if asset_type == "EQUITY":
        return _EQUITY_STRATEGY_LABELS.get(instruction, instruction)
    return instruction


def get_ticker_ledger(conn: sqlite3.Connection) -> list[dict]:
    """One entry per ticker (underlying, or symbol for equity rows), each with
    every EQUITY/OPTION trade for that ticker tagged with a strategy label, plus
    rollup totals. Replaces the old cycle-based grouping — no linking step that
    can drop a trade for not fitting a fixed CSP->assignment->CC sequence."""
    _prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    trade_rows = conn.execute(
        """
        SELECT * FROM wt_trades
        WHERE asset_type IN ('EQUITY', 'OPTION')
        ORDER BY executed_at
        """
    ).fetchall()
    position_rows = conn.execute(
        """
        SELECT underlying, symbol FROM wt_positions
        WHERE asset_type IN ('EQUITY', 'OPTION')
        """
    ).fetchall()
    conn.row_factory = _prev

    active_underlyings = {(r["underlying"] or r["symbol"]) for r in position_rows}

    groups: dict[str, list[dict]] = {}
    for row in trade_rows:
        trade = dict(row)
        trade["strategy"] = _strategy_label(trade["asset_type"], trade["option_type"], trade["instruction"])
        key = trade["underlying"] or trade["symbol"]
        groups.setdefault(key, []).append(trade)

    tickers = []
    for underlying, trades in groups.items():
        total_premium = sum(
            t["net_amount"] or 0 for t in trades if t["asset_type"] == "OPTION" and (t["net_amount"] or 0) > 0
        )
        realized_pnl = sum(t["net_amount"] or 0 for t in trades)
        tickers.append({
            "underlying": underlying,
            "status": "ACTIVE" if underlying in active_underlyings else "CLOSED",
            "total_premium": round(total_premium, 2),
            "realized_pnl": round(realized_pnl, 2),
            "trades": trades,
        })

    # Stable sort twice: recency first (secondary), then status (primary) —
    # a stable sort preserves the recency order within each status group.
    tickers.sort(key=lambda tk: tk["trades"][-1]["executed_at"], reverse=True)
    tickers.sort(key=lambda tk: tk["status"] != "ACTIVE")
    return tickers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_store.py -v`
Expected: all PASS (including the pre-existing tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/wheel_tracker/store.py tests/test_wheel_tracker_store.py
git commit -m "feat(wheel): add per-ticker ledger with strategy labels"
```

---

### Task 2: Redefine win-rate / ticker counts in `get_wheel_stats`

**Files:**
- Modify: `src/wheel_tracker/store.py` (`get_wheel_stats`)
- Test: `tests/test_wheel_tracker_store.py` (new tests near existing stats coverage — this file has no `get_wheel_stats` tests yet, add a new section at the end, before `test_insert_note`)

**Interfaces:**
- Consumes: `_trade()` test helper and `upsert_trade`/`upsert_position` from Task 1 (same file, no import changes needed).
- Produces: `get_wheel_stats(conn) -> dict` with keys `premium_mtd, premium_ytd, win_rate, total_tickers, active_tickers, max_short_put_delta` (renamed from `total_cycles`/`open_cycles`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_wheel_tracker_store.py`, after `test_get_ticker_ledger_orders_active_first_then_recency` (end of Task 1's additions):

```python
def test_wheel_stats_win_rate_counts_closed_legs_only():
    """A symbol with only an opening trade (still open) must not count toward
    win_rate at all — win_rate is closed-leg wins / closed-leg total."""
    from src.wheel_tracker.store import upsert_trade, get_wheel_stats

    conn = _conn()
    # Closed, net positive -> win
    upsert_trade(conn, _trade(
        schwab_transaction_id="w1", symbol="AAA   250117P00010000",
        instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00", net_amount=100.0,
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="w2", symbol="AAA   250117P00010000",
        instruction="EXPIRED", executed_at="2025-01-17T21:00:00", net_amount=0.0,
    ))
    # Closed, net negative -> loss
    upsert_trade(conn, _trade(
        schwab_transaction_id="l1", symbol="BBB   250117P00010000",
        instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00", net_amount=50.0,
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="l2", symbol="BBB   250117P00010000",
        instruction="BUY_TO_CLOSE", executed_at="2025-01-10T10:00:00", net_amount=-200.0,
    ))
    # Still open -> excluded entirely
    upsert_trade(conn, _trade(
        schwab_transaction_id="o1", symbol="CCC   250117P00010000",
        instruction="SELL_TO_OPEN", executed_at="2025-01-01T10:00:00", net_amount=75.0,
    ))

    stats = get_wheel_stats(conn)
    assert stats["win_rate"] == pytest.approx(0.5)


def test_wheel_stats_win_rate_none_when_no_closed_legs():
    from src.wheel_tracker.store import upsert_trade, get_wheel_stats

    conn = _conn()
    upsert_trade(conn, _trade(schwab_transaction_id="o1", instruction="SELL_TO_OPEN"))

    stats = get_wheel_stats(conn)
    assert stats["win_rate"] is None


def test_wheel_stats_ticker_counts():
    from src.wheel_tracker.store import upsert_trade, upsert_position, get_wheel_stats

    conn = _conn()
    upsert_trade(conn, _trade(
        schwab_transaction_id="t1", symbol="AAA   250117P00010000",
        underlying="AAA", instruction="SELL_TO_OPEN",
    ))
    upsert_trade(conn, _trade(
        schwab_transaction_id="t2", symbol="BBB   250117P00010000",
        underlying="BBB", instruction="SELL_TO_OPEN",
    ))
    upsert_position(conn, dict(
        account_id="ACC1", symbol="AAA   250117P00010000", underlying="AAA",
        asset_type="OPTION", option_type="PUT", strike=10.0, expiration="2025-01-17",
        dte=10, quantity=-1.0, average_price=1.0, current_price=0.5, market_value=-50.0,
        unrealized_pnl=50.0, delta=-0.2, refreshed_at="2025-01-07T17:00:00",
    ))

    stats = get_wheel_stats(conn)
    assert stats["total_tickers"] == 2
    assert stats["active_tickers"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_store.py -k wheel_stats -v`
Expected: FAIL — `KeyError` or `AssertionError` since current `get_wheel_stats` returns `total_cycles`/`open_cycles` and win_rate is cycle-based (all these fixtures have no `wt_cycles` rows, so current code returns `win_rate: None` and `total_cycles: 0` — assertions on `win_rate == 0.5` and `total_tickers` key will fail).

- [ ] **Step 3: Rewrite `get_wheel_stats`**

Replace the whole function body in `src/wheel_tracker/store.py`:

```python
def get_wheel_stats(conn: sqlite3.Connection) -> dict:
    from datetime import date

    today = date.today().isoformat()
    mtd_start = today[:7] + "-01"
    ytd_start = today[:4] + "-01-01"

    def _premium(start: str) -> float:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(net_amount), 0)
            FROM wt_trades
            WHERE asset_type = 'OPTION' AND net_amount > 0
              AND executed_at >= ?
            """,
            (start,),
        ).fetchone()
        return row[0]

    _OPEN_INSTR = ("SELL_TO_OPEN", "BUY_TO_OPEN")
    _CLOSE_INSTR = ("BUY_TO_CLOSE", "SELL_TO_CLOSE", "EXPIRED", "ASSIGNED")

    leg_rows = conn.execute(
        "SELECT symbol, instruction, net_amount FROM wt_trades WHERE asset_type = 'OPTION'"
    ).fetchall()
    legs: dict[str, dict] = {}
    for symbol, instruction, net_amount in leg_rows:
        leg = legs.setdefault(symbol, {"opened": False, "closed": False, "net": 0.0})
        leg["net"] += net_amount or 0
        if instruction in _OPEN_INSTR:
            leg["opened"] = True
        if instruction in _CLOSE_INSTR:
            leg["closed"] = True
    closed_legs = [leg for leg in legs.values() if leg["opened"] and leg["closed"]]
    won_legs = [leg for leg in closed_legs if leg["net"] > 0]

    total_tickers = conn.execute(
        """
        SELECT COUNT(DISTINCT COALESCE(underlying, symbol))
        FROM wt_trades WHERE asset_type IN ('EQUITY', 'OPTION')
        """
    ).fetchone()[0]
    active_tickers = conn.execute(
        """
        SELECT COUNT(DISTINCT COALESCE(underlying, symbol))
        FROM wt_positions WHERE asset_type IN ('EQUITY', 'OPTION')
        """
    ).fetchone()[0]

    max_delta_row = conn.execute(
        "SELECT MAX(ABS(delta)) FROM wt_positions WHERE asset_type='OPTION' AND quantity < 0 AND option_type='PUT'"
    ).fetchone()

    return {
        "premium_mtd": round(_premium(mtd_start), 2),
        "premium_ytd": round(_premium(ytd_start), 2),
        "win_rate": round(len(won_legs) / len(closed_legs), 3) if closed_legs else None,
        "total_tickers": total_tickers,
        "active_tickers": active_tickers,
        "max_short_put_delta": max_delta_row[0],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wheel_tracker/store.py tests/test_wheel_tracker_store.py
git commit -m "feat(wheel): redefine win_rate as per-closed-option-leg, add ticker counts"
```

---

### Task 3: Remove the cycle-linking engine

**Files:**
- Delete: `src/wheel_tracker/cycles.py`
- Delete: `tests/test_wheel_tracker_cycles.py`
- Modify: `src/wheel_tracker/store.py` (remove `create_cycle`, `update_cycle`, `set_trade_cycle`, `get_cycles`, `get_cycle_trades`)
- Modify: `src/wheel_tracker/sync.py` (remove `link_cycles` import + call in `run_sync`)
- Modify: `tests/test_wheel_tracker_store.py` (remove `test_create_and_update_cycle`)
- Modify: `tests/test_wheel_tracker_sync.py` (remove/replace the three `link_cycles` tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task only removes dead code. `run_sync()`'s return shape (`{"accounts_synced", "trades_imported", "positions_refreshed"}`) is unchanged; `check_alerts(conn)` still runs at the end of `run_sync`.

- [ ] **Step 1: Delete the cycle-linking module and its tests**

```bash
git rm src/wheel_tracker/cycles.py tests/test_wheel_tracker_cycles.py
```

- [ ] **Step 2: Remove cycle-management functions from `store.py`**

In `src/wheel_tracker/store.py`, delete these five functions in full — `insert_note` (just before `create_cycle`) and `get_open_positions` (just after `get_cycle_trades`) must both stay untouched:

```python
def create_cycle(conn: sqlite3.Connection, cycle: dict) -> int:
    cursor = conn.execute(
        """
        INSERT INTO wt_cycles
            (underlying, account_id, status, opened_at, closed_at,
             total_premium, realized_pnl, auto_detected)
        VALUES
            (:underlying, :account_id, :status, :opened_at, :closed_at,
             :total_premium, :realized_pnl, :auto_detected)
        """,
        cycle,
    )
    conn.commit()
    return cursor.lastrowid


def update_cycle(conn: sqlite3.Connection, cycle_id: int, updates: dict) -> None:
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    conn.execute(
        f"UPDATE wt_cycles SET {set_clause} WHERE id = :_id",
        {**updates, "_id": cycle_id},
    )
    conn.commit()


def set_trade_cycle(conn: sqlite3.Connection, trade_id: int, cycle_id: int) -> None:
    conn.execute("UPDATE wt_trades SET cycle_id = ? WHERE id = ?", (cycle_id, trade_id))
    conn.commit()
```

(These three sit between `get_unlinked_trades`/`get_distinct_accounts` and `insert_note` — delete them, keep `get_unlinked_trades`, `get_distinct_accounts`, and `insert_note`.)

```python
def get_cycles(conn: sqlite3.Connection, status: str | None = None, limit: int = 50) -> list[dict]:
    _prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    if status:
        rows = conn.execute(
            "SELECT * FROM wt_cycles WHERE status = ? ORDER BY opened_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM wt_cycles ORDER BY status ASC, opened_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.row_factory = _prev
    return [dict(r) for r in rows]


def get_cycle_trades(conn: sqlite3.Connection, cycle_id: int) -> list[dict]:
    _prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM wt_trades WHERE cycle_id = ? ORDER BY executed_at",
        (cycle_id,),
    ).fetchall()
    conn.row_factory = _prev
    return [dict(r) for r in rows]
```

(These two sit between the "---- API query helpers ----" comment / `get_open_positions` and `get_wheel_stats` — delete them, keep `get_open_positions` and `get_wheel_stats`.)

- [ ] **Step 3: Remove `test_create_and_update_cycle` from the store tests**

In `tests/test_wheel_tracker_store.py`, delete the `test_create_and_update_cycle` function (it calls the now-deleted `create_cycle`/`update_cycle`).

- [ ] **Step 4: Remove the `link_cycles` call from `run_sync`**

In `src/wheel_tracker/sync.py`, find this block near the end of `run_sync`:

```python
            # MCP session closed — now do CPU-only work on the populated tables
            from .alerts import check_alerts
            from .cycles import link_cycles

            new_cycles = link_cycles(conn)
            logger.info("wheel_tracker: linked %d new cycle(s)", new_cycles)
            alerts_sent = await check_alerts(conn)
            logger.info("wheel_tracker: sent %d alert(s)", len(alerts_sent))
```

Replace it with:

```python
            # MCP session closed — now do CPU-only work on the populated tables
            from .alerts import check_alerts

            alerts_sent = await check_alerts(conn)
            logger.info("wheel_tracker: sent %d alert(s)", len(alerts_sent))
```

- [ ] **Step 5: Update `tests/test_wheel_tracker_sync.py`**

Replace `test_run_sync_calls_link_cycles_and_check_alerts` and `test_run_sync_link_cycles_failure_is_caught_non_fatally` (delete both) with a single replacement test, and simplify `test_run_sync_check_alerts_failure_is_caught_non_fatally` to drop its `link_cycles` patch. The whole block from `async def test_run_sync_calls_link_cycles_and_check_alerts` through the end of `test_run_sync_check_alerts_failure_is_caught_non_fatally` becomes:

```python
@pytest.mark.asyncio
async def test_run_sync_calls_check_alerts(conn):
    """After a successful MCP sync, run_sync must invoke alerts.check_alerts
    on the populated tables (cycle-linking was removed — see ticker-ledger
    design, 2026-08-10)."""
    from src.wheel_tracker.sync import run_sync

    session = _mock_session("[]", "[]", "{}")
    patch_stream, patch_client_session = _patch_mcp_transport(session)

    with (
        patch_stream,
        patch_client_session,
        patch("src.wheel_tracker.sync._schwab_url", return_value="http://fake-schwab-mcp"),
        patch(
            "src.wheel_tracker.alerts.check_alerts",
            new=AsyncMock(return_value=["alert1", "alert2"]),
        ) as mock_alerts,
    ):
        await run_sync(conn)

        mock_alerts.assert_called_once_with(conn)


@pytest.mark.asyncio
async def test_run_sync_check_alerts_failure_is_caught_non_fatally(conn):
    """A raised exception from check_alerts must be caught by run_sync's existing
    exception handling (logged, not propagated)."""
    from src.wheel_tracker.sync import run_sync

    session = _mock_session("[]", "[]", "{}")
    patch_stream, patch_client_session = _patch_mcp_transport(session)

    with (
        patch_stream,
        patch_client_session,
        patch("src.wheel_tracker.sync._schwab_url", return_value="http://fake-schwab-mcp"),
        patch(
            "src.wheel_tracker.alerts.check_alerts", new=AsyncMock(side_effect=RuntimeError("boom"))
        ),
    ):
        summary = await run_sync(conn)  # must not raise

        assert summary == {"accounts_synced": 0, "trades_imported": 0, "positions_refreshed": 0}
```

- [ ] **Step 6: Run the full wheel-tracker test suite**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_store.py tests/test_wheel_tracker_sync.py tests/test_wheel_tracker_alerts.py -v`
Expected: all PASS, no references to `cycles` module remain (grep to confirm: `grep -rn "wheel_tracker.cycles\|link_cycles" src/ tests/` returns nothing).

- [ ] **Step 7: Commit**

```bash
git add -A src/wheel_tracker tests/test_wheel_tracker_store.py tests/test_wheel_tracker_sync.py tests/test_wheel_tracker_cycles.py
git commit -m "refactor(wheel): remove cycle-linking engine, superseded by ticker ledger"
```

---

### Task 4: Swap the `/api/wheel/cycles` endpoint for `/api/wheel/tickers`

**Files:**
- Modify: `src/api/main.py` (import block ~line 69-74, endpoint block ~line 938-950)

**Interfaces:**
- Consumes: `get_ticker_ledger` from Task 1 (`src/wheel_tracker/store.py`).
- Produces: `GET /api/wheel/tickers` returning `{"tickers": [...]}` (same shape `get_ticker_ledger` returns).

- [ ] **Step 1: Update the import block**

In `src/api/main.py`, replace:

```python
from ..wheel_tracker.store import (
    get_open_positions as wt_get_positions,
    get_cycles as wt_get_cycles,
    get_cycle_trades as wt_get_cycle_trades,
    get_wheel_stats as wt_get_stats,
)
```

with:

```python
from ..wheel_tracker.store import (
    get_open_positions as wt_get_positions,
    get_ticker_ledger as wt_get_ticker_ledger,
    get_wheel_stats as wt_get_stats,
)
```

- [ ] **Step 2: Replace the `/api/wheel/cycles` endpoint**

Replace:

```python
@app.get("/api/wheel/cycles")
def wheel_cycles(req: Request, status: str | None = None, limit: int = 50):
    try:
        with closing(sqlite3.connect(settings.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cycles = wt_get_cycles(conn, status=status, limit=limit)
            # Attach trade legs to each cycle
            for cycle in cycles:
                cycle["trades"] = wt_get_cycle_trades(conn, cycle["id"])
            return {"cycles": cycles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

with:

```python
@app.get("/api/wheel/tickers")
def wheel_tickers(req: Request):
    try:
        with closing(sqlite3.connect(settings.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return {"tickers": wt_get_ticker_ledger(conn)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 3: Restart the dev API container and smoke-test the endpoint**

Run:
```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build api
curl -s https://dev-mi.austin10berge.com/api/wheel/tickers | python3 -m json.tool | head -30
```
Expected: valid JSON with a `"tickers"` array; no 500 error. Also confirm the old route is gone: `curl -s -o /dev/null -w '%{http_code}' https://dev-mi.austin10berge.com/api/wheel/cycles` → `404`.

- [ ] **Step 4: Commit**

```bash
git add src/api/main.py
git commit -m "feat(wheel): replace /api/wheel/cycles with /api/wheel/tickers"
```

---

### Task 5: Frontend — render one card per ticker with strategy-labeled trades

**Files:**
- Modify: `src/web/v2/wheel.js`

**Interfaces:**
- Consumes: `GET /api/wheel/tickers` → `{"tickers": [{underlying, status, total_premium, realized_pnl, trades: [{...wt_trades columns, strategy}]}]}` (Task 4). `GET /api/wheel/stats` → now has `total_tickers`/`active_tickers` instead of `total_cycles`/`open_cycles` (Task 2).
- Produces: no new exports — `window.WheelView` keeps the same `{render, teardown}` shape other pages depend on.

- [ ] **Step 1: Rename `renderCycles` to `renderTickers` and change the row/card markup**

Replace the whole `renderCycles` function in `src/web/v2/wheel.js` with:

```javascript
    function renderTickers(tickers) {
        if (!tickers.length) return `<div class="list-message">No wheel activity yet — run the nightly sync to populate</div>`;
        return tickers.map((tk, i) => {
            const isActive  = tk.status === 'ACTIVE';
            const pnl       = tk.realized_pnl;
            const pnlColor  = pnl != null ? (pnl >= 0 ? 'var(--tv-green)' : 'var(--tv-red)') : 'var(--tv-muted)';
            const statusBg  = isActive ? 'rgba(41,98,255,0.12)' : 'rgba(120,123,134,0.12)';
            const statusClr = isActive ? '#5B8AF5' : 'var(--tv-muted)';
            const trades    = tk.trades || [];
            const tradeRows = trades.map(t => `
                <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--tv-border)">
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--tv-muted)">
                        ${esc(t.strategy||t.instruction||'')}${t.strike?' $'+parseFloat(t.strike).toFixed(0):''} · ${(t.executed_at||'').slice(0,10)}
                    </span>
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:${(t.net_amount||0)>=0?'var(--tv-green)':'var(--tv-red)'}">
                        ${t.net_amount!=null?((t.net_amount>=0?'+':'')+fmtMoney(t.net_amount)):'—'}
                    </span>
                </div>`).join('');
            return `
            <div class="overview-card" style="margin:6px 14px;animation:row-in 0.32s ease both;animation-delay:${i*40}ms">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:${trades.length?'8px':'0'}">
                    <div style="display:flex;align-items:center;gap:8px">
                        <span style="font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:600;color:#fff">${esc(tk.underlying||'')}</span>
                        <span style="font-size:11px;padding:2px 6px;border-radius:4px;background:${statusBg};color:${statusClr};font-family:'IBM Plex Mono',monospace;font-weight:600;letter-spacing:0.03em">${esc(tk.status||'')}</span>
                    </div>
                    <div style="text-align:right">
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;color:${pnlColor}">
                            ${pnl!=null?((pnl>=0?'+':'')+fmtMoney(pnl)):'—'}
                        </div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--tv-muted)">
                            premium ${fmtMoney(tk.total_premium)}
                        </div>
                    </div>
                </div>
                ${trades.length ? `
                <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--tv-muted);letter-spacing:0.05em;text-transform:uppercase;padding:2px 0">
                    ${trades.length} transaction${trades.length>1?'s':''}
                </div>
                <div style="margin-top:4px">${tradeRows}</div>` : ''}
            </div>`;
        }).join('');
    }
```

Note: all trades render unconditionally now (no `<details>` toggle) — hiding legs by default is what made covered calls hard to notice before.

- [ ] **Step 2: Update `renderStats` to use the renamed stat keys**

In `renderStats`, replace:

```javascript
                <div style="font-size:12px;color:var(--tv-muted);margin-top:2px">${s.total_cycles ?? 0} cycles · ${s.open_cycles ?? 0} open</div>
```

with:

```javascript
                <div style="font-size:12px;color:var(--tv-muted);margin-top:2px">${s.total_tickers ?? 0} tickers · ${s.active_tickers ?? 0} active</div>
```

- [ ] **Step 3: Update the section header, fetch call, and render wiring in `render()`**

In the `render(el)` function, replace:

```javascript
            <div class="section-header" style="padding-top:4px">
                <span class="section-title">Wheel Cycles</span>
            </div>
            <div id="whl-cycles" style="padding-bottom:16px"><div class="list-message loading">Loading…</div></div>
```

with:

```javascript
            <div class="section-header" style="padding-top:4px">
                <span class="section-title">Wheel Tickers</span>
            </div>
            <div id="whl-tickers" style="padding-bottom:16px"><div class="list-message loading">Loading…</div></div>
```

Then replace the `Promise.all` block:

```javascript
        Promise.all([
            fetch(`${base}/wheel/stats`).then(r => r.json()),
            fetch(`${base}/wheel/positions`).then(r => r.json()),
            fetch(`${base}/wheel/cycles`).then(r => r.json()),
        ]).then(([stats, posData, cycData]) => {
            if (!document.getElementById('whl-stats')) return;
            document.getElementById('whl-stats').innerHTML     = renderStats(stats);
            document.getElementById('whl-positions').innerHTML = renderPositions(posData.positions || []);
            document.getElementById('whl-cycles').innerHTML    = renderCycles(cycData.cycles || []);
            const badge = document.getElementById('whl-badge');
            if (badge) { badge.className = 'data-freshness-badge fresh'; badge.textContent = 'Live'; }
        }).catch(err => {
```

with:

```javascript
        Promise.all([
            fetch(`${base}/wheel/stats`).then(r => r.json()),
            fetch(`${base}/wheel/positions`).then(r => r.json()),
            fetch(`${base}/wheel/tickers`).then(r => r.json()),
        ]).then(([stats, posData, tickerData]) => {
            if (!document.getElementById('whl-stats')) return;
            document.getElementById('whl-stats').innerHTML   = renderStats(stats);
            document.getElementById('whl-positions').innerHTML = renderPositions(posData.positions || []);
            document.getElementById('whl-tickers').innerHTML = renderTickers(tickerData.tickers || []);
            const badge = document.getElementById('whl-badge');
            if (badge) { badge.className = 'data-freshness-badge fresh'; badge.textContent = 'Live'; }
        }).catch(err => {
```

- [ ] **Step 4: Verify in the browser with Playwright**

Navigate to `https://dev-mi.austin10berge.com/v2/` (or wherever the wheel tab is linked from — check `src/web/v2/app.js` for the route/tab name if unsure), open the Wheel tab, and use `browser_snapshot` to confirm:
- Exactly one card per ticker symbol (no duplicates — check SOFI, IOT, HOOD, DRAM specifically, which had 23/7/4/4 cycle rows before this change).
- At least one card shows a "Covered Call" or "Covered Call Closed" row (the 21 previously-hidden `SELL_TO_OPEN CALL` trades from the live DB).
- The stats bar shows "N tickers · N active" instead of "N cycles · N open".

Take a `browser_take_screenshot` as visual proof.

- [ ] **Step 5: Commit**

```bash
git add src/web/v2/wheel.js
git commit -m "feat(wheel): render one card per ticker with all trades and strategy labels"
```

---

### Task 6: Update the Discord bot's schema description

**Files:**
- Modify: `discord_bot/trade_system_prompt.txt` (lines ~118-139, the "Wheel Tracker Database" section)

**Interfaces:** None — this is a prompt-text change only, no code interface.

- [ ] **Step 1: Replace the wt_trades/wt_cycles description**

In `discord_bot/trade_system_prompt.txt`, replace:

```
wt_trades: Every executed Schwab transaction. Key columns: id, schwab_transaction_id, account_id,
  executed_at (ISO 8601), asset_type (EQUITY|OPTION), symbol (OCC symbol or ticker), underlying,
  option_type (PUT|CALL|null), strike, expiration, instruction (SELL_TO_OPEN/BUY_TO_CLOSE/
  EXPIRED/ASSIGNED/BUY/SELL/…), quantity (negative=short), price, commission, net_amount
  (positive=cash received), cycle_id (FK to wt_cycles, null if unlinked).

wt_positions: Current open positions (refreshed each nightly pipeline run). Key columns:
  id, account_id, symbol, underlying, asset_type, option_type, strike, expiration, dte,
  quantity (negative=short), average_price, current_price, market_value, unrealized_pnl, delta,
  cycle_id, refreshed_at.

wt_cycles: Wheel cycles grouping related trades. Key columns: id, underlying, account_id,
  status (OPEN|CLOSED), opened_at, closed_at, total_premium (sum of option credits received),
  realized_pnl (total P/L when closed), auto_detected (1=system-linked).

wt_notes: User notes. Key columns: id, trade_id (FK), cycle_id (FK), source (discord|dashboard),
  content, created_at.

Use these tables to answer questions like "what are my open CSPs", "show my AAPL wheel cycle",
"what's my total premium collected this month", "what's my win rate on CSPs".

Example: SELECT * FROM wt_positions WHERE asset_type='OPTION' AND quantity < 0 AND option_type='PUT';
```

with:

```
wt_trades: Every executed Schwab transaction. Key columns: id, schwab_transaction_id, account_id,
  executed_at (ISO 8601), asset_type (EQUITY|OPTION), symbol (OCC symbol or ticker), underlying,
  option_type (PUT|CALL|null), strike, expiration, instruction (SELL_TO_OPEN/BUY_TO_CLOSE/
  EXPIRED/ASSIGNED/BUY/SELL/…), quantity (negative=short), price, commission, net_amount
  (positive=cash received).

wt_positions: Current open positions (refreshed each nightly pipeline run). Key columns:
  id, account_id, symbol, underlying, asset_type, option_type, strike, expiration, dte,
  quantity (negative=short), average_price, current_price, market_value, unrealized_pnl, delta,
  refreshed_at.

wt_notes: User notes. Key columns: id, trade_id (FK), source (discord|dashboard), content, created_at.

There is no cycle/grouping table — group wt_trades yourself by COALESCE(underlying, symbol) to
answer per-ticker questions (e.g. "show my AAPL wheel activity" -> WHERE underlying='AAPL' OR
symbol='AAPL'). A trade's strategy (CSP, covered call, assignment, etc.) is inferred from
(asset_type, option_type, instruction): SELL_TO_OPEN+PUT=cash-secured put,
SELL_TO_OPEN+CALL=covered call, ASSIGNED+PUT=shares assigned in, ASSIGNED+CALL=shares called away.

Use these tables to answer questions like "what are my open CSPs", "show my AAPL wheel activity",
"what's my total premium collected this month", "what's my win rate on CSPs" (closed option legs
only — a symbol with both an opening and closing trade, net premium > 0 = win).

Example: SELECT * FROM wt_positions WHERE asset_type='OPTION' AND quantity < 0 AND option_type='PUT';
```

- [ ] **Step 2: Commit**

```bash
git add discord_bot/trade_system_prompt.txt
git commit -m "docs(wheel): update trade-chat schema description for retired cycle table"
```

---

### Task 7: Full verification pass

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend test suite**

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -q`
Expected: all PASS, 0 failures.

- [ ] **Step 2: Confirm no dangling references to the removed cycle engine**

Run: `grep -rn "wheel_tracker.cycles\|link_cycles\|get_cycles\|get_cycle_trades\|create_cycle(\|update_cycle(\|set_trade_cycle" src/ tests/ discord_bot/`
Expected: no output.

- [ ] **Step 3: Restart dev containers and re-verify the live page**

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build api dashboard
```

Then use Playwright MCP against `https://dev-mi.austin10berge.com/v2/`: open the Wheel tab, `browser_snapshot`, and confirm again (post full-suite changes) that SOFI/IOT/HOOD/DRAM each show as a single card and that covered-call rows are visible. `browser_console_messages` should show no JS errors.

- [ ] **Step 4: Report results to the user**

No commit for this task — it's a verification pass. Summarize pass/fail counts and the Playwright observations back to the user.
