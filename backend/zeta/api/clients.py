"""Client (user credential) CRUD under an inbound, plus share links."""

from __future__ import annotations

import base64
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Client, Inbound, User
from ..core import access_log, links, protocols, singbox, xray
from ..schemas import ClientCreate, ClientLink, ClientOut, ClientUpdate

router = APIRouter()


def _flush_or_409(db: Session, email: str) -> None:
    """Flush pending changes, translating a unique-email race into a 409.

    The pre-check (SELECT ... WHERE email = ...) in create/update_client is a
    TOCTOU race under concurrent requests — Client.email's DB-level unique
    constraint is the real guard, so a violation here must still become a
    normal API error instead of an unhandled IntegrityError -> raw 500.
    """
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"A client with email '{email}' already exists")

_GB = 1024 ** 3


def _get_inbound(db: Session, inbound_id: int) -> Inbound:
    ib = db.get(Inbound, inbound_id)
    if ib is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    return ib


def _get_client(db: Session, inbound_id: int, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.inbound_id != inbound_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    return client


def _apply(db: Session, ib: Inbound) -> None:
    """Apply the pending (flushed but uncommitted) change to `ib`'s core.

    If the core rejects the resulting config, roll back so the DB never ends
    up holding a client the core actually refused to run, and surface the
    real reason instead of silently discarding it.
    """
    res = (xray.apply if ib.core == "xray" else singbox.apply)(db)
    if not res.ok:
        db.rollback()
        detail = (res.stderr or res.stdout or "validation failed").strip()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Rejected by the {ib.core} core, no changes were applied: {detail}",
        )


def _apply_add(db: Session, ib: Inbound, client: Client) -> None:
    """Add ONE client to the live core WITHOUT restarting when the protocol
    supports it (Xray VLESS/VMess/Trojan via HandlerService) — so creating an
    account never drops the other users' tunnels. Falls back to a full apply()
    (write + restart) for everything else, or if the live add fails."""
    if xray.supports_live_user_ops(ib):
        if xray.add_user_live(ib, client).ok:
            xray.persist_config(db)  # keep the on-disk config in sync, no restart
            return
    _apply(db, ib)


def _apply_remove(db: Session, ib: Inbound, email: str) -> None:
    """Remove ONE client (by email) from the live core with no restart when
    supported, else a full apply()."""
    if xray.supports_live_user_ops(ib):
        if xray.remove_user_live(ib, email).ok:
            xray.persist_config(db)
            return
    _apply(db, ib)


def _apply_sync(db: Session, ib: Inbound, client: Client, old_email: str | None = None) -> None:
    """Re-sync ONE edited client on the live core with no restart when supported:
    drop its old entry, then re-add it only if it's still enabled+usable. Falls
    back to a full apply() otherwise or on any failure."""
    if xray.supports_live_user_ops(ib):
        if xray.remove_user_live(ib, old_email or client.email).ok:
            if not (client.enabled and client.is_usable):
                xray.persist_config(db)
                return
            if xray.add_user_live(ib, client).ok:
                xray.persist_config(db)
                return
    _apply(db, ib)


def _gen_password(ib: Inbound) -> str:
    """Generate a credential password. Shadowsocks-2022 needs a base64-encoded
    PSK of the cipher's exact key length; everything else gets a URL-safe token."""
    if ib.protocol == "shadowsocks":
        method = (ib.settings or {}).get("method", "")
        if method.startswith("2022"):
            key_bytes = protocols.SS2022_KEY_BYTES.get(method, 16)
            return base64.b64encode(os.urandom(key_bytes)).decode("ascii")
    return secrets.token_urlsafe(16)


def _default_flow(ib: Inbound, requested: str) -> str:
    if requested:
        return requested
    # VLESS + REALITY over raw TCP is strongest with Vision flow.
    if ib.protocol == "vless" and ib.security == "reality" and ib.network in ("tcp", "raw"):
        return "xtls-rprx-vision"
    return ""


@router.get("/{inbound_id}/clients", response_model=list[ClientOut])
def list_clients(
    inbound_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> list[ClientOut]:
    ib = _get_inbound(db, inbound_id)
    clients = db.query(Client).filter(Client.inbound_id == inbound_id).order_by(Client.id).all()
    # One snapshot read (no file/HTTP I/O — see access_log/singbox.client_activity())
    # reused for every row instead of one lookup per client.
    activity = access_log.client_activity() if ib.core == "xray" else singbox.client_activity()
    out = []
    for c in clients:
        item = ClientOut.model_validate(c)
        item.online_ips = activity.get(c.email, [])
        item.online = bool(item.online_ips)
        out.append(item)
    return out


@router.post("/{inbound_id}/clients", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(
    inbound_id: int,
    body: ClientCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Client:
    ib = _get_inbound(db, inbound_id)
    spec = protocols.spec(ib.protocol)

    # Email is the Xray stat key and is globally unique (see models.Client).
    if db.query(Client).filter(Client.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "A client with that email already exists")

    if ib.protocol == "shadowsocks":
        method = (ib.settings or {}).get("method", "")
        if not method.startswith("2022") and db.query(Client).filter(Client.inbound_id == inbound_id).count() >= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Legacy Shadowsocks ({method}) is single-user: only one client is served from the "
                "live config. Use a 2022 cipher (e.g. 2022-blake3-aes-128-gcm) for multiple clients, "
                "or create another inbound.",
            )

    uuid_val = body.uuid
    password = body.password
    if spec.credential == protocols.CRED_UUID:
        uuid_val = uuid_val or xray.gen_uuid()
        if ib.protocol == "tuic":  # TUIC needs both a uuid and a password
            password = password or secrets.token_urlsafe(16)
    elif spec.credential == protocols.CRED_PASSWORD:
        if password and ib.protocol == "shadowsocks":
            method = (ib.settings or {}).get("method", "")
            try:
                protocols.validate_ss2022_password(method, password)
            except ValueError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        password = password or _gen_password(ib)
    else:
        password = password or secrets.token_urlsafe(12)

    expiry_ms = 0
    if body.expiry_days and body.expiry_days > 0:
        expiry_ms = int((time.time() + body.expiry_days * 86400) * 1000)

    client = Client(
        inbound_id=inbound_id,
        email=body.email,
        uuid=uuid_val,
        password=password,
        flow=_default_flow(ib, body.flow),
        limit_ip=body.limit_ip,
        total_bytes=int(body.total_gb * _GB) if body.total_gb else 0,
        expiry_time=expiry_ms,
        enabled=body.enabled,
        sub_id=body.sub_id or secrets.token_hex(8),
        comment=body.comment,
    )
    db.add(client)
    _flush_or_409(db, body.email)
    _apply_add(db, ib, client)
    db.commit()
    db.refresh(client)
    return client


@router.patch("/{inbound_id}/clients/{client_id}", response_model=ClientOut)
def update_client(
    inbound_id: int,
    client_id: int,
    body: ClientUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Client:
    ib = _get_inbound(db, inbound_id)
    client = _get_client(db, inbound_id, client_id)
    data = body.model_dump(exclude_unset=True)
    old_email = client.email  # capture before any change, for the live re-sync

    if "email" in data and data["email"] != client.email:
        if db.query(Client).filter(Client.email == data["email"], Client.id != client.id).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "A client with that email already exists")

    if data.get("password") and ib.protocol == "shadowsocks":
        method = (ib.settings or {}).get("method", "")
        try:
            protocols.validate_ss2022_password(method, data["password"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    if "total_gb" in data:
        gb = data.pop("total_gb")
        client.total_bytes = int(gb * _GB) if gb else 0
    if "expiry_days" in data:
        days = data.pop("expiry_days")
        client.expiry_time = int((time.time() + days * 86400) * 1000) if days and days > 0 else 0
    for field, value in data.items():
        setattr(client, field, value)

    _flush_or_409(db, client.email)
    _apply_sync(db, ib, client, old_email=old_email)
    db.commit()
    db.refresh(client)
    return client


@router.delete("/{inbound_id}/clients/{client_id}")
def delete_client(
    inbound_id: int,
    client_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    ib = _get_inbound(db, inbound_id)
    client = _get_client(db, inbound_id, client_id)
    email = client.email  # capture before delete for the live removal
    db.delete(client)
    db.flush()
    _apply_remove(db, ib, email)
    db.commit()
    return {"ok": True}


@router.post("/{inbound_id}/clients/{client_id}/reset-traffic", response_model=ClientOut)
def reset_traffic(
    inbound_id: int,
    client_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Client:
    ib = _get_inbound(db, inbound_id)
    client = _get_client(db, inbound_id, client_id)
    client.up = 0
    client.down = 0
    db.flush()
    # Reload the core so a client previously cut for exceeding quota works again
    # immediately, instead of waiting for the next enforcement poll.
    _apply(db, ib)
    db.commit()
    db.refresh(client)
    return client


@router.get("/{inbound_id}/clients/{client_id}/link", response_model=ClientLink)
def client_link(
    inbound_id: int,
    client_id: int,
    qr: bool = True,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ClientLink:
    ib = _get_inbound(db, inbound_id)
    client = _get_client(db, inbound_id, client_id)
    link = links.client_link(ib, client)
    return ClientLink(
        email=client.email,
        sub_id=client.sub_id,
        link=link,
        qr=links.qr_data_url(link) if qr and link else None,
    )
