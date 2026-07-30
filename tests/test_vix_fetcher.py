"""Tests for VixFetcher event-loop-blocking fix."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.fetchers.vix import VixFetcher


@pytest.mark.asyncio
async def test_vix_fetch_does_not_block_event_loop():
    """fast_info access must go through asyncio.to_thread, not run inline."""
    spot_mock = MagicMock()
    spot_mock.fast_info.last_price = 15.0
    vix3m_mock = MagicMock()
    vix3m_mock.fast_info.last_price = 16.0

    async def _run_sync(fn, *a, **k):
        # asyncio.to_thread is itself a coroutine function, so patching it
        # produces an AsyncMock; the side_effect must be a coroutine function
        # too (a plain lambda's return value is not auto-awaited).
        return fn(*a, **k)

    with patch("src.fetchers.vix.yf.Ticker", side_effect=[spot_mock, vix3m_mock]):
        with patch("src.fetchers.vix.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = _run_sync
            signal = await VixFetcher().fetch()

    assert mock_to_thread.called
    assert signal is not None
    assert signal.metadata["spot"] == 15.0
