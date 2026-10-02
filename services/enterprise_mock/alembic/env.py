"""Alembic environment: migrates the database named by DATABASE_URL."""

from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.models import SCHEMAS, Base

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    # Only compare our own schemas; ignore anything else in the database.
    if type_ == "schema":
        return name in SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
