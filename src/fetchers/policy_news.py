"""Policy and government news fetcher via public RSS feeds.

Pulls market-moving government/policy headlines (tariffs, trade, Fed, regulation,
executive actions). Signal value is always 0.0 — informational context for LLM
narrative only, does not affect the composite score.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET

from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client

logger = logging.getLogger(__name__)

# RSS feeds focused on market-moving government and policy news
_RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://www.cnbc.com/id/10000113/device/rss/rss.html",
    "https://www.politico.com/rss/economy.xml",
]

# Case-insensitive substrings that flag an article as policy-relevant
_POLICY_KEYWORDS = {
    "tariff", "tariffs",
    "trade war", "trade deal", "trade policy", "trade talks",
    "federal reserve", "fed rate", "rate cut", "rate hike", "fomc",
    "treasury", "debt ceiling", "budget deficit",
    "executive order", "executive action",
    "sanction", "sanctions", "embargo",
    "regulation", "deregulation",
    "spending bill", "tax bill", "tax cut",
    "inflation", " cpi", " pce", " gdp",
    "recession",
    "antitrust", "sec ruling",
    "import duty", "export ban",
}

_MAX_ARTICLES = 8
_REQUEST_TIMEOUT = 15


def _is_policy_relevant(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in _POLICY_KEYWORDS)


def _parse_rss(xml_text: str) -> list[dict]:
    """Parse RSS XML and return list of {title, summary, published} dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items = []
    for item in root.iter("item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        date_el = item.find("pubDate")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        # Strip HTML tags crudely
        import re
        desc = re.sub(r"<[^>]+>", "", desc)[:200]
        pub = date_el.text.strip() if date_el is not None and date_el.text else ""
        items.append({"title": title, "summary": desc, "published": pub})
    return items


class PolicyNewsFetcher(BaseFetcher):
    """Fetch market-moving government and policy headlines from RSS feeds."""

    @property
    def name(self) -> str:
        return "Policy & Government News"

    async def fetch(self) -> Signal | None:
        client = await get_http_client()
        all_items: list[dict] = []

        async def _fetch_feed(url: str) -> list[dict]:
            try:
                resp = await client.get(url, timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                return _parse_rss(resp.text)
            except Exception as exc:
                logger.debug("Policy news: feed %s failed — %s", url, exc)
                return []

        results = await asyncio.gather(*[_fetch_feed(url) for url in _RSS_FEEDS])
        for items in results:
            all_items.extend(items)

        # Deduplicate by title prefix (first 60 chars)
        seen: set[str] = set()
        unique: list[dict] = []
        for item in all_items:
            key = item["title"][:60].lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(item)

        # Filter to policy-relevant articles
        relevant = [
            item for item in unique
            if _is_policy_relevant(item["title"] + " " + item["summary"])
        ][:_MAX_ARTICLES]

        if not relevant:
            logger.info("Policy news: no policy-relevant articles found across feeds")
            return None

        count = len(relevant)
        summary = f"Policy News: {count} market-relevant headline(s) (tariffs/trade/Fed/regulation)"

        return Signal(
            source=SignalSource.POLICY_NEWS,
            value=0.0,
            metadata={"headlines": relevant, "count": len(relevant)},
            summary=summary,
        )
