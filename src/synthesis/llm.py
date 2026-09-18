"""LLM client for market digest synthesis (Gemini primary, GPT-4o-mini fallback)."""

from __future__ import annotations

import asyncio
import logging
import time
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE

from ..config import settings

logger = logging.getLogger(__name__)

# Proactive rate-limiter for the Gemini free tier (5 RPM for gemini-2.5-flash).
# Space every call ≥13s apart so we never exceed quota. The 429 retry below is
# a safety net for edge cases (concurrent runs, external quota consumers).
_GEMINI_MIN_INTERVAL_S = 13.0
_gemini_last_call_ts: float = 0.0
_gemini_rate_lock: asyncio.Lock | None = None


def _get_gemini_lock() -> asyncio.Lock:
    global _gemini_rate_lock
    if _gemini_rate_lock is None:
        _gemini_rate_lock = asyncio.Lock()
    return _gemini_rate_lock


async def _gemini_pace() -> None:
    """Block until we are allowed to make the next Gemini API call."""
    global _gemini_last_call_ts
    async with _get_gemini_lock():
        gap = (_gemini_last_call_ts + _GEMINI_MIN_INTERVAL_S) - time.monotonic()
        if gap > 0:
            logger.debug("LLM: pacing %.1fs before Gemini call", gap)
            await asyncio.sleep(gap)
        _gemini_last_call_ts = time.monotonic()


async def synthesize(system_prompt: str, user_prompt: str) -> str:
    """Generate market digest text via LLM.

    Provider chain: Gemini (if key present) → Claude CLI → static fallback.
    """
    if settings.gemini_api_key:
        result = await _call_gemini(system_prompt, user_prompt)
        if result:
            return result
        logger.warning("LLM: Gemini failed, trying Claude CLI fallback")

    result = await _call_claude_cli(system_prompt, user_prompt)
    if result:
        return result

    logger.warning("LLM: No working provider — returning fallback")
    return _fallback_summary(user_prompt)


async def _call_claude_cli(system_prompt: str, user_prompt: str) -> str | None:
    """Run the ``claude`` CLI with the combined prompt and return the output.

    Args:
        system_prompt: System / instruction context for the model.
        user_prompt:   User-facing signal data prompt.

    Returns:
        Stripped stdout string on success, or None on any failure.
    """
    combined = f"{system_prompt}\n\n---\n\n{user_prompt}"
    try:
        proc = await create_subprocess_exec(
            "claude",
            "-p",
            "--tools",
            "",
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=combined.encode()), timeout=300
        )
        if proc.returncode == 0:
            output = stdout_bytes.decode().strip()
            if output:
                logger.info("LLM: Claude CLI returned %d chars", len(output))
                return output
            logger.warning("LLM: Claude CLI returned empty output")
            return None
        else:
            stderr_text = stderr_bytes.decode().strip()
            logger.warning(
                "LLM: Claude CLI exited with code %d — %s",
                proc.returncode,
                stderr_text[:500],
            )
            return None

    except TimeoutError:
        logger.warning("LLM: Claude CLI timed out after 120s")
        return None
    except FileNotFoundError:
        logger.warning("LLM: 'claude' binary not found — is the Claude CLI installed?")
        return None
    except Exception as exc:
        logger.exception("LLM: Claude CLI unexpected error: %s", exc)
        return None


_GEMINI_RETRIES = 2
_GEMINI_RETRY_BACKOFF_S = 10.0
# 429 RESOURCE_EXHAUSTED = per-minute quota hit; the server suggests ~15s but 35s gives headroom.
_GEMINI_RATE_LIMIT_WAIT_S = 35
_GEMINI_RATE_LIMIT_RETRIES = 3


async def _call_gemini(system_prompt: str, user_prompt: str) -> str | None:
    """Call Gemini API for synthesis, retrying transient failures.

    Server-side errors (5xx, e.g. "high demand") and other unexpected
    exceptions (network blips) are retried with backoff.
    429 RESOURCE_EXHAUSTED (per-minute quota) is retried after a fixed wait.
    Other 4xx client errors (bad auth, daily quota) are not retried.
    """
    from google import genai
    from google.genai import errors as genai_errors

    client = genai.Client(api_key=settings.gemini_api_key)
    rate_limit_attempts = 0

    for attempt in range(_GEMINI_RETRIES + 1):
        try:
            await _gemini_pace()
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.5-flash",
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                ),
            )
            text = response.text
            if text:
                logger.info(f"LLM: Gemini returned {len(text)} chars")
                return text.strip()

            logger.warning("LLM: Gemini returned empty response")
            return None

        except genai_errors.ClientError as exc:
            exc_str = str(exc)
            is_rate_limit = "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str
            if is_rate_limit and rate_limit_attempts < _GEMINI_RATE_LIMIT_RETRIES:
                rate_limit_attempts += 1
                logger.warning(
                    "LLM: Gemini 429 rate-limit (rate-limit retry %d/%d), waiting %ds",
                    rate_limit_attempts,
                    _GEMINI_RATE_LIMIT_RETRIES,
                    _GEMINI_RATE_LIMIT_WAIT_S,
                )
                await asyncio.sleep(_GEMINI_RATE_LIMIT_WAIT_S)
                continue
            logger.exception("LLM: Gemini call failed with a client error (not retrying): %s", exc)
            return None

        except Exception as exc:
            if attempt < _GEMINI_RETRIES:
                logger.warning(
                    "LLM: Gemini call failed (attempt %d/%d), retrying: %s",
                    attempt + 1,
                    _GEMINI_RETRIES + 1,
                    exc,
                )
                await asyncio.sleep(_GEMINI_RETRY_BACKOFF_S * (attempt + 1))
                continue
            logger.exception(
                "LLM: Gemini call failed after %d attempts: %s", _GEMINI_RETRIES + 1, exc
            )
            return None


def _fallback_summary(user_prompt: str) -> str:
    """Return a simple fallback message since main.py already renders raw signals."""
    return (
        "LLM unavailable. Check your GEMINI_API_KEY and API quota for full AI-synthesized analysis."
    )
