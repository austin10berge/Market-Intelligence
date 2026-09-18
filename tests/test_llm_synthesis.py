"""Unit tests for src.synthesis.llm Gemini retry behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import errors as genai_errors

from src.synthesis import llm
from src.synthesis.llm import _call_gemini


def _make_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


def _server_error() -> genai_errors.ServerError:
    return genai_errors.ServerError(
        code=503,
        response_json={"error": {"message": "high demand", "status": "UNAVAILABLE"}},
    )


def _client_error() -> genai_errors.ClientError:
    return genai_errors.ClientError(
        code=403,
        response_json={"error": {"message": "API key not valid", "status": "PERMISSION_DENIED"}},
    )


def _quota_error(quota_id: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(
        code=429,
        response_json={
            "error": {
                "code": 429,
                "message": "You exceeded your current quota.",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": quota_id, "quotaValue": "20"}],
                    }
                ],
            }
        },
    )


def _per_minute_quota_error() -> genai_errors.ClientError:
    return _quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")


def _per_day_quota_error() -> genai_errors.ClientError:
    return _quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")


# The proactive pacer sleeps via the same asyncio.sleep and keeps a module-level
# timestamp, so it is patched out here: these tests count retry sleeps only.
@patch("src.synthesis.llm._gemini_pace", new_callable=AsyncMock)
@patch("src.synthesis.llm.settings")
@patch("src.synthesis.llm.asyncio.sleep", new_callable=AsyncMock)
class TestCallGemini:
    async def test_succeeds_on_first_attempt(self, mock_sleep, mock_settings, mock_pace):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.return_value = (
                _make_response("hello world")
            )
            result = await _call_gemini("system", "user")
        assert result == "hello world"
        mock_sleep.assert_not_called()

    async def test_recovers_after_one_transient_server_error(
        self, mock_sleep, mock_settings, mock_pace
    ):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = [
                _server_error(),
                _make_response("recovered"),
            ]
            result = await _call_gemini("system", "user")
        assert result == "recovered"
        mock_sleep.assert_called_once()

    async def test_gives_up_after_exhausting_retries_on_persistent_server_error(
        self, mock_sleep, mock_settings, mock_pace
    ):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = _server_error()
            result = await _call_gemini("system", "user")
        assert result is None
        assert mock_sleep.call_count == 2  # 3 total attempts, 2 backoffs

    async def test_does_not_retry_on_non_rate_limit_client_error(
        self, mock_sleep, mock_settings, mock_pace
    ):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = _client_error()
            result = await _call_gemini("system", "user")
        assert result is None
        mock_sleep.assert_not_called()
        assert mock_client_cls.return_value.models.generate_content.call_count == 1

    async def test_does_not_retry_when_daily_quota_exhausted(
        self, mock_sleep, mock_settings, mock_pace
    ):
        """A per-day quota will not reset within the request — waiting only delays fallback."""
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = (
                _per_day_quota_error()
            )
            result = await _call_gemini("system", "user")
        assert result is None
        mock_sleep.assert_not_called()
        assert mock_client_cls.return_value.models.generate_content.call_count == 1

    async def test_recovers_after_per_minute_rate_limit(
        self, mock_sleep, mock_settings, mock_pace
    ):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = [
                _per_minute_quota_error(),
                _make_response("recovered"),
            ]
            result = await _call_gemini("system", "user")
        assert result == "recovered"
        mock_sleep.assert_called_once_with(llm._GEMINI_RATE_LIMIT_WAIT_S)

    async def test_every_rate_limit_wait_is_followed_by_a_call(
        self, mock_sleep, mock_settings, mock_pace
    ):
        """Persistent per-minute 429: N waits → N+1 calls, never a trailing wasted wait."""
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = (
                _per_minute_quota_error()
            )
            result = await _call_gemini("system", "user")
        assert result is None
        waits = llm._GEMINI_RATE_LIMIT_RETRIES
        assert mock_sleep.call_count == waits
        assert mock_client_cls.return_value.models.generate_content.call_count == waits + 1

    async def test_rate_limit_retries_do_not_consume_server_error_retries(
        self, mock_sleep, mock_settings, mock_pace
    ):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = [
                _per_minute_quota_error(),
                _per_minute_quota_error(),
                _server_error(),
                _server_error(),
                _make_response("recovered"),
            ]
            result = await _call_gemini("system", "user")
        assert result == "recovered"
