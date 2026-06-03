"""Alpha Vantage News & Sentiment fetcher.

Pulls the latest market-relevant headlines and their sentiment labels.
The signal value is always 0.0 — news informs the LLM narrative but does
not move the composite score on its own.
"""

from __future__ import annotations

import logging

from ..config import settings
from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client

logger = logging.getLogger(__name__)

_AV_BASE = "https://www.alphavantage.co/query"
_AV_TOPICS = "financial_markets,economy_monetary,economy_macro,technology,earnings"

# Topics considered relevant enough to keep an article
_RELEVANT_TOPICS = {
    "Financial Markets",
    "Economy - Monetary",
    "Economy - Macro",
    "Economy - Fiscal",
    "Technology",
    "Energy & Transportation",
    "Earnings",
}

_RELEVANCE_THRESHOLD = 0.5
_MAX_ARTICLES = 10
_SUMMARY_TRUNCATE = 150
_REQUEST_TIMEOUT = 30


class NewsFetcher(BaseFetcher):
    """Fetch recent market headlines and sentiment from Alpha Vantage."""

    @property
    def name(self) -> str:
        return "Market News"

    async def fetch(self) -> Signal | None:
        if not settings.alpha_vantage_api_key:
            logger.warning("Market News: ALPHA_VANTAGE_API_KEY not set — skipping")
            return None

        client = await get_http_client()

        try:
            resp = await client.get(
                _AV_BASE,
                params={
                    "function": "NEWS_SENTIMENT",
                    "apikey": settings.alpha_vantage_api_key,
                    "sort": "LATEST",
                    "limit": "20",
                    "topics": _AV_TOPICS,
                },
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

        except TimeoutError:
            logger.warning("Market News: request timed out after %ds", _REQUEST_TIMEOUT)
            return None
        except Exception as exc:
            logger.warning("Market News: HTTP/JSON error — %s", exc)
            return None

        feed = data.get("feed")
        if not isinstance(feed, list):
            logger.warning("Market News: unexpected response shape — 'feed' missing")
            return None

        headlines: list[dict] = []
        bullish = bearish = neutral = 0

        for item in feed:
            if len(headlines) >= _MAX_ARTICLES:
                break

            # Check relevance: keep if any topic meets the threshold
            topics_raw = item.get("topics", [])
            relevant_topics = [
                t["topic"]
                for t in topics_raw
                if t.get("topic") in _RELEVANT_TOPICS
                and _safe_float(t.get("relevance_score", "0")) >= _RELEVANCE_THRESHOLD
            ]

            if not relevant_topics:
                continue

            sentiment_label = item.get("overall_sentiment_label", "Neutral")
            label_lower = sentiment_label.lower()
            if "bullish" in label_lower:
                bullish += 1
            elif "bearish" in label_lower:
                bearish += 1
            else:
                neutral += 1

            headlines.append(
                {
                    "title": item["title"],
                    "summary": item.get("summary", "")[:_SUMMARY_TRUNCATE],
                    "sentiment": sentiment_label,
                    "topics": relevant_topics,
                }
            )

        count = len(headlines)
        summary = (
            f"Market News: {count} headlines "
            f"(sentiment: {bullish} bullish, {bearish} bearish, {neutral} neutral)"
        )

        return Signal(
            source=SignalSource.NEWS,
            value=0.0,
            metadata={
                "headlines": headlines,
                "count": count,
            },
            summary=summary,
        )


def _safe_float(value: object, default: float = 0.0) -> float:
    """Convert a value to float, returning *default* on failure."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
