"""SQLite storage layer for signals and digests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import settings


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
                json.dumps(metadata or {}),
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
