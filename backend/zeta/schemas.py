"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- Auth --------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str
    totp: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    expires_in: int


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class TotpSetup(BaseModel):
    secret: str
    uri: str
    qr: str


class TotpVerify(BaseModel):
    code: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    totp_enabled: bool
    last_login: datetime | None = None


# --- Inbounds ----------------------------------------------------------------

class InboundBase(BaseModel):
    tag: str = Field(min_length=1, max_length=64)
    remark: str = ""
    enabled: bool = True
    core: str = "xray"
    protocol: str
    listen: str = "0.0.0.0"
    port: int = Field(ge=1, le=65535)
    network: str = "tcp"
    security: str = "none"
    settings: dict = Field(default_factory=dict)
    stream_settings: dict = Field(default_factory=dict)
    sniffing: bool = True


class InboundCreate(InboundBase):
    # When true and security == "reality" with no keys supplied, the panel
    # generates an X25519 keypair + shortId automatically.
    auto_reality: bool = True


class InboundUpdate(BaseModel):
    remark: str | None = None
    enabled: bool | None = None
    listen: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    network: str | None = None
    security: str | None = None
    settings: dict | None = None
    stream_settings: dict | None = None
    sniffing: bool | None = None


class InboundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tag: str
    remark: str
    enabled: bool
    core: str
    protocol: str
    listen: str
    port: int
    network: str
    security: str
    settings: dict
    stream_settings: dict
    sniffing: bool
    up: int
    down: int
    created_at: datetime
    client_count: int = 0


# --- Clients -----------------------------------------------------------------

class ClientCreate(BaseModel):
    email: str = Field(min_length=1, max_length=128)
    uuid: str | None = None
    password: str | None = None
    flow: str = ""
    limit_ip: int = 0
    total_gb: float = 0  # convenience: gigabytes, converted to bytes on save
    expiry_days: int = 0  # convenience: days from now, converted to epoch ms
    enabled: bool = True
    sub_id: str = ""
    comment: str = ""


class ClientUpdate(BaseModel):
    email: str | None = None
    password: str | None = None
    flow: str | None = None
    limit_ip: int | None = None
    total_gb: float | None = None
    expiry_days: int | None = None
    enabled: bool | None = None
    comment: str | None = None


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    inbound_id: int
    email: str
    uuid: str | None
    password: str | None
    flow: str
    limit_ip: int
    total_bytes: int
    expiry_time: int
    enabled: bool
    sub_id: str
    comment: str
    up: int
    down: int
    created_at: datetime


class ClientLink(BaseModel):
    email: str
    link: str
    qr: str | None = None


# --- SSH accounts ------------------------------------------------------------

class SSHAccountCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)
    max_login: int = 1
    expiry_days: int = 30
    comment: str = ""


class SSHAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    max_login: int
    expiry_date: datetime | None
    enabled: bool
    comment: str
    created_at: datetime
    online: int = 0


# --- Settings / misc ---------------------------------------------------------

class SettingItem(BaseModel):
    key: str
    value: str


class ProtocolInfo(BaseModel):
    key: str
    label: str
    core: str
    credential: str
    transports: list[str]
    securities: list[str]
    default_transport: str
    default_security: str
    udp: bool
    supports_flow: bool
    notes: str


class ApplyResult(BaseModel):
    ok: bool
    detail: str = ""
