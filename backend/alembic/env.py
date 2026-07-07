"""Alembic environment — async (asyncpg), driven by the app's settings + models.

The DB URL comes from app.config.settings.sqlalchemy_url (env DATABASE_URL), so
migrations always target whatever DB the app uses. target_metadata is the app's
Base.metadata, so `--autogenerate` diffs against the live ORM models.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Make the `app` package importable when alembic runs from the backend/ dir.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from app import models  # noqa: E402,F401  (registers all tables on Base.metadata)
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
DB_URL = settings.sqlalchemy_url


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Ignore TimescaleDB-managed objects so autogenerate/check stay clean.

    create_hypertable() auto-adds a time index (e.g. usage_samples_ts_idx) and
    TimescaleDB keeps its own internal schemas/chunks — none of which belong in
    our ORM migrations.
    """
    if type_ == "index" and name and name.endswith("_ts_idx"):
        return False
    schema = getattr(obj, "schema", None)
    if schema and schema.startswith("_timescaledb"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DB_URL, poolclass=None)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
