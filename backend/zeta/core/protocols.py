"""Registry of supported protocols.

This drives both the UI (which fields to render for a given protocol) and config
generation (which core owns the protocol, what credential it needs, which
transports/securities are valid). Adding a protocol here + a branch in the
relevant core generator is all that's required to support it end to end.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

# Credential kinds a client needs for a given protocol.
CRED_UUID = "uuid"  # vless, vmess, tuic
CRED_PASSWORD = "password"  # trojan, shadowsocks, hysteria2
CRED_NONE = "none"  # socks/http (optional user), dokodemo


@dataclass(frozen=True)
class ProtocolSpec:
    key: str
    label: str
    core: str  # xray | singbox
    credential: str
    transports: tuple[str, ...]
    securities: tuple[str, ...]
    default_transport: str
    default_security: str
    udp: bool = False
    supports_flow: bool = False
    notes: str = ""
    # Default protocol `settings` block seeded into a new inbound.
    default_settings: dict = field(default_factory=dict)


REGISTRY: dict[str, ProtocolSpec] = {
    "vless": ProtocolSpec(
        key="vless",
        label="VLESS",
        core="xray",
        credential=CRED_UUID,
        transports=("tcp", "ws", "grpc", "httpupgrade", "xhttp"),
        securities=("none", "tls", "reality"),
        default_transport="tcp",
        default_security="reality",
        supports_flow=True,
        notes="Lightweight, stateless. Pair with REALITY + xtls-rprx-vision for the "
        "strongest censorship resistance.",
        default_settings={"decryption": "none"},
    ),
    "vmess": ProtocolSpec(
        key="vmess",
        label="VMess",
        core="xray",
        credential=CRED_UUID,
        transports=("tcp", "ws", "grpc", "httpupgrade"),
        securities=("none", "tls"),
        default_transport="ws",
        default_security="tls",
        notes="Legacy but universally supported; good behind a CDN over WebSocket.",
    ),
    "trojan": ProtocolSpec(
        key="trojan",
        label="Trojan",
        core="xray",
        credential=CRED_PASSWORD,
        transports=("tcp", "ws", "grpc"),
        securities=("tls", "reality"),
        default_transport="tcp",
        default_security="tls",
        notes="Masquerades as HTTPS; requires TLS (or REALITY).",
    ),
    "shadowsocks": ProtocolSpec(
        key="shadowsocks",
        label="Shadowsocks",
        core="xray",
        credential=CRED_PASSWORD,
        transports=("tcp",),
        securities=("none",),
        default_transport="tcp",
        default_security="none",
        udp=True,
        notes="Use the 2022 AEAD ciphers for modern security.",
        default_settings={"method": "2022-blake3-aes-128-gcm", "network": "tcp,udp"},
    ),
    "socks": ProtocolSpec(
        key="socks",
        label="SOCKS5",
        core="xray",
        credential=CRED_NONE,
        transports=("tcp",),
        securities=("none",),
        default_transport="tcp",
        default_security="none",
        udp=True,
        notes="Plain SOCKS5 for LAN/local use — do not expose unauthenticated.",
        default_settings={"auth": "password", "udp": True},
    ),
    "http": ProtocolSpec(
        key="http",
        label="HTTP",
        core="xray",
        credential=CRED_NONE,
        transports=("tcp",),
        securities=("none", "tls"),
        default_transport="tcp",
        default_security="none",
        notes="HTTP CONNECT proxy.",
    ),
    "hysteria2": ProtocolSpec(
        key="hysteria2",
        label="Hysteria2",
        core="singbox",
        credential=CRED_PASSWORD,
        transports=("udp",),
        securities=("tls",),
        default_transport="udp",
        default_security="tls",
        udp=True,
        notes="QUIC-based, excellent on lossy/high-latency links. Needs TLS cert.",
        default_settings={"up_mbps": 0, "down_mbps": 0},
    ),
    "tuic": ProtocolSpec(
        key="tuic",
        label="TUIC v5",
        core="singbox",
        credential=CRED_UUID,
        transports=("udp",),
        securities=("tls",),
        default_transport="udp",
        default_security="tls",
        udp=True,
        notes="QUIC-based UDP relay with 0-RTT; client needs uuid + password.",
        default_settings={"congestion_control": "bbr"},
    ),
}

# Transports nginx fronts on the shared public port 80 (or 443 once a domain
# + cert are configured) via a per-inbound path -> loopback proxy (see
# core/nginx.py) — xray itself only ever binds a 127.0.0.1 loopback port for
# these. UDP-only (QUIC) protocols never go through nginx at all.
WS_FAMILY_NETWORKS = {"ws", "httpupgrade", "xhttp"}
UDP_ONLY_PROTOCOLS = {"hysteria2", "tuic"}

# Sensible default public port per protocol when NOT using a WS-family
# transport (those are always 80 — see default_port()). Matches common
# convention (443 for TLS/REALITY-native protocols, traditional ports for
# the plaintext LAN-only ones) so a new inbound needs zero port guesswork,
# while staying fully editable in the API/UI for anyone who wants otherwise.
_DIRECT_DEFAULT_PORTS = {
    "vless": 443,
    "vmess": 80,  # default transport is ws -> nginx-fronted anyway
    "trojan": 443,
    "shadowsocks": 8388,
    "socks": 1080,
    "http": 8080,
    "hysteria2": 443,
    "tuic": 443,
}


def is_ws_family(network: str) -> bool:
    return network in WS_FAMILY_NETWORKS


def l4_family(protocol: str) -> str:
    """'tcp' or 'udp' — which port space this protocol actually occupies.

    Matters because e.g. VLESS-REALITY on 443/tcp and Hysteria2 on 443/udp
    are a normal, expected combo (same port number, independent port
    spaces) — port-conflict checks must not treat that as a collision.
    """
    return "udp" if protocol in UDP_ONLY_PROTOCOLS else "tcp"


def default_port(protocol: str, network: str | None = None) -> int:
    """Sensible default public port for a protocol (+ optional transport)."""
    net = network if network is not None else spec(protocol).default_transport
    if is_ws_family(net):
        return 80
    return _DIRECT_DEFAULT_PORTS.get(protocol, 443)


def compute_port_key(port: int, protocol: str, network: str, stream_settings: dict) -> str:
    """The real collision key for an inbound — see models.Inbound.port_key.

    WS-family inbounds all sit on :80 by design, differentiated by the nginx
    path nginx routes on, not the port number; direct inbounds collide only
    with others on the same port AND the same L4 family.
    """
    if is_ws_family(network):
        path = ((stream_settings or {}).get(network, {}) or {}).get("path") or "/"
        return f"80:{path}"
    return f"{port}:{l4_family(protocol)}"


# Shadowsocks-2022 ciphers and their required key length in bytes.
SS2022_KEY_BYTES = {
    "2022-blake3-aes-128-gcm": 16,
    "2022-blake3-aes-256-gcm": 32,
    "2022-blake3-chacha20-poly1305": 32,
}

# Legacy Shadowsocks ciphers (any password length).
SS_LEGACY_METHODS = ("aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305")


def validate_ss2022_password(method: str, password_b64: str) -> None:
    """Raise ValueError if `password_b64` isn't a valid PSK for `method`.

    Only auto-generated Shadowsocks-2022 PSKs were previously checked for the
    correct key length; an admin-supplied password bypassed this entirely and
    would make Xray refuse the whole config at reload. Applies to both the
    server-wide PSK and per-client PSKs.
    """
    key_bytes = SS2022_KEY_BYTES.get(method)
    if key_bytes is None:
        return  # not a 2022 cipher we know about; nothing to check
    try:
        decoded = base64.b64decode(password_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"password must be valid base64 for {method}") from exc
    if len(decoded) != key_bytes:
        raise ValueError(
            f"{method} requires a {key_bytes}-byte base64-encoded PSK, got {len(decoded)} bytes"
        )


def spec(protocol: str) -> ProtocolSpec:
    try:
        return REGISTRY[protocol]
    except KeyError as exc:
        raise ValueError(f"Unsupported protocol: {protocol}") from exc


def by_core(core: str) -> list[ProtocolSpec]:
    return [s for s in REGISTRY.values() if s.core == core]


def all_specs() -> list[ProtocolSpec]:
    return list(REGISTRY.values())
