"""SQLAlchemy ORM models for ZetaVPN.

A normalised, relational inbound/client schema (real tables and foreign keys,
not JSON blobs) that also covers sing-box inbounds and native SSH accounts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """A DateTime that always round-trips as timezone-aware UTC.

    SQLite has no native tz-aware datetime type, so plain
    ``DateTime(timezone=True)`` silently returns a *naive* datetime on read —
    comparing that against ``datetime.now(timezone.utc)`` anywhere (e.g. SSH
    account expiry checks) raises ``TypeError: can't compare offset-naive and
    offset-aware datetimes``. This coerces on both sides so every column
    reads back aware UTC regardless of backend.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class User(Base):
    """A panel operator (admin or reseller)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="admin")  # admin | reseller
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on password change to invalidate every previously issued token.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    last_login: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)


class Setting(Base):
    """Simple key/value store for panel + server settings."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Inbound(Base):
    """A proxy listener served by one of the cores (Xray or sing-box)."""

    __tablename__ = "inbounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tag: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    remark: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    core: Mapped[str] = mapped_column(String(16), default="xray")  # xray | singbox
    protocol: Mapped[str] = mapped_column(String(32))  # vless, vmess, trojan, shadowsocks, hysteria2, tuic, ...
    listen: Mapped[str] = mapped_column(String(64), default="0.0.0.0")
    # Public-facing port (what the client connects to). NOT unique by itself:
    # a TCP inbound (e.g. VLESS-REALITY) and a UDP inbound (e.g. Hysteria2)
    # legitimately share a port number — independent port spaces — and every
    # WS-family inbound intentionally shares :80 (see port_key below).
    port: Mapped[int] = mapped_column(Integer, index=True)
    # xray listens HERE (127.0.0.1:<internal_port>) for WS-family transports,
    # which are always nginx-fronted on the shared public `port` (core/nginx.py
    # generates the per-inbound path -> internal_port location block). NULL
    # for direct (non-nginx-fronted) inbounds, where xray binds `port` itself.
    internal_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "<port>:<tcp|udp>" for direct inbounds, or "80:<ws path>" for WS-family
    # ones — the actual real-world collision key, computed by the API layer
    # (core/protocols.compute_port_key) and enforced unique here so a race
    # between two concurrent requests can't both win.
    port_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    # Extra public ports this SAME inbound also listens on (direct xray binds),
    # sharing its clients/credentials/transport. e.g. a VLESS-WS inbound on :80
    # can also be reachable on 8080 and 8443. Empty for a single-port inbound;
    # each extra is collision-checked like a direct port (see api/inbounds).
    extra_ports: Mapped[list] = mapped_column(JSON, default=list)

    # Transport / stream. network = tcp|ws|grpc|httpupgrade|xhttp|kcp|quic (xray)
    network: Mapped[str] = mapped_column(String(24), default="tcp")
    security: Mapped[str] = mapped_column(String(16), default="none")  # none | tls | reality

    # Protocol-specific settings block (e.g. shadowsocks method, vless decryption).
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Full streamSettings object (ws/grpc/tls/reality details) as the core expects.
    stream_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    sniffing: Mapped[bool] = mapped_column(Boolean, default=True)

    # Aggregate traffic counters (bytes), updated by the stats poller.
    up: Mapped[int] = mapped_column(BigInteger, default=0)
    down: Mapped[int] = mapped_column(BigInteger, default=0)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, onupdate=_utcnow)

    clients: Mapped[list["Client"]] = relationship(
        back_populates="inbound", cascade="all, delete-orphan"
    )


class Client(Base):
    """A single user credential under an inbound."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inbound_id: Mapped[int] = mapped_column(ForeignKey("inbounds.id", ondelete="CASCADE"), index=True)

    # Globally unique: Xray keys per-user traffic stats by email, so a duplicate
    # email across inbounds would merge/double-count usage. Enforce uniqueness here.
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # Credential — one of these is used depending on the protocol.
    uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)  # vless/vmess/tuic
    password: Mapped[str | None] = mapped_column(String(128), nullable=True)  # trojan/ss/hy2
    flow: Mapped[str] = mapped_column(String(32), default="")  # e.g. xtls-rprx-vision

    # Limits.
    limit_ip: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited concurrent IPs
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # 0 = unlimited quota
    expiry_time: Mapped[int] = mapped_column(BigInteger, default=0)  # ms epoch, 0 = never
    # Set/cleared by tasks.py from the Xray access-log IP tracker (see
    # core/access_log.py) when the client is currently using more distinct
    # source IPs than limit_ip allows. Temporary, unlike enabled/expiry/quota
    # — clears itself once recent IP activity drops back under the limit.
    ip_limit_exceeded: Mapped[bool] = mapped_column(Boolean, default=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sub_id: Mapped[str] = mapped_column(String(64), index=True, default="")  # subscription group
    comment: Mapped[str] = mapped_column(String(255), default="")

    # Usage counters (bytes).
    up: Mapped[int] = mapped_column(BigInteger, default=0)
    down: Mapped[int] = mapped_column(BigInteger, default=0)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)

    inbound: Mapped["Inbound"] = relationship(back_populates="clients")

    @property
    def used_bytes(self) -> int:
        return (self.up or 0) + (self.down or 0)

    @property
    def is_expired(self) -> bool:
        if not self.expiry_time:
            return False
        return _utcnow().timestamp() * 1000 >= self.expiry_time

    @property
    def is_quota_exceeded(self) -> bool:
        if not self.total_bytes:
            return False
        return self.used_bytes >= self.total_bytes

    @property
    def is_usable(self) -> bool:
        return (
            self.enabled
            and not self.is_expired
            and not self.is_quota_exceeded
            and not self.ip_limit_exceeded
        )


class SSHAccount(Base):
    """A native SSH / tunnelling account (OpenSSH + Dropbear + WS/SSL)."""

    __tablename__ = "ssh_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # bcrypt hash, kept for reference / stale-password detection.
    password_hash: Mapped[str] = mapped_column(String(255))
    # Plaintext account password. This panel is single-tenant — only the VPS
    # owner ever reaches the dashboard — and the owner wants to read the
    # password back any time (show/copy, resend to a user), exactly like the
    # plaintext VLESS/Trojan client credentials already stored elsewhere. NULL
    # only for accounts created before this column existed.
    password: Mapped[str | None] = mapped_column(String(128), nullable=True)
    max_login: Mapped[int] = mapped_column(Integer, default=1)
    expiry_date: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)


class TrafficSnapshot(Base):
    """Periodic total-throughput samples for the dashboard network chart."""

    __tablename__ = "traffic_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, index=True)
    rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class AuditLog(Base):
    """Security-relevant events (logins, config pushes, account changes)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
