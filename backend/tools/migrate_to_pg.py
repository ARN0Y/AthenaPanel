"""One-shot data migration: SQLite -> PostgreSQL/TimescaleDB.

Copies every table preserving primary keys and exact values (used_bytes is
copied verbatim — no usage jump), then resets Postgres sequences and verifies
row-count + used_bytes parity. The source SQLite DB is only READ.

Usage (run from the backend/ dir so `app` is importable):
    SRC_URL='sqlite+aiosqlite:////root/vpn-pg-staging/vpn_snapshot.db' \
    DST_URL='postgresql+asyncpg://vpnpanel:***@127.0.0.1:5432/vpnpanel' \
    python tools/migrate_to_pg.py
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402  (registers all models on Base.metadata)
from app.database import Base  # noqa: E402

# Copy order (no hard FKs in the schema; order is just for readability). The
# time-series hypertable `usage_samples` is new/empty on SQLite -> skipped.
ORDER = [
    "admins", "admin_invites", "users", "sessions",
    "accounting", "traffic_samples", "audit_log", "app_settings",
]
SERIAL = ["admins", "admin_invites", "users", "sessions", "accounting", "traffic_samples", "audit_log"]
TABLES = {t.name: t for t in Base.metadata.sorted_tables}


def _utc(rows: list[dict]) -> list[dict]:
    """Make naive datetimes tz-aware (UTC) so asyncpg accepts timestamptz."""
    for r in rows:
        for k, v in r.items():
            if isinstance(v, datetime) and v.tzinfo is None:
                r[k] = v.replace(tzinfo=timezone.utc)
    return rows


async def main() -> int:
    src = create_async_engine(os.environ["SRC_URL"])
    dst = create_async_engine(os.environ["DST_URL"])

    print("== creating schema on Postgres ==")
    async with dst.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        try:
            await c.exec_driver_sql(
                "SELECT create_hypertable('usage_samples','ts',"
                "if_not_exists=>TRUE,migrate_data=>TRUE);"
            )
            print("   usage_samples -> hypertable OK")
        except Exception as e:  # noqa: BLE001
            print("   hypertable note:", e)

    if os.environ.get("WIPE") == "1":
        async with dst.begin() as c:
            await c.exec_driver_sql(
                "TRUNCATE " + ", ".join(ORDER) + ", usage_samples RESTART IDENTITY CASCADE;"
            )
        print("== wiped dest tables (WIPE=1) ==")

    print("== copying data ==")
    for name in ORDER:
        tbl = TABLES[name]
        async with src.connect() as sc:
            rows = [dict(r._mapping) for r in (await sc.execute(select(tbl))).all()]
        if rows:
            async with dst.begin() as dc:
                await dc.execute(insert(tbl), _utc(rows))
        print(f"   {name}: {len(rows)}")

    print("== resetting sequences ==")
    async with dst.begin() as dc:
        for name in SERIAL:
            await dc.exec_driver_sql(
                f"SELECT setval(pg_get_serial_sequence('{name}','id'),"
                f"GREATEST(COALESCE((SELECT MAX(id) FROM {name}),1),1), true);"
            )

    print("== parity check ==")
    ok = True
    async with src.connect() as sc, dst.connect() as dc:
        for name in ORDER:
            tbl = TABLES[name]
            s = (await sc.execute(select(func.count()).select_from(tbl))).scalar()
            d = (await dc.execute(select(func.count()).select_from(tbl))).scalar()
            flag = "OK" if s == d else "MISMATCH"
            ok = ok and s == d
            print(f"   {name:16} sqlite={s:<8} pg={d:<8} {flag}")
        su = (await sc.execute(text("SELECT COALESCE(SUM(used_bytes),0) FROM users"))).scalar()
        du = (await dc.execute(text("SELECT COALESCE(SUM(used_bytes),0) FROM users"))).scalar()
        flag = "OK" if su == du else "MISMATCH"
        ok = ok and su == du
        print(f"   sum(used_bytes)  sqlite={su} pg={du} {flag}")

    print("RESULT:", "ALL_OK" if ok else "PARITY_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
