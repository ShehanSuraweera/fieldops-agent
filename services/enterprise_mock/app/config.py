"""Runtime settings, read from environment variables."""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# Sri Lanka has no daylight saving time, so a fixed offset is exact.
LK_TZ = timezone(timedelta(hours=5, minutes=30), name="Asia/Colombo")


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    seed_today: date | None


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://fieldops:fieldops@localhost:5432/fieldops"
        ),
        api_key=os.environ.get("MOCK_API_KEY", "dev-mock-key"),
        seed_today=_parse_date(os.environ.get("SEED_TODAY")),
    )


settings = load_settings()


def now_lk() -> datetime:
    return datetime.now(LK_TZ)


def to_lk(value: datetime) -> datetime:
    """Interpret naive datetimes as Colombo local time; convert aware ones to it."""
    if value.tzinfo is None:
        return value.replace(tzinfo=LK_TZ)
    return value.astimezone(LK_TZ)
