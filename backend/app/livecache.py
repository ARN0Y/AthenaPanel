"""Shared in-memory live snapshot of sessions / per-user usage.

ONE background task (tasks.snapshot_sampler) refreshes this every ~10s by
reading sysfs once; every API request then serves from here instead of
enumerating /sys per call. That decouples request cost from the number of
connected devices — the key change that lets the panel serve many concurrent
admins while hundreds of sessions are online.

DISPLAY ONLY: quota/accounting enforcement uses its own fresh reads (the
enforcer), so a slightly stale snapshot can never affect billing. Single uvicorn
worker + single-threaded asyncio => updates are atomic (no await mid-update), so
readers always see a consistent snapshot.
"""

import time

_snap: dict = {
    "ts": 0.0,              # monotonic time of last refresh (0 = never)
    "sessions": [],         # list[SessionOut], ALL sessions (unscoped, incl WG)
    "live_by_user": {},     # username -> live overlay bytes (PPP only; WG excluded)
    "live_total": 0,        # aggregate overlay bytes (PPP only; WG excluded)
    "online": set(),        # usernames currently online (incl WG)
    "rx_rate_bps": 0,       # aggregate live upload bits/s
    "tx_rate_bps": 0,       # aggregate live download bits/s
}


def update(sessions: list, rx_rate_bps: int, tx_rate_bps: int) -> None:
    live: dict[str, int] = {}
    live_total = 0
    for s in sessions:
        # WireGuard bytes are committed CONTINUOUSLY to used_bytes by the enforcer,
        # so they must NOT also form the live overlay — that double-counts WG in
        # effective = used_bytes + overlay (usage reads ~2x while connected, then
        # "drops" to the real value on disconnect). Only PPP (L2TP/SSTP) sessions
        # are the overlay: their bytes reach used_bytes only at finalize, so the
        # overlay is where they live until then. WG still appears in `sessions`
        # and `online` below (Sessions page + online badge), just not in billing.
        if s.protocol == "WireGuard":
            continue
        b = s.rx_bytes + s.tx_bytes
        live[s.username] = live.get(s.username, 0) + b
        live_total += b
    # Single atomic dict.update (no await in between) -> readers stay consistent.
    _snap.update(
        ts=time.monotonic(),
        sessions=sessions,
        live_by_user=live,
        live_total=live_total,
        online={s.username for s in sessions},
        rx_rate_bps=rx_rate_bps,
        tx_rate_bps=tx_rate_bps,
    )


def snapshot() -> dict:
    return _snap


def age_seconds() -> float:
    return (time.monotonic() - _snap["ts"]) if _snap["ts"] else 1e9
