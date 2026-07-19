"""Scrapes mLabs Trading's weekly recap posts (blog.mlabstrading.com) for
his actual CSP trades — the structured ground truth behind is_prime
labeling, replacing the previous manual Reddit-transcription process.

Only the structured HTML trades-table format is supported (posts from
results_boring_puts_2026_01_05 onward). Earlier posts link to a PDF trade
log instead and have no trades-table element — fetch_recap_trades()
returns an empty list for those, which is a valid, expected outcome, not
an error. See
docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

import logging
import re

import httpx
import lxml.html

logger = logging.getLogger(__name__)

_BASE_URL = "https://blog.mlabstrading.com"
_SLUG_RE = re.compile(r"/posts/(results_boring_puts_\d{4}_\d{2}_\d{2})")


def fetch_post_index() -> list[str]:
    """Return every results_boring_puts_* slug found on the posts index,
    sorted ascending (oldest first)."""
    response = httpx.get(f"{_BASE_URL}/posts", timeout=30.0)
    response.raise_for_status()
    tree = lxml.html.fromstring(response.text)
    hrefs = tree.xpath('//a[contains(@href, "/posts/results_boring_puts_")]/@href')
    slugs = {m.group(1) for href in hrefs if (m := _SLUG_RE.search(href))}
    return sorted(slugs)


def _resolve_open_date(slug: str, month_day: str) -> str:
    """Combine a recap slug's year with a bare 'M/D' open-date cell,
    handling the December-to-January week rollover."""
    slug_year = int(slug.split("_")[-3])
    slug_month = int(slug.split("_")[-2])
    month_str, day_str = month_day.split("/")
    month, day = int(month_str), int(day_str)
    year = slug_year + 1 if month < slug_month - 1 else slug_year
    return f"{year:04d}-{month:02d}-{day:02d}"


def fetch_recap_trades(slug: str) -> list[dict]:
    """Fetch one recap post and return its CSP opening trades as
    [{"ticker": str, "open_date": "YYYY-MM-DD"}, ...].

    Returns an empty list (not an error) when the post has no
    trades-table element (PDF-era posts before 2026-01-05).
    """
    response = httpx.get(f"{_BASE_URL}/posts/{slug}", timeout=30.0)
    response.raise_for_status()
    tree = lxml.html.fromstring(response.text)

    tables = tree.xpath('//table[@class="trades-table"]')
    if not tables:
        return []

    trades = []
    for row in tables[0].xpath(".//tbody/tr"):
        cells = [td.text_content().strip() for td in row.xpath("./td")]
        if len(cells) < 5:
            continue
        trade_type, open_cell, ticker = cells[0], cells[1], cells[4]
        if trade_type != "CSP":
            continue
        trades.append({"ticker": ticker, "open_date": _resolve_open_date(slug, open_cell)})

    return trades
