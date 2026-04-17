"""Application configuration loaded from environment variables."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All application settings, loaded from .env or environment variables."""

    # ── LLM ──────────────────────────────────────────────
    gemini_api_key: str = Field(default="", description="Gemini API key (free tier)")

    # ── FRED API ─────────────────────────────────────────
    fred_api_key: str = Field(default="", description="FRED API key for liquidity/credit spreads")

    # ── Notifications: NTFY.sh (primary) ─────────────────
    ntfy_topic: str = Field(default="market-intelligence", description="NTFY topic name")
    ntfy_server: str = Field(default="https://ntfy.sh", description="NTFY server URL")

    # ── Notifications: Home Assistant (fallback) ─────────
    ha_url: str = Field(default="", description="Home Assistant URL")
    ha_token: str = Field(default="", description="Home Assistant long-lived access token")
    ha_notify_service: str = Field(
        default="notify.mobile_app_iphone",
        description="HA notification service name",
    )

    # ── Database ─────────────────────────────────────────
    db_path: str = Field(default="data/market_intelligence.db", description="SQLite DB path")

    # ── Schedule ─────────────────────────────────────────
    schedule_time: str = Field(
        default="19:00",
        description="Daily run time in 24-hour format (HH:MM)",
    )

    # ── Logging ──────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Logging level")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Singleton instance — import this throughout the app
settings = Settings()
