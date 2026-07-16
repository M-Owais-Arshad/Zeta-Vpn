"""sing-box config generation and service control.

sing-box handles the QUIC-family protocols Xray does not (Hysteria2, TUIC), so
ZetaVPN runs it as a second core alongside Xray. Inbounds with ``core == "singbox"``
are rendered here into ``/etc/sing-box/config.json``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Inbound
from . import services

log = logging.getLogger("zeta.core.singbox")

# Running per-connection (upload, download) totals last seen via the Clash
# API, keyed by connection id. Used to turn sing-box's live-connection
# snapshot into incremental deltas the same way xray.query_stats() does.
_last_seen: dict[str, tuple[int, int]] = {}

# user -> {ip: last_seen_epoch_seconds} — mirrors core/access_log.py's
# tracker so the "online + IPs" UI feature works the same way for
# sing-box-served clients (Hysteria2/TUIC) as it does for Xray's.
# Mutated by the poller thread (query_stats) and read by request threads
# (client_activity); guarded by _lock so the reader can't iterate mid-mutation.
_recent_ips: dict[str, dict[str, float]] = {}
_lock = threading.Lock()


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


def build_inbounds(inbound: Inbound) -> list[dict]:
    """The primary sing-box inbound plus one clone per extra_port (same users +
    TLS on a different listen_port), so a client works on every listed port."""
    primary = build_inbound(inbound)
    out = [primary]
    for p in (inbound.extra_ports or []):
        clone = dict(primary)
        clone["tag"] = f"{inbound.tag}@{p}"
        clone["listen_port"] = p
        out.append(clone)
    return out


def generate_config(db: Session) -> dict:
    inbounds = (
        db.query(Inbound)
        .filter(Inbound.core == "singbox", Inbound.enabled.is_(True))
        .order_by(Inbound.port.asc())
        .all()
    )
    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [x for ib in inbounds for x in build_inbounds(ib)],
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
            # Loopback-only Clash API — the panel's sole way to read per-user
            # traffic back out of sing-box (it has no gRPC stats service like
            # Xray). Never proxied by nginx; secret is panel-generated.
            "clash_api": {
                "external_controller": f"{settings.singbox_clash_api_host}:{settings.singbox_clash_api_port}",
                "secret": settings.singbox_clash_api_secret,
                "default_mode": "rule",
            },
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


def _binary_available() -> bool:
    return os.path.isfile(settings.singbox_bin) and os.access(settings.singbox_bin, os.X_OK)


def apply(db: Session) -> services.CommandResult:
    """Regenerate, validate, then reload sing-box.

    Mirrors xray.apply(): the candidate config is checked with
    ``sing-box check`` before it's written to the live path, so one bad
    inbound can't silently take down every sing-box listener.
    """
    config = generate_config(db)
    if not has_singbox_inbounds(db):
        # Nothing to serve; write the (empty) config, then stop AND disable the
        # core so it doesn't idle-waste ~35MB RSS on every reboot of an
        # Xray-only / SSH-only box (the install leaves it disabled; the panel
        # owns its lifecycle from here).
        write_config(config)
        services.systemctl("disable", settings.singbox_service)
        return services.systemctl("stop", settings.singbox_service)
    if _binary_available():
        check = validate_config(config)
        if not check.ok:
            detail = (check.stderr or check.stdout or "sing-box rejected the generated config").strip()
            return services.CommandResult(False, check.code, check.stdout, detail)
    write_config(config)
    # Enable so a legitimately-configured sing-box survives reboots (the install
    # ships it disabled to avoid the empty-config idle waste).
    services.systemctl("enable", settings.singbox_service)
    return services.restart(settings.singbox_service)


def validate_config(config: dict | None = None) -> services.CommandResult:
    """Ask sing-box to check a config without applying it."""
    if not _binary_available():
        return services.CommandResult(True, 0, "[skipped: sing-box binary unavailable]", "")
    if config is not None:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(config, fh)
            return services.run([str(settings.singbox_bin), "check", "-c", tmp], timeout=15)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return services.run(
        [str(settings.singbox_bin), "check", "-c", str(settings.singbox_config)], timeout=15
    )


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #

def query_stats(reset: bool = True) -> dict:
    """Return parsed per-user traffic deltas from sing-box's Clash API.

    sing-box has no gRPC StatsService like Xray; the Clash API's
    ``/connections`` endpoint reports each *live* connection's cumulative
    upload/download plus a ``metadata.user`` field (populated for
    multi-user inbounds — hysteria2, tuic, shadowsocks) identifying which
    configured user it belongs to. We diff against the totals seen on the
    previous poll to get a delta, the same shape ``xray.query_stats()``
    returns, so ``tasks.py`` can treat both cores identically.

    Best-effort: if sing-box isn't running or the Clash API is unreachable
    (e.g. dev box, core just restarted), returns empty stats rather than
    raising — matches the resilience of the rest of the stats pipeline.
    """
    out = {"users": {}, "inbounds": {}}
    url = f"http://{settings.singbox_clash_api_host}:{settings.singbox_clash_api_port}/connections"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {settings.singbox_clash_api_secret}"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("sing-box Clash API unreachable: %s", exc)
        return out

    seen_ids: set[str] = set()
    now = time.time()
    for conn in data.get("connections", []) or []:
        conn_id = conn.get("id")
        metadata = conn.get("metadata") or {}
        user = metadata.get("user")
        upload = int(conn.get("upload", 0) or 0)
        download = int(conn.get("download", 0) or 0)
        if not conn_id or not user:
            continue
        seen_ids.add(conn_id)
        prev_up, prev_down = _last_seen.get(conn_id, (0, 0))
        delta_up = max(0, upload - prev_up)
        delta_down = max(0, download - prev_down)
        _last_seen[conn_id] = (upload, download)
        if delta_up or delta_down:
            rec = out["users"].setdefault(user, {"up": 0, "down": 0})
            rec["up"] += delta_up
            rec["down"] += delta_down
            # Fold the same delta into the per-inbound bucket so Hysteria2/TUIC
            # inbounds record traffic. xray.query_stats populates out["inbounds"];
            # without this the QUIC inbounds AND the dashboard proxy_traffic total
            # (SUM(Inbound.up/down)) permanently read 0. The Clash API carries the
            # inbound tag in connection metadata; "<tag>@<port>" folding in
            # tasks.py still applies.
            inbound_tag = metadata.get("inbound") or metadata.get("inboundTag")
            if inbound_tag:
                irec = out["inbounds"].setdefault(inbound_tag, {"up": 0, "down": 0})
                irec["up"] += delta_up
                irec["down"] += delta_down
        source_ip = metadata.get("sourceIP")
        if source_ip:
            with _lock:
                _recent_ips.setdefault(user, {})[source_ip] = now

    if reset:
        # Drop bookkeeping for connections that have since closed so the dict
        # doesn't grow unbounded; their final delta was already captured on
        # the last poll that still listed them.
        for conn_id in list(_last_seen):
            if conn_id not in seen_ids:
                del _last_seen[conn_id]
        window_start = now - settings.ip_limit_window_seconds
        with _lock:
            for user in list(_recent_ips):
                ips = _recent_ips[user]
                for ip in [ip for ip, last_seen in ips.items() if last_seen < window_start]:
                    del ips[ip]
                if not ips:
                    del _recent_ips[user]

    return out


def client_activity() -> dict[str, list[str]]:
    """Read-only snapshot: ``{user: [ip, ...]}`` for currently-active clients.

    Mirrors ``core/access_log.client_activity()``; reflects state as of the
    last ``query_stats()`` poll rather than hitting the Clash API again.

    Uses the looser UI display window so the "online" badge doesn't flap
    between polls. Safe for the limit_ip path too: tasks.py calls this only
    right after ``query_stats(reset=True)`` has already pruned ``_recent_ips``
    to the short ip_limit window, so no stale IP survives to be over-counted.
    """
    now = time.time()
    window = max(settings.online_window_seconds, settings.stats_poll_seconds * 2)
    window_start = now - window
    with _lock:
        snapshot = {u: dict(ips) for u, ips in _recent_ips.items()}
    return {
        user: sorted(ip for ip, last_seen in ips.items() if last_seen >= window_start)
        for user, ips in snapshot.items()
        if any(last_seen >= window_start for last_seen in ips.values())
    }
