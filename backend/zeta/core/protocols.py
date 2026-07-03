"""Registry of supported protocols.

This drives both the UI (which fields to render for a given protocol) and config
generation (which core owns the protocol, what credential it needs, which
transports/securities are valid). Adding a protocol here + a branch in the
relevant core generator is all that's required to support it end to end.
"""

from __future__ import annotations

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

# Shadowsocks-2022 ciphers and their required key length in bytes.
SS2022_KEY_BYTES = {
    "2022-blake3-aes-128-gcm": 16,
    "2022-blake3-aes-256-gcm": 32,
    "2022-blake3-chacha20-poly1305": 32,
}

# Legacy Shadowsocks ciphers (any password length).
SS_LEGACY_METHODS = ("aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305")


def spec(protocol: str) -> ProtocolSpec:
    try:
        return REGISTRY[protocol]
    except KeyError as exc:
        raise ValueError(f"Unsupported protocol: {protocol}") from exc


def by_core(core: str) -> list[ProtocolSpec]:
    return [s for s in REGISTRY.values() if s.core == core]


def all_specs() -> list[ProtocolSpec]:
    return list(REGISTRY.values())
