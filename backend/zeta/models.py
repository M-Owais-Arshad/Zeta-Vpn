"""SQLAlchemy ORM models for ZetaVPN.

The schema mirrors the proven inbound/client shape used by the Xray panel family
(x-ui / 3x-ui) but is normalised into real relational tables and extended to also
cover sing-box inbounds and native SSH accounts.
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


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
    port: Mapped[int] = mapped_column(Integer, index=True)

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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

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

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sub_id: Mapped[str] = mapped_column(String(64), index=True, default="")  # subscription group
    comment: Mapped[str] = mapped_column(String(255), default="")

    # Usage counters (bytes).
    up: Mapped[int] = mapped_column(BigInteger, default=0)
    down: Mapped[int] = mapped_column(BigInteger, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

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
        return self.enabled and not self.is_expired and not self.is_quota_exceeded


class SSHAccount(Base):
    """A native SSH / tunnelling account (OpenSSH + Dropbear + WS/SSL)."""

    __tablename__ = "ssh_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password: Mapped[str] = mapped_column(String(128))
    max_login: Mapped[int] = mapped_column(Integer, default=1)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TrafficSnapshot(Base):
    """Periodic total-throughput samples for the dashboard network chart."""

    __tablename__ = "traffic_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class AuditLog(Base):
    """Security-relevant events (logins, config pushes, account changes)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
