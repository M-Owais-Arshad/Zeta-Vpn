"""Thin wrapper around the system service manager (systemd) and subprocesses.

All service control funnels through here so the rest of the code stays testable
and so a non-systemd environment (dev box, container) degrades gracefully instead
of crashing.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from ..config import settings


@dataclass
class CommandResult:
    ok: bool
    code: int
    stdout: str
    stderr: str


def run(cmd: list[str], timeout: int = 30, check: bool = False) -> CommandResult:
    """Run a command, capturing output. Never raises unless ``check`` is set."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )
        return CommandResult(proc.returncode == 0, proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError as exc:
        return CommandResult(False, 127, "", str(exc))
    except subprocess.TimeoutExpired:
        return CommandResult(False, 124, "", f"timeout after {timeout}s")
    except subprocess.CalledProcessError as exc:
        return CommandResult(False, exc.returncode, exc.stdout or "", exc.stderr or "")


def _systemctl_available() -> bool:
    return settings.service_manager == "systemd" and shutil.which("systemctl") is not None


def systemctl(action: str, unit: str) -> CommandResult:
    if not _systemctl_available():
        return CommandResult(True, 0, f"[skipped: no systemd] {action} {unit}", "")
    return run(["systemctl", action, unit], timeout=45)


def restart(unit: str) -> CommandResult:
    return systemctl("restart", unit)


def reload_or_restart(unit: str) -> CommandResult:
    return systemctl("reload-or-restart", unit)


def status(unit: str) -> dict:
    if not _systemctl_available():
        return {"unit": unit, "active": "unknown", "running": False}
    res = run(["systemctl", "is-active", unit], timeout=10)
    active = res.stdout.strip() or res.stderr.strip() or "unknown"
    return {"unit": unit, "active": active, "running": active == "active"}
