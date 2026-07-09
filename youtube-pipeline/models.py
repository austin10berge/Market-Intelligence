from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Video:
    id: str
    title: str
    url: str
    channel_name: str
    published_at: datetime
    duration_seconds: int | None = None


@dataclass
class Summary:
    video_id: str
    title: str
    channel_name: str
    url: str
    summary_text: str
    takeaways: list[str] = field(default_factory=list)
    proposed_trades: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PostRecord:
    video_id: str
    discord_message_id: str
    posted_at: datetime
