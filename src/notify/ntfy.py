"""NTFY.sh notification delivery (primary)."""

from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


async def send_ntfy(title: str, message: str, priority: int = 3, tags: str = "chart") -> bool:
    """Send a push notification via NTFY.sh.

    Args:
        title: Notification title
        message: Notification body (supports markdown)
        priority: 1-5 (1=min, 3=default, 5=urgent)
        tags: Comma-separated emoji shortcodes (e.g. "chart,warning")

    Returns:
        True if sent successfully, False otherwise.
    """
    if not settings.ntfy_topic:
        logger.warning("NTFY: no topic configured, skipping")
        return False

    url = f"{settings.ntfy_server.rstrip('/')}/{settings.ntfy_topic}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # NTFY requires ASCII headers — use UTF-8 encoding per NTFY spec
            encoded_title = title.encode("utf-8")

            resp = await client.post(
                url,
                content=message.encode("utf-8"),
                headers={
                    "Title": encoded_title,
                    "Priority": str(priority),
                    "Tags": tags,
                    "Markdown": "yes",
                },
            )
            resp.raise_for_status()
            logger.info(f"NTFY: notification sent to topic '{settings.ntfy_topic}'")
            return True

    except Exception:
        logger.exception("NTFY: failed to send notification")
        return False
