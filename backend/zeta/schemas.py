"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Rejects control characters (incl. \n \r \0) that could break out of a single
# line of subprocess stdin (e.g. chpasswd's "user:pass\n" protocol) or corrupt
# logs/config text. Applied to any free-text field that flows into a shell
# subprocess or a stats-key format that uses plain-text delimiters.
_NO_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _reject_control_chars(value: str, field_name: str) -> str:
    if _NO_CONTROL_CHARS_RE.search(value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


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
    # Optional: falls back to a sensible per-protocol/transport default (see
    # core/protocols.default_port) when omitted. For a WS-family transport,
    # :80/:443 => nginx-fronted (shared, path-routed); any other port =>
    # xray binds it directly (e.g. VLESS-WS on :8080). See core/protocols.is_fronted.
    port: int | None = Field(default=None, ge=1, le=65535)
    network: str = "tcp"
    security: str = "none"
    settings: dict = Field(default_factory=dict)
    stream_settings: dict = Field(default_factory=dict)
    sniffing: bool = True
    # Extra public ports this same inbound ALSO listens on (direct binds),
    # sharing its clients/credentials. e.g. VLESS-WS on 80 + also on 8080/8443.
    extra_ports: list[int] = Field(default_factory=list)

    @field_validator("tag")
    @classmethod
    def _clean_tag(cls, v: str) -> str:
        # The tag is a core-config identifier and a stats key. Keep it to a safe
        # printable set (no control chars, slashes, quotes or '@' — the last is
        # reserved for the internal "<tag>@<port>" extra-listener convention) so
        # it can never confuse the core config or the tag@port stats folding.
        _reject_control_chars(v, "tag")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]*", v):
            raise ValueError(
                "tag may contain only letters, digits, space, dot, underscore and "
                "hyphen, and must start with a letter or digit"
            )
        return v


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
    extra_ports: list[int] | None = None


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
    # Set only for WS-family transports — xray's real (loopback-only) listen
    # port; nginx proxies the public `port` to it (see core/nginx.py). None
    # for everything else, where xray binds `port` directly.
    internal_port: int | None = None
    extra_ports: list[int] = Field(default_factory=list)
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
    limit_ip: int = Field(default=0, ge=0)
    total_gb: float = Field(default=0, ge=0)  # convenience: gigabytes, converted to bytes on save
    expiry_days: int = Field(default=0, ge=0)  # convenience: days from now, converted to epoch ms
    enabled: bool = True
    sub_id: str = ""
    comment: str = ""

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        _reject_control_chars(v, "email")
        # Xray's stats keys are "scope>>>key>>>_>>>direction" — a ">" in the
        # email would corrupt that format and silently hide the client's
        # traffic from query_stats(), defeating quota enforcement.
        if ">" in v:
            raise ValueError("email must not contain '>'")
        return v

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str | None) -> str | None:
        if v is not None:
            _reject_control_chars(v, "password")
        return v


class ClientUpdate(BaseModel):
    email: str | None = None
    password: str | None = None
    flow: str | None = None
    limit_ip: int | None = Field(default=None, ge=0)
    total_gb: float | None = Field(default=None, ge=0)
    expiry_days: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    comment: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        if v is not None:
            _reject_control_chars(v, "email")
            if ">" in v:
                raise ValueError("email must not contain '>'")
        return v

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str | None) -> str | None:
        if v is not None:
            _reject_control_chars(v, "password")
        return v


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
    ip_limit_exceeded: bool = False
    # Live connection info from the core's access log / Clash API — not DB
    # columns, populated only by list_clients() which has recent poll data;
    # other endpoints correctly default to "no known activity yet".
    online: bool = False
    online_ips: list[str] = Field(default_factory=list)
    sub_id: str
    comment: str
    up: int
    down: int
    created_at: datetime


class ClientLink(BaseModel):
    email: str
    sub_id: str
    link: str
    qr: str | None = None


# --- SSH accounts ------------------------------------------------------------

class SSHAccountCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)
    max_login: int = Field(default=1, ge=0)
    expiry_days: int = Field(default=30, ge=0)
    comment: str = ""

    @field_validator("username", "password")
    @classmethod
    def _validate_no_control_chars(cls, v: str, info) -> str:  # noqa: ANN001
        # `chpasswd` reads "username:password\n" lines from stdin — a newline
        # (or NUL) in either field lets the value inject an extra line and
        # rewrite an arbitrary account's password (e.g. root's).
        return _reject_control_chars(v, info.field_name)


class SSHAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    # Plaintext password, shown to the (single, owner) admin so it can be
    # copied/resent any time. None for accounts created before this existed.
    password: str | None = None
    max_login: int
    expiry_date: datetime | None
    enabled: bool
    comment: str
    created_at: datetime
    # Total bytes relayed by this account (up + down combined — raw SSH has no
    # per-direction stats). Accumulated from per-uid iptables counters.
    used_bytes: int = 0
    online: int = 0
    # Source IPs of currently-connected sessions (direct OpenSSH/Dropbear).
    # May be shorter than `online` — SSL/WS sessions don't expose a real IP.
    online_ips: list[str] = Field(default_factory=list)


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
    # Sensible default public port for the *default* transport; the frontend
    # recomputes this per-transport-selection via ports_by_network below
    # (ws-family transports are always 80 — see core/protocols.py).
    default_port: int
    ports_by_network: dict[str, int]
    ws_family_networks: list[str]


class ApplyResult(BaseModel):
    ok: bool
    detail: str = ""
