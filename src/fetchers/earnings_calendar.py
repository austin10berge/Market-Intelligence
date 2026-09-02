"""Upcoming earnings calendar fetcher via Alpha Vantage.

Fetches earnings reports due in the next 21 calendar days from the AV
EARNINGS_CALENDAR endpoint. Informational signal (value=0.0) — helps the LLM
flag earnings risk or IV-crush opportunity for watchlist and CSP tickers.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from io import StringIO

from ..config import settings
from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client

logger = logging.getLogger(__name__)

_AV_BASE = "https://www.alphavantage.co/query"
_LOOKAHEAD_DAYS = 21
_REQUEST_TIMEOUT = 20


class EarningsCalendarFetcher(BaseFetcher):
    """Fetch upcoming earnings for the next 21 days via Alpha Vantage."""

    @property
    def name(self) -> str:
        return "Earnings Calendar"

    async def fetch(self) -> Signal | None:
        if not settings.alpha_vantage_api_key:
            logger.warning("Earnings Calendar: ALPHA_VANTAGE_API_KEY not set — skipping")
            return None

        client = await get_http_client()

        try:
            resp = await client.get(
                _AV_BASE,
                params={
                    "function": "EARNINGS_CALENDAR",
                    "apikey": settings.alpha_vantage_api_key,
                    "horizon": "3month",
                },
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            csv_text = resp.text
        except Exception as exc:
            logger.warning("Earnings Calendar: HTTP error — %s", exc)
            return None

        # AV returns HTTP 200 with a JSON body when the free-tier quota is exhausted
        # rather than a proper error code. Detect it by checking for a JSON envelope.
        if csv_text.lstrip().startswith("{"):
            logger.warning("Earnings Calendar: AV returned JSON instead of CSV — likely rate-limited")
            return None

        # AV returns CSV: symbol,name,reportDate,fiscalDateEnding,estimate,currency
        today = date.today()
        cutoff = today + timedelta(days=_LOOKAHEAD_DAYS)

        upcoming: list[dict] = []
        try:
            reader = csv.DictReader(StringIO(csv_text))
            # Verify this is actually an earnings CSV, not some other AV response.
            if reader.fieldnames and "reportDate" not in reader.fieldnames:
                logger.warning("Earnings Calendar: unexpected CSV schema — %s", reader.fieldnames)
                return None
            for row in reader:
                report_date_str = row.get("reportDate", "")
                try:
                    report_date = date.fromisoformat(report_date_str)
                except ValueError:
                    continue
                if today <= report_date <= cutoff:
                    estimate = row.get("estimate", "")
                    upcoming.append({
                        "symbol": row.get("symbol", ""),
                        "name": row.get("name", "")[:40],
                        "report_date": report_date_str,
                        "estimate": estimate if estimate else "N/A",
                    })
                elif report_date > cutoff:
                    # CSV is sorted ascending by date; once past the window, stop.
                    break
        except Exception as exc:
            logger.warning("Earnings Calendar: CSV parse error — %s", exc)
            return None

        if not upcoming:
            logger.info("Earnings Calendar: no earnings in the next %d days", _LOOKAHEAD_DAYS)
            return Signal(
                source=SignalSource.EARNINGS_CALENDAR,
                value=0.0,
                metadata={"upcoming": [], "count": 0, "lookahead_days": _LOOKAHEAD_DAYS},
                summary=f"Earnings Calendar: no reports in the next {_LOOKAHEAD_DAYS} days",
            )

        summary = (
            f"Earnings Calendar: {len(upcoming)} report(s) in next {_LOOKAHEAD_DAYS} days "
            f"(next: {upcoming[0]['symbol']} on {upcoming[0]['report_date']})"
        )

        return Signal(
            source=SignalSource.EARNINGS_CALENDAR,
            value=0.0,
            metadata={
                "upcoming": upcoming,
                "count": len(upcoming),
                "lookahead_days": _LOOKAHEAD_DAYS,
            },
            summary=summary,
        )
