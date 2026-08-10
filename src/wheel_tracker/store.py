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
        "id",
        "schwab_transaction_id",
        "executed_at",
        "asset_type",
        "symbol",
        "underlying",
        "option_type",
        "strike",
        "expiration",
        "instruction",
        "quantity",
        "net_amount",
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
    _prev = conn.row_factory
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
    conn.row_factory = _prev
    return [dict(r) for r in rows]


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

    total_csps = conn.execute("SELECT COUNT(*) FROM wt_cycles").fetchone()[0]
    closed_profitable = conn.execute(
        "SELECT COUNT(*) FROM wt_cycles WHERE status='CLOSED' AND realized_pnl > 0"
    ).fetchone()[0]
    closed_total = conn.execute("SELECT COUNT(*) FROM wt_cycles WHERE status='CLOSED'").fetchone()[0]

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
