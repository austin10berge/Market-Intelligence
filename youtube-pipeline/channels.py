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
        tabs: list[str] | None = None,
    ) -> list[Video]:
        """Return videos uploaded at or after `since`.

        Scans each tab in `tabs` (default: ["videos"]) and deduplicates by ID.
        """
        if tabs is None:
            tabs = ["videos", "streams"]
        seen_ids: set[str] = set()
        all_videos: list[Video] = []
        for tab in tabs:
            for v in self._get_videos_from_tab(channel_url, channel_name, since, tab):
                if v.id not in seen_ids:
                    seen_ids.add(v.id)
                    all_videos.append(v)
        return all_videos

    def _get_videos_from_tab(
        self,
        channel_url: str,
        channel_name: str,
        since: datetime,
        tab: str,
    ) -> list[Video]:
        # `--flat-playlist` gives metadata only (no transcript download).
        # We hit specific tabs (e.g. /videos, /streams) at the URL level to
        # avoid Shorts; filter defensively below since yt-dlp can be inconsistent.
        videos_url = channel_url.rstrip("/") + "/" + tab
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
            "--playlist-end",
            str(self.scan_depth),
            "--extractor-args",
            "youtubetab:approximate_date",
            videos_url,
        ]
        logger.debug("Running: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
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
            published_at = self._fetch_upload_date(video_id)
        if published_at is None:
            logger.warning("No publish time for %s — skipping", video_id)
            return None

        title = entry.get("title") or "(untitled)"
        url = (
            entry.get("url")
            or entry.get("webpage_url")
            or (f"https://www.youtube.com/watch?v={video_id}")
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
    def _fetch_upload_date(video_id: str) -> datetime | None:
        """Fetch upload_date for a single video when flat-playlist omits it."""
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-warnings",
            "--no-playlist",
            "--skip-download",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            if result.returncode != 0 or not result.stdout.strip():
                return None
            data = json.loads(result.stdout.strip())
            ts = data.get("timestamp")
            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            upload_date = data.get("upload_date")
            if isinstance(upload_date, str) and len(upload_date) == 8:
                d = datetime.strptime(upload_date, "%Y%m%d")
                return d.replace(tzinfo=timezone.utc) + timedelta(hours=23, minutes=59)
        except Exception as exc:
            logger.debug("Individual metadata fetch failed for %s: %s", video_id, exc)
        return None

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
