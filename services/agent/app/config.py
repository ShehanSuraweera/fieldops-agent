"""Agent settings, read from environment variables."""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Sri Lanka has no daylight saving time, so a fixed offset is exact.
LK_TZ = timezone(timedelta(hours=5, minutes=30), name="Asia/Colombo")


def _find_rules_file() -> Path:
    """Nearest config/rules.yaml above this file (the repo root when run from a checkout)."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "rules.yaml"
        if candidate.is_file():
            return candidate
    return Path("/config/rules.yaml")  # where docker-compose mounts it


@dataclass(frozen=True)
class Settings:
    mock_base_url: str
    mock_api_key: str
    rules_path: Path


def load_settings() -> Settings:
    return Settings(
        mock_base_url=os.environ.get("MOCK_BASE_URL", "http://localhost:8001"),
        mock_api_key=os.environ.get("MOCK_API_KEY", "dev-mock-key"),
        rules_path=Path(os.environ.get("RULES_PATH") or _find_rules_file()),
    )


settings = load_settings()


def now_lk() -> datetime:
    return datetime.now(LK_TZ)
