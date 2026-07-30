"""Tests for UnusualVolumeFetcher: event-loop-blocking fix + result caching."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture
def temp_db(monkeypatch):
    from src.config import settings

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(settings, "db_path", path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_get_unusual_volume_cache_returns_none_when_empty(temp_db):
    from src.db import get_unusual_volume_cache

    assert get_unusual_volume_cache() is None


def test_set_then_get_unusual_volume_cache_roundtrip(temp_db):
    from src.db import get_unusual_volume_cache, set_unusual_volume_cache

    set_unusual_volume_cache({"spikes": [{"symbol": "SOFI"}]})
    cached = get_unusual_volume_cache(max_age_hours=1.0)

    assert cached is not None
    assert cached["spikes"] == [{"symbol": "SOFI"}]


def test_get_unusual_volume_cache_expires(temp_db):
    from src.db import get_unusual_volume_cache, set_unusual_volume_cache

    set_unusual_volume_cache({"spikes": []})
    # max_age_hours=0 means "must be from the future" — always stale immediately.
    assert get_unusual_volume_cache(max_age_hours=0.0) is None


def _make_history_df(n_days: int = 25) -> pd.DataFrame:
    dates = pd.bdate_range(end="2024-06-28", periods=n_days)
    return pd.DataFrame(
        {
            "Open": [100.0] * n_days,
            "High": [101.0] * n_days,
            "Low": [99.0] * n_days,
            "Close": [100.0] * n_days,
            "Volume": [1_000_000] * (n_days - 1) + [5_000_000],  # spike on the last bar
        },
        index=dates,
    )


@pytest.mark.asyncio
async def test_unusual_volume_fetch_wraps_history_call_in_to_thread(temp_db, monkeypatch):
    from src.fetchers import unusual_volume

    monkeypatch.setattr(unusual_volume, "get_stock_watchlist", lambda: ["SOFI"])
    monkeypatch.setattr(unusual_volume, "get_unusual_volume_cache", lambda **k: None)
    monkeypatch.setattr(unusual_volume, "set_unusual_volume_cache", lambda data: None)

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = _make_history_df()

    async def _run_sync(fn, *a, **k):
        # asyncio.to_thread is itself a coroutine function, so patching it
        # produces an AsyncMock; the side_effect must be a coroutine function
        # too (a plain lambda's return value is not auto-awaited).
        return fn(*a, **k)

    with patch("src.fetchers.unusual_volume.yf.Ticker", return_value=fake_ticker):
        with patch("src.fetchers.unusual_volume.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = _run_sync
            signal = await unusual_volume.UnusualVolumeFetcher().fetch()

    assert mock_to_thread.called
    assert signal is not None


@pytest.mark.asyncio
async def test_unusual_volume_fetch_uses_cache_on_second_call(temp_db, monkeypatch):
    from src.fetchers import unusual_volume

    monkeypatch.setattr(unusual_volume, "get_stock_watchlist", lambda: ["SOFI"])

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = _make_history_df()

    call_count = 0

    def counting_history(*a, **k):
        nonlocal call_count
        call_count += 1
        return _make_history_df()

    fake_ticker.history.side_effect = counting_history

    with patch("src.fetchers.unusual_volume.yf.Ticker", return_value=fake_ticker):
        await unusual_volume.UnusualVolumeFetcher().fetch()
        await unusual_volume.UnusualVolumeFetcher().fetch()

    assert call_count == 1  # second call served from cache, no new yfinance hit
