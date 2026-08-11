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
        CREATE TABLE IF NOT EXISTS wt_equity_curve (
            date       TEXT PRIMARY KEY,
            equity     REAL NOT NULL,
            cash       REAL NOT NULL,
            deposits   REAL NOT NULL DEFAULT 0,
            spy_close  REAL
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


_OPEN_INSTRUCTIONS = {"SELL_TO_OPEN", "BUY_TO_OPEN"}
_CLOSE_INSTRUCTIONS = {"BUY_TO_CLOSE", "SELL_TO_CLOSE", "EXPIRED", "ASSIGNED"}


def _reconcile_options(trades: list[dict], active_option_symbols: set[str]) -> list[dict]:
    """Pair option opens with their closes by contract symbol (FIFO).

    After pairing, applies two corrections:
    1. If expiration < today and still OPEN → mark EXPIRED
    2. If contract not in active_option_symbols → mark CLOSED (inferred)

    Then detects credit/debit spreads (same underlying, expiration, open_date,
    option_type, one sell + one buy) and merges them into a single line."""
    from datetime import date, datetime

    today = date.today().isoformat()

    by_symbol: dict[str, list[dict]] = {}
    for t in trades:
        if t["asset_type"] != "OPTION":
            continue
        by_symbol.setdefault(t["symbol"], []).append(t)

    reconciled = []
    for sym, legs in by_symbol.items():
        pending_opens: list[dict] = []
        for leg in legs:
            instr = leg["instruction"]
            if instr in _OPEN_INSTRUCTIONS:
                pending_opens.append(leg)
            elif instr in _CLOSE_INSTRUCTIONS and pending_opens:
                opener = pending_opens.pop(0)
                open_dt = opener["executed_at"][:10]
                close_dt = leg["executed_at"][:10]
                try:
                    days = (datetime.fromisoformat(close_dt) - datetime.fromisoformat(open_dt)).days
                except (ValueError, TypeError):
                    days = None
                net = (opener["net_amount"] or 0) + (leg["net_amount"] or 0)
                strategy = _strategy_label(opener["asset_type"], opener["option_type"], opener["instruction"])
                reconciled.append({
                    "type": "option",
                    "strategy": strategy,
                    "option_type": opener["option_type"],
                    "strike": opener["strike"],
                    "expiration": opener["expiration"],
                    "open_date": open_dt,
                    "close_date": close_dt,
                    "close_reason": instr,
                    "days_held": days,
                    "net": round(net, 2),
                    "status": "CLOSED",
                    "_open_instr": opener["instruction"],
                    "_contract": sym,
                })
            elif instr in _CLOSE_INSTRUCTIONS:
                net = leg["net_amount"] or 0
                strategy = _strategy_label(leg["asset_type"], leg["option_type"], leg["instruction"])
                reconciled.append({
                    "type": "option",
                    "strategy": strategy,
                    "option_type": leg["option_type"],
                    "strike": leg["strike"],
                    "expiration": leg["expiration"],
                    "open_date": leg["executed_at"][:10],
                    "close_date": leg["executed_at"][:10],
                    "close_reason": instr,
                    "days_held": 0,
                    "net": round(net, 2),
                    "status": "CLOSED",
                    "_open_instr": instr,
                    "_contract": sym,
                })
        for opener in pending_opens:
            strategy = _strategy_label(opener["asset_type"], opener["option_type"], opener["instruction"])
            exp = opener["expiration"] or ""
            if exp and exp < today:
                open_dt = opener["executed_at"][:10]
                try:
                    days = (datetime.fromisoformat(exp) - datetime.fromisoformat(open_dt)).days
                except (ValueError, TypeError):
                    days = None
                reconciled.append({
                    "type": "option",
                    "strategy": strategy,
                    "option_type": opener["option_type"],
                    "strike": opener["strike"],
                    "expiration": exp,
                    "open_date": open_dt,
                    "close_date": exp,
                    "close_reason": "EXPIRED",
                    "days_held": days,
                    "net": round(opener["net_amount"] or 0, 2),
                    "status": "CLOSED",
                    "_open_instr": opener["instruction"],
                    "_contract": sym,
                })
            elif sym not in active_option_symbols:
                open_dt = opener["executed_at"][:10]
                reconciled.append({
                    "type": "option",
                    "strategy": strategy,
                    "option_type": opener["option_type"],
                    "strike": opener["strike"],
                    "expiration": exp,
                    "open_date": open_dt,
                    "close_date": None,
                    "close_reason": "INFERRED",
                    "days_held": None,
                    "net": round(opener["net_amount"] or 0, 2),
                    "status": "CLOSED",
                    "_open_instr": opener["instruction"],
                    "_contract": sym,
                })
            else:
                reconciled.append({
                    "type": "option",
                    "strategy": strategy,
                    "option_type": opener["option_type"],
                    "strike": opener["strike"],
                    "expiration": exp,
                    "open_date": opener["executed_at"][:10],
                    "close_date": None,
                    "close_reason": None,
                    "days_held": None,
                    "net": round(opener["net_amount"] or 0, 2),
                    "status": "OPEN",
                    "_open_instr": opener["instruction"],
                    "_contract": sym,
                })

    reconciled.sort(key=lambda r: r["open_date"])
    reconciled = _merge_spreads(reconciled)

    for r in reconciled:
        r.pop("_open_instr", None)
        r.pop("_contract", None)
    return reconciled


def _merge_spreads(reconciled: list[dict]) -> list[dict]:
    """Detect vertical spreads: two legs with same option_type, expiration, and
    open_date where one is SELL_TO_OPEN and the other BUY_TO_OPEN.  Merge into
    a single spread line."""
    used: set[int] = set()
    merged: list[dict] = []

    for i, a in enumerate(reconciled):
        if i in used or a["type"] != "option":
            continue
        for j, b in enumerate(reconciled):
            if j <= i or j in used or b["type"] != "option":
                continue
            if (
                a["option_type"] == b["option_type"]
                and a["expiration"] == b["expiration"]
                and a["open_date"] == b["open_date"]
                and a["close_date"] == b["close_date"]
                and a.get("_open_instr") in _OPEN_INSTRUCTIONS
                and b.get("_open_instr") in _OPEN_INSTRUCTIONS
                and a.get("_open_instr") != b.get("_open_instr")
            ):
                sell_leg = a if a["_open_instr"] == "SELL_TO_OPEN" else b
                buy_leg = b if a["_open_instr"] == "SELL_TO_OPEN" else a
                net = round(a["net"] + b["net"], 2)
                is_credit = (sell_leg["net"] + (buy_leg.get("net") or 0)) > 0 if a["status"] != "CLOSED" else net > 0
                spread_type = "Credit" if sell_leg["strike"] > buy_leg["strike"] and a["option_type"] == "PUT" else "Credit" if sell_leg["strike"] < buy_leg["strike"] and a["option_type"] == "CALL" else "Debit"
                opt_label = "Put" if a["option_type"] == "PUT" else "Call"
                strategy = f"{opt_label} {spread_type} Spread"
                hi_strike = max(sell_leg["strike"], buy_leg["strike"])
                lo_strike = min(sell_leg["strike"], buy_leg["strike"])
                days = a["days_held"] if a["days_held"] is not None else b["days_held"]
                merged.append({
                    "type": "option",
                    "strategy": strategy,
                    "option_type": a["option_type"],
                    "strike": hi_strike,
                    "strike_low": lo_strike,
                    "expiration": a["expiration"],
                    "open_date": a["open_date"],
                    "close_date": a["close_date"],
                    "close_reason": a["close_reason"] or b["close_reason"],
                    "days_held": days,
                    "net": net,
                    "status": a["status"],
                    "_open_instr": "SPREAD",
                    "_contract": a.get("_contract", ""),
                })
                used.add(i)
                used.add(j)
                break
        if i not in used:
            merged.append(a)
    return merged


def _reconcile_equity(trades: list[dict], positions: dict[str, dict]) -> list[dict]:
    """Collapse equity buy/sell trades into holdings with current valuation.

    Returns one row per ticker showing shares_held, cost_basis, current_value,
    and unrealized_pnl (from live position data) or realized P&L if fully sold."""
    by_ticker: dict[str, list[dict]] = {}
    for t in trades:
        if t["asset_type"] != "EQUITY":
            continue
        by_ticker.setdefault(t["symbol"], []).append(t)

    result = []
    for ticker, legs in by_ticker.items():
        shares_held = 0.0
        total_cost = 0.0
        realized = 0.0
        first_buy = None
        for leg in legs:
            qty = abs(leg["quantity"] or 0)
            if leg["instruction"] in ("BUY", "BUY_TO_OPEN"):
                if first_buy is None:
                    first_buy = leg["executed_at"][:10]
                shares_held += qty
                total_cost += abs(leg["net_amount"] or 0)
            elif leg["instruction"] in ("SELL", "SELL_TO_CLOSE"):
                if shares_held > 0:
                    avg_cost = total_cost / shares_held
                    realized += (leg["net_amount"] or 0) - avg_cost * qty
                    total_cost -= avg_cost * qty
                    shares_held -= qty

        pos = positions.get(ticker)
        if shares_held > 0 and pos:
            current_price = pos.get("current_price") or 0
            current_value = round(current_price * shares_held, 2)
            unrealized = round(current_value - total_cost, 2)
            avg_cost = round(total_cost / shares_held, 2) if shares_held else 0
            result.append({
                "type": "equity",
                "strategy": "Shares Held",
                "ticker": ticker,
                "shares": shares_held,
                "avg_cost": avg_cost,
                "current_price": round(current_price, 2),
                "current_value": current_value,
                "unrealized_pnl": unrealized,
                "first_buy": first_buy,
                "status": "OPEN",
            })
        elif shares_held > 0:
            avg_cost = round(total_cost / shares_held, 2) if shares_held else 0
            result.append({
                "type": "equity",
                "strategy": "Shares Held",
                "ticker": ticker,
                "shares": shares_held,
                "avg_cost": avg_cost,
                "current_price": None,
                "current_value": None,
                "unrealized_pnl": None,
                "first_buy": first_buy,
                "status": "OPEN",
            })
        else:
            result.append({
                "type": "equity",
                "strategy": "Shares Sold",
                "ticker": ticker,
                "shares": 0,
                "avg_cost": None,
                "current_price": None,
                "current_value": None,
                "unrealized_pnl": None,
                "realized_pnl": round(realized, 2),
                "first_buy": first_buy,
                "status": "CLOSED",
            })
    return result


def get_ticker_ledger(conn: sqlite3.Connection) -> list[dict]:
    """Per-ticker summary with reconciled trade legs.

    Options are paired open→close by contract symbol (FIFO).
    Equity trades are collapsed into current holdings with live valuation."""
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
        SELECT symbol, underlying, asset_type, current_price, average_price,
               market_value, unrealized_pnl, quantity
        FROM wt_positions
        """
    ).fetchall()
    conn.row_factory = _prev

    active_underlyings: set[str] = set()
    active_option_symbols: set[str] = set()
    equity_positions: dict[str, dict] = {}
    for r in position_rows:
        p = dict(r)
        key = p["underlying"] or p["symbol"]
        active_underlyings.add(key)
        if p["asset_type"] == "OPTION":
            active_option_symbols.add(p["symbol"])
        elif p["asset_type"] == "EQUITY":
            equity_positions[p["symbol"]] = p

    groups: dict[str, list[dict]] = {}
    for row in trade_rows:
        trade = dict(row)
        key = trade["underlying"] or trade["symbol"]
        groups.setdefault(key, []).append(trade)

    tickers = []
    for underlying, trades in groups.items():
        rec_options = _reconcile_options(trades, active_option_symbols)
        rec_equity = _reconcile_equity(trades, equity_positions)
        reconciled = rec_options + rec_equity

        option_premium = sum(r["net"] for r in rec_options if r["status"] == "CLOSED")
        equity_return = 0.0
        for eq in rec_equity:
            if eq["status"] == "OPEN" and eq.get("unrealized_pnl") is not None:
                equity_return += eq["unrealized_pnl"]
            elif eq["status"] == "CLOSED":
                equity_return += eq.get("realized_pnl", 0)

        total_return = round(option_premium + equity_return, 2)
        total_premium = sum(r["net"] for r in rec_options if r["net"] > 0)

        last_date = trades[-1]["executed_at"][:10] if trades else ""

        tickers.append({
            "underlying": underlying,
            "status": "ACTIVE" if underlying in active_underlyings else "CLOSED",
            "total_premium": round(total_premium, 2),
            "total_return": total_return,
            "trade_count": len(reconciled),
            "last_trade_date": last_date,
            "reconciled_trades": reconciled,
        })

    tickers.sort(key=lambda tk: tk["last_trade_date"], reverse=True)
    tickers.sort(key=lambda tk: tk["status"] != "ACTIVE")
    return tickers


def get_wheel_stats(conn: sqlite3.Connection) -> dict:
    from datetime import date

    today = date.today().isoformat()
    mtd_start = today[:7] + "-01"
    ytd_start = today[:4] + "-01-01"

    def _net_premium(start: str) -> float:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(net_amount), 0)
            FROM wt_trades
            WHERE asset_type = 'OPTION'
              AND instruction IN ('SELL_TO_OPEN', 'BUY_TO_CLOSE')
              AND executed_at >= ?
            """,
            (start,),
        ).fetchone()
        return row[0]

    def _net_returns() -> float | None:
        row = conn.execute(
            "SELECT equity, deposits FROM wt_equity_curve ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return row[0] - row[1]

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
        "premium_mtd": round(_net_premium(mtd_start), 2),
        "premium_ytd": round(_net_premium(ytd_start), 2),
        "returns_ytd": round(nr, 2) if (nr := _net_returns()) is not None else 0,
        "win_rate": round(len(won_legs) / len(closed_legs), 3) if closed_legs else None,
        "total_tickers": total_tickers,
        "active_tickers": active_tickers,
        "max_short_put_delta": max_delta_row[0],
    }


def write_equity_curve(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.execute("DELETE FROM wt_equity_curve")
    conn.executemany(
        """
        INSERT INTO wt_equity_curve (date, equity, cash, deposits, spy_close)
        VALUES (:date, :equity, :cash, :deposits, :spy_close)
        """,
        rows,
    )
    conn.commit()


def read_equity_curve(conn: sqlite3.Connection, since: str) -> list[dict]:
    _prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT date, equity, cash, deposits, spy_close FROM wt_equity_curve WHERE date >= ? ORDER BY date",
        (since,),
    ).fetchall()
    conn.row_factory = _prev
    return [dict(r) for r in rows]
