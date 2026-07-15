"""Unit tests for src.chat — ticker detection, formatting, prompt building."""
from __future__ import annotations

import asyncio
from datetime import date

import src.chat as chat_module
from src.chat import (
    TICKER_SKIP_WORDS,
    build_prompt,
    build_thread_title,
    detect_tickers,
    format_options_block,
    format_screener_block,
    gather_chat_blocks,
)

UNIVERSE = {"NVDA", "AAPL", "MSFT", "GOOG", "TSM", "QCOM", "SMCI"}


class TestDetectTickers:
    def test_detects_explicit_dollar_sign_tickers(self):
        result = detect_tickers("What do you think about $NVDA here?", UNIVERSE)
        assert result == ["NVDA"]

    def test_detects_bare_uppercase_tickers_in_universe(self):
        result = detect_tickers("AAPL looks interesting today", UNIVERSE)
        assert result == ["AAPL"]

    def test_skips_bare_words_not_in_universe(self):
        result = detect_tickers("TSLA looks great", UNIVERSE)
        assert result == []

    def test_explicit_dollar_sign_bypasses_universe_check(self):
        result = detect_tickers("$TSLA is moving", set())
        assert result == ["TSLA"]

    def test_skips_known_non_ticker_words(self):
        assert "RSI" in TICKER_SKIP_WORDS
        result = detect_tickers("RSI is at 60 and IV is high", UNIVERSE)
        assert result == []

    def test_deduplicates_tickers(self):
        result = detect_tickers("$NVDA and NVDA again", UNIVERSE)
        assert result == ["NVDA"]

    def test_preserves_order_of_first_mention(self):
        result = detect_tickers("$AAPL then $MSFT then $NVDA", UNIVERSE)
        assert result == ["AAPL", "MSFT", "NVDA"]

    def test_dollar_tickers_take_priority_over_bare(self):
        result = detect_tickers("$NVDA and AAPL", UNIVERSE)
        assert "NVDA" in result
        assert "AAPL" in result
        assert result.index("NVDA") < result.index("AAPL")


class TestBuildThreadTitle:
    def test_ticker_and_topic_words(self):
        result = build_thread_title(
            "What do you think about $AAPL here, is this an earnings pullback?", UNIVERSE
        )
        assert result == "AAPL: Here Earnings Pullback"

    def test_multiple_tickers_joined(self):
        result = build_thread_title("$AAPL and $MSFT both breaking out today", UNIVERSE)
        assert result == "AAPL, MSFT: Both Breaking Out"

    def test_caps_at_three_tickers(self):
        result = build_thread_title(
            "$AAPL $MSFT $NVDA $GOOG all ripping", UNIVERSE
        )
        assert result == "AAPL, MSFT, NVDA: All Ripping"

    def test_ticker_only_no_topic_words(self):
        result = build_thread_title("$AAPL", UNIVERSE)
        assert result == "AAPL"

    def test_topic_words_only_no_ticker(self):
        result = build_thread_title("thinking about a swing trade setup", UNIVERSE)
        assert result == "Thinking Swing Trade"

    def test_falls_back_to_date_when_nothing_survives(self):
        result = build_thread_title("what do you think about this", UNIVERSE)
        assert result.startswith("Trade Chat — ")

    def test_truncates_to_100_chars(self):
        long_content = "$AAPL " + "x" * 60 + " " + "y" * 60 + " " + "z" * 60
        result = build_thread_title(long_content, UNIVERSE)
        assert len(result) == 100


class TestFormatScreenerBlock:
    def _data(self, **overrides) -> dict:
        base = {
            "price": 138.42, "pct_1d": 0.8, "pct_1w": 2.1, "pct_1m": -4.3,
            "rsi": 56.2, "bb_width_pct": 13.1, "bb_upper": 142.10, "bb_lower": 134.90,
            "sma_200": 128.40, "sma_200_pct": 8.0, "ema_200": 130.10, "ema_200_pct": 6.4,
            "sma_50": 133.20, "pct_from_52wk_high": -8.4, "volume_ratio": 0.97,
            "adr20": 3.2, "atm_iv": 34.0, "iv_percentile": 42.0, "atm_iv_rv20": 1.18,
            "rv20": 28.8, "sector": "Technology", "market_cap": 3_380_000_000_000,
            "beta": 1.72, "pe": 48.2, "forward_pe": 29.1, "peg_ratio": 1.8,
            "eps_growth": 22.0, "revenue_growth": 12.0, "fcf": 60.8, "debt_to_equity": 0.42,
        }
        base.update(overrides)
        return base

    def test_includes_ticker_header(self):
        block = format_screener_block("NVDA", self._data())
        assert block.startswith("[NVDA")

    def test_includes_price(self):
        block = format_screener_block("NVDA", self._data())
        assert "138.42" in block

    def test_includes_rsi(self):
        block = format_screener_block("NVDA", self._data())
        assert "RSI: 56.2" in block

    def test_omits_na_fields(self):
        block = format_screener_block("NVDA", self._data(rsi="N/A", atm_iv="N/A"))
        assert "RSI" not in block
        assert "IV (ATM)" not in block

    def test_formats_market_cap_in_trillions(self):
        block = format_screener_block("NVDA", self._data(market_cap=3_380_000_000_000))
        assert "3.38T" in block

    def test_formats_market_cap_in_billions(self):
        block = format_screener_block("AAPL", self._data(market_cap=50_000_000_000))
        assert "50.0B" in block


class TestBuildPrompt:
    def test_includes_system_prompt(self):
        result = build_prompt("You are a trading partner.", [], "What about NVDA?", [])
        assert "You are a trading partner." in result

    def test_includes_current_user_message(self):
        result = build_prompt("sys", [], "What about NVDA?", [])
        assert "What about NVDA?" in result

    def test_includes_conversation_history(self):
        history = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ]
        result = build_prompt("sys", history, "New question", [])
        assert "Previous question" in result
        assert "Previous answer" in result

    def test_history_appears_before_current_message(self):
        history = [{"role": "user", "content": "Earlier"}]
        result = build_prompt("sys", history, "Now", [])
        assert result.index("Earlier") < result.index("Now")

    def test_screener_blocks_appended_to_current_message(self):
        blocks = ["[NVDA — live data]\nPrice: $138"]
        result = build_prompt("sys", [], "What about NVDA?", blocks)
        assert "Price: $138" in result

    def test_empty_screener_blocks_not_added(self):
        result = build_prompt("sys", [], "Hello?", [])
        assert "live data" not in result


class TestFormatOptionsBlock:
    def _row(self, **overrides) -> dict:
        base = {
            "expiration": date(2026, 7, 17),
            "dte": 11,
            "strike": 17.0,
            "bid": 0.19,
            "ask": 0.21,
            "mid": 0.20,
            "spread_pct": 10.0,
            "iv": 61.0,
            "delta": -0.18,
            "volume": 120,
        }
        base.update(overrides)
        return base

    def test_no_rows_returns_unavailable_message(self):
        assert format_options_block("SOFI", "put", []) == "[SOFI: no options data available]"

    def test_includes_header_with_puts_label(self):
        block = format_options_block("SOFI", "put", [self._row()])
        assert block.startswith("[SOFI — live puts chain")

    def test_includes_calls_label_and_suffix(self):
        block = format_options_block("SOFI", "call", [self._row(strike=19.0)])
        assert "19C" in block
        assert "live calls chain" in block

    def test_includes_expiration_and_dte(self):
        block = format_options_block("SOFI", "put", [self._row()])
        assert "Exp 7/17 (11 DTE)" in block

    def test_includes_bid_ask_mid(self):
        block = format_options_block("SOFI", "put", [self._row()])
        assert "0.19/0.21 (mid 0.20)" in block

    def test_includes_iv_and_delta(self):
        block = format_options_block("SOFI", "put", [self._row()])
        assert "IV 61%" in block
        assert "Δ-0.18" in block

    def test_omits_iv_and_delta_when_missing(self):
        block = format_options_block("SOFI", "put", [self._row(iv=None, delta=None)])
        assert "IV" not in block
        assert "Δ" not in block

    def test_wide_spread_marker_above_threshold(self):
        block = format_options_block("SOFI", "put", [self._row(spread_pct=25.0)])
        assert "(wide spread)" in block

    def test_no_wide_spread_marker_below_threshold(self):
        block = format_options_block("SOFI", "put", [self._row(spread_pct=5.0)])
        assert "(wide spread)" not in block

    def test_groups_multiple_expirations_on_separate_lines(self):
        rows = [self._row(), self._row(expiration=date(2026, 7, 24), dte=18, strike=17.5)]
        block = format_options_block("SOFI", "put", rows)
        exp_lines = [line for line in block.split("\n") if line.startswith("Exp")]
        assert len(exp_lines) == 2

    def test_multiple_strikes_same_expiration_joined_with_pipe(self):
        rows = [self._row(), self._row(strike=17.5, bid=0.24, ask=0.26, mid=0.25)]
        block = format_options_block("SOFI", "put", rows)
        assert " | " in block


class TestGatherChatBlocks:
    async def test_no_tickers_returns_empty_list(self):
        result = await gather_chat_blocks([], "hello")
        assert result == []

    async def test_screener_only_when_no_options_intent(self, monkeypatch):
        monkeypatch.setattr(
            chat_module, "screen_stocks", lambda tickers, persist: [{"price": 17.8}]
        )
        called = {"count": 0}

        def _track(*args, **kwargs):
            called["count"] += 1
            return []

        monkeypatch.setattr(chat_module, "fetch_options_grid", _track)
        result = await gather_chat_blocks(["SOFI"], "what do you think about SOFI")
        assert len(result) == 1
        assert result[0].startswith("[SOFI")
        assert called["count"] == 0

    async def test_options_block_appended_when_intent_detected(self, monkeypatch):
        monkeypatch.setattr(
            chat_module, "screen_stocks", lambda tickers, persist: [{"price": 17.8}]
        )
        monkeypatch.setattr(
            chat_module,
            "fetch_options_grid",
            lambda ticker, price, option_type: [
                {
                    "expiration": date(2026, 7, 17),
                    "dte": 11,
                    "strike": 17.0,
                    "bid": 0.19,
                    "ask": 0.21,
                    "mid": 0.20,
                    "spread_pct": 10.0,
                    "iv": 61.0,
                    "delta": -0.18,
                    "volume": 120,
                }
            ],
        )
        result = await gather_chat_blocks(
            ["SOFI"], "which strike should I sell a SOFI CSP at"
        )
        assert len(result) == 2
        assert "live puts chain" in result[1]

    async def test_screener_failure_falls_back_to_unavailable_marker(self, monkeypatch):
        def _raise(tickers, persist):
            raise RuntimeError("boom")

        monkeypatch.setattr(chat_module, "screen_stocks", _raise)
        result = await gather_chat_blocks(["SOFI"], "SOFI CSP")
        assert result == ["[SOFI: data unavailable]"]

    async def test_no_options_fetch_when_price_missing(self, monkeypatch):
        monkeypatch.setattr(
            chat_module, "screen_stocks", lambda tickers, persist: [{"price": "N/A"}]
        )
        called = {"count": 0}

        def _track(*args, **kwargs):
            called["count"] += 1
            return []

        monkeypatch.setattr(chat_module, "fetch_options_grid", _track)
        await gather_chat_blocks(["SOFI"], "SOFI CSP strike")
        assert called["count"] == 0


class TestCallClaudeChatMcpWiring:
    async def test_invokes_claude_with_mcp_config_and_alpaca_tools(self, monkeypatch):
        captured = {}

        class _FakeProcess:
            returncode = 0

            async def communicate(self, input=None):
                return b"ok", b""

        async def _fake_create_subprocess_exec(*args, **kwargs):
            captured["args"] = args
            return _FakeProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

        result = await chat_module.call_claude_chat("some prompt")

        assert result == "ok"
        argv = captured["args"]
        assert argv[0] == "claude"
        assert "-p" in argv
        assert "--strict-mcp-config" in argv

        mcp_config_idx = argv.index("--mcp-config")
        assert argv[mcp_config_idx + 1].replace("\\", "/").endswith("discord_bot/alpaca-mcp.json")
        assert argv[mcp_config_idx + 2].replace("\\", "/").endswith("discord_bot/schwab-mcp.json")

        allowed_idx = argv.index("--allowedTools")
        allowed = argv[allowed_idx + 1:]
        assert "WebSearch" in allowed
        assert "mcp__alpaca__get_option_chain" in allowed
        assert "mcp__alpaca__get_option_snapshot" in allowed
        assert "mcp__alpaca__get_stock_snapshot" in allowed
        assert "mcp__alpaca__get_option_contracts" in allowed
        assert "mcp__schwab__get_accounts" in allowed
        assert "mcp__schwab__get_orders" in allowed
        assert "mcp__schwab__get_quotes" in allowed
        assert not any(t.startswith("mcp__schwab__preview_") for t in allowed)
        assert len(allowed) == 41  # WebSearch + 24 Alpaca tools + 16 Schwab tools
