"""Async SQLAlchemy engine / session setup + lightweight migrations."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    """Async engine. On Postgres, size the pool for concurrency and add
    pre-ping (transparently recover connections dropped by a PG restart /
    idle timeout) + recycle (avoid using stale connections). SQLite has no
    server-side pool, so those knobs don't apply there."""
    if settings.is_postgres:
        return create_async_engine(
            settings.sqlalchemy_url,
            echo=False,
            future=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
        )
    return create_async_engine(settings.sqlalchemy_url, echo=False, future=True)


engine = _make_engine()

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


# columns added after the initial release -> ensure they exist on upgrade
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "users": [
        ("last_seen", "DATETIME"),
        ("total_sessions", "INTEGER NOT NULL DEFAULT 0"),
        ("created_by_admin_id", "INTEGER"),
        ("outbound", "VARCHAR(16) NOT NULL DEFAULT 'direct'"),
        ("l2tp_mode", "VARCHAR(8) NOT NULL DEFAULT 'ipsec'"),
    ],
    # Self-healing accounting (v2): per-session billing baseline + proto + a
    # debounce counter so a transient sysfs miss never drops a live session.
    "sessions": [
        ("proto", "VARCHAR(8) NOT NULL DEFAULT ''"),
        ("base_rx", "BIGINT NOT NULL DEFAULT 0"),
        ("base_tx", "BIGINT NOT NULL DEFAULT 0"),
        ("gone_polls", "INTEGER NOT NULL DEFAULT 0"),
        ("node_id", "INTEGER NOT NULL DEFAULT 1"),
        ("stale_since", "DATETIME"),
    ],
    "usage_samples": [("node_id", "INTEGER NOT NULL DEFAULT 1")],
    "accounting": [("node_id", "INTEGER NOT NULL DEFAULT 1")],
}


async def _migrate_columns(conn) -> None:
    for table, columns in _COLUMN_MIGRATIONS.items():
        try:
            res = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
            existing = {row[1] for row in res.fetchall()}
        except Exception:  # noqa: BLE001 (table may not exist yet)
            continue
        for name, ddl in columns:
            if name not in existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                )


# Postgres has no PRAGMA path; add post-release columns idempotently via DDL.
#
# Every entry below is a constant DEFAULT, which Postgres 11+ applies as
# metadata only — no table rewrite, no long lock, even on the 12M-row
# usage_samples hypertable.
_PG_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("users", "outbound", "VARCHAR(16) NOT NULL DEFAULT 'direct'"),
    ("users", "l2tp_mode", "VARCHAR(8) NOT NULL DEFAULT 'ipsec'"),
    # Multi-node: every session / sample / ledger row records which server
    # produced it. Existing rows are all from this server, so DEFAULT 1 is the
    # correct backfill and needs no data migration.
    ("sessions", "node_id", "INTEGER NOT NULL DEFAULT 1"),
    ("sessions", "stale_since", "TIMESTAMPTZ"),
    ("usage_samples", "node_id", "INTEGER NOT NULL DEFAULT 1"),
    ("accounting", "node_id", "INTEGER NOT NULL DEFAULT 1"),
]


async def _migrate_columns_pg(conn) -> None:
    for table, name, ddl in _PG_COLUMN_MIGRATIONS:
        await conn.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}"
        )


async def _migrate_indexes_pg(conn) -> None:
    """Re-shape indexes that changed meaning when nodes were introduced.

    `sessions.ifname` used to be globally unique, which is wrong the moment a
    second node exists: every node has its own ppp0. Uniqueness moves to
    (node_id, ifname). The table holds ~one row per live session, so this is
    effectively instant.

    Deliberately NOT done here: widening usage_samples' primary key from
    (ts, ifname) to (ts, node_id, ifname). That rebuilds the index on every
    hypertable chunk over 12M+ rows, and (ts, ifname) is still unique while
    node 1 is the only node. It must happen before the first remote node
    reports — see the note on UsageSample.node_id.
    """
    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_node_ifname "
        "ON sessions (node_id, ifname)"
    )
    # Drop the old global-unique index only AFTER the replacement exists, so the
    # table is never briefly unprotected against duplicate interfaces.
    await conn.exec_driver_sql("DROP INDEX IF EXISTS ix_sessions_ifname")
    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_sessions_ifname ON sessions (ifname)"
    )
    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_sessions_node_id ON sessions (node_id)"
    )
    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_accounting_node_id ON accounting (node_id)"
    )


async def _setup_timescale(conn) -> None:
    """Promote the high-volume time-series table to a TimescaleDB hypertable and
    attach a retention policy. Idempotent; silently skipped if the extension is
    absent so a plain-Postgres deploy still works."""
    try:
        await conn.exec_driver_sql(
            "SELECT create_hypertable('usage_samples', 'ts', "
            "if_not_exists => TRUE, migrate_data => TRUE);"
        )
        await conn.exec_driver_sql(
            "SELECT add_retention_policy('usage_samples', INTERVAL '90 days', "
            "if_not_exists => TRUE);"
        )
    except Exception:  # noqa: BLE001
        pass


async def init_db() -> None:
    """Create tables; on SQLite enable WAL + run column migrations, on Postgres
    set up TimescaleDB hypertables."""
    from . import models  # noqa: F401  (register models)

    async with engine.begin() as conn:
        if settings.is_postgres:
            await conn.run_sync(Base.metadata.create_all)
            await _migrate_columns_pg(conn)
            await _migrate_indexes_pg(conn)
            await _setup_timescale(conn)
        else:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            await conn.run_sync(Base.metadata.create_all)
            await _migrate_columns(conn)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


__all__ = ["Base", "engine", "AsyncSessionLocal", "init_db", "get_session", "text"]
