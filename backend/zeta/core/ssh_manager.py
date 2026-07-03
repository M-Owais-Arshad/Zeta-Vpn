"""Native SSH / tunnelling account management.

Creates real system users (used by OpenSSH, Dropbear, and the SSH-over-WS/SSL
front-ends) with an expiry date and a non-interactive shell. All privileged
actions go through :mod:`services`, so on a non-root/dev box they no-op cleanly
instead of raising.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from . import services

# Usernames we must never touch even if asked.
_RESERVED = {
    "root", "admin", "administrator", "daemon", "bin", "sys", "sync", "games",
    "man", "lp", "mail", "news", "uucp", "proxy", "www-data", "backup", "list",
    "nobody", "systemd-network", "sshd", "zeta", "ubuntu", "debian",
}
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")

# A shell that permits SSH port-forwarding/tunnelling but grants no interactive
# session — the standard setup for SSH-VPN accounts.
_TUNNEL_SHELL = "/bin/false"


def validate_username(username: str) -> None:
    if username.lower() in _RESERVED:
        raise ValueError(f"'{username}' is a reserved username")
    if not _USERNAME_RE.match(username):
        raise ValueError(
            "Username must be 3-32 chars, lowercase letters/digits/_/-, starting "
            "with a letter or underscore"
        )


def _fmt_expiry(expiry: date | datetime | None) -> str | None:
    if expiry is None:
        return None
    if isinstance(expiry, datetime):
        expiry = expiry.date()
    return expiry.strftime("%Y-%m-%d")


def create_account(
    username: str,
    password: str,
    expiry: date | datetime | None = None,
    shell: str = _TUNNEL_SHELL,
) -> services.CommandResult:
    validate_username(username)
    cmd = ["useradd", "-m", "-s", shell]
    exp = _fmt_expiry(expiry)
    if exp:
        cmd += ["-e", exp]
    cmd.append(username)
    res = services.run(cmd, timeout=20)
    if not res.ok:
        return res
    return set_password(username, password)


def set_password(username: str, password: str) -> services.CommandResult:
    validate_username(username)
    # `chpasswd` reads "user:password" on stdin — avoids exposing the password in argv.
    try:
        import subprocess

        proc = subprocess.run(
            ["chpasswd"],
            input=f"{username}:{password}\n",
            text=True,
            capture_output=True,
            timeout=20,
        )
        return services.CommandResult(proc.returncode == 0, proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError:
        return services.CommandResult(True, 0, "[skipped: chpasswd unavailable]", "")
    except Exception as exc:  # noqa: BLE001
        return services.CommandResult(False, 1, "", str(exc))


def set_expiry(username: str, expiry: date | datetime | None) -> services.CommandResult:
    validate_username(username)
    exp = _fmt_expiry(expiry)
    return services.run(["chage", "-E", exp or "-1", username], timeout=20)


def lock(username: str) -> services.CommandResult:
    validate_username(username)
    return services.run(["usermod", "-L", username], timeout=20)


def unlock(username: str) -> services.CommandResult:
    validate_username(username)
    return services.run(["usermod", "-U", username], timeout=20)


def delete_account(username: str) -> services.CommandResult:
    validate_username(username)
    return services.run(["userdel", "-r", username], timeout=30)


def kill_sessions(username: str) -> services.CommandResult:
    """Terminate all live processes/sessions for a user (enforce expiry/limits)."""
    validate_username(username)
    return services.run(["pkill", "-KILL", "-u", username], timeout=15)


def online_count(username: str) -> int:
    """Number of active SSH sessions for a user (best-effort via `who`)."""
    res = services.run(["who"], timeout=10)
    if not res.ok:
        return 0
    return sum(1 for line in res.stdout.splitlines() if line.split()[:1] == [username])
