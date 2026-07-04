"""Unit tests for src.chat — ticker detection, formatting, prompt building."""
from __future__ import annotations

from src.chat import (
    TICKER_SKIP_WORDS,
    build_prompt,
    detect_tickers,
    format_screener_block,
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
