"""Host monitoring via /proc and os (no external dependencies)."""

import os
import platform
import time

from .schemas import SystemStats

_prev_cpu: tuple[int, int] | None = None  # (idle, total)
_prev_net: tuple[float, int, int] | None = None  # (ts, rx, tx)


def _read_cpu_percent() -> float:
    global _prev_cpu
    try:
        with open("/proc/stat") as fh:
            line = fh.readline()
        parts = [int(x) for x in line.split()[1:]]
    except (OSError, ValueError):
        return 0.0
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    prev = _prev_cpu
    _prev_cpu = (idle, total)
    if not prev:
        return 0.0
    didle = idle - prev[0]
    dtotal = total - prev[1]
    if dtotal <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (1 - didle / dtotal) * 100)), 1)


def _read_mem() -> tuple[int, int, float]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                val = rest.strip().split()
                if val:
                    info[key] = int(val[0]) * 1024  # kB -> bytes
    except OSError:
        return 0, 0, 0.0
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used = max(0, total - avail)
    pct = round(used / total * 100, 1) if total else 0.0
    return total, used, pct


def _read_disk(path: str = "/") -> tuple[int, int, float]:
    try:
        st = os.statvfs(path)
    except OSError:
        return 0, 0, 0.0
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = max(0, total - free)
    pct = round(used / total * 100, 1) if total else 0.0
    return total, used, pct


def _read_net_rate() -> tuple[int, int]:
    """Aggregate host NIC throughput in bits/s (excludes lo and ppp/bh)."""
    global _prev_net
    rx = tx = 0
    try:
        with open("/proc/net/dev") as fh:
            lines = fh.readlines()[2:]
        for line in lines:
            name, _, data = line.partition(":")
            name = name.strip()
            if name == "lo" or name.startswith(("ppp", "bh-")):
                continue
            fields = data.split()
            if len(fields) >= 9:
                rx += int(fields[0])
                tx += int(fields[8])
    except (OSError, ValueError):
        return 0, 0
    now = time.monotonic()
    prev = _prev_net
    _prev_net = (now, rx, tx)
    if not prev:
        return 0, 0
    dt = now - prev[0]
    if dt <= 0 or rx < prev[1] or tx < prev[2]:
        return 0, 0
    return int((rx - prev[1]) * 8 / dt), int((tx - prev[2]) * 8 / dt)


def _uptime() -> int:
    try:
        with open("/proc/uptime") as fh:
            return int(float(fh.read().split()[0]))
    except (OSError, ValueError):
        return 0


def collect() -> SystemStats:
    mem_total, mem_used, mem_pct = _read_mem()
    disk_total, disk_used, disk_pct = _read_disk()
    net_rx, net_tx = _read_net_rate()
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0
    return SystemStats(
        cpu_percent=_read_cpu_percent(),
        mem_total=mem_total,
        mem_used=mem_used,
        mem_percent=mem_pct,
        disk_total=disk_total,
        disk_used=disk_used,
        disk_percent=disk_pct,
        net_rx_bps=net_rx,
        net_tx_bps=net_tx,
        load_1=round(load1, 2),
        load_5=round(load5, 2),
        load_15=round(load15, 2),
        uptime_seconds=_uptime(),
        hostname=platform.node(),
        kernel=platform.release(),
    )
