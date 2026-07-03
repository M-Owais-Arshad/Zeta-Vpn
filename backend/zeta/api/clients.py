"""Client (user credential) CRUD under an inbound, plus share links."""

from __future__ import annotations

import base64
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Client, Inbound, User
from ..core import links, protocols, singbox, xray
from ..schemas import ClientCreate, ClientLink, ClientOut, ClientUpdate

router = APIRouter()

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
    (xray.apply if ib.core == "xray" else singbox.apply)(db)


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
) -> list[Client]:
    _get_inbound(db, inbound_id)
    return db.query(Client).filter(Client.inbound_id == inbound_id).order_by(Client.id).all()


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

    uuid_val = body.uuid
    password = body.password
    if spec.credential == protocols.CRED_UUID:
        uuid_val = uuid_val or xray.gen_uuid()
        if ib.protocol == "tuic":  # TUIC needs both a uuid and a password
            password = password or secrets.token_urlsafe(16)
    elif spec.credential == protocols.CRED_PASSWORD:
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
    db.commit()
    db.refresh(client)
    _apply(db, ib)
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

    if "total_gb" in data:
        gb = data.pop("total_gb")
        client.total_bytes = int(gb * _GB) if gb else 0
    if "expiry_days" in data:
        days = data.pop("expiry_days")
        client.expiry_time = int((time.time() + days * 86400) * 1000) if days and days > 0 else 0
    for field, value in data.items():
        setattr(client, field, value)

    db.commit()
    db.refresh(client)
    _apply(db, ib)
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
    db.delete(client)
    db.commit()
    _apply(db, ib)
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
    db.commit()
    db.refresh(client)
    # Reload the core so a client previously cut for exceeding quota works again
    # immediately, instead of waiting for the next enforcement poll.
    _apply(db, ib)
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
    return ClientLink(email=client.email, link=link, qr=links.qr_data_url(link) if qr and link else None)
