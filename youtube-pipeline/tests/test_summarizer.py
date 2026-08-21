from datetime import datetime, timezone
from unittest.mock import patch

from summarizer import Summarizer

from models import Video


def _video():
    return Video(
        id="vid1",
        title="A talk",
        url="https://www.youtube.com/watch?v=vid1",
        channel_name="Chan",
        published_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
        duration_seconds=600,
    )


def _summarizer():
    return Summarizer(timeout_seconds=10, inter_call_sleep_seconds=0)


def test_extract_json_from_fence():
    text = """Here you go:
```json
{"summary": "A summary.", "takeaways": ["one", "two"]}
```
"""
    payload = Summarizer._extract_json_object(text)
    assert payload == {"summary": "A summary.", "takeaways": ["one", "two"]}


def test_extract_json_without_fence():
    text = '{"summary": "S", "takeaways": []}'
    payload = Summarizer._extract_json_object(text)
    assert payload == {"summary": "S", "takeaways": []}


def test_extract_json_with_prose_before_object():
    text = 'Sure! {"summary": "S", "takeaways": ["a"]}'
    payload = Summarizer._extract_json_object(text)
    assert payload == {"summary": "S", "takeaways": ["a"]}


def test_extract_returns_none_on_garbage():
    assert Summarizer._extract_json_object("totally not json") is None


def test_summarize_parses_claude_envelope():
    s = _summarizer()
    inner = '```json\n{"summary": "Cool.", "takeaways": ["t1", "t2"]}\n```'
    envelope = {"is_error": False, "result": inner}

    with patch.object(s, "_invoke_claude", return_value=__import__("json").dumps(envelope)):
        summary = s.summarize(_video(), "transcript text")

    assert summary is not None
    assert summary.summary_text == "Cool."
    assert summary.takeaways == ["t1", "t2"]


def test_summarize_handles_error_envelope():
    s = _summarizer()
    envelope = {"is_error": True, "result": "rate limit hit"}
    with patch.object(s, "_invoke_claude", return_value=__import__("json").dumps(envelope)):
        assert s.summarize(_video(), "transcript") is None


def test_summarize_handles_non_json_envelope():
    s = _summarizer()
    with patch.object(s, "_invoke_claude", return_value="not json at all"):
        assert s.summarize(_video(), "transcript") is None


def test_summarize_falls_back_when_inner_unparseable():
    s = _summarizer()
    envelope = {"is_error": False, "result": "model output that is not json"}
    with patch.object(s, "_invoke_claude", return_value=__import__("json").dumps(envelope)):
        summary = s.summarize(_video(), "transcript")
    assert summary is not None
    assert summary.summary_text == "model output that is not json"
    assert summary.takeaways == []


def test_invoke_claude_sends_long_prompt_via_stdin_not_argv():
    """A long prompt as a bare argv element hits the Linux kernel's
    per-argument MAX_ARG_STRLEN (128KB) and fails with OSError E2BIG,
    regardless of how small max_transcript_chars is set — it must go
    over stdin instead."""
    s = _summarizer()
    long_prompt = "x" * 200_000

    with patch("summarizer.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "{}"
        s._invoke_claude(long_prompt, "vid1")

    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert long_prompt not in cmd, "prompt must not be passed as a CLI argument"
    assert kwargs.get("input") == long_prompt, "prompt must be sent via stdin"
