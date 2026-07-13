"""Detect port collisions with services ZetaVPN doesn't manage.

Xray/sing-box's own config validation (``xray run -test`` / ``sing-box
check``) only checks the config is well-formed — it does NOT try to bind
sockets, so it happily accepts an inbound on a port nginx (or sshd, or
anything else) already owns. The core then crash-loops at runtime with
"address already in use", which looks like nothing is wrong from the API's
point of view (write config, restart succeeds, no error surfaced) until the
admin notices the client can't connect. This checks the *actual* OS-level
port table before an inbound is ever written to a core's config.
"""

from __future__ import annotations


_FAMILY_PATHS = {
    "tcp": ("/proc/net/tcp", "/proc/net/tcp6"),
    "udp": ("/proc/net/udp", "/proc/net/udp6"),
}


def _listening_ports(family: str) -> set[int]:
    """Ports with something bound, from /proc/net/{tcp,tcp6} or {udp,udp6}.

    TCP and UDP are independent port spaces — e.g. VLESS-REALITY on 443/tcp
    and Hysteria2 on 443/udp is a normal, expected combination, not a
    collision, so callers must check the right table for the protocol.
    Reading the port list (not which process owns it) needs no special
    privilege on Linux — this works fine from the unprivileged 'zetavpn'
    user. Best-effort: returns an empty set (no blocking) on any platform/
    sandbox where /proc/net isn't available, e.g. non-Linux dev boxes.
    """
    ports: set[int] = set()
    for path in _FAMILY_PATHS.get(family, ()):
        try:
            with open(path, encoding="ascii", errors="ignore") as fh:
                next(fh, None)  # header line
                for line in fh:
                    fields = line.split()
                    if len(fields) < 2:
                        continue
                    local = fields[1]  # "0100007F:0050"
                    if ":" not in local:
                        continue
                    try:
                        ports.add(int(local.rsplit(":", 1)[1], 16))
                    except ValueError:
                        continue
        except OSError:
            continue
    return ports


def external_conflict(port: int, family: str, own_ports: set[int]) -> bool:
    """True if `port`/`family` is occupied by something outside our own inbounds.

    `own_ports` is every port already recorded in ZetaVPN's own DB *for the
    same L4 family* — a port an inbound already has is not a "conflict",
    it's expected, and is handled separately by the DB's own port_key
    uniqueness.
    """
    if port in own_ports:
        return False
    return port in _listening_ports(family)
