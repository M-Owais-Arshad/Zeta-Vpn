"""Tail Xray's access log to enforce per-client concurrent-IP limits.

Xray's gRPC stats API only exposes byte counters — the access log (enabled
via ``log.access`` in xray.py's generated config) is the only place a source
IP shows up, in lines shaped like::

    2026/07/13 10:15:23 from 203.0.113.5:51234 accepted tcp:example.com:443 [vless-reality -> direct] email: user1@example.com

Each poll we read only the bytes appended since the last read (like ``tail
-f``), so cost stays proportional to recent traffic, not log size.
"""

from __future__ import annotations

import os
import re
import time

from ..config import settings

# IPv6 sources are logged bracketed, e.g. "from [2001:db8::1]:51234" (Go's
# net.JoinHostPort) — the optional [...] must be matched or every IPv6 client
# is silently invisible to limit_ip enforcement.
_LINE_RE = re.compile(r"from \[?(?P<ip>[0-9a-fA-F.:]+)\]?:\d+ accepted .*email:\s*(?P<email>\S+)")

# Tail position so we only read newly-appended bytes on each poll.
_offset = 0

# email -> {ip: last_seen_epoch_seconds}
_recent_ips: dict[str, dict[str, float]] = {}


def _read_new_lines() -> list[str]:
    global _offset
    path = settings.xray_access_log
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size < _offset:
        # Log was rotated/truncated since the last read; start from the top.
        _offset = 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(_offset)
            data = fh.read()
            _offset = fh.tell()
    except OSError:
        return []
    return data.splitlines()


def poll_concurrent_ips(active_emails: set[str] | None = None) -> dict[str, int]:
    """Update the rolling IP-activity window and return distinct-IP counts.

    Xray only writes an access-log line when a connection is *first
    accepted* — a long-lived WS/REALITY tunnel that's stayed open and kept
    transferring data for an hour never gets a second line. Relying on the
    access log alone would make a genuinely still-active client silently
    "go offline" after `ip_limit_window_seconds`. `active_emails` (clients
    whose byte counters moved this poll, from tasks.py) refreshes the
    last-seen time of their already-known IPs even with no new accept
    event, so "online" tracks actual traffic, not just connection age.

    Returns ``{email: distinct_ip_count_in_window}`` for every email seen
    recently enough to still be in the window (stale entries are pruned so
    this dict — and the per-email IP sets — can't grow without bound).
    """
    now = time.time()
    for line in _read_new_lines():
        m = _LINE_RE.search(line)
        if not m:
            continue
        email = m.group("email")
        ip = m.group("ip")
        _recent_ips.setdefault(email, {})[ip] = now

    for email in active_emails or ():
        ips = _recent_ips.get(email)
        if ips:
            for ip in ips:
                ips[ip] = now

    window_start = now - settings.ip_limit_window_seconds
    counts: dict[str, int] = {}
    for email in list(_recent_ips):
        ips = _recent_ips[email]
        for ip in [ip for ip, last_seen in ips.items() if last_seen < window_start]:
            del ips[ip]
        if ips:
            counts[email] = len(ips)
        else:
            del _recent_ips[email]
    return counts


def client_activity() -> dict[str, list[str]]:
    """Read-only snapshot: ``{email: [ip, ...]}`` for currently-active clients.

    Reflects state as of the last ``poll_concurrent_ips()`` call (from
    tasks.py's poll loop) — doesn't touch the log file or the tail offset, so
    it's safe to call from a request handler without racing the poller.
    Used to show an "online" badge + recent IPs per client in the UI.
    """
    now = time.time()
    # UI-only display window: must span at least one poll interval (timestamps
    # refresh only per poll) or an active client flaps offline between polls.
    # This is looser than the ip_limit_window used for enforcement — it never
    # relaxes the limit_ip cap, since poll_concurrent_ips() already pruned
    # _recent_ips to the short window at the last poll.
    window = max(settings.online_window_seconds, settings.stats_poll_seconds + 15)
    window_start = now - window
    return {
        email: sorted(ip for ip, last_seen in ips.items() if last_seen >= window_start)
        for email, ips in _recent_ips.items()
        if any(last_seen >= window_start for last_seen in ips.values())
    }
