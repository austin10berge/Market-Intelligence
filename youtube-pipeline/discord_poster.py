from __future__ import annotations

import logging
import time

import httpx

from models import Summary

logger = logging.getLogger(__name__)


# Discord limits
EMBED_DESCRIPTION_MAX = 4096
EMBED_FIELD_VALUE_MAX = 1024
EMBED_TITLE_MAX = 256
YOUTUBE_RED = 0xFF0000


class DiscordPoster:
    def __init__(self, webhook_url: str):
        if not webhook_url:
            raise ValueError("DISCORD_WEBHOOK_URL is required")
        self.webhook_url = webhook_url

    def post_summary(self, summary: Summary) -> str | None:
        """Post a rich embed for one video. Returns Discord message ID on
        success, None on permanent failure."""
        embed = self._build_embed(summary)
        return self._post({"embeds": [embed]}, context=f"summary {summary.video_id}")

    def post_run_summary(self, text: str) -> None:
        """Plain text run summary. Best-effort; failures are logged not raised."""
        self._post({"content": text[:2000]}, context="run summary")

    def _build_embed(self, summary: Summary) -> dict:
        description = _truncate_at_sentence(summary.summary_text, EMBED_DESCRIPTION_MAX)
        takeaways_value = self._format_takeaways(summary.takeaways)

        embed: dict = {
            "title": _truncate(summary.title, EMBED_TITLE_MAX),
            "url": summary.url,
            "description": description,
            "color": YOUTUBE_RED,
            "author": {"name": summary.channel_name},
            "footer": {
                "text": f"Summarized by Claude • {summary.generated_at.strftime('%Y-%m-%d')}"
            },
            "thumbnail": {
                "url": f"https://img.youtube.com/vi/{summary.video_id}/mqdefault.jpg"
            },
        }
        if takeaways_value:
            embed["fields"] = [
                {"name": "Key Takeaways", "value": takeaways_value, "inline": False}
            ]
        return embed

    @staticmethod
    def _format_takeaways(takeaways: list[str]) -> str:
        if not takeaways:
            return ""
        bullets = [f"• {t}" for t in takeaways]
        joined = "\n".join(bullets)
        if len(joined) <= EMBED_FIELD_VALUE_MAX:
            return joined
        # Drop bullets from the end until we fit.
        while bullets and len("\n".join(bullets)) > EMBED_FIELD_VALUE_MAX:
            bullets.pop()
        return "\n".join(bullets) if bullets else joined[: EMBED_FIELD_VALUE_MAX - 1] + "…"

    def _post(self, payload: dict, *, context: str) -> str | None:
        # `wait=true` makes Discord return the created message object so we get
        # the ID back.
        url = self.webhook_url + ("&" if "?" in self.webhook_url else "?") + "wait=true"

        for attempt in (1, 2):
            try:
                resp = httpx.post(url, json=payload, timeout=15.0)
            except httpx.HTTPError as e:
                logger.warning(
                    "Discord network error on %s (attempt %d): %s", context, attempt, e
                )
                if attempt == 1:
                    time.sleep(5)
                    continue
                return None

            if 200 <= resp.status_code < 300:
                try:
                    return str(resp.json().get("id", ""))
                except ValueError:
                    return ""

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "5"))
                logger.warning(
                    "Discord rate-limited %s; sleeping %.1fs",
                    context,
                    retry_after,
                )
                time.sleep(retry_after)
                continue

            if 500 <= resp.status_code < 600 and attempt == 1:
                logger.warning(
                    "Discord 5xx on %s (rc=%d), retrying once", context, resp.status_code
                )
                time.sleep(5)
                continue

            logger.error(
                "Discord rejected %s (rc=%d): %s",
                context,
                resp.status_code,
                resp.text[:500],
            )
            return None

        return None


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _truncate_at_sentence(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    head = s[:limit]
    for sep in (". ", "! ", "? "):
        idx = head.rfind(sep)
        if idx > limit * 0.7:
            return head[: idx + 1].rstrip() + " …"
    return head.rstrip() + "…"
