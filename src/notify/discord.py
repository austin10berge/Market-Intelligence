"""Discord webhook notification delivery for nightly market digest."""

from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_POSTURE_COLORS: dict[str, int] = {
    "STRONGLY_BULLISH": 0x2ECC71,
    "BULLISH": 0x2ECC71,
    "CAUTIOUSLY_BULLISH": 0xF0E68C,
    "NEUTRAL": 0x95A5A6,
    "CAUTIOUSLY_BEARISH": 0xE67E22,
    "BEARISH": 0xE74C3C,
    "STRONGLY_BEARISH": 0xE74C3C,
}

_MAX_DESCRIPTION_LENGTH = 4000
_TRUNCATION_SUFFIX = "...\n*(truncated)*"


async def send_discord_digest(
    title: str,
    message: str,
    posture: str,
    composite_score: float,
) -> bool:
    """Post the nightly digest as a Discord embed via webhook.

    Args:
        title:           Embed title (e.g. market posture headline).
        message:         Digest body text (markdown supported).
        posture:         Posture string key used to pick embed colour.
        composite_score: Composite signal score shown in the footer.

    Returns:
        True if the webhook returned HTTP 2xx, False on any failure.
    """
    if not settings.discord_digest_webhook_url:
        logger.debug("Discord digest: no webhook URL configured, skipping")
        return False

    # Truncate description to Discord's 4000-char limit
    if len(message) > _MAX_DESCRIPTION_LENGTH:
        cut = _MAX_DESCRIPTION_LENGTH - len(_TRUNCATION_SUFFIX)
        message = message[:cut] + _TRUNCATION_SUFFIX

    color = _POSTURE_COLORS.get(posture.upper(), 0x95A5A6)

    payload = {
        "embeds": [
            {
                "title": title,
                "description": message,
                "color": color,
                "footer": {"text": f"Composite Score: {composite_score:+.3f}"},
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                settings.discord_digest_webhook_url,
                json=payload,
            )
            resp.raise_for_status()
            logger.info("Discord digest: embed sent successfully")
            return True

    except Exception:
        logger.exception("Discord digest: failed to send embed")
        return False
