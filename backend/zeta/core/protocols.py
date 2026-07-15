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

# Transports nginx CAN front on the shared public port 80 (or 443 once a domain
# + cert are configured) via a per-inbound path -> loopback proxy (see
# core/nginx.py). UDP-only (QUIC) protocols never go through nginx at all.
WS_FAMILY_NETWORKS = {"ws", "httpupgrade", "xhttp"}
UDP_ONLY_PROTOCOLS = {"hysteria2", "tuic"}

# A WS-family inbound is "nginx-fronted" (xray binds a 127.0.0.1 loopback port,
# nginx routes its path on the shared public port) ONLY when it sits on a port
# nginx itself owns — :80, or :443 once TLS is configured. On ANY other port it
# binds that port DIRECTLY (xray listens on 0.0.0.0:<port> with the WS
# transport), exactly like a direct TCP inbound. This is what lets a user run
# e.g. VLESS-WS on :8080, or several WS inbounds of the same protocol on
# different ports, without disturbing the shared :80 stack — the port_key
# registry keeps every one collision-free (see compute_port_key).
FRONTED_PORTS = {80, 443}

# Sensible default public port per protocol when NOT using a WS-family
# transport (those are always 80 — see default_port()). Matches common
# convention (443 for TLS/REALITY-native protocols, traditional ports for
# the plaintext LAN-only ones) so a new inbound needs zero port guesswork,
# while staying fully editable in the API/UI for anyone who wants otherwise.
_DIRECT_DEFAULT_PORTS = {
    "vless": 443,
    # vmess's default transport is ws (nginx-fronted on :80), but if an admin
    # picks a DIRECT transport (tcp/grpc) the auto port must not be 80 — nginx
    # owns it, so it would always 409. 443 matches vless/trojan and is free on
    # the standard stack.
    "vmess": 443,
    "trojan": 443,
    "shadowsocks": 8388,
    "socks": 1080,
    "http": 8080,
    "hysteria2": 443,
    "tuic": 443,
}


def is_ws_family(network: str) -> bool:
    return network in WS_FAMILY_NETWORKS


def is_fronted(network: str, port: int) -> bool:
    """True if this WS-family inbound is served via nginx on the shared port.

    Fronted => xray binds a loopback port + nginx routes the path (needs a
    path, shares :80/:443). Not fronted => xray binds `port` directly and the
    path is optional/free (its own dedicated port). Non-WS protocols are never
    fronted.
    """
    return is_ws_family(network) and port in FRONTED_PORTS


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

    An nginx-fronted WS inbound shares the public port (:80/:443) with others,
    differentiated by the path nginx routes on -> key is "<port>:<path>". A
    DIRECT inbound (any non-WS, or a WS-family inbound on a non-fronted port
    like :8080) owns its port and collides only with others on the same port
    AND the same L4 family -> key is "<port>:<tcp|udp>". This lets multiple WS
    inbounds coexist both on shared paths (:80) and on their own ports.
    """
    if is_fronted(network, port):
        # All fronted WS inbounds share nginx's include (served on :80 and, on a
        # TLS box, :443) so the PATH must be unique across every fronted inbound
        # regardless of 80-vs-443 — key on the literal shared port, not `port`,
        # or two same-path inbounds (one :80, one :443) would emit a duplicate
        # nginx `location` and break the reload. (Also keeps the key format
        # backward-compatible with rows created before per-port direct WS.)
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
