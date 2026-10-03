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
    database_url: str
    api_key: str
    fixed_now: datetime | None  # AGENT_NOW pins the agent's clock (demos, evals)
    tool_transport: str  # "rest" calls the mock APIs directly; "mcp" goes through the MCP server
    mcp_url: str
    mcp_api_key: str


def load_settings() -> Settings:
    return Settings(
        mock_base_url=os.environ.get("MOCK_BASE_URL", "http://localhost:8001"),
        mock_api_key=os.environ.get("MOCK_API_KEY", "dev-mock-key"),
        rules_path=Path(os.environ.get("RULES_PATH") or _find_rules_file()),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://fieldops:fieldops@localhost:5432/fieldops"
        ),
        api_key=os.environ.get("AGENT_API_KEY", "dev-agent-key"),
        fixed_now=parse_now(os.environ.get("AGENT_NOW")),
        tool_transport=(os.environ.get("TOOL_TRANSPORT") or "rest").strip().lower(),
        mcp_url=os.environ.get("MCP_URL", "http://localhost:8003/mcp"),
        mcp_api_key=os.environ.get("MCP_API_KEY", "dev-mcp-key"),
    )


def parse_now(value: str | None) -> datetime | None:
    """ISO datetime; naive values are Colombo local time."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=LK_TZ)


settings = load_settings()


def now_lk() -> datetime:
    return datetime.now(LK_TZ)


def agent_now() -> datetime:
    """The agent's clock: AGENT_NOW if set, otherwise the real time in Colombo."""
    return settings.fixed_now or now_lk()
