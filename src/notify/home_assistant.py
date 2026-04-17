"""Home Assistant notification delivery (fallback)."""

from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


async def send_ha_notification(title: str, message: str) -> bool:
    """Send a push notification via Home Assistant REST API.

    Args:
        title: Notification title
        message: Notification body

    Returns:
        True if sent successfully, False otherwise.
    """
    if not settings.ha_url or not settings.ha_token:
        logger.debug("HA: not configured, skipping")
        return False

    url = f"{settings.ha_url.rstrip('/')}/api/services/{settings.ha_notify_service}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={"title": title, "message": message},
                headers={
                    "Authorization": f"Bearer {settings.ha_token}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            logger.info("HA: notification sent")
            return True

    except Exception:
        logger.exception("HA: failed to send notification")
        return False
