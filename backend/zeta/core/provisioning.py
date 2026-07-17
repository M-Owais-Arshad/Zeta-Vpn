"""Shared account-provisioning service — the ONE place that creates/removes
clients and SSH accounts, so the HTTP API and the Telegram bot produce
byte-for-byte identical results and can never drift out of sync.

Business-rule failures raise :class:`ProvisionError(code, detail)`; the HTTP
routes map that to ``HTTPException`` and the bot maps it to a user message.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

import base64
import os
import secrets
import time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Client, Inbound
from . import protocols, singbox, xray

_GB = 1024 ** 3


class ProvisionError(Exception):
    """A business-rule rejection with an HTTP-ish status code + message."""

    def __init__(self, code: int, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _gen_password(ib: Inbound) -> str:
    if ib.protocol == "shadowsocks":
        method = (ib.settings or {}).get("method", "")
        if method.startswith("2022"):
            key_bytes = protocols.SS2022_KEY_BYTES.get(method, 16)
            return base64.b64encode(os.urandom(key_bytes)).decode("ascii")
    return secrets.token_urlsafe(16)


def default_flow(ib: Inbound, requested: str) -> str:
    if requested:
        return requested
    if ib.protocol == "vless" and ib.security == "reality" and ib.network in ("tcp", "raw"):
        return "xtls-rprx-vision"
    return ""


def apply_core(db: Session, ib: Inbound) -> None:
    """Regenerate + reload the inbound's core; roll back on rejection."""
    res = (xray.apply if ib.core == "xray" else singbox.apply)(db)
    if not res.ok:
        db.rollback()
        detail = (res.stderr or res.stdout or "validation failed").strip()
        raise ProvisionError(422, f"Rejected by the {ib.core} core, no changes were applied: {detail}")


def apply_replace(db: Session, ib: Inbound, client: Client, *,
                  old_ib: Inbound | None = None, old_email: str | None = None) -> None:
    """Apply a bot delete-old + create-new provisioning as ONE change.

    When the target is an Xray VLESS/VMess/Trojan inbound, do it on the LIVE core
    via HandlerService — remove the old user, add the new one, NO restart — so a
    signup/renewal never drops the other users' tunnels. Falls back to a full
    core reload for anything the live API can't do or if a live op fails."""
    # Old user on a DIFFERENT live-capable inbound: drop it live there.
    if old_ib is not None and old_ib is not ib and old_email and xray.supports_live_user_ops(old_ib):
        if xray.remove_user_live(old_ib.tag, old_email).ok:
            xray.persist_config(db)
        else:
            apply_core(db, old_ib)
        old_ib = None  # handled
    if xray.supports_live_user_ops(ib):
        if old_ib is ib and old_email:
            xray.remove_user_live(ib.tag, old_email)  # same inbound: drop before re-add
        if xray.add_user_live(ib, client).ok:
            xray.persist_config(db)
            return
    # Fallback: full reload of the affected core(s).
    apply_core(db, ib)
    if old_ib is not None and old_ib is not ib and old_ib.core != ib.core:
        apply_core(db, old_ib)


def flush_or_conflict(db: Session, email: str) -> None:
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ProvisionError(409, f"A client with email '{email}' already exists")


def create_client(
    db: Session,
    ib: Inbound,
    *,
    email: str,
    uuid: str | None = None,
    password: str | None = None,
    flow: str = "",
    limit_ip: int = 0,
    total_gb: float = 0,
    expiry_days: int = 0,
    enabled: bool = True,
    sub_id: str = "",
    comment: str = "",
    _commit: bool = True,
) -> Client:
    """Create a client under ``ib`` exactly as the dashboard would.

    With ``_commit=False`` the row is added + flushed but the core reload and
    commit are left to the caller, so a delete+create replacement can run in one
    atomic transaction (see bot/provision.provision_for)."""
    spec = protocols.spec(ib.protocol)

    if db.query(Client).filter(Client.email == email).first():
        raise ProvisionError(409, "A client with that email already exists")

    if ib.protocol == "shadowsocks":
        method = (ib.settings or {}).get("method", "")
        if not method.startswith("2022") and db.query(Client).filter(Client.inbound_id == ib.id).count() >= 1:
            raise ProvisionError(
                400,
                f"Legacy Shadowsocks ({method}) is single-user: use a 2022 cipher for multiple clients.",
            )

    uuid_val, pw = uuid, password
    if spec.credential == protocols.CRED_UUID:
        uuid_val = uuid_val or xray.gen_uuid()
        if ib.protocol == "tuic":
            pw = pw or secrets.token_urlsafe(16)
    elif spec.credential == protocols.CRED_PASSWORD:
        if pw and ib.protocol == "shadowsocks":
            method = (ib.settings or {}).get("method", "")
            try:
                protocols.validate_ss2022_password(method, pw)
            except ValueError as exc:
                raise ProvisionError(400, str(exc))
        pw = pw or _gen_password(ib)
    else:
        pw = pw or secrets.token_urlsafe(12)

    expiry_ms = int((time.time() + expiry_days * 86400) * 1000) if expiry_days and expiry_days > 0 else 0

    client = Client(
        inbound_id=ib.id,
        email=email,
        uuid=uuid_val,
        password=pw,
        flow=default_flow(ib, flow),
        limit_ip=limit_ip,
        total_bytes=int(total_gb * _GB) if total_gb else 0,
        expiry_time=expiry_ms,
        enabled=enabled,
        sub_id=sub_id or secrets.token_hex(8),
        comment=comment,
    )
    db.add(client)
    flush_or_conflict(db, email)
    if _commit:
        apply_core(db, ib)
        db.commit()
        db.refresh(client)
    return client


def delete_client(db: Session, ib: Inbound, client: Client, _commit: bool = True) -> None:
    db.delete(client)
    db.flush()
    if _commit:
        apply_core(db, ib)
        db.commit()


def create_ssh_account(
    db: Session,
    *,
    username: str,
    password: str,
    max_login: int = 1,
    expiry_days: int = 30,
    comment: str = "",
):
    """Create an SSH tunnel account (useradd + DB row) — shared by the API and
    the bot. Returns the SSHAccount. Raises ProvisionError on validation /
    duplicate / OS failure."""
    from datetime import datetime, timedelta, timezone

    from .. import auth as auth_lib
    from ..models import SSHAccount
    from . import ssh_manager

    try:
        ssh_manager.validate_username(username)
    except ValueError as exc:
        raise ProvisionError(400, str(exc))

    if db.query(SSHAccount).filter(SSHAccount.username == username).first():
        raise ProvisionError(409, "Username already exists")

    expiry = datetime.now(timezone.utc) + timedelta(days=expiry_days) if expiry_days else None
    res = ssh_manager.create_account(username, password, expiry)
    if not res.ok:
        raise ProvisionError(500, f"useradd failed: {res.stderr}")

    acc = SSHAccount(
        username=username,
        password_hash=auth_lib.hash_password(password),
        password=password,
        max_login=max_login,
        expiry_date=expiry,
        comment=comment,
        enabled=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc
