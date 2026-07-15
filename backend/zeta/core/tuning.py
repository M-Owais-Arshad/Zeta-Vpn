"""Elite gaming / low-latency network tuning — start, stop, status.

All the real work (snapshot, apply, exact revert) lives in the root-owned
``scripts/zeta-tuning.sh`` (installed to ``/usr/local/sbin/zeta-tuning``); the
panel only ever triggers its three fixed sub-actions through the
``zeta-privileged`` wrapper, so the unprivileged panel user can toggle tuning
without being able to run arbitrary sysctl/tc/iptables itself.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

from . import services

TUNING_BIN = "/usr/local/sbin/zeta-tuning"


def _run(sub: str) -> services.CommandResult:
    return services.run_privileged(["tuning", sub], [TUNING_BIN, sub], timeout=60)


def status() -> dict:
    """``{"active": bool, "detail": str}`` — safe to call from a request."""
    res = services.run([TUNING_BIN, "status"], timeout=10)
    active = res.stdout.strip() == "active"
    return {"active": active, "detail": res.stdout.strip() or res.stderr.strip()}


def start() -> dict:
    res = _run("apply")
    return {"ok": res.ok, "detail": (res.stdout or res.stderr).strip(), "active": res.ok}


def stop() -> dict:
    res = _run("revert")
    return {"ok": res.ok, "detail": (res.stdout or res.stderr).strip(), "active": not res.ok}
