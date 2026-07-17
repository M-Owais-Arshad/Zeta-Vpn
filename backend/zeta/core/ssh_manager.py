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


def system_ssh_port() -> int:
    """The port the system's OpenSSH actually listens on (default 22).

    Some providers ship the VPS with sshd moved off :22 (e.g. 22022). The panel
    shows the real port in the SSH connection info and the installer opens it in
    the firewall, so a non-standard SSH port never locks the admin out. Parses
    the readable sshd config + drop-ins; best-effort, falls back to 22."""
    import glob

    files = ["/etc/ssh/sshd_config", *sorted(glob.glob("/etc/ssh/sshd_config.d/*.conf"))]
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    parts = line.strip().split()
                    # An uncommented "Port <n>" — the first one wins for display.
                    if len(parts) == 2 and parts[0].lower() == "port" and parts[1].isdigit():
                        return int(parts[1])
        except OSError:
            continue
    return 22


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
    exp = _fmt_expiry(expiry)
    direct_cmd = ["useradd", "-m", "-s", shell]
    if exp:
        direct_cmd += ["-e", exp]
    direct_cmd.append(username)
    wrapper_args = ["useradd", username, exp] if exp else ["useradd", username]
    res = services.run_privileged(wrapper_args, direct_cmd, timeout=20)
    if not res.ok:
        return res
    return set_password(username, password)


def set_password(username: str, password: str) -> services.CommandResult:
    validate_username(username)
    # `chpasswd` reads "user:password" lines on stdin — avoids exposing the
    # password in argv, but a newline/NUL in `password` would inject an extra
    # line and let the caller rewrite an arbitrary account's password (e.g.
    # root's). The API layer already rejects control characters in the
    # password field (schemas.py); this is defense-in-depth for any other
    # caller (e.g. the `zeta` CLI) that reaches this function directly.
    if "\n" in password or "\r" in password or "\x00" in password:
        return services.CommandResult(False, 1, "", "password must not contain control characters")
    try:
        import subprocess

        # Via the wrapper the username is passed as an argv the wrapper itself
        # validates (incl. the reserved-name/uid guard) and only the password
        # goes on stdin; the direct/root path keeps chpasswd's "user:password"
        # stdin format. Either way a control char can't inject a second account.
        needs_sudo = services._needs_sudo()
        argv = services.privileged_argv(["chpasswd", username], ["chpasswd"])
        stdin = f"{password}\n" if needs_sudo else f"{username}:{password}\n"
        proc = subprocess.run(
            argv,
            input=stdin,
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
    return services.run_privileged(
        ["chage", username, exp or "-1"], ["chage", "-E", exp or "-1", username], timeout=20
    )


def lock(username: str) -> services.CommandResult:
    validate_username(username)
    return services.run_privileged(
        ["usermod-lock", username], ["usermod", "-L", username], timeout=20
    )


def unlock(username: str) -> services.CommandResult:
    validate_username(username)
    return services.run_privileged(
        ["usermod-unlock", username], ["usermod", "-U", username], timeout=20
    )


def delete_account(username: str) -> services.CommandResult:
    validate_username(username)
    return services.run_privileged(
        ["userdel", username], ["userdel", "-r", username], timeout=30
    )


def kill_sessions(username: str) -> services.CommandResult:
    """Terminate all live processes/sessions for a user (enforce expiry/limits)."""
    validate_username(username)
    return services.run_privileged(
        ["pkill", username], ["pkill", "-KILL", "-u", username], timeout=15
    )


# The per-connection session process each SSH transport leaves running AS the
# authenticated account user. After auth, OpenSSH's unprivileged child (older
# `sshd`, modern `sshd-session`) and Dropbear's child both drop to the account
# user, so counting these by OWNER yields the live concurrent-session count for
# every front-end: OpenSSH, Dropbear, stunnel→Dropbear and SSH-over-WS→OpenSSH.
_SESSION_COMMS = {"sshd", "sshd-session", "dropbear"}


def online_counts() -> dict[str, int]:
    """Active SSH session count per username (best-effort via one `ps` call).

    Counts the per-connection session process each transport leaves running as
    the account user. `who`/utmp is NOT usable here: ZetaVPN accounts are
    tunnel-only (`/bin/false` shell, no PTY), so a live tunnel never records a
    utmp entry and `who` always reported 0 online — the bug this replaces. The
    root-owned listeners key to 'root', which no account matches, so they're
    ignored. Username column widened (`user:64`) so it isn't truncated to 8.
    """
    res = services.run(["ps", "-eo", "user:64,comm", "--no-headers"], timeout=10)
    if not res.ok:
        return {}
    counts: dict[str, int] = {}
    for line in res.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        user, comm = parts[0], parts[1].strip()
        if comm in _SESSION_COMMS:
            counts[user] = counts.get(user, 0) + 1
    return counts


def _peer_ip(peer: str) -> str:
    """'1.2.3.4:5678' -> '1.2.3.4'; '[2001:db8::1]:5678' -> '2001:db8::1'."""
    if peer.startswith("["):
        return peer[1:].split("]", 1)[0]
    return peer.rsplit(":", 1)[0] if ":" in peer else peer


def online_sessions() -> dict[str, list[str]]:
    """Best-effort ``{username: [source_ip, ...]}`` for live SSH sessions.

    OpenSSH keeps each connection's socket in a root-owned process, so seeing
    which pid owns a connection needs root — this goes through the read-only
    ``ssh-conns`` privileged verb (established SSH-stack sockets + the pids
    holding them). The pids are then resolved to their owning user with an
    unprivileged ``ps``, and each peer IP is attributed to the account user
    (uid >= 1000) among a connection's processes. Only direct OpenSSH/Dropbear
    expose the real client IP here; stunnel(:445)/WS sessions terminate the
    real IP at a root/loopback front-end, so their peer is 127.0.0.1 and is
    dropped (they still show as online, just without a resolvable IP).
    """
    res = services.run_privileged(
        ["ssh-conns"],
        ["ss", "-tnHp", "state", "established",
         "( sport = :22 or sport = :109 or sport = :143 )"],
        timeout=10,
    )
    if not res.ok or not res.stdout.strip():
        return {}
    ps = services.run(["ps", "-eo", "pid=,uid=,user:64="], timeout=10)
    owner: dict[str, tuple[int, str]] = {}
    for line in ps.stdout.splitlines():
        f = line.split(None, 2)
        if len(f) == 3 and f[0].isdigit() and f[1].isdigit():
            owner[f[0]] = (int(f[1]), f[2])
    out: dict[str, list[str]] = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        ip = _peer_ip(parts[3])
        if not ip or ip.startswith("127.") or ip == "::1":
            continue
        for pid in re.findall(r"pid=(\d+)", line):
            info = owner.get(pid)
            if info and info[0] >= 1000:
                ips = out.setdefault(info[1], [])
                if ip not in ips:
                    ips.append(ip)
                break
    return out


def traffic_deltas(uids: list[int]) -> dict[int, int]:
    """``{uid: bytes_transferred_since_the_last_call}`` for SSH-account uids.

    Raw SSH tunnelling has no per-user stats API like Xray, so usage is measured
    with a per-uid iptables owner-match byte counter maintained by the privileged
    ``ssh-traffic`` verb. It reads-and-zeroes, so each call returns just this
    interval's delta — the caller accumulates it into SSHAccount.used_bytes.
    Best-effort: returns ``{}`` on any failure or on a non-Linux/dev box."""
    if not uids:
        return {}
    res = services.run_privileged(["ssh-traffic", *[str(u) for u in uids]], ["true"], timeout=15)
    out: dict[int, int] = {}
    if not res.ok:
        return out
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            out[int(parts[0])] = int(parts[1])
    return out


def enforce_max_login(username: str, max_login: int) -> int:
    """Terminate the account's NEWEST sessions beyond ``max_login`` (keeping the
    oldest), returning how many were killed. This keeps a legitimate user's
    established sessions and only kicks the over-limit extras, so there's no
    thrash. Best-effort; the privileged ``killpid`` verb only kills uid>=1000
    pids, so it can never touch a system/root process."""
    if max_login <= 0:
        return 0
    res = services.run(["ps", "-o", "pid=,etimes=,comm=", "-u", username], timeout=10)
    if not res.ok:
        return 0
    sessions: list[tuple[int, str]] = []  # (elapsed_seconds, pid)
    for line in res.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2].strip() in _SESSION_COMMS:
            try:
                sessions.append((int(parts[1]), parts[0]))
            except ValueError:
                continue
    if len(sessions) <= max_login:
        return 0
    sessions.sort(key=lambda s: s[0], reverse=True)  # oldest (largest etimes) first
    killed = 0
    for _elapsed, pid in sessions[max_login:]:  # the newest excess sessions
        if services.run_privileged(["killpid", pid], ["kill", "-KILL", pid], timeout=10).ok:
            killed += 1
    return killed
