"""Tests for trade chat DB tables and accessor functions."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    with patch("src.db.settings") as mock_settings:
        mock_settings.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


from src.db import (
    get_trade_chat_channel_id,
    set_trade_chat_channel_id,
    save_trade_chat_message,
    get_trade_chat_history,
    is_trade_chat_thread,
)


def test_channel_id_is_none_when_not_set():
    assert get_trade_chat_channel_id() is None


def test_set_and_get_channel_id():
    set_trade_chat_channel_id("123456789")
    assert get_trade_chat_channel_id() == "123456789"


def test_set_channel_id_overwrites_previous():
    set_trade_chat_channel_id("111")
    set_trade_chat_channel_id("222")
    assert get_trade_chat_channel_id() == "222"


def test_save_and_retrieve_history():
    save_trade_chat_message("thread_abc", "user", "What do you think about NVDA?")
    save_trade_chat_message("thread_abc", "assistant", "Looks interesting here.")
    history = get_trade_chat_history("thread_abc")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "What do you think about NVDA?"
    assert history[1]["role"] == "assistant"


def test_history_is_empty_for_unknown_thread():
    assert get_trade_chat_history("nonexistent_thread") == []


def test_history_isolated_by_thread_id():
    save_trade_chat_message("thread_x", "user", "message in x")
    save_trade_chat_message("thread_y", "user", "message in y")
    assert len(get_trade_chat_history("thread_x")) == 1
    assert len(get_trade_chat_history("thread_y")) == 1


def test_is_trade_chat_thread_returns_false_for_new_thread():
    assert is_trade_chat_thread("brand_new_thread") is False


def test_is_trade_chat_thread_returns_true_after_first_message():
    save_trade_chat_message("known_thread", "user", "hello")
    assert is_trade_chat_thread("known_thread") is True
