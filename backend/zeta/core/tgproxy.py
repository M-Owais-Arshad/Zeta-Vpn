"""Telegram MTProto proxy (mtg) — build, remove, status + client link.

The heavy lifting (binary install, secret, config, firewall, systemd unit)
lives in the root-owned ``scripts/zeta-tgproxy.sh`` (installed to
``/usr/local/sbin/zeta-tgproxy``); the panel only triggers its three fixed
sub-actions through the ``zeta-privileged`` wrapper. The proxy binds a
dedicated public port (8443/tcp by default) so it never collides with nginx,
xray, the SSH stack or the panel — see core/portcheck.py's registry.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

from . import services
from ..config import settings

TGPROXY_BIN = "/usr/local/sbin/zeta-tgproxy"
LINK_FILE = "/etc/mtg/link"


def _parse_link() -> dict:
    """Read the helper's /etc/mtg/link (secret/port/domain) and build the
    Telegram proxy URLs, filling in the public host from panel settings (mtg's
    own report shows the private EC2 IP, which is useless to clients)."""
    data: dict[str, str] = {}
    try:
        with open(LINK_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    data[k] = v
    except OSError:
        return {}
    host = settings.server_domain or settings.server_address or ""
    secret, port = data.get("secret", ""), data.get("port", "")
    if not (host and secret and port):
        return {"host": host, "port": port, "secret": secret, "domain": data.get("domain", ""),
                "needs_address": not host,
                "hint": "Set your server address/domain in Settings to generate the proxy link." if not host else ""}
    q = f"server={host}&port={port}&secret={secret}"
    return {
        "host": host, "port": port, "secret": secret, "domain": data.get("domain", ""),
        "tg_url": f"tg://proxy?{q}",
        "tme_url": f"https://t.me/proxy?{q}",
    }


def status() -> dict:
    res = services.run([TGPROXY_BIN, "status"], timeout=15)
    active = res.stdout.strip().splitlines()[:1] == ["active"]
    out = {"active": active}
    if active:
        out.update(_parse_link())
    return out


def start() -> dict:
    res = services.run_privileged(["tgproxy", "start"], [TGPROXY_BIN, "start"], timeout=120)
    out = {"ok": res.ok, "detail": (res.stdout or res.stderr).strip(), "active": res.ok}
    if res.ok:
        out.update(_parse_link())
    return out


def stop() -> dict:
    res = services.run_privileged(["tgproxy", "stop"], [TGPROXY_BIN, "stop"], timeout=60)
    return {"ok": res.ok, "detail": (res.stdout or res.stderr).strip(), "active": not res.ok}
