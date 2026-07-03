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


def net_throughput(sample_seconds: float = 1.0) -> dict:
    """Instantaneous RX/TX rate in bytes per second (blocks ``sample_seconds``)."""
    rx0, tx0 = _net_totals()
    time.sleep(sample_seconds)
    rx1, tx1 = _net_totals()
    dt = sample_seconds or 1.0
    return {"rx_bps": int((rx1 - rx0) / dt), "tx_bps": int((tx1 - tx0) / dt)}


def services_health() -> list[dict]:
    """Status of the cores + panel-managed units for the dashboard."""
    units = [
        ("Panel", "zeta-panel"),
        ("Xray", settings.xray_service),
        ("sing-box", settings.singbox_service),
        ("SSH", "ssh"),
        ("Dropbear", "dropbear"),
        ("Nginx", "nginx"),
        ("WS Proxy", "zeta-ws"),
    ]
    out = []
    for label, unit in units:
        st = services.status(unit)
        out.append({"label": label, "unit": unit, "running": st["running"], "state": st["active"]})
    return out
