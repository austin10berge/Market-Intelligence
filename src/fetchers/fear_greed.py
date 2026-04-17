"""CNN Fear & Greed Index fetcher."""

from __future__ import annotations

import logging

from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client

logger = logging.getLogger(__name__)

# Unofficial CNN endpoint — returns JSON with current + historical F&G data
FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


class FearGreedFetcher(BaseFetcher):
    """Fetch the CNN Fear & Greed Index (0–100 composite score)."""

    @property
    def name(self) -> str:
        return "Fear & Greed Index"

    async def fetch(self) -> Signal | None:
        client = await get_http_client()
        resp = await client.get(FEAR_GREED_URL)
        resp.raise_for_status()

        data = resp.json()

        # The response has a nested structure; extract the current score
        fg_data = data.get("fear_and_greed", {})
        score = fg_data.get("score")
        rating = fg_data.get("rating", "")
        previous_close = fg_data.get("previous_close", 0)

        if score is None:
            logger.warning("Fear & Greed: no score found in response")
            return None

        score = round(float(score), 1)
        prev = round(float(previous_close), 1) if previous_close else None

        # Determine direction arrow
        if prev is not None:
            change = score - prev
            arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
            change_str = f" {arrow} from {prev}"
        else:
            change_str = ""

        return Signal(
            source=SignalSource.FEAR_GREED,
            value=score,
            metadata={
                "rating": rating,
                "previous_close": prev,
                "raw_response_keys": list(fg_data.keys()),
            },
            summary=f"Fear & Greed: {score} ({rating}){change_str}",
        )
