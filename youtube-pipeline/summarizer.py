from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from datetime import datetime, timezone

from models import Summary, Video

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """\
You are a concise financial content summarizer. Summarize this YouTube video transcript.

Video: {title}
Channel: {channel_name}

Transcript:
{transcript}

Respond with ONLY a JSON object in a ```json code fence, with this exact shape:

```json
{{
  "summary": "2-4 sentence summary of what the video covers and its main argument or conclusion",
  "takeaways": [
    "takeaway 1",
    "takeaway 2",
    "takeaway 3"
  ],
  "proposed_trades": [
    "specific trade or position mentioned (e.g. 'Buy AAPL calls, strike $200, exp June 20')",
    "another trade if present"
  ]
}}
```

- 3-5 takeaways, each one a complete sentence.
- proposed_trades: list only concrete, actionable trade ideas the presenter explicitly proposes — specific tickers, options, entries, or positions. If the presenter mentions watching a stock or general market views but no specific trade, omit it. If no trades are proposed, return an empty array [].
- No prose outside the code fence.
"""


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


class ClaudeCLIError(RuntimeError):
    """Raised when the `claude` binary is missing or unusable. Fatal."""


class Summarizer:
    def __init__(
        self,
        *,
        timeout_seconds: int,
        inter_call_sleep_seconds: float,
        claude_bin: str = "claude",
    ):
        self.timeout_seconds = timeout_seconds
        self.inter_call_sleep_seconds = inter_call_sleep_seconds
        self.claude_bin = claude_bin

    def verify_cli_available(self) -> None:
        """Run `claude --version` to fail fast if the CLI is missing."""
        try:
            result = subprocess.run(
                [self.claude_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except FileNotFoundError as e:
            raise ClaudeCLIError(
                f"`{self.claude_bin}` not found on PATH. Install Claude Code "
                f"and authenticate as the user running this pipeline."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ClaudeCLIError("`claude --version` timed out") from e

        if result.returncode != 0:
            raise ClaudeCLIError(
                f"`claude --version` failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        logger.info("claude CLI: %s", result.stdout.strip() or "ok")

    def summarize(self, video: Video, transcript: str) -> Summary | None:
        prompt = PROMPT_TEMPLATE.format(
            title=video.title,
            channel_name=video.channel_name,
            transcript=transcript,
        )

        stdout = self._invoke_claude(prompt, video.id)
        if stdout is None:
            return None

        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            logger.error(
                "claude -p did not return JSON envelope for %s; raw: %s",
                video.id,
                stdout[:200],
            )
            return None

        if envelope.get("is_error") or envelope.get("subtype") == "error":
            logger.error(
                "claude -p reported error for %s: %s",
                video.id,
                envelope.get("result") or envelope,
            )
            return None

        inner_text = envelope.get("result")
        if not isinstance(inner_text, str):
            logger.error(
                "claude -p envelope missing 'result' for %s: %s",
                video.id,
                envelope,
            )
            return None

        return self._parse_inner_json(video, inner_text)

    def sleep_between_calls(self) -> None:
        if self.inter_call_sleep_seconds > 0:
            time.sleep(self.inter_call_sleep_seconds)

    def _invoke_claude(self, prompt: str, video_id: str) -> str | None:
        cmd = [self.claude_bin, "-p", "--output-format", "json", prompt]
        for attempt in (1, 2):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                logger.error("claude -p timed out for %s (attempt %d)", video_id, attempt)
                return None
            except FileNotFoundError as e:
                raise ClaudeCLIError(
                    f"`{self.claude_bin}` disappeared mid-run"
                ) from e

            if result.returncode == 0:
                return result.stdout.strip()

            stderr = result.stderr.lower()
            if "rate limit" in stderr or "rate_limit" in stderr or "429" in stderr:
                if attempt == 1:
                    logger.warning(
                        "claude -p hit rate limit for %s, sleeping 60s and retrying",
                        video_id,
                    )
                    time.sleep(60)
                    continue
                logger.error("claude -p still rate-limited for %s after retry", video_id)
                return None

            logger.error(
                "claude -p failed for %s (rc=%s): %s",
                video_id,
                result.returncode,
                result.stderr.strip(),
            )
            return None

        return None

    def _parse_inner_json(self, video: Video, inner: str) -> Summary:
        payload = self._extract_json_object(inner)
        summary_text = ""
        takeaways: list[str] = []

        proposed_trades: list[str] = []
        if payload is not None:
            summary_text = str(payload.get("summary", "")).strip()
            raw_takeaways = payload.get("takeaways") or []
            if isinstance(raw_takeaways, list):
                takeaways = [str(t).strip() for t in raw_takeaways if str(t).strip()]
            raw_trades = payload.get("proposed_trades") or []
            if isinstance(raw_trades, list):
                proposed_trades = [str(t).strip() for t in raw_trades if str(t).strip()]
        else:
            logger.warning(
                "Could not extract JSON payload for %s; storing raw model output",
                video.id,
            )
            summary_text = inner.strip()

        return Summary(
            video_id=video.id,
            title=video.title,
            channel_name=video.channel_name,
            url=video.url,
            summary_text=summary_text,
            takeaways=takeaways,
            proposed_trades=proposed_trades,
            generated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        match = _FENCE_RE.search(text)
        candidate = match.group(1) if match else text
        candidate = candidate.strip()
        if not candidate.startswith("{"):
            # Sometimes the model returns JSON without a fence; find first {.
            brace = candidate.find("{")
            if brace == -1:
                return None
            candidate = candidate[brace:]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
