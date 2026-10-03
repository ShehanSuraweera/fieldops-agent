"""Alembic environment for the agent's own tables (schema "agent")."""

from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.models import Base

SCHEMA = "agent"
VERSION_TABLE = "alembic_version"  # kept inside the agent schema, apart from the mock's


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    if type_ == "schema":
        return name == SCHEMA
    # LangGraph's checkpoint tables are created and migrated by PostgresSaver.setup().
    return not (type_ == "table" and name is not None and name.startswith("checkpoint"))


def run_migrations_online() -> None:
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            include_schemas=True,
            include_name=include_name,
            version_table=VERSION_TABLE,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
