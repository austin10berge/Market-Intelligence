"""Unit tests for src.screener.options_lookup."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx

from src.config import settings
from src.screener.options_lookup import MIN_DTE, detect_options_intent, fetch_options_grid


@pytest.fixture(autouse=True)
def _alpaca_creds(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_api_key", "test-key")
    monkeypatch.setattr(settings, "alpaca_api_secret", "test-secret")


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

    # False-positive fix tests: ensure word-boundary matching, not substring matching
    def test_no_match_put_in_computer(self):
        """'put' substring in 'computer' should not trigger."""
        assert detect_options_intent("the computer says buy") is None

    def test_no_match_cc_in_according(self):
        """'cc' substring in 'according' should not trigger."""
        assert detect_options_intent("according to the chart, buy") is None

    def test_no_match_cc_in_accept(self):
        """'cc' substring in 'accept' should not trigger."""
        assert detect_options_intent("I accept that risk") is None

    def test_no_match_cc_in_occurred(self):
        """'cc' substring in 'occurred' should not trigger."""
        assert detect_options_intent("it occurred to me") is None

    def test_no_match_put_in_dispute(self):
        """'put' substring in 'dispute' should not trigger."""
        assert detect_options_intent("I dispute that") is None

    def test_no_match_put_in_reputation(self):
        """'put' substring in 'reputation' should not trigger."""
        assert detect_options_intent("reputation matters") is None

    # Verify existing positive cases still work after the fix
    def test_still_match_csp_full_word(self):
        """'csp' as a full word should still trigger."""
        assert detect_options_intent("should I sell a CSP on SOFI") == "put"

    def test_still_match_covered_call_phrase(self):
        """'covered call' as a full phrase should still trigger."""
        assert detect_options_intent("what about a SOFI covered call") == "call"

    def test_still_match_wheel_full_word(self):
        """'wheel' as a full word should still trigger."""
        assert detect_options_intent("thinking about the wheel on SOFI") == "put"


class TestFetchOptionsGrid:
    @respx.mock
    def test_parses_put_contract_row(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SOFI260717P00017000": {
                            "latestQuote": {"bp": 0.19, "ap": 0.21},
                            "greeks": {"delta": -0.18},
                            "impliedVolatility": 0.61,
                            "dailyBar": {"v": 120},
                        }
                    }
                },
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "put")
        assert len(rows) == 1
        row = rows[0]
        assert row["strike"] == 17.0
        assert row["bid"] == 0.19
        assert row["ask"] == 0.21
        assert row["mid"] == 0.20
        assert row["delta"] == -0.18
        assert row["iv"] == 61.0
        assert row["volume"] == 120
        assert row["expiration"] == date(2026, 7, 17)

    @respx.mock
    def test_filters_out_wrong_option_type(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SOFI260717C00019000": {
                            "latestQuote": {"bp": 0.30, "ap": 0.35},
                        }
                    }
                },
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "put")
        assert rows == []

    @respx.mock
    def test_drops_contracts_with_no_live_quote(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={"snapshots": {"SOFI260717P00017000": {"dailyBar": {"v": 5}}}},
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "put")
        assert rows == []

    @respx.mock
    def test_paginates_through_next_page_token(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "snapshots": {
                            "SOFI260717P00017000": {
                                "latestQuote": {"bp": 0.19, "ap": 0.21},
                            }
                        },
                        "next_page_token": "abc123",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "snapshots": {
                            "SOFI260724P00017000": {
                                "latestQuote": {"bp": 0.29, "ap": 0.31},
                            }
                        }
                    },
                ),
            ]
        )
        rows = fetch_options_grid("SOFI", 17.8, "put")
        assert len(rows) == 2
        assert rows[0]["expiration"] < rows[1]["expiration"]

    @respx.mock
    def test_network_error_returns_empty_list(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            side_effect=httpx.ConnectError("boom")
        )
        assert fetch_options_grid("SOFI", 17.8, "put") == []

    def test_missing_credentials_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(settings, "alpaca_api_key", "")
        assert fetch_options_grid("SOFI", 17.8, "put") == []

    def test_non_positive_price_returns_empty_list(self):
        assert fetch_options_grid("SOFI", 0.0, "put") == []


class TestExpirationWindow:
    @respx.mock
    def test_expiration_gte_uses_min_dte_floor(self):
        route = respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(200, json={"snapshots": {}})
        )
        fetch_options_grid("SOFI", 17.8, "put")

        request = route.calls[0].request
        params = dict(httpx.QueryParams(request.url.query))
        expected_gte = (date.today() + timedelta(days=MIN_DTE)).isoformat()
        assert params["expiration_date_gte"] == expected_gte
        assert MIN_DTE == 4


class TestDeltaFloorTrimming:
    @respx.mock
    def test_trims_calls_beyond_delta_floor_but_keeps_boundary_strike(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SOFI260717C00018000": {
                            "latestQuote": {"bp": 1.50, "ap": 1.55},
                            "greeks": {"delta": 0.45},
                        },
                        "SOFI260717C00020000": {
                            "latestQuote": {"bp": 0.60, "ap": 0.65},
                            "greeks": {"delta": 0.20},
                        },
                        "SOFI260717C00022000": {
                            "latestQuote": {"bp": 0.10, "ap": 0.15},
                            "greeks": {"delta": 0.08},
                        },
                    }
                },
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "call")
        assert [r["strike"] for r in rows] == [18.0, 20.0]

    @respx.mock
    def test_trims_puts_beyond_delta_floor_but_keeps_boundary_strike(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SOFI260717P00019000": {
                            "latestQuote": {"bp": 1.20, "ap": 1.25},
                            "greeks": {"delta": -0.45},
                        },
                        "SOFI260717P00017000": {
                            "latestQuote": {"bp": 0.55, "ap": 0.60},
                            "greeks": {"delta": -0.20},
                        },
                        "SOFI260717P00015000": {
                            "latestQuote": {"bp": 0.08, "ap": 0.12},
                            "greeks": {"delta": -0.05},
                        },
                    }
                },
            )
        )
        rows = fetch_options_grid("SOFI", 20.0, "put")
        assert [r["strike"] for r in rows] == [17.0, 19.0]

    @respx.mock
    def test_keeps_all_strikes_when_delta_never_crosses_floor(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SOFI260717C00018000": {
                            "latestQuote": {"bp": 1.50, "ap": 1.55},
                            "greeks": {"delta": 0.45},
                        },
                        "SOFI260717C00020000": {
                            "latestQuote": {"bp": 0.60, "ap": 0.65},
                            "greeks": {"delta": 0.30},
                        },
                    }
                },
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "call")
        assert [r["strike"] for r in rows] == [18.0, 20.0]

    @respx.mock
    def test_missing_delta_does_not_trigger_early_trim(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots/SOFI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "SOFI260717C00018000": {
                            "latestQuote": {"bp": 1.50, "ap": 1.55},
                        },
                        "SOFI260717C00020000": {
                            "latestQuote": {"bp": 0.60, "ap": 0.65},
                            "greeks": {"delta": 0.20},
                        },
                        "SOFI260717C00022000": {
                            "latestQuote": {"bp": 0.10, "ap": 0.15},
                            "greeks": {"delta": 0.08},
                        },
                    }
                },
            )
        )
        rows = fetch_options_grid("SOFI", 17.8, "call")
        assert [r["strike"] for r in rows] == [18.0, 20.0]
