"""Open/close ufw rules for admin-chosen proxy inbound ports.

Xray/sing-box will happily bind any port the admin picks in the panel, but
that's useless if the firewall never heard about it — ufw default-denies
incoming traffic (see scripts/firewall.sh), and only the fixed set of ports
known at install time (22/80/443/SSH-stack/443-udp) are opened there. Every
other port an inbound uses has to be opened dynamically, here, when the
inbound is created/moved/deleted.

Best-effort by design: a missing/disabled ufw shouldn't block inbound CRUD
(e.g. a dev box, or an admin who intentionally manages the firewall some
other way) — callers should not treat a failure here as fatal.
"""

from __future__ import annotations

import logging

from . import protocols, services

log = logging.getLogger("zeta.core.firewall")


def protos_for(protocol: str) -> set[str]:
    """Which L4 protocol(s) a proxy protocol's port actually needs opened."""
    if protocol in protocols.UDP_ONLY_PROTOCOLS:
        return {"udp"}
    try:
        spec = protocols.spec(protocol)
    except ValueError:
        return {"tcp"}
    return {"tcp", "udp"} if spec.udp else {"tcp"}


def allow(port: int, protocol: str) -> None:
    for proto in protos_for(protocol):
        res = services.run_privileged(
            ["ufw-allow", str(port), proto], ["ufw", "allow", f"{port}/{proto}"], timeout=15
        )
        if not res.ok:
            log.warning("Could not open firewall for %s/%s: %s", port, proto, res.stderr or res.stdout)


def revoke(port: int, protocol: str) -> None:
    for proto in protos_for(protocol):
        res = services.run_privileged(
            ["ufw-delete", str(port), proto], ["ufw", "delete", "allow", f"{port}/{proto}"], timeout=15
        )
        if not res.ok:
            log.warning("Could not close firewall for %s/%s: %s", port, proto, res.stderr or res.stdout)
