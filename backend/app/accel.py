"""Thin wrapper around `accel-cmd` (the accel-ppp control client).

Used to terminate sessions and reload chap-secrets. Safe no-ops if accel-cmd
is not installed (e.g. during the xl2tpd era or before the daemon is up).
"""

import asyncio
import re

from .config import settings


def _host_port() -> tuple[str, str]:
    host, _, port = settings.accel_cli.partition(":")
    return host or "127.0.0.1", port or "2000"


async def _run(command: str, timeout: float = 5.0) -> str:
    host, port = _host_port()
    try:
        proc = await asyncio.create_subprocess_exec(
            "accel-cmd", "-H", host, "-p", port, command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return out.decode("utf-8", "replace")
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return ""


async def reload() -> None:
    """Ask accel-ppp to re-read its config / chap-secrets."""
    await _run("reload")


async def terminate(username: str) -> bool:
    safe = re.sub(r"[^A-Za-z0-9_.@\-]", "", username or "")
    if not safe:
        return False
    await _run(f"terminate username {safe}")
    return True


async def session_map() -> dict[str, str]:
    """ifname -> username from `accel-cmd show sessions` (the SSTP engine).

    Lets the accounting collector name orphan SSTP interfaces it discovers on the
    host but has no session row for. Empty dict if accel-cmd is unavailable.
    """
    out = await _run("show sessions")
    mapping: dict[str, str] = {}
    for line in out.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and parts[0].startswith("ppp") and parts[1]:
            mapping[parts[0]] = parts[1]
    return mapping
