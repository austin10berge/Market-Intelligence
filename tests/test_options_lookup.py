"""Unit tests for src.screener.options_lookup."""
from __future__ import annotations

from src.screener.options_lookup import detect_options_intent


class TestDetectOptionsIntent:
    def test_detects_put_language(self):
        assert detect_options_intent("should I sell a CSP on SOFI") == "put"

    def test_detects_call_language(self):
        assert detect_options_intent("what about a SOFI covered call") == "call"

    def test_detects_call_shorthand(self):
        assert detect_options_intent("SOFI 7/17 21c") == "call"

    def test_detects_put_shorthand(self):
        assert detect_options_intent("SOFI 7/17 21p") == "put"

    def test_detects_date_shorthand_alone(self):
        assert detect_options_intent("SOFI 7/17 options") == "put"

    def test_generic_options_words_default_to_put(self):
        assert detect_options_intent("which strike should I sell a SOFI CSP at") == "put"

    def test_no_trigger_on_plain_ticker_mention(self):
        assert detect_options_intent("what do you think about SOFI") is None

    def test_no_trigger_on_bare_price_talk(self):
        assert detect_options_intent("SOFI is up 3% today") is None

    def test_call_word_and_put_shorthand_prefers_put(self):
        assert detect_options_intent("SOFI call or put, 21p works") == "put"

    def test_wheel_keyword_triggers_put(self):
        assert detect_options_intent("thinking about the wheel on SOFI") == "put"
