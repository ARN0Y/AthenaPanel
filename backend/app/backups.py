"""PostgreSQL backups (pg_dump custom format) + retention.

A backup is a `pg_dump -Fc` of the panel database written atomically to
BACKUP_DIR. pg_dump only READS the database (no locks that block writers), so
taking a backup never disrupts the live panel. The panel runs as root, so it
shells out as the postgres OS user (peer auth) — no DB password handling.

Restore (TimescaleDB-aware, manual — intentionally NOT a UI button):
    createdb -O vpnpanel vpnpanel_restored
    psql -d vpnpanel_restored -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
    psql -d vpnpanel_restored -c "SELECT timescaledb_pre_restore();"
    pg_restore -d vpnpanel_restored --no-owner <file.dump>
    psql -d vpnpanel_restored -c "SELECT timescaledb_post_restore();"
"""

import asyncio
import glob
import os
import re
import time
from datetime import datetime, timezone

BACKUP_DIR = "/var/lib/vpn-panel/backups"
KEEP = 14  # retain the newest N backups
_DB = "vpnpanel"
_NAME_RE = re.compile(r"^vpnpanel-\d{8}-\d{6}\.dump$")


def _info(path: str) -> dict:
    st = os.stat(path)
    return {
        "name": os.path.basename(path),
        "size": st.st_size,
        "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
    }


def list_backups() -> list[dict]:
    paths = glob.glob(os.path.join(BACKUP_DIR, "vpnpanel-*.dump"))
    return sorted((_info(p) for p in paths), key=lambda d: d["name"], reverse=True)


def safe_path(name: str) -> str | None:
    """Validate a user-supplied backup name (no path traversal)."""
    if not _NAME_RE.match(name or ""):
        return None
    path = os.path.join(BACKUP_DIR, name)
    return path if os.path.isfile(path) else None


def delete_backup(name: str) -> bool:
    path = safe_path(name)
    if not path:
        return False
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def newest_age_hours() -> float:
    paths = glob.glob(os.path.join(BACKUP_DIR, "vpnpanel-*.dump"))
    if not paths:
        return 1e9
    return (time.time() - max(os.path.getmtime(p) for p in paths)) / 3600.0


def _rotate() -> None:
    paths = sorted(glob.glob(os.path.join(BACKUP_DIR, "vpnpanel-*.dump")))
    for p in paths[:-KEEP] if len(paths) > KEEP else []:
        try:
            os.unlink(p)
        except OSError:
            pass


async def create_backup() -> dict:
    """Take a fresh pg_dump. Atomic: write to .part then rename."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"vpnpanel-{ts}.dump")
    tmp = path + ".part"

    fh = open(tmp, "wb")
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-u", "postgres", "pg_dump", "-Fc", _DB,
            stdout=fh, stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await proc.communicate()
    finally:
        fh.close()

    if proc.returncode != 0:
        if os.path.exists(tmp):
            os.unlink(tmp)
        msg = (err or b"").decode("utf-8", "replace").strip()[:400]
        raise RuntimeError(msg or "pg_dump failed")

    os.chmod(tmp, 0o600)
    os.replace(tmp, path)  # atomic
    _rotate()
    return _info(path)


# Everything EXCEPT the usage_samples hypertable (whose chunks live in
# _timescaledb_internal and dominate the dump size). This is the data that
# actually matters for recovery; on restore the panel recreates the empty
# usage_samples hypertable at startup.
_ESSENTIAL_TABLES = (
    "admins", "admin_invites", "users", "sessions", "traffic_samples",
    "accounting", "audit_log", "app_settings", "wg_peers",
)


async def create_essential_dump() -> str:
    """A lighter pg_dump of only the essential tables (users, admins, quotas,
    wg_peers, the accounting ledger, app_settings, …) — i.e. everything except
    the huge, reconstructible usage_samples time-series. Written to a temp file
    small enough for off-site delivery under Telegram's 50MB cap. Caller deletes
    the returned path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    tmp = os.path.join(BACKUP_DIR, f".essential-{ts}.dump")
    args = ["sudo", "-u", "postgres", "pg_dump", "-Fc"]
    for t in _ESSENTIAL_TABLES:
        args += ["-t", t]
    args += [_DB]
    fh = open(tmp, "wb")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=fh, stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await proc.communicate()
    finally:
        fh.close()
    if proc.returncode != 0:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise RuntimeError((err or b"").decode("utf-8", "replace").strip()[:400] or "pg_dump failed")
    os.chmod(tmp, 0o600)
    return tmp
