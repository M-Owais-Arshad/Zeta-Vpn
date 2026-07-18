"""Client share-link (URI) and subscription generation.

Produces the standard ``vless://`` / ``vmess://`` / ``trojan://`` / ``ss://`` /
``hysteria2://`` / ``tuic://`` URIs understood by v2rayN, v2rayNG, NekoBox,
Hiddify, Streisand, etc., plus an aggregate base64 subscription and QR codes.
"""

from __future__ import annotations

import base64
import io
import json
from urllib.parse import quote, urlencode

from ..config import settings
from ..models import Client, Inbound
from . import protocols


def _b64(data: str) -> str:
    return base64.b64encode(data.encode("utf-8")).decode("ascii")


def _b64url(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode("utf-8")).decode("ascii").rstrip("=")


def _address(override: str | None = None) -> str:
    return override or settings.server_domain or settings.server_address or "127.0.0.1"


def _address_for(inbound: Inbound, override: str | None = None) -> str:
    """The host a client should dial for THIS inbound.

    Only nginx-fronted WS-family inbounds (the shared :80/:443 path-routed ones)
    ride a CDN, so they use the domain. Everything else — REALITY, direct TLS,
    raw TCP, Hysteria2/TUIC (QUIC/UDP) — needs the RAW origin: a Cloudflare-proxied
    domain terminates TLS / drops UDP and cannot carry them, which is exactly why
    REALITY on the domain "fails to connect". Those get the server IP (falling
    back to the domain, then loopback, only if no IP is configured)."""
    if override:
        return override
    if protocols.is_fronted(inbound.network, inbound.port) and settings.server_domain:
        return settings.server_domain
    return settings.server_address or settings.server_domain or "127.0.0.1"


def _stream_query(inbound: Inbound) -> dict:
    """Common transport/security query params shared by VLESS & Trojan URIs."""
    ss = inbound.stream_settings or {}
    q: dict[str, str] = {"type": inbound.network, "security": inbound.security}

    if inbound.network == "ws":
        cfg = ss.get("ws", {})
        q["path"] = cfg.get("path", "/")
        if cfg.get("host"):
            q["host"] = cfg["host"]
    elif inbound.network == "grpc":
        cfg = ss.get("grpc", {})
        q["serviceName"] = cfg.get("serviceName", "zeta")
        q["mode"] = "multi" if cfg.get("multiMode") else "gun"
    elif inbound.network in ("httpupgrade", "xhttp"):
        cfg = ss.get(inbound.network, {})
        q["path"] = cfg.get("path", "/")
        if cfg.get("host"):
            q["host"] = cfg["host"]
        if inbound.network == "xhttp":
            q["mode"] = cfg.get("mode", "auto")

    if inbound.security == "tls":
        tls = ss.get("tls", {})
        q["sni"] = tls.get("serverName", _address())
        q["fp"] = tls.get("fingerprint", "chrome")
        if tls.get("alpn"):
            q["alpn"] = ",".join(tls["alpn"])
    elif inbound.security == "reality":
        r = ss.get("reality", {})
        names = r.get("serverNames", [])
        q["sni"] = names[0] if names else ""
        q["fp"] = r.get("fingerprint", "chrome")
        q["pbk"] = r.get("publicKey", "")
        sids = r.get("shortIds", [""])
        q["sid"] = sids[0] if sids else ""
        q["spx"] = "/"
    return q


def _remark(inbound: Inbound, client: Client) -> str:
    label = client.email or "client"
    return f"{settings.brand}-{inbound.remark or inbound.tag}-{label}"


def _vless(inbound: Inbound, client: Client, address: str) -> str:
    q = _stream_query(inbound)
    q["encryption"] = "none"
    if client.flow:
        q["flow"] = client.flow
    query = urlencode({k: v for k, v in q.items() if v != ""}, safe="/")
    return f"vless://{client.uuid}@{address}:{inbound.port}?{query}#{quote(_remark(inbound, client))}"


def _vmess(inbound: Inbound, client: Client, address: str) -> str:
    ss = inbound.stream_settings or {}
    host = path = ""
    net = inbound.network
    if net == "ws":
        cfg = ss.get("ws", {})
        path, host = cfg.get("path", "/"), cfg.get("host", "")
    elif net == "grpc":
        path = ss.get("grpc", {}).get("serviceName", "zeta")
    elif net in ("httpupgrade", "xhttp"):
        cfg = ss.get(net, {})
        path, host = cfg.get("path", "/"), cfg.get("host", "")

    tls = ss.get("tls", {})
    obj = {
        "v": "2",
        "ps": _remark(inbound, client),
        "add": address,
        "port": str(inbound.port),
        "id": client.uuid,
        "aid": "0",
        "scy": "auto",
        "net": net,
        "type": "none",
        "host": host,
        "path": path,
        "tls": "tls" if inbound.security == "tls" else "",
        "sni": tls.get("serverName", "") if inbound.security == "tls" else "",
        "alpn": ",".join(tls.get("alpn", [])) if inbound.security == "tls" else "",
        "fp": tls.get("fingerprint", "chrome") if inbound.security == "tls" else "",
    }
    return "vmess://" + _b64(json.dumps(obj, ensure_ascii=False))


def _trojan(inbound: Inbound, client: Client, address: str) -> str:
    q = _stream_query(inbound)
    query = urlencode({k: v for k, v in q.items() if v != ""}, safe="/")
    return f"trojan://{client.password}@{address}:{inbound.port}?{query}#{quote(_remark(inbound, client))}"


def _shadowsocks(inbound: Inbound, client: Client, address: str) -> str:
    st = inbound.settings or {}
    method = st.get("method", "aes-256-gcm")
    password = client.password or ""
    if method.startswith("2022") and st.get("password"):
        password = f"{st['password']}:{password}"  # server PSK : user PSK
    userinfo = _b64url(f"{method}:{password}")
    return f"ss://{userinfo}@{address}:{inbound.port}#{quote(_remark(inbound, client))}"


def _tls_insecure() -> str:
    """"1" (skip cert verify) unless a real domain is configured. Without a domain
    the cert is the self-signed fallback, so a client verifying it aborts the
    handshake ("io: read/write on closed pipe") — Hysteria2/TUIC clients must skip
    verification there. A real Let's Encrypt cert (domain set) verifies normally."""
    return "0" if settings.server_domain else "1"


def _tls_sni(inbound: Inbound, address: str) -> str:
    tls = (inbound.stream_settings or {}).get("tls", {})
    return tls.get("serverName") or settings.server_domain or address


def _hysteria2(inbound: Inbound, client: Client, address: str) -> str:
    q = {"sni": _tls_sni(inbound, address), "insecure": _tls_insecure()}
    return (
        f"hysteria2://{quote(client.password or '')}@{address}:{inbound.port}"
        f"?{urlencode(q)}#{quote(_remark(inbound, client))}"
    )


def _tuic(inbound: Inbound, client: Client, address: str) -> str:
    q = {
        "sni": _tls_sni(inbound, address),
        "congestion_control": (inbound.settings or {}).get("congestion_control", "bbr"),
        "alpn": "h3",
        "allow_insecure": _tls_insecure(),
    }
    return (
        f"tuic://{client.uuid}:{quote(client.password or '')}@{address}:{inbound.port}"
        f"?{urlencode(q)}#{quote(_remark(inbound, client))}"
    )


_BUILDERS = {
    "vless": _vless,
    "vmess": _vmess,
    "trojan": _trojan,
    "shadowsocks": _shadowsocks,
    "hysteria2": _hysteria2,
    "tuic": _tuic,
}


def client_link(inbound: Inbound, client: Client, address: str | None = None) -> str:
    builder = _BUILDERS.get(inbound.protocol)
    if builder is None:
        return ""
    return builder(inbound, client, _address_for(inbound, address))


def subscription_for(clients: list[tuple[Inbound, Client]], address: str | None = None) -> str:
    """Return a base64-encoded newline-joined subscription for the given clients."""
    links = [client_link(ib, c, address) for ib, c in clients]
    links = [l for l in links if l]
    return _b64("\n".join(links))


def qr_data_url(text: str) -> str:
    """Return a PNG QR code for ``text`` as a ``data:`` URL (lazy import qrcode)."""
    import qrcode

    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
