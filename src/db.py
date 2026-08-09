"""SQLite storage layer for signals and digests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import settings


def _json_default(obj: object) -> object:
    """Handle non-standard types when serializing to JSON (e.g. numpy bools)."""
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, (int,)):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    # Try numpy types if numpy is available
    try:
        import numpy as np

        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _get_connection() -> sqlite3.Connection:
    """Get a SQLite connection, creating the DB and tables if needed."""
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            source      TEXT NOT NULL,
            raw_value   REAL NOT NULL,
            scored_value INTEGER NOT NULL,
            direction   TEXT NOT NULL,
            extreme     INTEGER NOT NULL DEFAULT 0,
            metadata    TEXT NOT NULL DEFAULT '{}',
            summary     TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_signals_date_source
            ON daily_signals(date, source);

        CREATE TABLE IF NOT EXISTS digests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT NOT NULL UNIQUE,
            composite_score REAL NOT NULL,
            posture         TEXT NOT NULL,
            llm_summary     TEXT NOT NULL DEFAULT '',
            full_text       TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stock_iv_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            atm_iv REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_iv_history_date_symbol
            ON stock_iv_history(date, symbol);

        CREATE TABLE IF NOT EXISTS stock_iv_backfill_attempts (
            symbol       TEXT PRIMARY KEY,
            attempt_date TEXT NOT NULL,
            result_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backtest_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            definition TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trade_chat_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trade_chat_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id  TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            timestamp  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trade_chat_history_thread
            ON trade_chat_history(thread_id);
    """)

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


def store_signal(
    signal_date: date,
    source: str,
    raw_value: float,
    scored_value: int,
    direction: str,
    extreme: bool = False,
    metadata: dict | None = None,
    summary: str = "",
) -> None:
    """Store or update a daily signal."""
    import math

    if isinstance(raw_value, float) and (math.isnan(raw_value) or math.isinf(raw_value)):
        raw_value = 0.0
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO daily_signals (date, source, raw_value, scored_value,
                                       direction, extreme, metadata, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, source) DO UPDATE SET
                raw_value = excluded.raw_value,
                scored_value = excluded.scored_value,
                direction = excluded.direction,
                extreme = excluded.extreme,
                metadata = excluded.metadata,
                summary = excluded.summary
            """,
            (
                signal_date.isoformat(),
                source,
                raw_value,
                scored_value,
                direction,
                int(extreme),
                json.dumps(metadata or {}, default=_json_default),
                summary,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_signals(source: str, days: int = 5) -> list[dict]:
    """Get the most recent N days of signals for a source (for rolling averages)."""
    conn = _get_connection()
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT * FROM daily_signals
            WHERE source = ? AND date >= ?
            ORDER BY date DESC
            """,
            (source, cutoff),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_rolling_average(source: str, days: int = 5) -> float | None:
    """Compute the rolling average of raw_value for a source over N days."""
    signals = get_recent_signals(source, days)
    if not signals:
        return None
    return sum(s["raw_value"] for s in signals) / len(signals)


def store_digest(
    digest_date: date,
    composite_score: float,
    posture: str,
    llm_summary: str = "",
    full_text: str = "",
) -> None:
    """Store or update the nightly digest."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO digests (date, composite_score, posture, llm_summary, full_text)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                composite_score = excluded.composite_score,
                posture = excluded.posture,
                llm_summary = excluded.llm_summary,
                full_text = excluded.full_text
            """,
            (digest_date.isoformat(), composite_score, posture, llm_summary, full_text),
        )
        conn.commit()
    finally:
        conn.close()


def get_watchlist() -> list[str]:
    """Retrieve the custom options screener watchlist, or return defaults."""
    conn = _get_connection()
    try:
        row = conn.execute("SELECT value FROM app_config WHERE key = 'watchlist'").fetchone()
        if row:
            return json.loads(row["value"])

        # Defaults
        return [
            "AAPL",
            "MSFT",
            "GOOGL",
            "AMZN",
            "META",
            "NVDA",
            "TSLA",
            "NFLX",
            "AMD",
            "SPY",
            "QQQ",
            "IWM",
            "DIA",
            "XLE",
            "XLF",
            "XLK",
            "XLV",
            "JPM",
            "V",
            "MA",
            "SOFI",
        ]
    finally:
        conn.close()


def update_watchlist(tickers: list[str]) -> None:
    """Save the custom options screener watchlist to the database."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_config (key, value)
            VALUES ('watchlist', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(tickers),),
        )
        conn.commit()
    finally:
        conn.close()


def get_csp_settings() -> dict:
    """Retrieve the CSP screener logic parameters.

    Always merges the stored value on top of the full defaults so that any
    keys added after a row was first written are transparently backfilled.
    This means old DB rows (missing technical filter keys, score weights, etc.)
    never cause KeyErrors in the screener — no migration required.
    """
    defaults = {
        "min_dte": 30,
        "max_dte": 45,
        "min_delta": 0.15,
        "max_delta": 0.40,
        "min_roc": 1.0,
        "max_spread_pct": 25.0,
        # Technical filter fields
        "min_iv": 25.0,
        "min_rsi": 38.0,
        "max_rsi": 65.0,
        "min_adx": 15.0,
        "max_adx": 40.0,
        "pullback_mode": False,
        # Composite score weights
        "score_weight_ay": 0.35,
        "score_weight_pop": 0.20,
        "score_weight_iv_pct": 0.20,
        "score_weight_rsi": 0.15,
        "score_weight_adx": 0.10,
    }
    conn = _get_connection()
    try:
        row = conn.execute("SELECT value FROM app_config WHERE key = 'csp_settings'").fetchone()
        if row:
            stored = json.loads(row["value"])
            # Merge: defaults supply any key the stored row is missing
            return {**defaults, **stored}
        return defaults
    finally:
        conn.close()


def update_csp_settings(settings: dict) -> None:
    """Save the CSP screener logic parameters."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_config (key, value)
            VALUES ('csp_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(settings),),
        )
        conn.commit()
    finally:
        conn.close()


def get_stock_watchlist() -> list[str]:
    """Retrieve the stock screener watchlist."""
    conn = _get_connection()
    try:
        row = conn.execute("SELECT value FROM app_config WHERE key = 'stock_watchlist'").fetchone()
        if row:
            return json.loads(row["value"])

        # Defaults
        return ["NBIS", "WULF", "IONQ", "MARA", "QUBT", "AAPL", "MSFT"]
    finally:
        conn.close()


def update_stock_watchlist(tickers: list[str]) -> None:
    """Save the stock screener watchlist to the database."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_config (key, value)
            VALUES ('stock_watchlist', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(tickers),),
        )
        conn.commit()
    finally:
        conn.close()


def get_youtube_channels() -> list[str]:
    """Retrieve the monitored YouTube channel URLs list."""
    conn = _get_connection()
    try:
        row = conn.execute("SELECT value FROM app_config WHERE key = 'youtube_channels'").fetchone()
        if row:
            raw = row["value"]
            return [line for line in raw.splitlines() if line.strip()]
        return []
    finally:
        conn.close()


def save_youtube_channels(channels: list[str]) -> None:
    """Save the monitored YouTube channel URLs to the database."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_config (key, value)
            VALUES ('youtube_channels', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("\n".join(channels),),
        )
        conn.commit()
    finally:
        conn.close()


def store_stock_iv_snapshot(snapshot_date: date, symbol: str, atm_iv: float) -> None:
    """Store or update a daily ATM IV snapshot for a stock symbol."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO stock_iv_history (date, symbol, atm_iv)
            VALUES (?, ?, ?)
            ON CONFLICT(date, symbol) DO UPDATE SET
                atm_iv = excluded.atm_iv
            """,
            (snapshot_date.isoformat(), symbol, atm_iv),
        )
        conn.commit()
    finally:
        conn.close()


def get_stock_iv_history(symbol: str, lookback_days: int = 252) -> list[dict]:
    """Return the most recent ATM IV history points for a symbol."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT date, symbol, atm_iv
            FROM stock_iv_history
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (symbol, lookback_days),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_iv_backfill_attempt(symbol: str) -> dict | None:
    """Return the most recent IV backfill attempt record for a symbol, or None."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT symbol, attempt_date, result_count FROM stock_iv_backfill_attempts WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def record_iv_backfill_attempt(symbol: str, attempt_date: date, result_count: int) -> None:
    """Record (upsert) the outcome of an IV backfill attempt for a symbol."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO stock_iv_backfill_attempts (symbol, attempt_date, result_count)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                attempt_date = excluded.attempt_date,
                result_count = excluded.result_count
            """,
            (symbol, attempt_date.isoformat(), result_count),
        )
        conn.commit()
    finally:
        conn.close()


def get_signal_history(source: str, days: int = 30) -> list[dict]:
    """Return recent daily_signals rows for a given source, with metadata parsed."""
    conn = _get_connection()
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT date, source, raw_value, scored_value, direction,
                   extreme, metadata, summary
            FROM daily_signals
            WHERE source = ? AND date >= ?
            ORDER BY date DESC
            """,
            (source, cutoff),
        ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            if r.get("metadata"):
                try:
                    r["metadata"] = json.loads(r["metadata"])
                except Exception:
                    pass
            results.append(r)
        return results
    finally:
        conn.close()


def get_latest_signal(source: str) -> dict | None:
    """Return the most recent daily_signals row for a given source."""
    rows = get_signal_history(source, days=7)
    return rows[0] if rows else None


def get_insider_cache(max_age_hours: int = 12) -> dict | None:
    """Return cached insider trading data if it exists and is fresh enough."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = 'cache_insider_trading'"
        ).fetchone()
        if not row:
            return None
        cached = json.loads(row["value"])
        cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
        age_hours = (datetime.now() - cached_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        return cached
    finally:
        conn.close()


def set_insider_cache(data: dict) -> None:
    """Store insider trading fetch results in the cache."""
    conn = _get_connection()
    try:
        data["cached_at"] = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO app_config (key, value)
            VALUES ('cache_insider_trading', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(data, default=_json_default),),
        )
        conn.commit()
    finally:
        conn.close()


def get_unusual_volume_cache(max_age_hours: float = 0.25) -> dict | None:
    """Return cached unusual-volume scan results if they exist and are fresh enough."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = 'cache_unusual_volume'"
        ).fetchone()
        if not row:
            return None
        cached = json.loads(row["value"])
        cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
        age_hours = (datetime.now() - cached_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        return cached
    finally:
        conn.close()


def set_unusual_volume_cache(data: dict) -> None:
    """Store unusual-volume scan results in the cache."""
    conn = _get_connection()
    try:
        data["cached_at"] = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO app_config (key, value)
            VALUES ('cache_unusual_volume', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(data, default=_json_default),),
        )
        conn.commit()
    finally:
        conn.close()


def get_congressional_cache(max_age_hours: int = 12) -> dict | None:
    """Return cached congressional trades data if it exists and is fresh enough."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = 'cache_congressional_trades'"
        ).fetchone()
        if not row:
            return None
        cached = json.loads(row["value"])
        cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
        age_hours = (datetime.now() - cached_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        return cached
    finally:
        conn.close()


def set_congressional_cache(data: dict) -> None:
    """Store congressional trades fetch results in the cache."""
    conn = _get_connection()
    try:
        data["cached_at"] = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO app_config (key, value)
            VALUES ('cache_congressional_trades', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(data, default=_json_default),),
        )
        conn.commit()
    finally:
        conn.close()


def save_strategy(name: str, definition: dict) -> int:
    """Save or update a strategy definition."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO backtest_strategies (name, definition, updated_at)
            VALUES (?, ?, datetime('now'))
            """,
            (name, json.dumps(definition, default=_json_default)),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_strategies() -> list[dict]:
    """Get all saved strategies."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, name, definition, created_at, updated_at
            FROM backtest_strategies
            ORDER BY updated_at DESC
            """
        ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            try:
                r["definition"] = json.loads(r["definition"])
            except Exception:
                pass
            results.append(r)
        return results
    finally:
        conn.close()


def get_strategy(strategy_id: int) -> dict | None:
    """Get a specific strategy by ID."""
    conn = _get_connection()
    try:
        row = conn.execute(
            """
            SELECT id, name, definition, created_at, updated_at
            FROM backtest_strategies
            WHERE id = ?
            """,
            (strategy_id,),
        ).fetchone()
        if not row:
            return None
        r = dict(row)
        try:
            r["definition"] = json.loads(r["definition"])
        except Exception:
            pass
        return r
    finally:
        conn.close()


def delete_strategy(strategy_id: int) -> bool:
    """Delete a strategy by ID."""
    conn = _get_connection()
    try:
        cursor = conn.execute("DELETE FROM backtest_strategies WHERE id = ?", (strategy_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ── Trade chat ────────────────────────────────────────────────────────────────


def get_trade_chat_channel_id() -> str | None:
    """Return the configured Discord channel ID for trade chat, or None."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM trade_chat_config WHERE key = 'channel_id'"
        ).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_trade_chat_channel_id(channel_id: str) -> None:
    """Upsert the designated trade chat channel ID."""
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO trade_chat_config(key, value) VALUES('channel_id', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (channel_id,),
        )
        conn.commit()
    finally:
        conn.close()


def save_trade_chat_message(thread_id: str, role: str, content: str) -> None:
    """Append a message to a trade chat thread's history."""
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO trade_chat_history(thread_id, role, content) VALUES(?, ?, ?)",
            (thread_id, role, content),
        )
        conn.commit()
    finally:
        conn.close()


_TRADE_CHAT_HISTORY_LIMIT = 40  # cap LLM prompt growth on long-running threads


def get_trade_chat_history(thread_id: str, limit: int = _TRADE_CHAT_HISTORY_LIMIT) -> list[dict]:
    """Return the most recent `limit` messages for a thread, in chronological order."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content FROM ("
            "  SELECT id, role, content FROM trade_chat_history"
            "  WHERE thread_id = ? ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (thread_id, limit),
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]
    finally:
        conn.close()


def is_trade_chat_thread(thread_id: str) -> bool:
    """Return True if this thread_id has any messages in trade_chat_history."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM trade_chat_history WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
