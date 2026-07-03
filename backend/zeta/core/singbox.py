"""sing-box config generation and service control.

sing-box handles the QUIC-family protocols Xray does not (Hysteria2, TUIC), so
ZetaVPN runs it as a second core alongside Xray. Inbounds with ``core == "singbox"``
are rendered here into ``/etc/sing-box/config.json``.
"""

from __future__ import annotations

import json
import os
import tempfile

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Inbound
from . import services


def _tls_block(inbound: Inbound, alpn_default: list[str]) -> dict:
    tls = (inbound.stream_settings or {}).get("tls", {})
    return {
        "enabled": True,
        "server_name": tls.get("serverName", settings.server_domain),
        "alpn": tls.get("alpn", alpn_default),
        "certificate_path": tls.get("certificateFile", ""),
        "key_path": tls.get("keyFile", ""),
    }


def build_inbound(inbound: Inbound) -> dict:
    clients = [c for c in inbound.clients if c.enabled and c.is_usable]
    listen = "::" if inbound.listen in ("0.0.0.0", "", "::") else inbound.listen

    if inbound.protocol == "hysteria2":
        obj = {
            "type": "hysteria2",
            "tag": inbound.tag,
            "listen": listen,
            "listen_port": inbound.port,
            "users": [{"name": c.email, "password": c.password or ""} for c in clients],
            "tls": _tls_block(inbound, ["h3"]),
        }
        up = (inbound.settings or {}).get("up_mbps", 0)
        down = (inbound.settings or {}).get("down_mbps", 0)
        if up:
            obj["up_mbps"] = up
        if down:
            obj["down_mbps"] = down
        return obj

    if inbound.protocol == "tuic":
        return {
            "type": "tuic",
            "tag": inbound.tag,
            "listen": listen,
            "listen_port": inbound.port,
            "users": [
                {"name": c.email, "uuid": c.uuid or "", "password": c.password or ""}
                for c in clients
            ],
            "congestion_control": (inbound.settings or {}).get("congestion_control", "bbr"),
            "tls": _tls_block(inbound, ["h3"]),
        }

    # Generic: assume protocol/settings map directly onto sing-box fields.
    obj = {"type": inbound.protocol, "tag": inbound.tag, "listen": listen, "listen_port": inbound.port}
    obj.update(inbound.settings or {})
    return obj


def generate_config(db: Session) -> dict:
    inbounds = (
        db.query(Inbound)
        .filter(Inbound.core == "singbox", Inbound.enabled.is_(True))
        .order_by(Inbound.port.asc())
        .all()
    )
    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [build_inbound(ib) for ib in inbounds],
        "outbounds": [
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ],
        "route": {
            "rules": [
                {"protocol": "bittorrent", "outbound": "block"},
                {"ip_is_private": True, "outbound": "block"},
            ]
        },
        "experimental": {
            "cache_file": {"enabled": True, "path": "/etc/sing-box/cache.db"},
        },
    }


def write_config(config: dict) -> None:
    path = settings.singbox_config
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def has_singbox_inbounds(db: Session) -> bool:
    return (
        db.query(Inbound)
        .filter(Inbound.core == "singbox", Inbound.enabled.is_(True))
        .count()
        > 0
    )


def apply(db: Session) -> services.CommandResult:
    write_config(generate_config(db))
    if not has_singbox_inbounds(db):
        # Nothing to serve; stop the core rather than run an empty listener set.
        return services.systemctl("stop", settings.singbox_service)
    return services.restart(settings.singbox_service)


def validate_config() -> services.CommandResult:
    return services.run(
        [str(settings.singbox_bin), "check", "-c", str(settings.singbox_config)], timeout=15
    )
