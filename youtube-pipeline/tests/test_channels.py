from channels import ChannelFetcher


def _fetcher():
    return ChannelFetcher(scan_depth=10, min_duration_seconds=120)


def test_skip_short_video():
    f = _fetcher()
    entry = {
        "id": "abc",
        "title": "Short",
        "duration": 45,
        "timestamp": 1716595200,
    }
    assert f._entry_to_video(entry, "Channel") is None


def test_skip_livestream():
    f = _fetcher()
    entry = {
        "id": "abc",
        "title": "Live",
        "duration": 600,
        "timestamp": 1716595200,
        "live_status": "is_live",
    }
    assert f._entry_to_video(entry, "Channel") is None


def test_skip_upcoming_premiere():
    f = _fetcher()
    entry = {
        "id": "abc",
        "title": "Coming Soon",
        "duration": None,
        "timestamp": 1716595200,
        "live_status": "is_upcoming",
    }
    assert f._entry_to_video(entry, "Channel") is None


def test_accept_normal_long_video():
    f = _fetcher()
    entry = {
        "id": "abc",
        "title": "Normal",
        "duration": 600,
        "timestamp": 1716595200,
        "url": "https://www.youtube.com/watch?v=abc",
    }
    v = f._entry_to_video(entry, "Channel")
    assert v is not None
    assert v.id == "abc"
    assert v.duration_seconds == 600


def test_parses_upload_date_fallback():
    entry = {
        "id": "abc",
        "title": "Normal",
        "duration": 600,
        "upload_date": "20260524",
    }
    v = ChannelFetcher._parse_published(entry)
    assert v is not None
    assert v.year == 2026 and v.month == 5 and v.day == 24


def test_no_publish_time_returns_none():
    f = _fetcher()
    entry = {"id": "abc", "title": "x", "duration": 600}
    assert f._entry_to_video(entry, "Channel") is None
