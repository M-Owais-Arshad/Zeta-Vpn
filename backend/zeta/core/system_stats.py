"""System metrics for the dashboard (CPU, memory, disk, network, uptime)."""

from __future__ import annotations

import time

import psutil

from ..config import settings
from . import services

_BOOT = psutil.boot_time()


def _net_totals() -> tuple[int, int]:
    io = psutil.net_io_counters()
    return io.bytes_recv, io.bytes_sent


def snapshot() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    swap = psutil.swap_memory()
    rx, tx = _net_totals()
    try:
        load1, load5, load15 = psutil.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = 0.0

    return {
        "brand": settings.brand,
        "version": settings.version,
        "cpu_percent": psutil.cpu_percent(interval=0.0),
        "cpu_count": psutil.cpu_count(),
        "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "mem": {"total": vm.total, "used": vm.used, "percent": vm.percent},
        "swap": {"total": swap.total, "used": swap.used, "percent": swap.percent},
        "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        "net": {"rx_bytes": rx, "tx_bytes": tx},
        "uptime_seconds": int(time.time() - _BOOT),
    }


_last_net: tuple[float, int, int] | None = None  # (timestamp, rx_bytes, tx_bytes)


def net_throughput() -> dict:
    """RX/TX rate in bytes per second, computed from the delta since the
    previous call — no blocking sleep.

    The dashboard polls this every 2s while open (app.js), so the interval
    between calls is itself a perfectly good sampling window; sleeping
    ``sample_seconds`` inside the request handler used to tie up a
    thread-pool worker for a full second per poll for nothing, which adds up
    fast on a small VPS if the dashboard tab is left open. First call (or a
    call more than ~5x the expected poll interval after the last one, e.g.
    after the panel restarts) has no prior sample to diff against, so it
    reports 0 rather than a meaningless huge delta over an unknown gap.
    """
    global _last_net
    now = time.monotonic()
    rx, tx = _net_totals()
    prev = _last_net
    _last_net = (now, rx, tx)
    if prev is None:
        return {"rx_bps": 0, "tx_bps": 0}
    prev_t, prev_rx, prev_tx = prev
    dt = now - prev_t
    if dt <= 0 or dt > 30:
        return {"rx_bps": 0, "tx_bps": 0}
    return {"rx_bps": int((rx - prev_rx) / dt), "tx_bps": int((tx - prev_tx) / dt)}


def services_health() -> list[dict]:
    """Status of the cores + panel-managed units for the dashboard."""
    units = [
        ("Panel", "zeta-panel"),
        ("Xray", settings.xray_service),
        ("sing-box", settings.singbox_service),
        ("SSH", "ssh"),
        ("Dropbear", "dropbear"),
        ("SSL", "stunnel4"),
        ("Nginx", "nginx"),
        ("WS Proxy", "zeta-ws"),
    ]
    # One batched `systemctl is-active` instead of 8 sequential fork/execs on
    # every ~8s dashboard poll.
    states = services.status_many([u for _, u in units])
    return [
        {"label": label, "unit": unit,
         "running": states.get(unit) == "active", "state": states.get(unit, "unknown")}
        for label, unit in units
    ]
