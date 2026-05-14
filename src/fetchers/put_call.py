"""CBOE equity put/call ratio fetcher."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

import httpx

from .. import db
from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client

logger = logging.getLogger(__name__)

# CBOE publishes daily equity put/call ratio as a downloadable CSV
# Format: https://cdn.cboe.com/data/us/options/market_statistics/daily/options_YYYY-MM-DD.csv
CBOE_CSV_BASE = "https://cdn.cboe.com/data/us/options/market_statistics/daily/options_{date}.csv"
CBOE_PC_URL = "https://www.cboe.com/us/options/market_statistics/daily/"

# Equity P/C ratios outside this range are almost certainly parse errors
_PC_MIN, _PC_MAX = 0.3, 2.5


def _validate(ratio: float) -> float | None:
    if _PC_MIN <= ratio <= _PC_MAX:
        return ratio
    logger.warning("Put/Call: parsed value %.3f is outside valid range [%.1f, %.1f] — discarding", ratio, _PC_MIN, _PC_MAX)
    return None


class PutCallFetcher(BaseFetcher):
    """Fetch CBOE equity put/call ratio with rolling average context."""

    @property
    def name(self) -> str:
        return "Put/Call Ratio"

    async def fetch(self) -> Signal | None:
        client = await get_http_client()

        ratio = await self._fetch_from_cboe_csv(client)

        if ratio is None:
            ratio = await self._fetch_from_cboe_html(client)

        if ratio is None:
            ratio = await self._fetch_fallback()

        if ratio is None:
            logger.warning("Put/Call: unable to retrieve ratio from any source")
            return None

        ratio = round(ratio, 3)

        rolling_avg = db.get_rolling_average(SignalSource.PUT_CALL.value, days=5)
        rolling_str = f" | 5d avg: {rolling_avg:.3f}" if rolling_avg else ""

        if ratio > 1.2:
            extreme_note = " (elevated — near contrarian buy zone)"
        elif ratio < 0.7:
            extreme_note = " (low — excessive complacency)"
        else:
            extreme_note = ""

        return Signal(
            source=SignalSource.PUT_CALL,
            value=ratio,
            metadata={
                "rolling_avg_5d": rolling_avg,
                "extreme_high": ratio > 1.2,
                "extreme_low": ratio < 0.7,
            },
            summary=f"Put/Call: {ratio}{extreme_note}{rolling_str}",
        )

    async def _fetch_from_cboe_csv(self, client: httpx.AsyncClient) -> float | None:
        """Fetch equity P/C ratio from CBOE's daily CSV data file.

        CBOE posts today's file after market close. We try today then fall back
        one trading day in case the file isn't posted yet.
        """
        for days_back in range(5):
            target = date.today() - timedelta(days=days_back)
            # Skip weekends
            if target.weekday() >= 5:
                continue
            url = CBOE_CSV_BASE.format(date=target.isoformat())
            try:
                resp = await client.get(url)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                ratio = self._parse_cboe_csv(resp.text)
                if ratio is not None:
                    logger.debug("Put/Call: CSV source returned %.3f for %s", ratio, target)
                    return ratio
            except Exception:
                logger.debug("Put/Call: CSV fetch failed for %s", target, exc_info=True)
        return None

    def _parse_cboe_csv(self, text: str) -> float | None:
        """Parse CBOE daily options CSV and return the equity P/C ratio."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) < 2:
            return None

        header = [h.strip().lower() for h in lines[0].split(",")]
        # Locate the equity put/call ratio column
        col_candidates = ["equity p/c ratio", "equity p/c", "equity put/call ratio"]
        col_idx = None
        for candidate in col_candidates:
            if candidate in header:
                col_idx = header.index(candidate)
                break

        if col_idx is None:
            logger.debug("Put/Call: could not find equity P/C column in CSV header: %s", header)
            return None

        # Use the last data row (most recent date)
        last_row = [v.strip() for v in lines[-1].split(",")]
        if col_idx >= len(last_row):
            return None
        try:
            return _validate(float(last_row[col_idx]))
        except ValueError:
            return None

    async def _fetch_from_cboe_html(self, client: httpx.AsyncClient) -> float | None:
        """Fallback: parse CBOE market statistics HTML page.

        The CBOE site is JS-rendered so this may not work, but it's worth trying
        before going to yfinance. Values outside a sane range are rejected.
        """
        try:
            resp = await client.get(CBOE_PC_URL)
            resp.raise_for_status()
            text = resp.text

            patterns = [
                r"equity.*?put.*?call.*?ratio.*?([\d]+\.[\d]+)",
                r"total.*?put/call.*?([\d]+\.[\d]+)",
                r"put/call.*?ratio.*?([\d]+\.[\d]+)",
            ]

            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    try:
                        ratio = _validate(float(match.group(1)))
                        if ratio is not None:
                            return ratio
                    except ValueError:
                        pass

            logger.debug("Put/Call: could not parse CBOE HTML page")
            return None
        except Exception:
            logger.debug("Put/Call: CBOE HTML fetch failed", exc_info=True)
            return None

    async def _fetch_fallback(self) -> float | None:
        """Derive put/call ratio from SPY options volume across near-term expiries."""
        try:
            import yfinance as yf

            spy = yf.Ticker("SPY")
            if not spy.options:
                return None

            # Aggregate across the first few expiries for a more representative ratio
            total_puts = 0.0
            total_calls = 0.0
            for expiry in spy.options[:3]:
                try:
                    chain = spy.option_chain(expiry)
                    puts_vol = chain.puts["volume"].dropna().sum()
                    calls_vol = chain.calls["volume"].dropna().sum()
                    total_puts += float(puts_vol)
                    total_calls += float(calls_vol)
                except Exception:
                    continue

            if total_calls > 0:
                ratio = _validate(total_puts / total_calls)
                if ratio is not None:
                    logger.info("Put/Call: using SPY options fallback — %.3f", ratio)
                    return ratio
            return None
        except Exception:
            logger.debug("Put/Call: fallback also failed", exc_info=True)
            return None
