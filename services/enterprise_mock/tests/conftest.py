"""Test fixtures: a real Postgres test database, migrated once and reseeded per test."""

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path

# Point the app at the test database *before* importing anything from it.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://fieldops:fieldops@localhost:5432/fieldops_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.seed import seed

ROOT = Path(__file__).resolve().parents[1]
SEED_TODAY = date(2026, 10, 1)  # a Thursday


def _ensure_database(url: str) -> None:
    db_url = make_url(url)
    assert db_url.database and db_url.database.endswith("_test"), "tests must run against a *_test database"
    admin = create_engine(db_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_url.database}
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{db_url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    """Fresh schema per test session; also proves the migration downgrades cleanly."""
    _ensure_database(TEST_DATABASE_URL)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def seeded(database: None) -> None:
    with SessionLocal() as session:
        seed(session, SEED_TODAY)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, headers={"X-API-Key": settings.api_key}) as test_client:
        yield test_client


@pytest.fixture
def anon_client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def ticket(client: TestClient) -> dict:
    response = client.post(
        "/crm/tickets",
        json={
            "customer_id": "CUST-001",
            "asset_id": "FRZ-1043",
            "description": "Freezer #3 stopped cooling.",
        },
    )
    assert response.status_code == 201
    return response.json()


def assert_error(response, status: int, code: str) -> dict:
    """Assert the shared error body and return its ``error`` object."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    return body["error"]
