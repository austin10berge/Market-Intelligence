"""LLM client for market digest synthesis (Gemini primary, GPT-4o-mini fallback)."""

from __future__ import annotations

import logging

from ..config import settings

logger = logging.getLogger(__name__)


async def synthesize(system_prompt: str, user_prompt: str) -> str:
    """Generate market digest text via LLM.

    Tries Gemini first, falls back to a formatted summary if no API key is configured.
    """
    if settings.gemini_api_key:
        result = await _call_gemini(system_prompt, user_prompt)
        if result:
            return result

    # Fallback: return the raw user prompt as a basic digest
    logger.warning("LLM: No API key configured — returning raw signal summary")
    return _fallback_summary(user_prompt)


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
