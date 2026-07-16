"""Thin wrapper around the system service manager (systemd) and subprocesses.

All service control funnels through here so the rest of the code stays testable
and so a non-systemd environment (dev box, container) degrades gracefully instead
of crashing.
"""

from __future__ import annotations

import os
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


PRIVILEGED_WRAPPER = "/usr/local/sbin/zeta-privileged"


def _needs_sudo() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() != 0


def privileged_argv(wrapper_args: list[str], direct_cmd: list[str]) -> list[str]:
    """Resolve the argv for a privileged action without running it.

    Same routing as `run_privileged()`, exposed separately for the one
    caller (ssh_manager.set_password) that needs to pipe input via stdin
    rather than go through `run()`.
    """
    if _needs_sudo():
        sudo_bin = str(shutil.which("sudo") or "sudo")
        return [sudo_bin, "-n", PRIVILEGED_WRAPPER, *wrapper_args]
    return direct_cmd


def run_privileged(wrapper_args: list[str], direct_cmd: list[str], **kwargs) -> "CommandResult":
    """Run a privileged OS action, via the zeta-privileged wrapper when non-root.

    The panel runs as the unprivileged `zetavpn` system user in production
    (see systemd/zeta-panel.service); a handful of operations — managing SSH
    tunnel accounts and reloading the proxy core services — genuinely need
    root. sudo's own argument matching can't safely express "useradd with
    any username/date but nothing else" (modern sudo rejects wildcards in
    command *arguments*, only the command path may glob), so the sudoers
    rule (installed by install.sh) grants exactly one fixed-path command —
    `zeta-privileged` — which does the argument validation itself in one
    auditable place. A bug in the (much larger) HTTP-facing app surface
    still can't be leveraged into arbitrary-file-write-as-root the way it
    could when the process itself was root (the CVE-2026-55477 pattern).
    When already root (dev box, container), skip the wrapper and run the
    direct command — the wrapper doesn't exist there anyway.
    """
    return run(privileged_argv(wrapper_args, direct_cmd), **kwargs)


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
    # `is-active`/`status` are unprivileged reads and deliberately skip the
    # wrapper so dashboard polling works even if the sudoers rule is ever
    # missing/broken. Mutating actions need root.
    if action in ("is-active", "status", "is-enabled", "show"):
        return run(["systemctl", action, unit], timeout=45)
    return run_privileged(["systemctl", action, unit], ["systemctl", action, unit], timeout=45)


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


def status_many(units: list[str]) -> dict[str, str]:
    """Active-state of several units in ONE `systemctl is-active` call, instead
    of a fork/exec per unit. systemctl prints one state per line in argument
    order (and exits non-zero if any unit isn't active, but run() still captures
    stdout); a missing/short line maps to 'unknown'."""
    if not units:
        return {}
    if not _systemctl_available():
        return {u: "unknown" for u in units}
    res = run(["systemctl", "is-active", *units], timeout=15)
    lines = res.stdout.splitlines()
    return {
        u: (lines[i].strip() if i < len(lines) and lines[i].strip() else "unknown")
        for i, u in enumerate(units)
    }
