"""Xray-core config generation, service control and traffic stats.

The panel is the source of truth: inbounds/clients live in the DB and this module
renders them into ``/usr/local/etc/xray/config.json`` and reloads the core. It
also enables Xray's gRPC stats API and reads per-client / per-inbound counters
back out so the DB stays in sync.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid as _uuid

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Inbound
from . import protocols, services


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def gen_uuid() -> str:
    return str(_uuid.uuid4())


def gen_reality_keypair() -> dict:
    """Generate an X25519 keypair for REALITY via the xray binary.

    Falls back to a clearly-marked placeholder if the binary is unavailable so the
    panel still starts in a dev environment; the installer always has xray present.
    """
    res = services.run([str(settings.xray_bin), "x25519"], timeout=10)
    if res.ok and res.stdout:
        priv = pub = ""
        for line in res.stdout.splitlines():
            low = line.lower()
            if "private" in low:
                priv = line.split(":", 1)[-1].strip()
            elif "public" in low:
                pub = line.split(":", 1)[-1].strip()
        if priv and pub:
            return {"privateKey": priv, "publicKey": pub}
    return {"privateKey": "", "publicKey": ""}


def gen_short_id(length: int = 8) -> str:
    return os.urandom(length // 2).hex()


# --------------------------------------------------------------------------- #
# streamSettings
# --------------------------------------------------------------------------- #

def build_stream_settings(inbound: Inbound) -> dict:
    """Render an Xray ``streamSettings`` object from the stored inbound.

    ``inbound.stream_settings`` is a dict keyed by transport/security name, e.g.::

        {"ws": {"path": "/zeta", "host": "cdn.example.com"},
         "reality": {"dest": "www.microsoft.com:443",
                     "serverNames": ["www.microsoft.com"],
                     "privateKey": "...", "shortIds": [""]}}
    """
    ss = inbound.stream_settings or {}
    network = inbound.network
    security = inbound.security
    stream: dict = {"network": network, "security": security}

    # --- transport ---
    if network == "ws":
        cfg = ss.get("ws", {})
        stream["wsSettings"] = {
            "path": cfg.get("path", "/"),
            "host": cfg.get("host", ""),
            "headers": cfg.get("headers", {}),
        }
    elif network == "grpc":
        cfg = ss.get("grpc", {})
        stream["grpcSettings"] = {
            "serviceName": cfg.get("serviceName", "zeta"),
            "multiMode": bool(cfg.get("multiMode", False)),
        }
    elif network == "httpupgrade":
        cfg = ss.get("httpupgrade", {})
        stream["httpupgradeSettings"] = {
            "path": cfg.get("path", "/"),
            "host": cfg.get("host", ""),
        }
    elif network == "xhttp":
        cfg = ss.get("xhttp", {})
        stream["xhttpSettings"] = {
            "path": cfg.get("path", "/"),
            "host": cfg.get("host", ""),
            "mode": cfg.get("mode", "auto"),
        }
    elif network in ("tcp", "raw"):
        cfg = ss.get("tcp", {})
        stream["tcpSettings"] = {"header": cfg.get("header", {"type": "none"})}
    elif network == "kcp":
        cfg = ss.get("kcp", {})
        stream["kcpSettings"] = {
            "seed": cfg.get("seed", ""),
            "header": cfg.get("header", {"type": "none"}),
        }

    # --- security ---
    if security == "tls":
        tls = ss.get("tls", {})
        stream["tlsSettings"] = {
            "serverName": tls.get("serverName", settings.server_domain),
            "alpn": tls.get("alpn", ["h2", "http/1.1"]),
            "minVersion": tls.get("minVersion", "1.2"),
            "certificates": [
                {
                    "certificateFile": tls.get("certificateFile", ""),
                    "keyFile": tls.get("keyFile", ""),
                }
            ],
        }
    elif security == "reality":
        r = ss.get("reality", {})
        stream["realitySettings"] = {
            "show": False,
            "dest": r.get("dest", "www.microsoft.com:443"),
            "xver": r.get("xver", 0),
            "serverNames": r.get("serverNames", ["www.microsoft.com"]),
            "privateKey": r.get("privateKey", ""),
            "shortIds": r.get("shortIds", [""]),
            "fingerprint": r.get("fingerprint", "chrome"),
        }

    return stream


# --------------------------------------------------------------------------- #
# protocol settings (clients)
# --------------------------------------------------------------------------- #

def _client_entry(protocol: str, client) -> dict:  # noqa: ANN001
    if protocol == "vless":
        entry = {"id": client.uuid or gen_uuid(), "email": client.email}
        if client.flow:
            entry["flow"] = client.flow
        return entry
    if protocol == "vmess":
        return {"id": client.uuid or gen_uuid(), "email": client.email, "alterId": 0}
    if protocol == "trojan":
        return {"password": client.password or "", "email": client.email}
    if protocol == "shadowsocks":
        return {"password": client.password or "", "email": client.email}
    return {"email": client.email}


def build_inbound_settings(inbound: Inbound) -> dict:
    protocol = inbound.protocol
    base = dict(inbound.settings or {})
    clients = [c for c in inbound.clients if c.enabled and c.is_usable]

    if protocol == "vless":
        return {
            "clients": [_client_entry("vless", c) for c in clients],
            "decryption": base.get("decryption", "none"),
            "fallbacks": base.get("fallbacks", []),
        }
    if protocol == "vmess":
        return {"clients": [_client_entry("vmess", c) for c in clients]}
    if protocol == "trojan":
        return {
            "clients": [_client_entry("trojan", c) for c in clients],
            "fallbacks": base.get("fallbacks", []),
        }
    if protocol == "shadowsocks":
        method = base.get("method", "2022-blake3-aes-128-gcm")
        network = base.get("network", "tcp,udp")
        if method.startswith("2022"):
            # Multi-user: server PSK at top level, per-client passwords below.
            return {
                "method": method,
                "password": base.get("password", ""),
                "network": network,
                "clients": [_client_entry("shadowsocks", c) for c in clients],
            }
        # Legacy single-user.
        pw = clients[0].password if clients else base.get("password", "")
        return {"method": method, "password": pw, "network": network}
    if protocol == "socks":
        return {
            "auth": base.get("auth", "password"),
            "udp": base.get("udp", True),
            "accounts": [
                {"user": c.email, "pass": c.password or ""} for c in clients
            ],
        }
    if protocol == "http":
        return {
            "accounts": [{"user": c.email, "pass": c.password or ""} for c in clients]
        }
    return base


def build_inbound(inbound: Inbound) -> dict:
    # WS-family inbounds are always nginx-fronted on the shared public port
    # (core/nginx.py) — xray itself only ever binds the private loopback
    # port nginx proxies to, never the public one (which nginx already owns).
    if protocols.is_ws_family(inbound.network) and inbound.internal_port:
        listen, port = "127.0.0.1", inbound.internal_port
    else:
        listen, port = inbound.listen or "0.0.0.0", inbound.port
    obj = {
        "tag": inbound.tag,
        "listen": listen,
        "port": port,
        "protocol": inbound.protocol,
        "settings": build_inbound_settings(inbound),
        "streamSettings": build_stream_settings(inbound),
    }
    if inbound.sniffing:
        obj["sniffing"] = {"enabled": True, "destOverride": ["http", "tls", "quic"]}
    return obj


# --------------------------------------------------------------------------- #
# full config
# --------------------------------------------------------------------------- #

def _api_inbound() -> dict:
    return {
        "tag": "api",
        "listen": settings.xray_api_host,
        "port": settings.xray_api_port,
        "protocol": "dokodemo-door",
        "settings": {"address": settings.xray_api_host},
    }


def generate_config(db: Session) -> dict:
    inbounds = (
        db.query(Inbound)
        .filter(Inbound.core == "xray", Inbound.enabled.is_(True))
        .order_by(Inbound.port.asc())
        .all()
    )
    rendered = [build_inbound(ib) for ib in inbounds]
    rendered.append(_api_inbound())

    return {
        # `access` enables per-connection logging (source IP + email) — the
        # only way to see client IPs at all, since the gRPC stats API only
        # exposes byte counters. core/access_log.py tails this file to
        # enforce Client.limit_ip. loglevel stays "warning" for the general
        # error/debug stream; access logging is independent of it.
        "log": {"loglevel": "warning", "access": str(settings.xray_access_log)},
        "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "inbounds": rendered,
        "outbounds": [
            {"tag": "direct", "protocol": "freedom", "settings": {}},
            {"tag": "blocked", "protocol": "blackhole", "settings": {}},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "protocol": ["bittorrent"], "outboundTag": "blocked"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
            ],
        },
    }


def write_config(config: dict) -> None:
    path = settings.xray_config
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so a crash mid-write never leaves the core with a truncated file.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _binary_available() -> bool:
    return os.path.isfile(settings.xray_bin) and os.access(settings.xray_bin, os.X_OK)


def apply(db: Session) -> services.CommandResult:
    """Regenerate, validate, then reload Xray.

    The candidate config is checked with ``xray run -test`` *before* it's
    written to the live path or the service is restarted — a bad inbound
    (e.g. a malformed stream_settings block or a mis-sized SS2022 PSK) is
    rejected here instead of silently taking down every other inbound
    sharing the same config file.
    """
    config = generate_config(db)
    if _binary_available():
        check = validate_config(config)
        if not check.ok:
            detail = (check.stderr or check.stdout or "xray rejected the generated config").strip()
            return services.CommandResult(False, check.code, check.stdout, detail)
    write_config(config)
    return services.restart(settings.xray_service)


def validate_config(config: dict | None = None) -> services.CommandResult:
    """Ask xray to test a config file without applying it."""
    if not _binary_available():
        return services.CommandResult(True, 0, "[skipped: xray binary unavailable]", "")
    if config is not None:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(config, fh)
            return services.run([str(settings.xray_bin), "run", "-test", "-config", tmp], timeout=15)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return services.run(
        [str(settings.xray_bin), "run", "-test", "-config", str(settings.xray_config)], timeout=15
    )


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #

def query_stats(reset: bool = True) -> dict:
    """Return parsed traffic stats from Xray's gRPC API.

    Result shape::

        {"users": {email: {"up": int, "down": int}},
         "inbounds": {tag: {"up": int, "down": int}}}

    When ``reset`` is True the counters are zeroed on read so callers accumulate
    deltas into the DB.
    """
    server = f"{settings.xray_api_host}:{settings.xray_api_port}"
    cmd = [str(settings.xray_bin), "api", "statsquery", f"--server={server}"]
    if reset:
        cmd.append("-reset")
    res = services.run(cmd, timeout=15)
    out = {"users": {}, "inbounds": {}}
    if not res.ok or not res.stdout.strip():
        return out
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return out
    for item in data.get("stat", []) or []:
        name = item.get("name", "")
        value = int(item.get("value", 0) or 0)
        parts = name.split(">>>")
        if len(parts) != 4:
            continue
        scope, key, _, direction = parts
        bucket = out["users"] if scope == "user" else out["inbounds"] if scope == "inbound" else None
        if bucket is None:
            continue
        rec = bucket.setdefault(key, {"up": 0, "down": 0})
        if direction == "uplink":
            rec["up"] += value
        elif direction == "downlink":
            rec["down"] += value
    return out
