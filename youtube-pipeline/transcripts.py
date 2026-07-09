from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from models import Video

logger = logging.getLogger(__name__)


# SRT cue separator: blank line between cues.
_TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}.*$")
_CUE_INDEX_RE = re.compile(r"^\d+$")
_TAG_RE = re.compile(r"<[^>]+>")


class TranscriptFetcher:
    def __init__(self, *, max_chars: int):
        self.max_chars = max_chars

    def get_transcript(self, video: Video) -> str | None:
        """Download English captions (auto then manual) via yt-dlp, clean to
        plain text, and truncate at a sentence boundary if needed.

        Returns None when no English captions exist."""
        with tempfile.TemporaryDirectory(prefix="yt-pipeline-") as tmpdir:
            tmp = Path(tmpdir)
            output_template = str(tmp / "%(id)s.%(ext)s")
            cmd = [
                "yt-dlp",
                "--write-auto-sub",
                "--write-sub",
                "--sub-lang",
                "en.*,en",
                "--skip-download",
                "--convert-subs",
                "srt",
                "--no-warnings",
                "--output",
                output_template,
                video.url,
            ]
            logger.debug("Running: %s", " ".join(cmd))
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=120, check=False
                )
            except FileNotFoundError as e:
                raise RuntimeError(
                    "yt-dlp not found on PATH. Install with `pip install yt-dlp`."
                ) from e
            except subprocess.TimeoutExpired:
                logger.error("Transcript fetch timed out for %s", video.id)
                return None

            if result.returncode != 0:
                logger.warning(
                    "yt-dlp transcript fetch failed for %s (rc=%s): %s",
                    video.id,
                    result.returncode,
                    result.stderr.strip(),
                )
                return None

            srt_path = self._pick_srt(tmp, video.id)
            if srt_path is None:
                logger.info("No transcript available for %s", video.id)
                return None

            text = self._clean_srt(srt_path.read_text(encoding="utf-8", errors="replace"))
            if not text.strip():
                return None
            return self._truncate(text, self.max_chars)

    @staticmethod
    def _pick_srt(tmp: Path, video_id: str) -> Path | None:
        # Prefer manual over auto-generated. yt-dlp encodes the lang in the
        # filename, e.g. <id>.en.srt or <id>.en-orig.srt.
        candidates = sorted(tmp.glob(f"{video_id}*.srt"))
        if not candidates:
            return None

        # Manual subs don't include "auto" markers; pick those first.
        manual = [p for p in candidates if "auto" not in p.name.lower()]
        return (manual or candidates)[0]

    @staticmethod
    def _clean_srt(srt: str) -> str:
        lines = srt.splitlines()
        out: list[str] = []
        last_text = ""
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if _CUE_INDEX_RE.match(line):
                continue
            if _TIMESTAMP_RE.match(line):
                continue
            cleaned = _TAG_RE.sub("", line).strip()
            if not cleaned:
                continue
            # Auto-captions duplicate lines as words roll in — dedupe runs.
            if cleaned == last_text:
                continue
            out.append(cleaned)
            last_text = cleaned

        text = " ".join(out)
        # Collapse repeated whitespace.
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        head = text[:max_chars]
        # Truncate at the last sentence boundary we can find.
        for sep in (". ", "! ", "? "):
            idx = head.rfind(sep)
            if idx > max_chars * 0.8:
                return head[: idx + 1].strip() + " […truncated]"
        return head.rstrip() + " […truncated]"
