"""Multi-format subscription output: Clash/Mihomo YAML and sing-box JSON.

Complements the plain base64 subscription in :mod:`links`. The subscription
endpoint picks a format from ``?target=`` or the client's User-Agent so a single
URL serves v2rayN/Hiddify (base64), Clash Verge/Mihomo (YAML) and NekoBox/sing-box
(JSON) alike.
"""

from __future__ import annotations

import json

import yaml

from ..config import settings
from ..models import Client, Inbound


def _sni(ib: Inbound, address: str) -> str:
    ss = ib.stream_settings or {}
    if ib.security == "tls":
        return ss.get("tls", {}).get("serverName", address)
    if ib.security == "reality":
        names = ss.get("reality", {}).get("serverNames", [])
        return names[0] if names else address
    return address


def _name(ib: Inbound, c: Client) -> str:
    return f"{settings.brand}-{ib.remark or ib.tag}-{c.email}"


def _fingerprint(ib: Inbound) -> str:
    ss = ib.stream_settings or {}
    if ib.security == "reality":
        return ss.get("reality", {}).get("fingerprint", "chrome")
    return ss.get("tls", {}).get("fingerprint", "chrome")


# --------------------------------------------------------------------------- #
# Clash / Mihomo (Clash.Meta) YAML
# --------------------------------------------------------------------------- #

def _clash_transport(ib: Inbound) -> dict:
    ss = ib.stream_settings or {}
    opts: dict = {}
    if ib.network in ("ws", "httpupgrade"):
        cfg = ss.get(ib.network, {})
        opts["network"] = "ws"
        ws = {"path": cfg.get("path", "/")}
        if cfg.get("host"):
            ws["headers"] = {"Host": cfg["host"]}
        if ib.network == "httpupgrade":
            ws["v2ray-http-upgrade"] = True  # Mihomo flag for HTTPUpgrade transport
        opts["ws-opts"] = ws
    elif ib.network == "grpc":
        opts["network"] = "grpc"
        opts["grpc-opts"] = {"grpc-service-name": ss.get("grpc", {}).get("serviceName", "zeta")}
    return opts


def _clash_proxy(ib: Inbound, c: Client, address: str) -> dict | None:
    # Clash/Mihomo has no representation for the XHTTP transport — skip those
    # inbounds rather than emit a proxy that would silently fall back to TCP.
    if ib.network == "xhttp":
        return None
    name = _name(ib, c)
    sni = _sni(ib, address)
    tls = ib.security in ("tls", "reality")
    base = {"name": name, "server": address, "port": ib.port, "udp": True}

    if ib.protocol == "vless":
        p = {**base, "type": "vless", "uuid": c.uuid, "tls": tls, "servername": sni,
             "client-fingerprint": _fingerprint(ib), **_clash_transport(ib)}
        if c.flow:
            p["flow"] = c.flow
        if ib.security == "reality":
            r = (ib.stream_settings or {}).get("reality", {})
            p["reality-opts"] = {"public-key": r.get("publicKey", ""),
                                 "short-id": (r.get("shortIds", [""]) or [""])[0]}
        return p
    if ib.protocol == "vmess":
        return {**base, "type": "vmess", "uuid": c.uuid, "alterId": 0, "cipher": "auto",
                "tls": tls, "servername": sni if tls else "", **_clash_transport(ib)}
    if ib.protocol == "trojan":
        return {**base, "type": "trojan", "password": c.password, "sni": sni, **_clash_transport(ib)}
    if ib.protocol == "shadowsocks":
        st = ib.settings or {}
        pw = c.password or ""
        if str(st.get("method", "")).startswith("2022") and st.get("password"):
            pw = f"{st['password']}:{pw}"
        return {**base, "type": "ss", "cipher": st.get("method", "aes-256-gcm"), "password": pw}
    if ib.protocol == "hysteria2":
        return {**base, "type": "hysteria2", "password": c.password, "sni": sni,
                "alpn": ["h3"], "skip-cert-verify": True}
    if ib.protocol == "tuic":
        return {**base, "type": "tuic", "uuid": c.uuid, "password": c.password, "sni": sni,
                "alpn": ["h3"], "congestion-controller": "bbr", "skip-cert-verify": True}
    return None


def to_clash_yaml(clients: list[tuple[Inbound, Client]], address: str) -> str:
    proxies = [p for p in (_clash_proxy(ib, c, address) for ib, c in clients) if p]
    names = [p["name"] for p in proxies]
    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxies,
        "proxy-groups": [
            {"name": settings.brand, "type": "select", "proxies": ["AUTO", *names]},
            {"name": "AUTO", "type": "url-test", "proxies": names or ["DIRECT"],
             "url": "http://www.gstatic.com/generate_204", "interval": 300},
        ],
        "rules": [
            "GEOIP,private,DIRECT,no-resolve",
            f"MATCH,{settings.brand}",
        ],
    }
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


# --------------------------------------------------------------------------- #
# sing-box JSON (client config)
# --------------------------------------------------------------------------- #

def _singbox_transport(ib: Inbound) -> dict:
    ss = ib.stream_settings or {}
    if ib.network == "ws":
        cfg = ss.get("ws", {})
        t = {"type": "ws", "path": cfg.get("path", "/")}
        if cfg.get("host"):
            t["headers"] = {"Host": cfg["host"]}
        return {"transport": t}
    if ib.network == "grpc":
        return {"transport": {"type": "grpc", "service_name": ss.get("grpc", {}).get("serviceName", "zeta")}}
    return {}


def _singbox_tls(ib: Inbound, address: str) -> dict:
    if ib.security not in ("tls", "reality"):
        return {}
    tls = {"enabled": True, "server_name": _sni(ib, address), "utls": {"enabled": True, "fingerprint": _fingerprint(ib)}}
    if ib.security == "reality":
        r = (ib.stream_settings or {}).get("reality", {})
        tls["reality"] = {"enabled": True, "public_key": r.get("publicKey", ""),
                          "short_id": (r.get("shortIds", [""]) or [""])[0]}
    elif ib.protocol in ("hysteria2", "tuic"):
        tls["alpn"] = ["h3"]
    # Hysteria2/TUIC terminate on the raw IP with a self-signed cert + camouflage
    # SNI, so the client must skip verification or the QUIC handshake aborts.
    if ib.protocol in ("hysteria2", "tuic"):
        tls["insecure"] = True
    return {"tls": tls}


def _singbox_outbound(ib: Inbound, c: Client, address: str) -> dict | None:
    tag = _name(ib, c)
    base = {"tag": tag, "server": address, "server_port": ib.port}
    if ib.protocol == "vless":
        o = {"type": "vless", **base, "uuid": c.uuid, **_singbox_transport(ib), **_singbox_tls(ib, address)}
        if c.flow:
            o["flow"] = c.flow
        return o
    if ib.protocol == "vmess":
        return {"type": "vmess", **base, "uuid": c.uuid, "alter_id": 0, "security": "auto",
                **_singbox_transport(ib), **_singbox_tls(ib, address)}
    if ib.protocol == "trojan":
        return {"type": "trojan", **base, "password": c.password, **_singbox_transport(ib), **_singbox_tls(ib, address)}
    if ib.protocol == "shadowsocks":
        st = ib.settings or {}
        pw = c.password or ""
        if str(st.get("method", "")).startswith("2022") and st.get("password"):
            pw = f"{st['password']}:{pw}"
        return {"type": "shadowsocks", **base, "method": st.get("method", "aes-256-gcm"), "password": pw}
    if ib.protocol == "hysteria2":
        return {"type": "hysteria2", **base, "password": c.password, **_singbox_tls(ib, address)}
    if ib.protocol == "tuic":
        return {"type": "tuic", **base, "uuid": c.uuid, "password": c.password,
                "congestion_control": "bbr", **_singbox_tls(ib, address)}
    return None


def to_singbox_json(clients: list[tuple[Inbound, Client]], address: str) -> str:
    outbounds = [o for o in (_singbox_outbound(ib, c, address) for ib, c in clients) if o]
    tags = [o["tag"] for o in outbounds]
    config = {
        "log": {"level": "warn"},
        "outbounds": [
            {"type": "selector", "tag": settings.brand, "outbounds": ["auto", *tags], "default": "auto"},
            {"type": "urltest", "tag": "auto", "outbounds": tags or ["direct"],
             "url": "http://www.gstatic.com/generate_204", "interval": "5m"},
            *outbounds,
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ],
        "route": {"rules": [{"ip_is_private": True, "outbound": "direct"}], "final": settings.brand},
    }
    return json.dumps(config, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Format negotiation
# --------------------------------------------------------------------------- #

def detect_format(target: str | None, user_agent: str) -> str:
    if target:
        t = target.lower()
        if t in ("clash", "clashmeta", "mihomo", "meta"):
            return "clash"
        if t in ("singbox", "sing-box", "sfa", "sfi"):
            return "singbox"
        if t in ("v2ray", "base64", "raw"):
            return "base64"
    ua = (user_agent or "").lower()
    if any(k in ua for k in ("clash", "mihomo", "meta", "stash", "flclash")):
        return "clash"
    if any(k in ua for k in ("sing-box", "singbox", "nekobox", "nekoray", "hiddify")):
        # Hiddify understands base64; sing-box native clients prefer JSON.
        return "singbox" if "sing" in ua or "neko" in ua else "base64"
    return "base64"
