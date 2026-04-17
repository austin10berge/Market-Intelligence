"""Helper functions for FRED API requests."""

from __future__ import annotations

import httpx

from ..config import settings


async def fetch_fred_series(client: httpx.AsyncClient, series_id: str) -> float | None:
    """Fetch the latest observation for a FRED series."""
    if not settings.fred_api_key:
        raise ValueError("FRED_API_KEY is not configured")

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": settings.fred_api_key,
        "file_type": "json",
        "limit": 1,
        "sort_order": "desc",
    }
    
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    
    data = resp.json()
    observations = data.get("observations", [])
    if not observations:
        return None
        
    # Some older FRED series return "." for missing values
    val_str = observations[0].get("value", "")
    if val_str == ".":
        return None
        
    return float(val_str)
