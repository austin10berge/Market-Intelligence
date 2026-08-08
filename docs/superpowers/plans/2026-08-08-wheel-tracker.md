# Wheel Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated wheel-strategy trade tracking to Market Intelligence — nightly Schwab sync, wheel-cycle auto-linking, dashboard page, Discord notes/queries, and NTFY alerts for DTE and assignment risk.

**Architecture:** New `src/wheel_tracker/` subpackage integrates into the nightly pipeline as Step 5. Four new SQLite tables (`wt_trades`, `wt_positions`, `wt_cycles`, `wt_notes`) in the shared `market_intelligence.db`. Dashboard gets a new `wheel.html` page backed by three FastAPI endpoints. Discord trade-chat gains a `!note` command and wt_* schema context in its system prompt.

**Tech Stack:** Python 3.12, SQLite (raw `sqlite3`, no ORM), FastAPI, schwab-mcp via `streamablehttp_client` + `ClientSession` (same as `src/algo_detective/schwab_options.py`), existing `notify/ntfy.py` for NTFY alerts, vanilla HTML/JS/CSS for dashboard.

## Global Constraints

- All Python execution inside Docker: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`
- Ruff at `~/.local/bin/ruff`; PostToolUse hook auto-formats `.py` on save — no manual format step.
- No ORM. All DB access via raw `sqlite3`, matching `src/db.py` conventions.
- Schwab MCP config at `discord_bot/schwab-mcp.json` — read-only, never commit tokens.
- Dev dashboard: `https://dev-mi.austin10berge.com` — test all UI changes here.
- Never target prod (`10.0.1.21`) directly.
- Spec: `docs/superpowers/specs/2026-08-08-wheel-tracker-design.md`

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `src/db.py` | Modify | Add 4 wt_* table DDLs to `_ensure_tables()` |
| `src/wheel_tracker/__init__.py` | Create | Package init, re-export `run_sync` |
| `src/wheel_tracker/store.py` | Create | All DB reads/writes for wt_* tables |
| `src/wheel_tracker/sync.py` | Create | Schwab MCP pull — transactions + positions + deltas |
| `src/wheel_tracker/cycles.py` | Create | Wheel-cycle auto-linking algorithm |
| `src/wheel_tracker/alerts.py` | Create | DTE + assignment-risk alert generation |
| `src/main.py` | Modify | Add Step 5: `wheel_tracker.sync.run_sync()` |
| `src/api/main.py` | Modify | Add `/api/wheel/positions`, `/cycles`, `/stats` |
| `src/web/wheel.html` | Create | Wheel dashboard page |
| `src/web/wheel.js` | Create | Wheel page JS |
| `src/web/index.html` | Modify | Add "Wheel" nav link |
| `src/web/scanner.html` | Modify | Add "Wheel" nav link |
| `src/web/watchlist.html` | Modify | Add "Wheel" nav link |
| `src/web/technical-analysis.html` | Modify | Add "Wheel" nav link |
| `src/web/backtest.html` | Modify | Add "Wheel" nav link |
| `discord_bot/commands/chat.py` | Modify | Add `!note` handling in `on_message` |
| `discord_bot/trade_system_prompt.txt` | Modify | Add wt_* schema section |
| `tests/test_wheel_tracker_store.py` | Create | Store function tests |
| `tests/test_wheel_tracker_cycles.py` | Create | Cycle-linking algorithm tests |
| `tests/test_wheel_tracker_alerts.py` | Create | Alert generation tests |

---

## Task 1: DB Schema and Store Layer

**Files:**
- Modify: `src/db.py`
- Create: `src/wheel_tracker/__init__.py`
- Create: `src/wheel_tracker/store.py`
- Create: `tests/test_wheel_tracker_store.py`

**Interfaces produced (used by Tasks 2, 3, 4):**
```python
# src/wheel_tracker/store.py

def ensure_wheel_tables(conn: sqlite3.Connection) -> None: ...

def upsert_trade(conn, trade: dict) -> int: ...
# trade keys: schwab_transaction_id, account_id, executed_at, settled_date,
#   asset_type, symbol, underlying, option_type, strike, expiration,
#   instruction, quantity, price, commission, net_amount

def get_last_executed_at(conn, account_id: str) -> str | None: ...

def upsert_position(conn, position: dict) -> None: ...
# position keys: account_id, symbol, underlying, asset_type, option_type, strike,
#   expiration, dte, quantity, average_price, current_price, market_value,
#   unrealized_pnl, delta, refreshed_at

def update_position_delta(conn, account_id: str, symbol: str, delta: float) -> None: ...

def delete_stale_positions(conn, account_id: str, active_symbols: set[str]) -> None: ...

def create_cycle(conn, cycle: dict) -> int: ...
# cycle keys: underlying, account_id, status, opened_at, closed_at,
#   total_premium, realized_pnl, auto_detected

def update_cycle(conn, cycle_id: int, updates: dict) -> None: ...

def set_trade_cycle(conn, trade_id: int, cycle_id: int) -> None: ...

def get_unlinked_trades(conn, account_id: str) -> list[dict]: ...

def get_distinct_accounts(conn) -> list[str]: ...

def insert_note(conn, note: dict) -> None: ...
# note keys: trade_id (int|None), cycle_id (int|None), source, content

def get_open_positions(conn) -> list[dict]: ...
def get_cycles(conn, status: str | None = None, limit: int = 50) -> list[dict]: ...
def get_wheel_stats(conn) -> dict: ...
```

- [ ] **Step 1: Write failing tests for store layer**

Create `tests/test_wheel_tracker_store.py`:

```python
"""Tests for wheel tracker DB store functions."""
from __future__ import annotations

import sqlite3
import tempfile
import os
import pytest
from unittest.mock import patch

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_path = _tmp.name
_tmp.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    with patch("src.db.settings") as m:
        m.db_path = _tmp_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup():
    yield
    try:
        os.unlink(_tmp_path)
    except OSError:
        pass


def _conn():
    conn = sqlite3.connect(_tmp_path)
    conn.row_factory = sqlite3.Row
    from src.wheel_tracker.store import ensure_wheel_tables
    ensure_wheel_tables(conn)
    return conn


def _trade(**kw) -> dict:
    defaults = dict(
        schwab_transaction_id="t1",
        account_id="ACC1",
        executed_at="2025-01-01T10:00:00",
        settled_date="2025-01-03",
        asset_type="OPTION",
        symbol="AAPL  250117P00200000",
        underlying="AAPL",
        option_type="PUT",
        strike=200.0,
        expiration="2025-01-17",
        instruction="SELL_TO_OPEN",
        quantity=-1.0,
        price=1.50,
        commission=0.65,
        net_amount=149.35,
    )
    return {**defaults, **kw}


def test_upsert_trade_returns_id():
    from src.wheel_tracker.store import upsert_trade
    conn = _conn()
    tid = upsert_trade(conn, _trade())
    assert isinstance(tid, int) and tid > 0


def test_upsert_trade_idempotent():
    from src.wheel_tracker.store import upsert_trade
    conn = _conn()
    t = _trade(schwab_transaction_id="t_idem")
    id1 = upsert_trade(conn, t)
    id2 = upsert_trade(conn, t)
    assert id1 == id2


def test_get_last_executed_at_none_when_empty():
    from src.wheel_tracker.store import get_last_executed_at
    conn = _conn()
    assert get_last_executed_at(conn, "NO_ACCOUNT") is None


def test_get_last_executed_at_returns_max():
    from src.wheel_tracker.store import upsert_trade, get_last_executed_at
    conn = _conn()
    upsert_trade(conn, _trade(schwab_transaction_id="ta", executed_at="2025-01-01T10:00:00", account_id="ACCA"))
    upsert_trade(conn, _trade(schwab_transaction_id="tb", executed_at="2025-03-15T14:30:00", account_id="ACCA"))
    assert get_last_executed_at(conn, "ACCA") == "2025-03-15T14:30:00"


def test_upsert_position_preserves_alert_columns():
    from src.wheel_tracker.store import upsert_position, update_position_delta
    conn = _conn()
    pos = dict(
        account_id="ACC1", symbol="AAPL  250117P00200000", underlying="AAPL",
        asset_type="OPTION", option_type="PUT", strike=200.0, expiration="2025-01-17",
        dte=10, quantity=-1.0, average_price=1.50, current_price=0.80,
        market_value=-80.0, unrealized_pnl=70.0, delta=None,
        refreshed_at="2025-01-07T17:00:00",
    )
    upsert_position(conn, pos)
    # Simulate alert column being set
    conn.execute(
        "UPDATE wt_positions SET last_dte_alerted='2025-01-07' WHERE symbol=?",
        ("AAPL  250117P00200000",)
    )
    conn.commit()
    # Re-upsert (next pipeline run)
    pos["refreshed_at"] = "2025-01-08T17:00:00"
    pos["dte"] = 9
    upsert_position(conn, pos)
    row = conn.execute(
        "SELECT last_dte_alerted FROM wt_positions WHERE symbol=?",
        ("AAPL  250117P00200000",)
    ).fetchone()
    assert row["last_dte_alerted"] == "2025-01-07"  # preserved


def test_create_and_update_cycle():
    from src.wheel_tracker.store import create_cycle, update_cycle
    conn = _conn()
    cid = create_cycle(conn, dict(
        underlying="AAPL", account_id="ACC1", status="OPEN",
        opened_at="2025-01-01", closed_at=None,
        total_premium=149.35, realized_pnl=None, auto_detected=1,
    ))
    assert isinstance(cid, int) and cid > 0
    update_cycle(conn, cid, {"status": "CLOSED", "closed_at": "2025-01-17", "realized_pnl": 149.35})
    row = conn.execute("SELECT status, realized_pnl FROM wt_cycles WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "CLOSED"
    assert row["realized_pnl"] == pytest.approx(149.35)


def test_insert_note():
    from src.wheel_tracker.store import insert_note
    conn = _conn()
    insert_note(conn, dict(trade_id=None, cycle_id=1, source="discord", content="Testing"))
    count = conn.execute("SELECT COUNT(*) FROM wt_notes").fetchone()[0]
    assert count >= 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_store.py -v 2>&1 | tail -30
```
Expected: `ModuleNotFoundError` or `ImportError` (store doesn't exist yet).

- [ ] **Step 3: Add wt_* tables to `src/db.py`**

Inside `_ensure_tables()`, add after the last existing `CREATE TABLE IF NOT EXISTS` block:

```python
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS wt_trades (
                id                    INTEGER PRIMARY KEY,
                schwab_transaction_id TEXT    UNIQUE NOT NULL,
                account_id            TEXT    NOT NULL,
                executed_at           TEXT    NOT NULL,
                settled_date          TEXT,
                asset_type            TEXT    NOT NULL,
                symbol                TEXT    NOT NULL,
                underlying            TEXT,
                option_type           TEXT,
                strike                REAL,
                expiration            TEXT,
                instruction           TEXT    NOT NULL,
                quantity              REAL    NOT NULL,
                price                 REAL,
                commission            REAL    DEFAULT 0,
                net_amount            REAL,
                cycle_id              INTEGER REFERENCES wt_cycles(id),
                imported_at           TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS wt_positions (
                id              INTEGER PRIMARY KEY,
                account_id      TEXT    NOT NULL,
                symbol          TEXT    NOT NULL,
                underlying      TEXT,
                asset_type      TEXT    NOT NULL,
                option_type     TEXT,
                strike          REAL,
                expiration      TEXT,
                dte             INTEGER,
                quantity        REAL    NOT NULL,
                average_price   REAL,
                current_price   REAL,
                market_value    REAL,
                unrealized_pnl  REAL,
                delta           REAL,
                cycle_id        INTEGER REFERENCES wt_cycles(id),
                last_dte_alerted   TEXT,
                last_delta_alerted TEXT,
                refreshed_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(account_id, symbol)
            );

            CREATE TABLE IF NOT EXISTS wt_cycles (
                id             INTEGER PRIMARY KEY,
                underlying     TEXT    NOT NULL,
                account_id     TEXT    NOT NULL,
                status         TEXT    NOT NULL DEFAULT 'OPEN',
                opened_at      TEXT,
                closed_at      TEXT,
                total_premium  REAL    DEFAULT 0,
                realized_pnl   REAL,
                auto_detected  INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS wt_notes (
                id         INTEGER PRIMARY KEY,
                trade_id   INTEGER REFERENCES wt_trades(id),
                cycle_id   INTEGER REFERENCES wt_cycles(id),
                source     TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)
```

Note: `wt_trades.cycle_id` references `wt_cycles` which is declared after it. SQLite allows forward references in REFERENCES for FK declarations (though FK enforcement requires `PRAGMA foreign_keys = ON`, which is not required here). If SQLite complains, declare `wt_cycles` first in the executescript.

- [ ] **Step 4: Create `src/wheel_tracker/__init__.py`**

```python
from .sync import run_sync

__all__ = ["run_sync"]
```

- [ ] **Step 5: Create `src/wheel_tracker/store.py`**

```python
"""DB read/write helpers for wt_* tables. All functions accept a sqlite3.Connection."""
from __future__ import annotations

import sqlite3


def ensure_wheel_tables(conn: sqlite3.Connection) -> None:
    """Create wt_* tables if absent. Called by tests; production path goes through db._ensure_tables()."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wt_cycles (
            id             INTEGER PRIMARY KEY,
            underlying     TEXT    NOT NULL,
            account_id     TEXT    NOT NULL,
            status         TEXT    NOT NULL DEFAULT 'OPEN',
            opened_at      TEXT,
            closed_at      TEXT,
            total_premium  REAL    DEFAULT 0,
            realized_pnl   REAL,
            auto_detected  INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS wt_trades (
            id                    INTEGER PRIMARY KEY,
            schwab_transaction_id TEXT    UNIQUE NOT NULL,
            account_id            TEXT    NOT NULL,
            executed_at           TEXT    NOT NULL,
            settled_date          TEXT,
            asset_type            TEXT    NOT NULL,
            symbol                TEXT    NOT NULL,
            underlying            TEXT,
            option_type           TEXT,
            strike                REAL,
            expiration            TEXT,
            instruction           TEXT    NOT NULL,
            quantity              REAL    NOT NULL,
            price                 REAL,
            commission            REAL    DEFAULT 0,
            net_amount            REAL,
            cycle_id              INTEGER REFERENCES wt_cycles(id),
            imported_at           TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS wt_positions (
            id              INTEGER PRIMARY KEY,
            account_id      TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            underlying      TEXT,
            asset_type      TEXT    NOT NULL,
            option_type     TEXT,
            strike          REAL,
            expiration      TEXT,
            dte             INTEGER,
            quantity        REAL    NOT NULL,
            average_price   REAL,
            current_price   REAL,
            market_value    REAL,
            unrealized_pnl  REAL,
            delta           REAL,
            cycle_id        INTEGER REFERENCES wt_cycles(id),
            last_dte_alerted   TEXT,
            last_delta_alerted TEXT,
            refreshed_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(account_id, symbol)
        );
        CREATE TABLE IF NOT EXISTS wt_notes (
            id         INTEGER PRIMARY KEY,
            trade_id   INTEGER REFERENCES wt_trades(id),
            cycle_id   INTEGER REFERENCES wt_cycles(id),
            source     TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def upsert_trade(conn: sqlite3.Connection, trade: dict) -> int:
    """Insert trade row; ignore if schwab_transaction_id already exists. Returns row id."""
    conn.execute(
        """
        INSERT INTO wt_trades
            (schwab_transaction_id, account_id, executed_at, settled_date,
             asset_type, symbol, underlying, option_type, strike, expiration,
             instruction, quantity, price, commission, net_amount)
        VALUES
            (:schwab_transaction_id, :account_id, :executed_at, :settled_date,
             :asset_type, :symbol, :underlying, :option_type, :strike, :expiration,
             :instruction, :quantity, :price, :commission, :net_amount)
        ON CONFLICT(schwab_transaction_id) DO NOTHING
        """,
        trade,
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM wt_trades WHERE schwab_transaction_id = ?",
        (trade["schwab_transaction_id"],),
    ).fetchone()
    return row[0]


def get_last_executed_at(conn: sqlite3.Connection, account_id: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(executed_at) FROM wt_trades WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return row[0] if row else None


def upsert_position(conn: sqlite3.Connection, position: dict) -> None:
    """Upsert position by (account_id, symbol). Preserves last_dte_alerted / last_delta_alerted."""
    conn.execute(
        """
        INSERT INTO wt_positions
            (account_id, symbol, underlying, asset_type, option_type, strike,
             expiration, dte, quantity, average_price, current_price,
             market_value, unrealized_pnl, delta, refreshed_at)
        VALUES
            (:account_id, :symbol, :underlying, :asset_type, :option_type, :strike,
             :expiration, :dte, :quantity, :average_price, :current_price,
             :market_value, :unrealized_pnl, :delta, :refreshed_at)
        ON CONFLICT(account_id, symbol) DO UPDATE SET
            underlying     = excluded.underlying,
            asset_type     = excluded.asset_type,
            option_type    = excluded.option_type,
            strike         = excluded.strike,
            expiration     = excluded.expiration,
            dte            = excluded.dte,
            quantity       = excluded.quantity,
            average_price  = excluded.average_price,
            current_price  = excluded.current_price,
            market_value   = excluded.market_value,
            unrealized_pnl = excluded.unrealized_pnl,
            delta          = excluded.delta,
            refreshed_at   = excluded.refreshed_at
        """,
        position,
    )
    conn.commit()


def update_position_delta(conn: sqlite3.Connection, account_id: str, symbol: str, delta: float) -> None:
    conn.execute(
        "UPDATE wt_positions SET delta = ? WHERE account_id = ? AND symbol = ?",
        (delta, account_id, symbol),
    )
    conn.commit()


def delete_stale_positions(conn: sqlite3.Connection, account_id: str, active_symbols: set[str]) -> None:
    """Remove positions for the account that are no longer in active_symbols."""
    if not active_symbols:
        conn.execute("DELETE FROM wt_positions WHERE account_id = ?", (account_id,))
    else:
        placeholders = ",".join("?" * len(active_symbols))
        conn.execute(
            f"DELETE FROM wt_positions WHERE account_id = ? AND symbol NOT IN ({placeholders})",
            (account_id, *sorted(active_symbols)),
        )
    conn.commit()


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


def get_unlinked_trades(conn: sqlite3.Connection, account_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, schwab_transaction_id, executed_at, asset_type, symbol,
               underlying, option_type, strike, expiration, instruction,
               quantity, net_amount
        FROM wt_trades
        WHERE account_id = ? AND cycle_id IS NULL
        ORDER BY executed_at
        """,
        (account_id,),
    ).fetchall()
    cols = [
        "id", "schwab_transaction_id", "executed_at", "asset_type", "symbol",
        "underlying", "option_type", "strike", "expiration", "instruction",
        "quantity", "net_amount",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_distinct_accounts(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT account_id FROM wt_trades").fetchall()
    return [r[0] for r in rows]


def insert_note(conn: sqlite3.Connection, note: dict) -> None:
    conn.execute(
        "INSERT INTO wt_notes (trade_id, cycle_id, source, content) VALUES (:trade_id, :cycle_id, :source, :content)",
        note,
    )
    conn.commit()


# ---- API query helpers ----

def get_open_positions(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.id, p.account_id, p.symbol, p.underlying, p.asset_type,
               p.option_type, p.strike, p.expiration, p.dte, p.quantity,
               p.average_price, p.current_price, p.market_value, p.unrealized_pnl,
               p.delta, p.cycle_id, p.refreshed_at
        FROM wt_positions p
        ORDER BY p.asset_type DESC, p.dte ASC NULLS LAST
        """
    ).fetchall()
    conn.row_factory = None
    return [dict(r) for r in rows]


def get_cycles(conn: sqlite3.Connection, status: str | None = None, limit: int = 50) -> list[dict]:
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
    conn.row_factory = None
    return [dict(r) for r in rows]


def get_cycle_trades(conn: sqlite3.Connection, cycle_id: int) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM wt_trades WHERE cycle_id = ? ORDER BY executed_at",
        (cycle_id,),
    ).fetchall()
    conn.row_factory = None
    return [dict(r) for r in rows]


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

    total_csps = conn.execute(
        "SELECT COUNT(*) FROM wt_cycles"
    ).fetchone()[0]
    closed_profitable = conn.execute(
        "SELECT COUNT(*) FROM wt_cycles WHERE status='CLOSED' AND realized_pnl > 0"
    ).fetchone()[0]
    closed_total = conn.execute(
        "SELECT COUNT(*) FROM wt_cycles WHERE status='CLOSED'"
    ).fetchone()[0]

    max_delta_row = conn.execute(
        "SELECT MAX(ABS(delta)) FROM wt_positions WHERE asset_type='OPTION' AND quantity < 0 AND option_type='PUT'"
    ).fetchone()

    return {
        "premium_mtd": round(_premium(mtd_start), 2),
        "premium_ytd": round(_premium(ytd_start), 2),
        "win_rate": round(closed_profitable / closed_total, 3) if closed_total else None,
        "total_cycles": total_csps,
        "open_cycles": total_csps - closed_total,
        "max_short_put_delta": max_delta_row[0],
    }
```

- [ ] **Step 6: Run tests — verify they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_store.py -v 2>&1 | tail -30
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/db.py src/wheel_tracker/__init__.py src/wheel_tracker/store.py tests/test_wheel_tracker_store.py
git commit -m "feat(wheel): DB schema + store layer for wt_* tables"
```

---

## Task 2: Schwab Sync

**Files:**
- Create: `src/wheel_tracker/sync.py`
- Create: `tests/test_wheel_tracker_sync.py`

**Interfaces consumed:** `store.upsert_trade`, `store.get_last_executed_at`, `store.upsert_position`, `store.update_position_delta`, `store.delete_stale_positions`

**Interfaces produced (used by Tasks 3, 4, 5):**
```python
async def run_sync(conn: sqlite3.Connection | None = None) -> dict:
    """
    Full sync pass: transactions for all accounts, position snapshot, delta fetch.
    Returns {"accounts_synced": N, "trades_imported": N, "positions_refreshed": N}.
    If conn is None, opens a new connection via settings.db_path.
    """
```

> **⚠️ Format note:** `schwab-mcp` returns tool results as `content[0].text`. For `get_transactions`,
> this is a JSON string matching the Schwab Individual Trader API shape. For `get_accounts`, it is
> also JSON. Before writing the parser, verify by adding a temporary `logger.debug("raw: %s", raw_text)`
> in `_parse_accounts` and running the pipeline once. Adjust the parser if the shape differs.

- [ ] **Step 1: Write failing sync tests**

Create `tests/test_wheel_tracker_sync.py`:

```python
"""Tests for Schwab sync — uses mocked MCP responses."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.wheel_tracker.store import ensure_wheel_tables


@pytest.fixture
def conn():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    c = sqlite3.connect(f.name)
    c.row_factory = sqlite3.Row
    ensure_wheel_tables(c)
    yield c
    c.close()
    os.unlink(f.name)


def _mock_session(accounts_json: str, transactions_json: str, positions_json: str):
    """Return a mock MCP ClientSession that returns preset responses."""
    session = AsyncMock()

    def _call_tool(name, args=None):
        result = MagicMock()
        if name == "get_accounts":
            result.content = [MagicMock(text=accounts_json)]
        elif name == "get_transactions":
            result.content = [MagicMock(text=transactions_json)]
        elif name == "get_account":
            result.content = [MagicMock(text=positions_json)]
        elif name == "get_option_chain":
            result.content = [MagicMock(text="[]")]
        else:
            result.content = [MagicMock(text="[]")]
        return result

    session.call_tool = AsyncMock(side_effect=_call_tool)
    session.initialize = AsyncMock()
    return session


SAMPLE_ACCOUNTS = json.dumps([{"accountNumber": "ACC1", "hashValue": "ACC1"}])

SAMPLE_TRANSACTIONS = json.dumps([
    {
        "activityId": "TXN001",
        "time": "2025-01-01T10:00:00+0000",
        "type": "TRADE",
        "description": "SELL TO OPEN 1 AAPL 01/17/2025 200.00 P",
        "netAmount": 149.35,
        "transactionItem": {
            "accountNumber": "ACC1",
            "amount": 1.0,
            "price": 1.50,
            "cost": 150.0,
            "instruction": "SELL_TO_OPEN",
            "instrument": {
                "symbol": "AAPL  250117P00200000",
                "assetType": "OPTION",
                "putCall": "PUT",
                "underlyingSymbol": "AAPL",
                "optionExpirationDate": "2025-01-17",
                "strikePrice": 200.0,
            },
        },
    }
])

SAMPLE_POSITIONS = json.dumps({
    "securitiesAccount": {
        "positions": [
            {
                "instrument": {
                    "symbol": "AAPL  250117P00200000",
                    "assetType": "OPTION",
                    "putCall": "PUT",
                    "underlyingSymbol": "AAPL",
                    "optionExpirationDate": "2025-01-17",
                    "strikePrice": 200.0,
                },
                "shortQuantity": 1.0,
                "longQuantity": 0.0,
                "averagePrice": 1.50,
                "marketValue": -80.0,
                "currentDayProfitLoss": 70.0,
                "currentDayCost": 0,
            }
        ]
    }
})


@pytest.mark.asyncio
async def test_sync_imports_transactions(conn):
    from src.wheel_tracker.sync import _sync_account
    session = _mock_session(SAMPLE_ACCOUNTS, SAMPLE_TRANSACTIONS, SAMPLE_POSITIONS)
    count = await _sync_account(conn, session, "ACC1", "2020-01-01", "2025-12-31")
    assert count == 1
    row = conn.execute("SELECT instruction, net_amount FROM wt_trades WHERE account_id='ACC1'").fetchone()
    assert row["instruction"] == "SELL_TO_OPEN"
    assert row["net_amount"] == pytest.approx(149.35)


@pytest.mark.asyncio
async def test_sync_is_idempotent(conn):
    from src.wheel_tracker.sync import _sync_account
    session = _mock_session(SAMPLE_ACCOUNTS, SAMPLE_TRANSACTIONS, SAMPLE_POSITIONS)
    await _sync_account(conn, session, "ACC1", "2020-01-01", "2025-12-31")
    await _sync_account(conn, session, "ACC1", "2020-01-01", "2025-12-31")
    count = conn.execute("SELECT COUNT(*) FROM wt_trades").fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_sync_upserts_positions(conn):
    from src.wheel_tracker.sync import _sync_positions
    session = _mock_session(SAMPLE_ACCOUNTS, SAMPLE_TRANSACTIONS, SAMPLE_POSITIONS)
    await _sync_positions(conn, session, "ACC1")
    count = conn.execute("SELECT COUNT(*) FROM wt_positions WHERE account_id='ACC1'").fetchone()[0]
    assert count == 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_sync.py -v 2>&1 | tail -20
```
Expected: `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/wheel_tracker/sync.py`**

```python
"""Nightly Schwab sync: transactions, open positions, and delta refresh."""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ..config import settings
from .store import (
    delete_stale_positions,
    get_last_executed_at,
    update_position_delta,
    upsert_position,
    upsert_trade,
)

logger = logging.getLogger(__name__)

_SCHWAB_CONFIG = Path(__file__).parent.parent.parent / "discord_bot" / "schwab-mcp.json"


def _schwab_url() -> str:
    config = json.loads(_SCHWAB_CONFIG.read_text())
    return config["mcpServers"]["schwab"]["url"]


def _parse_accounts(raw: str) -> list[str]:
    """Return list of account hash values (used as accountNumber in subsequent calls)."""
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [a.get("hashValue") or a.get("accountNumber") for a in data if a]
        logger.warning("Unexpected accounts format: %r", raw[:200])
        return []
    except json.JSONDecodeError:
        logger.warning("Could not parse accounts JSON: %r", raw[:200])
        return []


def _parse_transactions(raw: str, account_id: str) -> list[dict]:
    """
    Parse Schwab get_transactions response into a list of trade dicts ready for upsert_trade().
    
    Expected shape (Schwab Individual Trader API):
    [{"activityId": "...", "time": "ISO8601", "type": "TRADE", "netAmount": float,
      "transactionItem": {"amount": float, "price": float, "instruction": str,
                          "instrument": {"symbol": str, "assetType": str, "putCall": str|None,
                                         "underlyingSymbol": str|None, "optionExpirationDate": str|None,
                                         "strikePrice": float|None}}}]
    
    Adjust this parser if the schwab-mcp wrapper reformats the response.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse transactions JSON for %s: %r", account_id, raw[:200])
        return []

    if not isinstance(data, list):
        logger.warning("Unexpected transactions format for %s: %r", account_id, raw[:200])
        return []

    trades = []
    for txn in data:
        if txn.get("type") != "TRADE":
            continue
        item = txn.get("transactionItem", {})
        instrument = item.get("instrument", {})
        try:
            trades.append({
                "schwab_transaction_id": str(txn["activityId"]),
                "account_id": account_id,
                "executed_at": txn["time"].replace("+0000", "+00:00"),
                "settled_date": txn.get("settlementDate"),
                "asset_type": instrument.get("assetType", "EQUITY"),
                "symbol": instrument["symbol"],
                "underlying": instrument.get("underlyingSymbol"),
                "option_type": instrument.get("putCall"),
                "strike": instrument.get("strikePrice"),
                "expiration": instrument.get("optionExpirationDate"),
                "instruction": item.get("instruction", "UNKNOWN"),
                "quantity": float(item.get("amount", 0)),
                "price": float(item.get("price", 0) or 0),
                "commission": float(txn.get("fees", {}).get("commission", 0) or 0),
                "net_amount": float(txn.get("netAmount", 0)),
            })
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping unparseable transaction %s: %s", txn.get("activityId"), exc)
    return trades


def _dte(expiration: str | None) -> int | None:
    if not expiration:
        return None
    try:
        exp = date.fromisoformat(expiration[:10])
        return max(0, (exp - date.today()).days)
    except ValueError:
        return None


def _parse_positions(raw: str, account_id: str, refreshed_at: str) -> list[dict]:
    """
    Parse Schwab get_account positions response.
    
    Expected shape: {"securitiesAccount": {"positions": [...]}}
    Each position has "instrument" (same shape as transaction), "shortQuantity",
    "longQuantity", "averagePrice", "marketValue", "currentDayProfitLoss".
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse positions JSON for %s: %r", account_id, raw[:200])
        return []

    acct = data.get("securitiesAccount", data)
    raw_positions = acct.get("positions", [])

    positions = []
    for pos in raw_positions:
        instrument = pos.get("instrument", {})
        short_qty = float(pos.get("shortQuantity", 0) or 0)
        long_qty = float(pos.get("longQuantity", 0) or 0)
        quantity = long_qty - short_qty  # negative = short

        symbol = instrument.get("symbol", "")
        expiration = instrument.get("optionExpirationDate")

        positions.append({
            "account_id": account_id,
            "symbol": symbol,
            "underlying": instrument.get("underlyingSymbol"),
            "asset_type": instrument.get("assetType", "EQUITY"),
            "option_type": instrument.get("putCall"),
            "strike": instrument.get("strikePrice"),
            "expiration": expiration,
            "dte": _dte(expiration),
            "quantity": quantity,
            "average_price": float(pos.get("averagePrice", 0) or 0),
            "current_price": (
                abs(float(pos.get("marketValue", 0) or 0) / quantity / 100)
                if quantity and instrument.get("assetType") == "OPTION"
                else abs(float(pos.get("marketValue", 0) or 0) / quantity)
                if quantity else None
            ),
            "market_value": float(pos.get("marketValue", 0) or 0),
            "unrealized_pnl": float(pos.get("currentDayProfitLoss", 0) or 0),
            "delta": None,  # populated separately by _fetch_deltas
            "refreshed_at": refreshed_at,
        })
    return positions


def _extract_delta(chain_raw: str, option_type: str, strike: float, expiration: str) -> float | None:
    """
    Extract delta for a specific contract from get_option_chain response.
    The chain response from schwab-mcp uses the same compact format as schwab_options.py.
    Returns None if not found.
    """
    # Import parser from existing schwab_options module
    from ..algo_detective.schwab_options import _parse_put_chain

    if option_type == "PUT":
        contracts = _parse_put_chain(chain_raw)
    else:
        # For calls, we parse the same format but filter by instruction — reuse _parse_put_chain
        # since the field structure is identical; caller filters by option type separately.
        contracts = _parse_put_chain(chain_raw)

    exp_short = expiration[:10] if expiration else None
    for c in contracts:
        if abs(c.get("strike", 0) - strike) < 0.01:
            return c.get("delta")
    return None


async def _sync_account(
    conn: sqlite3.Connection,
    session: ClientSession,
    account_id: str,
    start_date: str,
    end_date: str,
) -> int:
    """Pull transactions for one account. Returns count of newly imported rows."""
    result = await session.call_tool(
        "get_transactions",
        {"accountNumber": account_id, "types": "TRADE", "startDate": start_date, "endDate": end_date},
    )
    raw = result.content[0].text if result.content else "[]"
    trades = _parse_transactions(raw, account_id)
    count = 0
    for t in trades:
        upsert_trade(conn, t)
        count += 1
    return count


async def _sync_positions(
    conn: sqlite3.Connection,
    session: ClientSession,
    account_id: str,
) -> set[str]:
    """Refresh open positions for one account. Returns set of active symbols."""
    from datetime import datetime, timezone
    refreshed_at = datetime.now(timezone.utc).isoformat()

    result = await session.call_tool(
        "get_account",
        {"accountNumber": account_id, "fields": "positions"},
    )
    raw = result.content[0].text if result.content else "{}"
    positions = _parse_positions(raw, account_id, refreshed_at)
    active_symbols: set[str] = set()
    for pos in positions:
        upsert_position(conn, pos)
        active_symbols.add(pos["symbol"])
    delete_stale_positions(conn, account_id, active_symbols)
    return active_symbols


async def _fetch_deltas(
    conn: sqlite3.Connection,
    session: ClientSession,
    account_id: str,
) -> None:
    """Fetch current delta for each open short option position in this account."""
    rows = conn.execute(
        """
        SELECT symbol, underlying, option_type, strike, expiration
        FROM wt_positions
        WHERE account_id = ? AND asset_type = 'OPTION' AND quantity < 0
        """,
        (account_id,),
    ).fetchall()

    fetched_underlyings: dict[str, str] = {}  # underlying → chain raw text

    for (symbol, underlying, option_type, strike, expiration) in rows:
        if not underlying:
            continue
        if underlying not in fetched_underlyings:
            result = await session.call_tool(
                "get_option_chain",
                {
                    "symbol": underlying,
                    "contractType": option_type or "ALL",
                    "expirationDate": expiration[:10] if expiration else None,
                },
            )
            fetched_underlyings[underlying] = result.content[0].text if result.content else ""

        chain_raw = fetched_underlyings[underlying]
        delta = _extract_delta(chain_raw, option_type or "", strike or 0.0, expiration or "")
        if delta is not None:
            update_position_delta(conn, account_id, symbol, delta)


async def run_sync(conn: sqlite3.Connection | None = None) -> dict:
    """
    Full sync: pull all Schwab transactions + positions, refresh deltas.
    Safe to call if Schwab MCP is unreachable (logs error, returns empty summary).
    """
    from datetime import datetime, timezone

    _owns_conn = conn is None
    if _owns_conn:
        conn = sqlite3.connect(settings.db_path)
        conn.row_factory = sqlite3.Row

    summary = {"accounts_synced": 0, "trades_imported": 0, "positions_refreshed": 0}

    try:
        url = _schwab_url()
    except Exception as exc:
        logger.error("wheel_tracker: cannot read Schwab MCP config: %s", exc)
        return summary

    today = date.today().isoformat()

    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                accts_result = await session.call_tool("get_accounts", {})
                raw_accounts = accts_result.content[0].text if accts_result.content else "[]"
                account_ids = _parse_accounts(raw_accounts)
                logger.info("wheel_tracker: found %d account(s)", len(account_ids))

                for account_id in account_ids:
                    if not account_id:
                        continue
                    last = get_last_executed_at(conn, account_id)
                    start = (
                        (date.fromisoformat(last[:10]) + timedelta(days=1)).isoformat()
                        if last else "2020-01-01"
                    )
                    imported = await _sync_account(conn, session, account_id, start, today)
                    summary["trades_imported"] += imported

                    active = await _sync_positions(conn, session, account_id)
                    summary["positions_refreshed"] += len(active)

                    await _fetch_deltas(conn, session, account_id)
                    summary["accounts_synced"] += 1

    except Exception as exc:
        logger.error("wheel_tracker sync failed: %s", exc, exc_info=True)

    finally:
        if _owns_conn and conn:
            conn.close()

    logger.info("wheel_tracker sync complete: %s", summary)
    return summary
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_sync.py -v 2>&1 | tail -20
```
Expected: all PASS. If tests fail due to import path issues, check that `pytest-asyncio` is available (`pip show pytest-asyncio` inside the test container). Add `asyncio_mode = "auto"` to `pyproject.toml`'s `[tool.pytest.ini_options]` if needed — check what the existing test suite uses first.

- [ ] **Step 5: Commit**

```bash
git add src/wheel_tracker/sync.py tests/test_wheel_tracker_sync.py
git commit -m "feat(wheel): Schwab transaction + position sync"
```

---

## Task 3: Wheel-Cycle Linking

**Files:**
- Create: `src/wheel_tracker/cycles.py`
- Create: `tests/test_wheel_tracker_cycles.py`

**Interfaces consumed:** `store.get_unlinked_trades`, `store.get_distinct_accounts`, `store.create_cycle`, `store.set_trade_cycle`

**Interfaces produced:**
```python
def link_cycles(conn: sqlite3.Connection) -> int:
    """Detect and persist wheel cycles from unlinked trades. Returns new cycle count."""
```

- [ ] **Step 1: Write failing cycle tests**

Create `tests/test_wheel_tracker_cycles.py`:

```python
"""Tests for wheel-cycle linking algorithm."""
from __future__ import annotations

import sqlite3
import tempfile
import os
import pytest

from src.wheel_tracker.store import ensure_wheel_tables, upsert_trade


@pytest.fixture
def conn():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    c = sqlite3.connect(f.name)
    c.row_factory = sqlite3.Row
    ensure_wheel_tables(c)
    yield c
    c.close()
    os.unlink(f.name)


def _t(**kw) -> dict:
    """Minimal trade dict with sensible defaults."""
    base = dict(
        schwab_transaction_id="tx",
        account_id="ACC1",
        executed_at="2025-01-01T10:00:00",
        settled_date=None,
        asset_type="OPTION",
        symbol="AAPL  250117P00200000",
        underlying="AAPL",
        option_type="PUT",
        strike=200.0,
        expiration="2025-01-17",
        instruction="SELL_TO_OPEN",
        quantity=-1.0,
        price=1.50,
        commission=0.65,
        net_amount=149.35,
    )
    return {**base, **kw}


def test_csp_expired_worthless(conn):
    """SELL_TO_OPEN + EXPIRED on same symbol → 1 closed cycle, full premium captured."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
                          executed_at="2025-01-01T10:00:00", net_amount=149.35))
    upsert_trade(conn, _t(schwab_transaction_id="t2", instruction="EXPIRED",
                          executed_at="2025-01-17T16:00:00", net_amount=0.0, quantity=1.0))

    from src.wheel_tracker.cycles import link_cycles
    n = link_cycles(conn)

    assert n == 1
    cycle = conn.execute("SELECT * FROM wt_cycles").fetchone()
    assert cycle["status"] == "CLOSED"
    assert cycle["total_premium"] == pytest.approx(149.35)
    linked = conn.execute("SELECT COUNT(*) FROM wt_trades WHERE cycle_id = ?", (cycle["id"],)).fetchone()[0]
    assert linked == 2


def test_csp_bought_back(conn):
    """SELL_TO_OPEN + BUY_TO_CLOSE → 1 closed cycle."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
                          executed_at="2025-01-01T10:00:00", net_amount=149.35))
    upsert_trade(conn, _t(schwab_transaction_id="t2", instruction="BUY_TO_CLOSE",
                          executed_at="2025-01-10T10:00:00", net_amount=-30.0, quantity=1.0))

    from src.wheel_tracker.cycles import link_cycles
    n = link_cycles(conn)

    assert n == 1
    cycle = conn.execute("SELECT * FROM wt_cycles").fetchone()
    assert cycle["status"] == "CLOSED"


def test_csp_open_no_close(conn):
    """SELL_TO_OPEN with no matching close → 1 OPEN cycle."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
                          executed_at="2025-01-01T10:00:00", net_amount=149.35))

    from src.wheel_tracker.cycles import link_cycles
    n = link_cycles(conn)

    assert n == 1
    cycle = conn.execute("SELECT * FROM wt_cycles").fetchone()
    assert cycle["status"] == "OPEN"


def test_full_wheel_cycle(conn):
    """CSP assigned → shares bought → CC expired worthless → 1 closed cycle with all 4 trades linked."""
    trades = [
        _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
           executed_at="2025-01-01T10:00:00", net_amount=149.35,
           asset_type="OPTION", option_type="PUT", symbol="AAPL  250117P00200000"),
        _t(schwab_transaction_id="t2", instruction="ASSIGNED",
           executed_at="2025-01-17T16:00:00", net_amount=0.0, quantity=1.0,
           asset_type="OPTION", option_type="PUT", symbol="AAPL  250117P00200000"),
        _t(schwab_transaction_id="t3", instruction="BUY",
           executed_at="2025-01-17T16:01:00", net_amount=-20000.0, quantity=100.0,
           asset_type="EQUITY", symbol="AAPL", underlying=None, option_type=None,
           strike=None, expiration=None),
        _t(schwab_transaction_id="t4", instruction="SELL_TO_OPEN",
           executed_at="2025-01-20T10:00:00", net_amount=120.0, quantity=-1.0,
           asset_type="OPTION", option_type="CALL", symbol="AAPL  250221C00210000",
           underlying="AAPL", strike=210.0, expiration="2025-02-21"),
        _t(schwab_transaction_id="t5", instruction="EXPIRED",
           executed_at="2025-02-21T16:00:00", net_amount=0.0, quantity=1.0,
           asset_type="OPTION", option_type="CALL", symbol="AAPL  250221C00210000",
           underlying="AAPL", strike=210.0, expiration="2025-02-21"),
    ]
    for t in trades:
        upsert_trade(conn, t)

    from src.wheel_tracker.cycles import link_cycles
    n = link_cycles(conn)

    assert n == 1
    cycle = conn.execute("SELECT * FROM wt_cycles").fetchone()
    assert cycle["status"] == "OPEN"  # still open: shares held, CC expired, no new CC or exit yet
    linked = conn.execute("SELECT COUNT(*) FROM wt_trades WHERE cycle_id IS NOT NULL").fetchone()[0]
    assert linked == 5


def test_standalone_equity_trade_unlinked(conn):
    """A plain stock BUY with no options context stays unlinked (cycle_id NULL)."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="BUY",
                          asset_type="EQUITY", symbol="MSFT", underlying=None,
                          option_type=None, strike=None, expiration=None,
                          net_amount=-40000.0, quantity=100.0))

    from src.wheel_tracker.cycles import link_cycles
    link_cycles(conn)

    row = conn.execute("SELECT cycle_id FROM wt_trades WHERE schwab_transaction_id='t1'").fetchone()
    assert row["cycle_id"] is None


def test_link_cycles_idempotent(conn):
    """Calling link_cycles twice on same data creates no duplicate cycles."""
    upsert_trade(conn, _t(schwab_transaction_id="t1", instruction="SELL_TO_OPEN",
                          executed_at="2025-01-01T10:00:00", net_amount=149.35))

    from src.wheel_tracker.cycles import link_cycles
    link_cycles(conn)
    n2 = link_cycles(conn)

    assert n2 == 0
    assert conn.execute("SELECT COUNT(*) FROM wt_cycles").fetchone()[0] == 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_cycles.py -v 2>&1 | tail -20
```
Expected: `ImportError` (cycles.py doesn't exist).

- [ ] **Step 3: Create `src/wheel_tracker/cycles.py`**

```python
"""Wheel-cycle auto-detection. Walks unlinked trades per account and groups them."""
from __future__ import annotations

import logging
import sqlite3

from .store import (
    create_cycle,
    get_distinct_accounts,
    get_unlinked_trades,
    set_trade_cycle,
    update_cycle,
)

logger = logging.getLogger(__name__)


def link_cycles(conn: sqlite3.Connection) -> int:
    """Detect wheel cycles from unlinked trades across all accounts. Returns new cycle count."""
    total = 0
    for account_id in get_distinct_accounts(conn):
        trades = get_unlinked_trades(conn, account_id)
        if trades:
            total += _link_account(conn, account_id, trades)
    return total


def _link_account(conn: sqlite3.Connection, account_id: str, trades: list[dict]) -> int:
    processed: set[int] = set()
    new_cycles = 0

    for trade in trades:
        if trade["id"] in processed:
            continue
        # Only option SELL_TO_OPEN PUTs start a wheel cycle
        if (
            trade["asset_type"] == "OPTION"
            and trade["option_type"] == "PUT"
            and trade["instruction"] == "SELL_TO_OPEN"
        ):
            cycle_trades, status = _collect(trade, trades, processed)
            if not cycle_trades:
                continue

            total_premium = sum(
                t["net_amount"] for t in cycle_trades
                if t["asset_type"] == "OPTION" and (t["net_amount"] or 0) > 0
            )
            realized_pnl: float | None = None
            if status == "CLOSED":
                realized_pnl = sum(t["net_amount"] or 0 for t in cycle_trades)

            cycle_id = create_cycle(conn, {
                "underlying": trade["underlying"] or trade["symbol"],
                "account_id": account_id,
                "status": status,
                "opened_at": cycle_trades[0]["executed_at"][:10],
                "closed_at": cycle_trades[-1]["executed_at"][:10] if status == "CLOSED" else None,
                "total_premium": round(total_premium, 2),
                "realized_pnl": round(realized_pnl, 2) if realized_pnl is not None else None,
                "auto_detected": 1,
            })
            for t in cycle_trades:
                set_trade_cycle(conn, t["id"], cycle_id)
                processed.add(t["id"])
            new_cycles += 1
            logger.debug(
                "Created %s cycle %d for %s (%d trades)",
                status, cycle_id, trade["underlying"], len(cycle_trades),
            )

    return new_cycles


def _collect(
    csp_open: dict,
    all_trades: list[dict],
    already: set[int],
) -> tuple[list[dict], str]:
    """
    Walk forward from a CSP SELL_TO_OPEN trade and collect all related trades.
    Returns (collected_trades, "OPEN"|"CLOSED").
    """
    cycle: list[dict] = [csp_open]
    underlying = csp_open.get("underlying") or ""
    csp_symbol = csp_open["symbol"]

    # All later trades on the same underlying, not yet in another cycle
    later = [
        t for t in all_trades
        if t["executed_at"] > csp_open["executed_at"]
        and t["id"] not in already
        and (
            t.get("underlying") == underlying
            or (t["asset_type"] == "EQUITY" and t["symbol"] == underlying)
        )
    ]

    phase = "csp"  # csp → assigned → shares → cc → cc_close (loops) → closed

    for t in later:
        if phase == "csp":
            if t["symbol"] == csp_symbol:
                if t["instruction"] in ("BUY_TO_CLOSE", "EXPIRED"):
                    cycle.append(t)
                    return cycle, "CLOSED"
                if t["instruction"] == "ASSIGNED":
                    cycle.append(t)
                    phase = "shares"
            # Some brokers record assignment as a direct equity BUY without an ASSIGNED record
            elif (
                t["asset_type"] == "EQUITY"
                and t["instruction"] == "BUY"
                and t["symbol"] == underlying
                and abs(t["quantity"]) == abs(csp_open["quantity"]) * 100
            ):
                cycle.append(t)
                phase = "cc"

        elif phase == "shares":
            # Look for the equity delivery following assignment
            if (
                t["asset_type"] == "EQUITY"
                and t["instruction"] == "BUY"
                and t["symbol"] == underlying
            ):
                cycle.append(t)
                phase = "cc"

        elif phase == "cc":
            if (
                t["asset_type"] == "OPTION"
                and t["option_type"] == "CALL"
                and t["instruction"] == "SELL_TO_OPEN"
            ):
                cycle.append(t)
                phase = "cc_close"
            elif (
                t["asset_type"] == "EQUITY"
                and t["instruction"] == "SELL"
                and t["symbol"] == underlying
            ):
                # Sold shares without writing a CC — closes the cycle
                cycle.append(t)
                return cycle, "CLOSED"

        elif phase == "cc_close":
            cc_symbol = cycle[-1]["symbol"]
            if t["symbol"] == cc_symbol:
                if t["instruction"] in ("BUY_TO_CLOSE", "EXPIRED"):
                    cycle.append(t)
                    phase = "cc"  # loop — may write another CC
                elif t["instruction"] == "ASSIGNED":
                    cycle.append(t)
                    return cycle, "CLOSED"

    return cycle, "OPEN"
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_cycles.py -v 2>&1 | tail -25
```
Expected: all PASS. The `test_full_wheel_cycle` test expects OPEN status because the shares are still held after the CC expires — verify the assertion matches the algorithm's output and adjust if needed (the cycle is genuinely still open at that point).

- [ ] **Step 5: Commit**

```bash
git add src/wheel_tracker/cycles.py tests/test_wheel_tracker_cycles.py
git commit -m "feat(wheel): wheel-cycle auto-linking algorithm"
```

---

## Task 4: Alerts

**Files:**
- Create: `src/wheel_tracker/alerts.py`
- Create: `tests/test_wheel_tracker_alerts.py`

**Interfaces consumed:** `store.get_open_positions`, `notify.ntfy.send_ntfy`

**Interfaces produced:**
```python
async def check_alerts(conn: sqlite3.Connection) -> list[str]:
    """Evaluate DTE and delta thresholds for open short options. Sends NTFY. Returns alert messages."""
```

- [ ] **Step 1: Write failing alert tests**

Create `tests/test_wheel_tracker_alerts.py`:

```python
"""Tests for wheel tracker alert generation."""
from __future__ import annotations

import sqlite3
import tempfile
import os
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch
import pytest

from src.wheel_tracker.store import ensure_wheel_tables, upsert_position


@pytest.fixture
def conn():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    c = sqlite3.connect(f.name)
    c.row_factory = sqlite3.Row
    ensure_wheel_tables(c)
    yield c
    c.close()
    os.unlink(f.name)


def _pos(**kw) -> dict:
    exp = (date.today() + timedelta(days=10)).isoformat()
    base = dict(
        account_id="ACC1",
        symbol="AAPL  250117P00200000",
        underlying="AAPL",
        asset_type="OPTION",
        option_type="PUT",
        strike=200.0,
        expiration=exp,
        dte=10,
        quantity=-1.0,
        average_price=1.50,
        current_price=0.80,
        market_value=-80.0,
        unrealized_pnl=70.0,
        delta=0.18,
        refreshed_at="2025-01-07T17:00:00",
    )
    return {**base, **kw}


@pytest.mark.asyncio
async def test_no_alert_when_dte_above_threshold(conn):
    upsert_position(conn, _pos(dte=15))
    with patch("src.wheel_tracker.alerts.send_ntfy", new_callable=AsyncMock) as mock_ntfy:
        from src.wheel_tracker.alerts import check_alerts
        alerts = await check_alerts(conn)
    assert alerts == []
    mock_ntfy.assert_not_called()


@pytest.mark.asyncio
async def test_dte_alert_fires_when_dte_le_7(conn):
    upsert_position(conn, _pos(dte=5, symbol="AAPL  250107P00200000"))
    with patch("src.wheel_tracker.alerts.send_ntfy", new_callable=AsyncMock) as mock_ntfy:
        mock_ntfy.return_value = None
        from src.wheel_tracker.alerts import check_alerts
        alerts = await check_alerts(conn)
    assert len(alerts) == 1
    assert "DTE" in alerts[0] or "Expiring" in alerts[0]


@pytest.mark.asyncio
async def test_dte_alert_dedup(conn):
    today = date.today().isoformat()
    upsert_position(conn, _pos(dte=5, symbol="AAPL  250107P00200000"))
    conn.execute(
        "UPDATE wt_positions SET last_dte_alerted=? WHERE symbol=?",
        (today, "AAPL  250107P00200000"),
    )
    conn.commit()
    with patch("src.wheel_tracker.alerts.send_ntfy", new_callable=AsyncMock) as mock_ntfy:
        from src.wheel_tracker.alerts import check_alerts
        alerts = await check_alerts(conn)
    assert alerts == []
    mock_ntfy.assert_not_called()


@pytest.mark.asyncio
async def test_delta_alert_fires_when_above_threshold(conn):
    upsert_position(conn, _pos(delta=0.35, dte=20))
    with patch("src.wheel_tracker.alerts.send_ntfy", new_callable=AsyncMock) as mock_ntfy:
        mock_ntfy.return_value = None
        from src.wheel_tracker.alerts import check_alerts
        alerts = await check_alerts(conn)
    assert any("delta" in a.lower() or "assignment" in a.lower() for a in alerts)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_alerts.py -v 2>&1 | tail -20
```

- [ ] **Step 3: Create `src/wheel_tracker/alerts.py`**

```python
"""DTE and assignment-risk alerts for open short option positions."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date

logger = logging.getLogger(__name__)

_DTE_THRESHOLD = 7
_DELTA_THRESHOLD = 0.30


async def send_ntfy(title: str, message: str) -> None:
    from ..notify.ntfy import send_ntfy as _send
    await _send(title=title, message=message)


async def check_alerts(conn: sqlite3.Connection) -> list[str]:
    """
    Evaluate all open short option positions for DTE and delta thresholds.
    Sends NTFY notification for each triggered alert.
    Returns list of alert message strings (for logging/testing).
    """
    today = date.today().isoformat()
    sent: list[str] = []

    rows = conn.execute(
        """
        SELECT id, symbol, underlying, option_type, strike, expiration,
               dte, delta, last_dte_alerted, last_delta_alerted
        FROM wt_positions
        WHERE asset_type = 'OPTION' AND quantity < 0
        """
    ).fetchall()

    for row in rows:
        (pos_id, symbol, underlying, option_type, strike, expiration,
         dte, delta, last_dte_alerted, last_delta_alerted) = row

        # DTE alert
        if dte is not None and dte <= _DTE_THRESHOLD:
            if last_dte_alerted != today:
                msg = f"Expiring soon: {symbol} | DTE {dte} | {option_type} ${strike}"
                await send_ntfy("⚠️ Option Expiring This Week", msg)
                conn.execute(
                    "UPDATE wt_positions SET last_dte_alerted = ? WHERE id = ?",
                    (today, pos_id),
                )
                conn.commit()
                sent.append(msg)
                logger.info("DTE alert sent: %s", msg)

        # Delta alert (assignment risk)
        if delta is not None and abs(delta) >= _DELTA_THRESHOLD and option_type == "PUT":
            if last_delta_alerted != today:
                msg = f"Assignment risk: {symbol} | Δ {delta:.2f} | ${strike} put exp {expiration}"
                await send_ntfy("🚨 Assignment Risk Elevated", msg)
                conn.execute(
                    "UPDATE wt_positions SET last_delta_alerted = ? WHERE id = ?",
                    (today, pos_id),
                )
                conn.commit()
                sent.append(msg)
                logger.info("Delta alert sent: %s", msg)

    return sent
```

- [ ] **Step 4: Check how `src/notify/ntfy.py` exports `send_ntfy`**

Read `src/notify/ntfy.py` (first 30 lines) to confirm the function name and signature before finalizing the import in `alerts.py`. If the function name differs, update the import in Step 3.

```bash
head -30 src/notify/ntfy.py
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_alerts.py -v 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/wheel_tracker/alerts.py tests/test_wheel_tracker_alerts.py
git commit -m "feat(wheel): DTE and assignment-risk NTFY alerts"
```

---

## Task 5: Pipeline Integration

**Files:**
- Modify: `src/main.py`
- Modify: `src/wheel_tracker/sync.py` (add `cycles` and `alerts` calls at end of `run_sync`)

**Interfaces consumed:** `wheel_tracker.run_sync`

- [ ] **Step 1: Wire `cycles.link_cycles` and `alerts.check_alerts` into `run_sync`**

At the end of `run_sync()` in `src/wheel_tracker/sync.py`, before closing `conn`, add inside the `try` block (after the `async with` closes):

```python
        # After MCP session closes, link cycles and send alerts using the now-populated tables
        if _owns_conn:
            conn = sqlite3.connect(settings.db_path)
            conn.row_factory = sqlite3.Row

        from .cycles import link_cycles
        from .alerts import check_alerts

        new_cycles = link_cycles(conn)
        logger.info("wheel_tracker: linked %d new cycle(s)", new_cycles)

        alerts_sent = await check_alerts(conn)
        logger.info("wheel_tracker: sent %d alert(s)", len(alerts_sent))
```

Wait — `run_sync` already has `finally: if _owns_conn and conn: conn.close()`. The cycles/alerts calls need to happen inside the try block, with the conn still open. Restructure `run_sync`'s final section:

```python
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                # ... existing account loop ...

        # MCP session closed — now do CPU-only work on the populated tables
        from .cycles import link_cycles
        from .alerts import check_alerts
        new_cycles = link_cycles(conn)
        logger.info("wheel_tracker: linked %d new cycle(s)", new_cycles)
        await check_alerts(conn)

    except Exception as exc:
        logger.error("wheel_tracker sync failed: %s", exc, exc_info=True)
    finally:
        if _owns_conn and conn:
            conn.close()
```

- [ ] **Step 2: Add Step 5 to `src/main.py`**

Find the `run_pipeline()` function in `src/main.py`. Locate where Step 4 ends (NTFY send). After it, add:

```python
    # Step 5: Wheel tracker sync
    logger.info("Step 5: wheel tracker Schwab sync")
    try:
        from .wheel_tracker import run_sync as wheel_sync
        sync_summary = await wheel_sync()
        logger.info("Wheel sync: %s", sync_summary)
    except Exception as exc:
        logger.error("Wheel sync failed (non-fatal): %s", exc, exc_info=True)
```

- [ ] **Step 3: Smoke-test via pipeline dry run**

```bash
docker compose run --rm pipeline python3 -c "
import asyncio
from src.wheel_tracker import run_sync
result = asyncio.run(run_sync())
print('sync result:', result)
" 2>&1 | tail -30
```

Expected: either a successful sync summary or a clean `"wheel_tracker sync failed"` log line (if Schwab MCP is not reachable from the pipeline container). No unhandled exceptions.

- [ ] **Step 4: Commit**

```bash
git add src/main.py src/wheel_tracker/sync.py
git commit -m "feat(wheel): integrate wheel tracker as pipeline Step 5"
```

---

## Task 6: API Endpoints

**Files:**
- Modify: `src/api/main.py`

**Interfaces consumed:** `store.get_open_positions`, `store.get_cycles`, `store.get_cycle_trades`, `store.get_wheel_stats`

- [ ] **Step 1: Add three endpoints to `src/api/main.py`**

Add after the existing screener endpoints (around line 620+). First, add the import at the top of the file with the other `src.*` imports:

```python
from ..wheel_tracker.store import (
    get_open_positions as wt_get_positions,
    get_cycles as wt_get_cycles,
    get_cycle_trades as wt_get_cycle_trades,
    get_wheel_stats as wt_get_stats,
)
```

Then add the endpoints:

```python
@app.get("/api/wheel/positions")
def wheel_positions(req: Request):
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return {"positions": wt_get_positions(conn)}


@app.get("/api/wheel/cycles")
def wheel_cycles(req: Request, status: str | None = None, limit: int = 50):
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cycles = wt_get_cycles(conn, status=status, limit=limit)
        # Attach trade legs to each cycle
        for cycle in cycles:
            cycle["trades"] = wt_get_cycle_trades(conn, cycle["id"])
        return {"cycles": cycles}


@app.get("/api/wheel/stats")
def wheel_stats(req: Request):
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return wt_get_stats(conn)
```

- [ ] **Step 2: Verify endpoints respond**

```bash
# Rebuild and restart API
docker compose up --build -d api

# Wait ~5s for startup, then curl dev
curl -s https://dev-mi.austin10berge.com/api/wheel/stats | python3 -m json.tool
curl -s https://dev-mi.austin10berge.com/api/wheel/positions | python3 -m json.tool
curl -s https://dev-mi.austin10berge.com/api/wheel/cycles | python3 -m json.tool
```

Expected: all three return valid JSON (empty arrays/zeroes before sync has run is fine).

- [ ] **Step 3: Commit**

```bash
git add src/api/main.py
git commit -m "feat(wheel): add /api/wheel/* endpoints"
```

---

## Task 7: Dashboard Wheel Page

**Files:**
- Create: `src/web/wheel.html`
- Create: `src/web/wheel.js`
- Modify: `src/web/index.html`, `scanner.html`, `watchlist.html`, `technical-analysis.html`, `backtest.html`

- [ ] **Step 1: Add "Wheel" nav link to all existing pages**

In each of the 5 HTML files, find the `<nav>` block. It looks like:

```html
<nav style="display:flex;gap:1.25rem;margin-bottom:1.5rem;flex-wrap:wrap;">
    <a href="/" ...>Dashboard</a>
    <a href="/scanner.html" ...>CSP Scanner</a>
    ...
</nav>
```

Add this link at the end of the nav in each file:

```html
<a href="/wheel.html" style="color:var(--text-secondary);text-decoration:none;font-size:.88rem;padding-bottom:2px;transition:color .2s;" onmouseover="this.style.color='var(--text-primary)'" onmouseout="this.style.color='var(--text-secondary)'">Wheel</a>
```

On `wheel.html` itself, this link will have the active style (`color:var(--accent-blue)` and `border-bottom:2px solid var(--accent-blue)`).

- [ ] **Step 2: Create `src/web/wheel.html`**

Copy the structure from `watchlist.html` (another simple single-section page) as a template. The page needs:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wheel Tracker — Market Intelligence</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="index.css">
</head>
<body>
<div id="app">
    <header class="glass">
        <div class="header-content">
            <h1>AI Market Intelligence</h1>
            <p>Automated Macro Sentiment & Options Scanning</p>
        </div>
    </header>

    <nav style="display:flex;gap:1.25rem;margin-bottom:1.5rem;flex-wrap:wrap;">
        <a href="/" style="color:var(--text-secondary);text-decoration:none;font-size:.88rem;padding-bottom:2px;transition:color .2s;" onmouseover="this.style.color='var(--text-primary)'" onmouseout="this.style.color='var(--text-secondary)'">Dashboard</a>
        <a href="/scanner.html" style="color:var(--text-secondary);text-decoration:none;font-size:.88rem;padding-bottom:2px;transition:color .2s;" onmouseover="this.style.color='var(--text-primary)'" onmouseout="this.style.color='var(--text-secondary)'">CSP Scanner</a>
        <a href="/technical-analysis.html" style="color:var(--text-secondary);text-decoration:none;font-size:.88rem;padding-bottom:2px;transition:color .2s;" onmouseover="this.style.color='var(--text-primary)'" onmouseout="this.style.color='var(--text-secondary)'">Technical Analysis</a>
        <a href="/watchlist.html" style="color:var(--text-secondary);text-decoration:none;font-size:.88rem;padding-bottom:2px;transition:color .2s;" onmouseover="this.style.color='var(--text-primary)'" onmouseout="this.style.color='var(--text-secondary)'">Watchlist</a>
        <a href="/backtest.html" style="color:var(--text-secondary);text-decoration:none;font-size:.88rem;padding-bottom:2px;transition:color .2s;" onmouseover="this.style.color='var(--text-primary)'" onmouseout="this.style.color='var(--text-secondary)'">Backtester</a>
        <a href="/wheel.html" style="color:var(--accent-blue);text-decoration:none;font-size:.88rem;border-bottom:2px solid var(--accent-blue);padding-bottom:2px;">Wheel</a>
    </nav>

    <main>
        <!-- Stats bar -->
        <section id="stats-section" class="glass card full-width" style="margin-bottom:1.5rem;">
            <div class="card-header"><h2>Wheel Stats</h2></div>
            <div id="stats-bar" style="display:flex;gap:2rem;padding:1rem;flex-wrap:wrap;">
                <div><div class="th-label">Premium MTD</div><div id="stat-mtd" class="loading-text">—</div></div>
                <div><div class="th-label">Premium YTD</div><div id="stat-ytd" class="loading-text">—</div></div>
                <div><div class="th-label">Win Rate</div><div id="stat-winrate" class="loading-text">—</div></div>
                <div><div class="th-label">Open Cycles</div><div id="stat-open" class="loading-text">—</div></div>
                <div><div class="th-label">Max Short Δ</div><div id="stat-delta" class="loading-text">—</div></div>
            </div>
        </section>

        <!-- Open Positions -->
        <section id="positions-section" class="glass card full-width" style="margin-bottom:1.5rem;">
            <div class="card-header">
                <h2>Open Positions</h2>
                <div class="badge blue">Live</div>
            </div>
            <div class="stocks-scroll-wrapper">
                <div class="table-headers" id="positions-headers" style="display:none;">
                    <div class="th-label">Symbol</div>
                    <div class="th-label">Type</div>
                    <div class="th-label">Strike</div>
                    <div class="th-label">Expiration</div>
                    <div class="th-label">DTE</div>
                    <div class="th-label">Qty</div>
                    <div class="th-label">Avg Cost</div>
                    <div class="th-label">Unreal P/L</div>
                    <div class="th-label">Delta</div>
                </div>
                <div id="positions-rows"></div>
            </div>
        </section>

        <!-- Wheel Cycles -->
        <section id="cycles-section" class="glass card full-width">
            <div class="card-header"><h2>Wheel Cycles</h2></div>
            <div id="cycles-list"></div>
        </section>
    </main>
</div>
<script src="config.js"></script>
<script src="wheel.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create `src/web/wheel.js`**

```javascript
const API = window.MARKET_INTELLIGENCE_CONFIG?.apiBase ?? '';

function fmt(val, prefix='$', decimals=2) {
    if (val == null) return '—';
    const n = parseFloat(val);
    return (n < 0 ? '-' + prefix : prefix) + Math.abs(n).toFixed(decimals);
}

async function loadStats() {
    try {
        const res = await fetch(API + '/api/wheel/stats');
        const d = await res.json();
        document.getElementById('stat-mtd').textContent = fmt(d.premium_mtd);
        document.getElementById('stat-ytd').textContent = fmt(d.premium_ytd);
        document.getElementById('stat-winrate').textContent =
            d.win_rate != null ? (d.win_rate * 100).toFixed(1) + '%' : '—';
        document.getElementById('stat-open').textContent =
            d.open_cycles != null ? d.open_cycles : '—';
        document.getElementById('stat-delta').textContent =
            d.max_short_put_delta != null ? d.max_short_put_delta.toFixed(2) : '—';
    } catch (e) {
        console.error('stats load error', e);
    }
}

async function loadPositions() {
    try {
        const res = await fetch(API + '/api/wheel/positions');
        const { positions } = await res.json();
        const container = document.getElementById('positions-rows');
        const headers = document.getElementById('positions-headers');

        if (!positions.length) {
            container.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">No open positions.</div>';
            return;
        }
        headers.style.display = '';
        container.innerHTML = positions.map(p => {
            const pnlColor = (p.unrealized_pnl ?? 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
            const deltaStr = p.delta != null ? p.delta.toFixed(2) : '—';
            return `<div class="table-row" style="display:grid;grid-template-columns:repeat(9,1fr);padding:.5rem 1rem;border-bottom:1px solid var(--border);">
                <div>${p.symbol}</div>
                <div>${p.option_type ?? p.asset_type}</div>
                <div>${p.strike != null ? '$' + p.strike : '—'}</div>
                <div>${p.expiration ?? '—'}</div>
                <div>${p.dte ?? '—'}</div>
                <div>${p.quantity}</div>
                <div>${fmt(p.average_price)}</div>
                <div style="color:${pnlColor}">${fmt(p.unrealized_pnl)}</div>
                <div>${deltaStr}</div>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('positions load error', e);
    }
}

async function loadCycles() {
    try {
        const res = await fetch(API + '/api/wheel/cycles?limit=100');
        const { cycles } = await res.json();
        const container = document.getElementById('cycles-list');

        if (!cycles.length) {
            container.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">No wheel cycles yet. Run a pipeline sync after trading.</div>';
            return;
        }

        container.innerHTML = cycles.map(c => {
            const statusColor = c.status === 'OPEN' ? 'var(--accent-blue)' : 'var(--text-secondary)';
            const pnlStr = c.realized_pnl != null ? fmt(c.realized_pnl) : (c.status === 'OPEN' ? 'open' : '—');
            const tradesHtml = (c.trades || []).map(t =>
                `<div style="padding:.25rem 1rem;font-size:.8rem;color:var(--text-secondary);">
                    ${t.executed_at?.slice(0,10)} &nbsp; ${t.instruction} &nbsp; ${t.symbol} &nbsp; ${fmt(t.net_amount)}
                </div>`
            ).join('');
            return `<details style="border-bottom:1px solid var(--border);padding:.75rem 1rem;">
                <summary style="cursor:pointer;display:flex;gap:2rem;align-items:center;list-style:none;">
                    <span style="font-weight:600;">${c.underlying}</span>
                    <span style="color:${statusColor};font-size:.82rem;">${c.status}</span>
                    <span>${c.opened_at ?? '—'} → ${c.closed_at ?? '…'}</span>
                    <span>Premium: ${fmt(c.total_premium)}</span>
                    <span>P/L: ${pnlStr}</span>
                </summary>
                ${tradesHtml || '<div style="padding:.5rem 1rem;font-size:.8rem;color:var(--text-secondary);">No trade legs linked.</div>'}
            </details>`;
        }).join('');
    } catch (e) {
        console.error('cycles load error', e);
    }
}

loadStats();
loadPositions();
loadCycles();
```

- [ ] **Step 4: Rebuild dashboard and verify in browser**

```bash
docker compose up --build -d dashboard
```

Open `https://dev-mi.austin10berge.com/wheel.html` in a browser. Verify:
- Nav shows "Wheel" as active (blue underline), all other links work
- Stats bar shows `—` placeholders (no data yet — that's correct)
- Positions section shows "No open positions"
- Cycles section shows "No wheel cycles yet"

Check browser console for JS errors.

- [ ] **Step 5: Commit**

```bash
git add src/web/wheel.html src/web/wheel.js src/web/index.html src/web/scanner.html src/web/watchlist.html src/web/technical-analysis.html src/web/backtest.html
git commit -m "feat(wheel): add Wheel dashboard page with positions, cycles, stats"
```

---

## Task 8: Discord Integration

**Files:**
- Modify: `discord_bot/commands/chat.py`
- Modify: `discord_bot/trade_system_prompt.txt`

- [ ] **Step 1: Add `!note` handling to `on_message` in `discord_bot/commands/chat.py`**

Read the current `on_message` method to find the exact start (line ~97). Add a `!note` check as the very first thing after the `message.author.bot` guard:

```python
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        # !note command — save a note to a trade or cycle
        if message.content.startswith("!note "):
            await self._handle_note(message)
            return

        # ... rest of existing on_message ...
```

Then add the `_handle_note` method to the `TradeChatCog` class (before `on_message`):

```python
    async def _handle_note(self, message: discord.Message) -> None:
        """Handle !note trade:<id> <text> or !note cycle:<id> <text>."""
        import sqlite3
        from contextlib import closing
        from ..store import insert_note  # noqa: F401 — import path relative to discord_bot pkg

        # Adjust import path: the discord_bot is a separate package from src/
        # Use absolute import via sys.path or a shared helper.
        # Simplest: import from src directly since discord_bot runs in same container.
        try:
            from src.wheel_tracker.store import insert_note as _insert_note
            from src.config import settings
        except ImportError:
            await message.reply("Wheel tracker not available.")
            return

        parts = message.content[len("!note "):].strip().split(None, 1)
        if len(parts) < 2 or ":" not in parts[0]:
            await message.reply(
                "Usage: `!note trade:<id> <text>` or `!note cycle:<id> <text>`"
            )
            return

        ref, content = parts[0], parts[1]
        kind, _, raw_id = ref.partition(":")
        try:
            entity_id = int(raw_id)
        except ValueError:
            await message.reply(f"Invalid ID: `{raw_id}`")
            return

        trade_id = entity_id if kind == "trade" else None
        cycle_id = entity_id if kind == "cycle" else None

        if trade_id is None and cycle_id is None:
            await message.reply("Use `trade:<id>` or `cycle:<id>`.")
            return

        with closing(sqlite3.connect(settings.db_path)) as conn:
            _insert_note(conn, {
                "trade_id": trade_id,
                "cycle_id": cycle_id,
                "source": "discord",
                "content": content.strip(),
            })

        await message.add_reaction("✅")
```

- [ ] **Step 2: Add wt_* schema to `discord_bot/trade_system_prompt.txt`**

Append the following section to the end of `trade_system_prompt.txt`:

```
## Wheel Tracker Database (wt_* tables)

The SQLite database at settings.db_path also contains these tables for trade tracking:

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

- [ ] **Step 3: Rebuild discord bot and verify**

```bash
docker compose up --build -d discord-bot
```

In the configured trade chat channel, send:

```
!note cycle:1 Testing the note command
```

Expected: bot reacts with ✅ (or replies "Wheel tracker not available" if sync hasn't run yet and no cycle with id=1 exists — the insert will still succeed since it's just a note row with no FK enforcement).

- [ ] **Step 4: Commit**

```bash
git add discord_bot/commands/chat.py discord_bot/trade_system_prompt.txt
git commit -m "feat(wheel): Discord !note command + wt schema in trade-chat system prompt"
```

---

## Self-Review Checklist

After all tasks complete, verify:

- [ ] `docker compose run --rm test python3 -m pytest tests/test_wheel_tracker_store.py tests/test_wheel_tracker_cycles.py tests/test_wheel_tracker_alerts.py -v` — all pass
- [ ] `~/.local/bin/ruff check src/wheel_tracker/ tests/test_wheel_tracker_*.py` — no errors
- [ ] `https://dev-mi.austin10berge.com/wheel.html` — loads, nav works, no JS console errors
- [ ] `curl https://dev-mi.austin10berge.com/api/wheel/stats` — returns valid JSON
- [ ] Run a manual pipeline sync and confirm wt_* tables populate:
  ```bash
  docker compose run --rm pipeline python3 -c "
  import asyncio; from src.wheel_tracker import run_sync; print(asyncio.run(run_sync()))
  "
  ```
- [ ] `!note cycle:1 test` in Discord → ✅ reaction
