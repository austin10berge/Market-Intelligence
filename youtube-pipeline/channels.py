from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone

from models import Video

logger = logging.getLogger(__name__)


class ChannelFetcher:
    """Lists recent uploads for a channel via yt-dlp and filters out Shorts /
    livestreams / re-uploads outside the lookback window."""

    def __init__(self, *, scan_depth: int, min_duration_seconds: int):
        self.scan_depth = scan_depth
        self.min_duration_seconds = min_duration_seconds

    def get_recent_videos(
        self,
        channel_url: str,
        channel_name: str,
        since: datetime,
    ) -> list[Video]:
        """Return videos uploaded at or after `since`."""
        # `--flat-playlist` gives metadata only (no transcript download).
        # We hit /videos to skip Shorts and Live tabs entirely at the URL level,
        # but still filter defensively below since yt-dlp can be inconsistent.
        videos_url = channel_url.rstrip("/") + "/videos"
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
            "--playlist-end",
            str(self.scan_depth),
            videos_url,
        ]
        logger.debug("Running: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=90, check=False
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "yt-dlp not found on PATH. Install with `pip install yt-dlp`."
            ) from e
        except subprocess.TimeoutExpired:
            logger.error("yt-dlp timed out listing %s", channel_url)
            return []

        if result.returncode != 0:
            logger.error(
                "yt-dlp failed for %s (rc=%s): %s",
                channel_url,
                result.returncode,
                result.stderr.strip(),
            )
            return []

        videos: list[Video] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping non-JSON line from yt-dlp")
                continue

            video = self._entry_to_video(entry, channel_name)
            if video is None:
                continue
            if video.published_at < since:
                continue
            videos.append(video)

        return videos

    def _entry_to_video(self, entry: dict, channel_name: str) -> Video | None:
        video_id = entry.get("id")
        if not video_id:
            return None

        # Filter livestreams / upcoming streams.
        live_status = entry.get("live_status")
        if live_status in {"is_live", "is_upcoming", "post_live"}:
            logger.info("Skipping live/upcoming video %s", video_id)
            return None

        duration = entry.get("duration")
        if duration is not None and duration < self.min_duration_seconds:
            logger.info(
                "Skipping short video %s (%ss < %ss)",
                video_id,
                duration,
                self.min_duration_seconds,
            )
            return None

        # `--flat-playlist` emits `timestamp` (epoch). Fall back to `upload_date`
        # (YYYYMMDD) when timestamp is missing.
        published_at = self._parse_published(entry)
        if published_at is None:
            logger.warning("No publish time for %s — skipping", video_id)
            return None

        title = entry.get("title") or "(untitled)"
        url = entry.get("url") or entry.get("webpage_url") or (
            f"https://www.youtube.com/watch?v={video_id}"
        )

        return Video(
            id=video_id,
            title=title,
            url=url,
            channel_name=channel_name,
            published_at=published_at,
            duration_seconds=int(duration) if duration else None,
        )

    @staticmethod
    def _parse_published(entry: dict) -> datetime | None:
        ts = entry.get("timestamp")
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)

        upload_date = entry.get("upload_date")
        if isinstance(upload_date, str) and len(upload_date) == 8:
            try:
                d = datetime.strptime(upload_date, "%Y%m%d")
                # yt-dlp's upload_date is date-only; treat as end-of-day UTC so
                # we don't lose videos uploaded "today" in lookback comparisons.
                return d.replace(tzinfo=timezone.utc) + timedelta(hours=23, minutes=59)
            except ValueError:
                return None
        return None
