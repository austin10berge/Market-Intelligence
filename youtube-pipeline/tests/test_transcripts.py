from transcripts import TranscriptFetcher


def test_clean_srt_strips_indices_and_timestamps():
    raw = """1
00:00:00,000 --> 00:00:02,500
Hello world

2
00:00:02,500 --> 00:00:05,000
This is a <c.colorE5E5E5>test</c>
"""
    cleaned = TranscriptFetcher._clean_srt(raw)
    assert cleaned == "Hello world This is a test"


def test_clean_srt_dedupes_rolling_captions():
    # Auto-captions repeat lines as words appear.
    raw = """1
00:00:00,000 --> 00:00:01,000
Hello

2
00:00:01,000 --> 00:00:02,000
Hello

3
00:00:02,000 --> 00:00:03,000
Hello world
"""
    cleaned = TranscriptFetcher._clean_srt(raw)
    assert cleaned == "Hello Hello world"


def test_truncate_at_sentence_boundary():
    text = "First sentence. Second sentence. Third sentence."
    out = TranscriptFetcher._truncate(text, max_chars=20)
    assert out.endswith("[…truncated]")
    # Should land on a sentence boundary if one exists in the late window.
    assert "First sentence." in out


def test_truncate_noop_when_under_limit():
    text = "short text"
    assert TranscriptFetcher._truncate(text, max_chars=100) == text
