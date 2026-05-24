from datetime import datetime, timezone

from discord_poster import (
    DiscordPoster,
    EMBED_DESCRIPTION_MAX,
    EMBED_FIELD_VALUE_MAX,
    _truncate_at_sentence,
)
from models import Summary


def _summary(**overrides):
    base = dict(
        video_id="vid1",
        title="A reasonable title",
        channel_name="Chan",
        url="https://www.youtube.com/watch?v=vid1",
        summary_text="First sentence. Second sentence. Third sentence.",
        takeaways=["takeaway one", "takeaway two", "takeaway three"],
        generated_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Summary(**base)


def test_embed_basic_fields():
    poster = DiscordPoster("https://discord.com/api/webhooks/x/y")
    embed = poster._build_embed(_summary())

    assert embed["title"] == "A reasonable title"
    assert embed["url"] == "https://www.youtube.com/watch?v=vid1"
    assert embed["color"] == 0xFF0000
    assert embed["author"]["name"] == "Chan"
    assert "2026-05-24" in embed["footer"]["text"]
    assert embed["thumbnail"]["url"] == "https://img.youtube.com/vi/vid1/mqdefault.jpg"
    assert len(embed["fields"]) == 1
    assert "takeaway one" in embed["fields"][0]["value"]


def test_embed_omits_takeaways_field_when_empty():
    poster = DiscordPoster("https://discord.com/api/webhooks/x/y")
    embed = poster._build_embed(_summary(takeaways=[]))
    assert "fields" not in embed


def test_long_description_truncated_at_sentence():
    long_text = ("Sentence one. " * 1000)  # well over 4096
    out = _truncate_at_sentence(long_text, EMBED_DESCRIPTION_MAX)
    assert len(out) <= EMBED_DESCRIPTION_MAX + 2  # +2 for trailing ellipsis chars
    assert out.endswith("…")


def test_long_takeaways_fit_field_limit():
    poster = DiscordPoster("https://discord.com/api/webhooks/x/y")
    many = ["x" * 200 for _ in range(20)]
    formatted = poster._format_takeaways(many)
    assert len(formatted) <= EMBED_FIELD_VALUE_MAX


def test_empty_webhook_url_rejected():
    import pytest

    with pytest.raises(ValueError):
        DiscordPoster("")
