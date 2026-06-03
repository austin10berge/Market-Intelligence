"""LLM client for market digest synthesis (Gemini primary, GPT-4o-mini fallback)."""

from __future__ import annotations

import asyncio
import logging
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE

from ..config import settings

logger = logging.getLogger(__name__)


async def synthesize(system_prompt: str, user_prompt: str) -> str:
    """Generate market digest text via LLM.

    Checks ``settings.llm_provider`` to decide whether to try the Claude CLI
    first.  Falls back to Gemini, then a static fallback if both fail.
    """
    provider = settings.llm_provider.lower() if hasattr(settings, "llm_provider") else "gemini"

    if provider == "claude":
        result = await _call_claude_cli(system_prompt, user_prompt)
        if result:
            return result
        logger.warning("LLM: Claude CLI failed, falling back to Gemini")

    if settings.gemini_api_key:
        result = await _call_gemini(system_prompt, user_prompt)
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
            "claude", "-p", combined,
            stdout=PIPE,
            stderr=PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=120
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
                stderr_text[:200],
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


async def _call_gemini(system_prompt: str, user_prompt: str) -> str | None:
    """Call Gemini API for synthesis."""
    try:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)

        response = client.models.generate_content(
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

    except Exception as e:
        logger.exception("LLM: Gemini call failed: %s", e)
        return None


def _fallback_summary(user_prompt: str) -> str:
    """Return a simple fallback message since main.py already renders raw signals."""
    return "LLM unavailable. Check your GEMINI_API_KEY and API quota for full AI-synthesized analysis."
