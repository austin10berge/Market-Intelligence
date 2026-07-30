"""Tests for PutCallFetcher's SPY fallback event-loop-blocking fix."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.fetchers.put_call import PutCallFetcher


@pytest.mark.asyncio
async def test_fetch_fallback_wraps_yfinance_calls_in_to_thread():
    fetcher = PutCallFetcher()

    fake_chain = MagicMock()
    fake_chain.puts = {"volume": MagicMock(dropna=lambda: MagicMock(sum=lambda: 100))}
    fake_chain.calls = {"volume": MagicMock(dropna=lambda: MagicMock(sum=lambda: 50))}

    fake_spy = MagicMock()
    fake_spy.options = ["2024-07-19"]
    fake_spy.option_chain.return_value = fake_chain

    async def _run_sync(fn, *a, **k):
        # asyncio.to_thread is itself a coroutine function, so patching it
        # produces an AsyncMock; the side_effect must be a coroutine function
        # too (a plain lambda's return value is not auto-awaited).
        return fn(*a, **k)

    with patch("yfinance.Ticker", return_value=fake_spy):
        with patch("src.fetchers.put_call.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = _run_sync
            await fetcher._fetch_fallback()

    assert mock_to_thread.called
