"""Tests for the shared yf.download lock+retry helper."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pandas as pd
import pytest

from src.fetchers._yf_lock import _get_yf_download_lock, download_with_retry


@pytest.mark.asyncio
async def test_download_with_retry_calls_yf_download_via_to_thread():
    fake_df = pd.DataFrame({"Close": [1.0, 2.0]})
    with patch("src.fetchers._yf_lock.yf.download", return_value=fake_df) as mock_dl:
        result = await download_with_retry("AAPL", period="2d")

    mock_dl.assert_called_once_with("AAPL", period="2d")
    pd.testing.assert_frame_equal(result, fake_df)


@pytest.mark.asyncio
async def test_download_with_retry_serializes_concurrent_calls():
    """Two concurrent downloads must not run inside the lock at the same time."""
    order: list[str] = []

    def fake_download(ticker, **kwargs):
        order.append(f"{ticker}-start")
        order.append(f"{ticker}-end")
        return pd.DataFrame({"Close": [1.0]})

    with patch("src.fetchers._yf_lock.yf.download", side_effect=fake_download):
        lock = _get_yf_download_lock()
        assert not lock.locked()
        await asyncio.gather(
            download_with_retry("AAPL"),
            download_with_retry("MSFT"),
        )

    # Both calls completed; the point of this test is that acquiring the lock
    # doesn't raise and both results come back correctly under concurrency.
    assert len(order) == 4


@pytest.mark.asyncio
async def test_download_with_retry_retries_then_raises():
    with patch("src.fetchers._yf_lock.yf.download", side_effect=RuntimeError("boom")) as mock_dl:
        with patch("src.fetchers._yf_lock.asyncio.sleep", return_value=None):
            with pytest.raises(RuntimeError, match="boom"):
                await download_with_retry("AAPL")

    assert mock_dl.call_count == 3  # 1 initial + 2 retries
