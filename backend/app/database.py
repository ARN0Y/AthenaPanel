"""Async SQLAlchemy engine / session setup + lightweight migrations."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


log = logging.getLogger("vpn-panel.database")


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
    "wg_peers": [
        ("online_since", "DATETIME"),
        ("session_base_rx", "BIGINT NOT NULL DEFAULT 0"),
        ("session_base_tx", "BIGINT NOT NULL DEFAULT 0"),
    ],
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
    # Gives a WireGuard peer a "current session" so the live view can show real
    # connected time and this-session bytes instead of rekey age and a lifetime
    # total. Existing peers start with no session; the enforcer anchors one on
    # the next offline->online edge it observes.
    ("wg_peers", "online_since", "TIMESTAMPTZ"),
    ("wg_peers", "session_base_rx", "BIGINT NOT NULL DEFAULT 0"),
    ("wg_peers", "session_base_tx", "BIGINT NOT NULL DEFAULT 0"),
    # Node control plane.
    ("nodes", "token", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("nodes", "hostname", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("nodes", "kernel", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("nodes", "last_report", "TEXT NOT NULL DEFAULT ''"),
]


async def _try_ddl(conn, sql: str) -> bool:
    """Run one migration statement; never let it abort startup.

    A panel that refuses to boot is strictly worse than a panel with one
    missing column: the API, the UI and the enforcer all stop, the ppp hooks
    start timing out, and sessions that connect meanwhile are never registered.
    A failed step is logged loudly and the rest continue, so the server keeps
    serving while an operator fixes the cause.

    Learned the hard way: `usage_samples` was owned by the panel role but its
    TimescaleDB chunks were still owned by `postgres`, so ALTER TABLE raised
    InsufficientPrivilege and took the whole service down on deploy.

    The caller must supply an AUTOCOMMIT connection — inside a transaction the
    first failure would poison every statement after it.
    """
    try:
        await conn.exec_driver_sql(sql)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("SCHEMA MIGRATION STEP FAILED, continuing startup: %s -> %s", sql, exc)
        return False


async def _migrate_columns_pg(conn) -> int:
    failed = 0
    for table, name, ddl in _PG_COLUMN_MIGRATIONS:
        ok = await _try_ddl(
            conn, f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}"
        )
        failed += 0 if ok else 1
    return failed


async def _migrate_indexes_pg(conn) -> int:
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
    failed = 0
    # Create the replacement BEFORE dropping the old one, so the table is never
    # briefly unprotected against duplicate interfaces.
    for sql in (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_node_ifname "
        "ON sessions (node_id, ifname)",
        "DROP INDEX IF EXISTS ix_sessions_ifname",
        "CREATE INDEX IF NOT EXISTS ix_sessions_ifname ON sessions (ifname)",
        "CREATE INDEX IF NOT EXISTS ix_sessions_node_id ON sessions (node_id)",
        "CREATE INDEX IF NOT EXISTS ix_accounting_node_id ON accounting (node_id)",
    ):
        failed += 0 if await _try_ddl(conn, sql) else 1
    return failed


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
        if not settings.is_postgres:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        await conn.run_sync(Base.metadata.create_all)
        if not settings.is_postgres:
            await _migrate_columns(conn)

    if settings.is_postgres:
        # AUTOCOMMIT so one failed statement cannot poison the rest: inside a
        # transaction Postgres rejects every following statement with "current
        # transaction is aborted", turning a single bad step into a total
        # migration failure.
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            failed = await _migrate_columns_pg(conn)
            failed += await _migrate_indexes_pg(conn)
            await _setup_timescale(conn)
        if failed:
            log.error(
                "%d schema migration step(s) failed — the panel is running but "
                "may be missing columns or indexes; fix and restart", failed
            )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


__all__ = ["Base", "engine", "AsyncSessionLocal", "init_db", "get_session", "text"]
