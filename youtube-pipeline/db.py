from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models import PostRecord, Video


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_videos (
              video_id TEXT PRIMARY KEY,
              channel_name TEXT NOT NULL,
              title TEXT NOT NULL,
              published_at TEXT NOT NULL,
              first_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS posted_videos (
              video_id TEXT PRIMARY KEY,
              discord_message_id TEXT NOT NULL,
              posted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pipeline_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              success INTEGER NOT NULL DEFAULT 0,
              videos_found INTEGER NOT NULL DEFAULT 0,
              videos_posted INTEGER NOT NULL DEFAULT 0
            );
            """
        )

    # --- dedup: "posted" is the source of truth for skipping ---
    def has_been_posted(self, video_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM posted_videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row is not None

    def record_post(self, record: PostRecord) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO posted_videos (video_id, discord_message_id, posted_at) "
            "VALUES (?, ?, ?)",
            (record.video_id, record.discord_message_id, record.posted_at.isoformat()),
        )

    def get_recent_posts(self, days: int) -> list[PostRecord]:
        rows = self.conn.execute(
            "SELECT video_id, discord_message_id, posted_at FROM posted_videos "
            "WHERE posted_at >= datetime('now', ?) ORDER BY posted_at DESC",
            (f"-{days} days",),
        ).fetchall()
        return [
            PostRecord(
                video_id=r[0],
                discord_message_id=r[1],
                posted_at=datetime.fromisoformat(r[2]),
            )
            for r in rows
        ]

    # --- seen: debug/observability only, not used for dedup ---
    def mark_seen(self, video: Video) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_videos "
            "(video_id, channel_name, title, published_at, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                video.id,
                video.channel_name,
                video.title,
                video.published_at.isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def has_been_seen(self, video_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen_videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row is not None

    # --- pipeline runs: tracks "last successful run" for adaptive lookback ---
    def start_run(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO pipeline_runs (started_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        return cur.lastrowid

    def finish_run(
        self, run_id: int, *, success: bool, videos_found: int, videos_posted: int
    ) -> None:
        self.conn.execute(
            "UPDATE pipeline_runs SET finished_at = ?, success = ?, "
            "videos_found = ?, videos_posted = ? WHERE id = ?",
            (
                datetime.now(timezone.utc).isoformat(),
                1 if success else 0,
                videos_found,
                videos_posted,
                run_id,
            ),
        )

    def last_successful_run_at(self) -> datetime | None:
        row = self.conn.execute(
            "SELECT finished_at FROM pipeline_runs "
            "WHERE success = 1 AND finished_at IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row[0])

    def close(self) -> None:
        self.conn.close()
