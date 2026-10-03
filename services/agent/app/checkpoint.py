"""LangGraph checkpointer backed by Postgres, so paused runs survive restarts.

Checkpoint tables live in the ``agent`` schema next to agent.runs. They are
created and migrated by LangGraph's own ``setup()``, not by our Alembic migrations.
"""

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy.engine import make_url

from app.config import settings

CHECKPOINT_SCHEMA = "agent"


def _libpq_url(database_url: str) -> str:
    """SQLAlchemy's postgresql+psycopg://... URL as a plain libpq connection string."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def postgres_checkpointer(database_url: str | None = None, *, max_size: int = 10) -> PostgresSaver:
    pool = ConnectionPool(
        conninfo=_libpq_url(database_url or settings.database_url),
        min_size=1,
        max_size=max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={CHECKPOINT_SCHEMA}",
        },
        open=True,
    )
    with pool.connection() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
    saver = PostgresSaver(pool)
    saver.setup()  # idempotent: creates or migrates the checkpoint tables
    return saver


def close_checkpointer(saver: object) -> None:
    pool = getattr(saver, "conn", None)
    if isinstance(pool, ConnectionPool):
        pool.close()
