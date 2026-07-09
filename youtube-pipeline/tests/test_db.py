from datetime import datetime, timedelta, timezone

from db import Database
from models import PostRecord, Video


def _make_video(vid="abc123"):
    return Video(
        id=vid,
        title="Test",
        url=f"https://www.youtube.com/watch?v={vid}",
        channel_name="Test Channel",
        published_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
        duration_seconds=600,
    )


def test_database_roundtrip(tmp_path):
    db = Database(tmp_path / "state.db")
    v = _make_video()

    assert not db.has_been_seen(v.id)
    assert not db.has_been_posted(v.id)

    db.mark_seen(v)
    assert db.has_been_seen(v.id)
    assert not db.has_been_posted(v.id)

    db.record_post(
        PostRecord(
            video_id=v.id,
            discord_message_id="msg-1",
            posted_at=datetime.now(timezone.utc),
        )
    )
    assert db.has_been_posted(v.id)


def test_pipeline_run_tracking(tmp_path):
    db = Database(tmp_path / "state.db")
    assert db.last_successful_run_at() is None

    run_id = db.start_run()
    db.finish_run(run_id, success=True, videos_found=3, videos_posted=2)

    last = db.last_successful_run_at()
    assert last is not None
    assert (datetime.now(timezone.utc) - last) < timedelta(seconds=5)


def test_failed_run_not_counted_as_success(tmp_path):
    db = Database(tmp_path / "state.db")
    run_id = db.start_run()
    db.finish_run(run_id, success=False, videos_found=0, videos_posted=0)
    assert db.last_successful_run_at() is None


def test_recent_posts_window(tmp_path):
    db = Database(tmp_path / "state.db")
    db.record_post(
        PostRecord("a", "msg-a", datetime.now(timezone.utc))
    )
    posts = db.get_recent_posts(days=1)
    assert len(posts) == 1
    assert posts[0].video_id == "a"
