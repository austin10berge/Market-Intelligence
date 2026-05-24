from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from channels import ChannelFetcher
from db import Database
from discord_poster import DiscordPoster
from models import PostRecord
from summarizer import ClaudeCLIError, Summarizer
from transcripts import TranscriptFetcher

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_DIR / "config.yaml"
DEFAULT_DB = PROJECT_DIR / "data" / "state.db"
LOG_FILE = PROJECT_DIR / "logs" / "pipeline.log"


def _setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    rotating = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=5
    )
    rotating.setFormatter(fmt)
    root.addHandler(rotating)


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve_lookback_since(db: Database, lookback_hours: int) -> datetime:
    """Return the cutoff `since` datetime. Uses last successful run when
    available so a missed day still gets picked up; otherwise falls back to
    the configured floor."""
    floor = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    last = db.last_successful_run_at()
    if last is None:
        return floor
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    # 1-hour overlap so we don't drop a video published right at the boundary.
    return min(floor, last - timedelta(hours=1))


def run(
    *,
    config_path: Path,
    db_path: Path,
    dry_run: bool,
    channel_filter: str | None,
) -> int:
    logger = logging.getLogger("pipeline")
    logger.info(
        "Pipeline starting (dry_run=%s, channel=%s)", dry_run, channel_filter or "*"
    )
    start = time.monotonic()

    config = _load_config(config_path)
    pipeline_cfg = config["pipeline"]
    channels = config["channels"]
    if channel_filter:
        channels = [c for c in channels if c["name"] == channel_filter]
        if not channels:
            logger.error("No channel named %r in config", channel_filter)
            return 2

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url and not dry_run:
        logger.error("DISCORD_WEBHOOK_URL is not set")
        return 2

    db = Database(db_path)
    run_id = None if dry_run else db.start_run()
    videos_found = 0
    videos_posted = 0
    videos_skipped_no_transcript = 0
    videos_skipped_summary_failed = 0

    success = False
    try:
        summarizer = Summarizer(
            timeout_seconds=int(pipeline_cfg["claude_timeout_seconds"]),
            inter_call_sleep_seconds=float(pipeline_cfg.get("inter_call_sleep_seconds", 3)),
        )
        if not dry_run:
            summarizer.verify_cli_available()

        channel_fetcher = ChannelFetcher(
            scan_depth=int(pipeline_cfg.get("channel_scan_depth", 15)),
            min_duration_seconds=int(pipeline_cfg.get("min_duration_seconds", 120)),
        )
        transcript_fetcher = TranscriptFetcher(
            max_chars=int(pipeline_cfg["max_transcript_chars"]),
        )
        poster = DiscordPoster(webhook_url) if not dry_run else None

        since = _resolve_lookback_since(
            db, int(pipeline_cfg.get("lookback_hours", 26))
        )
        logger.info("Lookback cutoff: %s", since.isoformat())

        first_summary = True
        for channel in channels:
            logger.info("Checking channel: %s", channel["name"])
            videos = channel_fetcher.get_recent_videos(
                channel_url=channel["url"],
                channel_name=channel["name"],
                since=since,
            )
            logger.info("  found %d candidate video(s)", len(videos))

            for video in videos:
                videos_found += 1
                if db.has_been_posted(video.id):
                    logger.info("  already posted: %s", video.id)
                    continue
                if not dry_run:
                    db.mark_seen(video)

                transcript = transcript_fetcher.get_transcript(video)
                if not transcript:
                    videos_skipped_no_transcript += 1
                    logger.info("  no transcript: %s — %s", video.id, video.title)
                    continue

                if dry_run:
                    logger.info(
                        "  [dry-run] would summarize %s (%d chars)",
                        video.id,
                        len(transcript),
                    )
                    summary = _placeholder_summary(video)
                else:
                    if not first_summary:
                        summarizer.sleep_between_calls()
                    first_summary = False
                    summary = summarizer.summarize(video, transcript)
                    if summary is None:
                        videos_skipped_summary_failed += 1
                        logger.warning("  summarization failed: %s", video.id)
                        continue

                if dry_run:
                    logger.info("  [dry-run] would post %s to Discord", video.id)
                    continue

                message_id = poster.post_summary(summary)
                if not message_id:
                    logger.warning("  Discord post failed: %s", video.id)
                    continue

                db.record_post(
                    PostRecord(
                        video_id=video.id,
                        discord_message_id=message_id,
                        posted_at=datetime.now(timezone.utc),
                    )
                )
                videos_posted += 1
                logger.info("  posted %s -> Discord %s", video.id, message_id)

        success = True
    except ClaudeCLIError as e:
        logger.error("Fatal: %s", e)
        return 3
    except Exception:
        logger.error("Unhandled exception:\n%s", traceback.format_exc())
        return 1
    finally:
        if run_id is not None:
            db.finish_run(
                run_id,
                success=success,
                videos_found=videos_found,
                videos_posted=videos_posted,
            )
        elapsed = time.monotonic() - start
        summary_line = (
            f"Run complete: {len(channels)} channel(s) checked, "
            f"{videos_found} new video(s) found, "
            f"{videos_posted} posted, "
            f"{videos_skipped_no_transcript} skipped (no transcript), "
            f"{videos_skipped_summary_failed} skipped (summary failed). "
            f"Elapsed: {elapsed:.1f}s"
        )
        logger.info(summary_line)
        if not dry_run and webhook_url and (videos_posted or videos_skipped_no_transcript or videos_skipped_summary_failed):
            try:
                DiscordPoster(webhook_url).post_run_summary(summary_line)
            except Exception as e:
                logger.warning("Run summary post failed: %s", e)
        db.close()

    return 0


def _placeholder_summary(video):
    from models import Summary

    return Summary(
        video_id=video.id,
        title=video.title,
        channel_name=video.channel_name,
        url=video.url,
        summary_text=f"[dry-run placeholder] Would summarize: {video.title}",
        takeaways=[
            "Dry-run takeaway 1",
            "Dry-run takeaway 2",
            "Dry-run takeaway 3",
        ],
        generated_at=datetime.now(timezone.utc),
    )


def main() -> int:
    load_dotenv(PROJECT_DIR / ".env")
    _setup_logging()

    parser = argparse.ArgumentParser(description="YouTube summary pipeline")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.yaml"
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help="Path to SQLite state DB"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run end-to-end without calling claude or posting to Discord",
    )
    parser.add_argument(
        "--channel",
        type=str,
        default=None,
        help="Only process this channel name (must match config.yaml)",
    )
    args = parser.parse_args()

    return run(
        config_path=args.config,
        db_path=args.db,
        dry_run=args.dry_run,
        channel_filter=args.channel,
    )


if __name__ == "__main__":
    sys.exit(main())
