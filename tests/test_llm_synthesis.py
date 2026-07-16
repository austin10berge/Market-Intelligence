"""Unit tests for src.synthesis.llm Gemini retry behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import errors as genai_errors

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
        code=429,
        response_json={"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}},
    )


@patch("src.synthesis.llm.settings")
@patch("src.synthesis.llm.asyncio.sleep", new_callable=AsyncMock)
class TestCallGemini:
    async def test_succeeds_on_first_attempt(self, mock_sleep, mock_settings):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.return_value = (
                _make_response("hello world")
            )
            result = await _call_gemini("system", "user")
        assert result == "hello world"
        mock_sleep.assert_not_called()

    async def test_recovers_after_one_transient_server_error(self, mock_sleep, mock_settings):
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
        self, mock_sleep, mock_settings
    ):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = _server_error()
            result = await _call_gemini("system", "user")
        assert result is None
        assert mock_sleep.call_count == 2  # 3 total attempts, 2 backoffs

    async def test_does_not_retry_on_client_error(self, mock_sleep, mock_settings):
        mock_settings.gemini_api_key = "fake-key"
        with patch("google.genai.Client") as mock_client_cls:
            mock_client_cls.return_value.models.generate_content.side_effect = _client_error()
            result = await _call_gemini("system", "user")
        assert result is None
        mock_sleep.assert_not_called()
        assert mock_client_cls.return_value.models.generate_content.call_count == 1
