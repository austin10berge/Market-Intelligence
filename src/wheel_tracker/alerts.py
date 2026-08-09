"""DTE and assignment-risk alerts for open short option positions."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

logger = logging.getLogger(__name__)

_DTE_THRESHOLD = 7
_DELTA_THRESHOLD = 0.30


async def _send_alert(title: str, message: str, tags: str = "warning") -> None:
    from ..notify.ntfy import send_ntfy

    await send_ntfy(title=title, message=message, priority=4, tags=tags)


async def check_alerts(conn: sqlite3.Connection) -> list[str]:
    """Evaluate all open short option positions for DTE and delta thresholds.

    Sends NTFY notification for each triggered alert.
    Returns list of alert message strings (for logging/testing).
    """
    today = date.today().isoformat()
    sent: list[str] = []

    rows = conn.execute(
        """
        SELECT id, symbol, underlying, option_type, strike, expiration,
               dte, delta, last_dte_alerted, last_delta_alerted
        FROM wt_positions
        WHERE asset_type = 'OPTION' AND quantity < 0
        """
    ).fetchall()

    for row in rows:
        (
            pos_id,
            symbol,
            underlying,
            option_type,
            strike,
            expiration,
            dte,
            delta,
            last_dte_alerted,
            last_delta_alerted,
        ) = row

        # DTE alert
        if dte is not None and dte <= _DTE_THRESHOLD:
            if last_dte_alerted != today:
                msg = f"Expiring soon: {symbol} | DTE {dte} | {option_type} ${strike}"
                await _send_alert(
                    "Option Expiring This Week",
                    msg,
                    tags="warning,calendar",
                )
                conn.execute(
                    "UPDATE wt_positions SET last_dte_alerted = ? WHERE id = ?",
                    (today, pos_id),
                )
                conn.commit()
                sent.append(msg)
                logger.info("DTE alert sent: %s", msg)

        # Delta alert (assignment risk) — PUT positions only
        if delta is not None and abs(delta) >= _DELTA_THRESHOLD and option_type == "PUT":
            if last_delta_alerted != today:
                msg = f"Assignment risk: {symbol} | delta {delta:.2f} | ${strike} put exp {expiration}"
                await _send_alert(
                    "Assignment Risk Elevated",
                    msg,
                    tags="rotating_light,chart",
                )
                conn.execute(
                    "UPDATE wt_positions SET last_delta_alerted = ? WHERE id = ?",
                    (today, pos_id),
                )
                conn.commit()
                sent.append(msg)
                logger.info("Delta alert sent: %s", msg)

    return sent
