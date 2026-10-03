"""Shared fixtures. Database tests use a separate *_test database and are skipped without Postgres."""

import os
from collections.abc import Iterator
from pathlib import Path

# Point the agent at the test database before anything imports app.config.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://fieldops:fieldops@localhost:5432/fieldops_agent_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.pop("AGENT_NOW", None)

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[1]


def _ensure_database(url: str) -> None:
    db_url = make_url(url)
    assert db_url.database and db_url.database.endswith("_test"), "tests must run against a *_test database"
    admin = create_engine(db_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_url.database}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_url.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def agent_db() -> Iterator[str]:
    """A migrated agent test database (downgrade + upgrade proves the migration is reversible)."""
    try:
        _ensure_database(TEST_DATABASE_URL)
    except OperationalError:
        pytest.skip("Postgres is not reachable")
    cfg = Config(str(ROOT / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield TEST_DATABASE_URL
