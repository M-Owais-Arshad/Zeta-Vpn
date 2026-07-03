"""Inbound CRUD + core config application."""

from __future__ import annotations

import base64
import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Inbound, User
from ..core import protocols, singbox, xray
from ..schemas import ApplyResult, InboundCreate, InboundOut, InboundUpdate

router = APIRouter()


def _to_out(ib: Inbound) -> InboundOut:
    out = InboundOut.model_validate(ib)
    out.client_count = len(ib.clients)
    return out


def _apply_core(db: Session, core: str) -> ApplyResult:
    res = xray.apply(db) if core == "xray" else singbox.apply(db)
    return ApplyResult(ok=res.ok, detail=(res.stderr or res.stdout).strip())


def _seed_reality(stream: dict) -> dict:
    stream = dict(stream or {})
    r = dict(stream.get("reality", {}))
    if not r.get("privateKey"):
        keys = xray.gen_reality_keypair()
        r["privateKey"] = keys["privateKey"]
        r["publicKey"] = keys["publicKey"]
    if not r.get("shortIds"):
        r["shortIds"] = [xray.gen_short_id()]
    r.setdefault("dest", "www.microsoft.com:443")
    r.setdefault("serverNames", ["www.microsoft.com"])
    r.setdefault("fingerprint", "chrome")
    stream["reality"] = r
    return stream


def _seed_ss_key(settings_block: dict) -> dict:
    block = dict(settings_block or {})
    method = block.get("method", "2022-blake3-aes-128-gcm")
    if method.startswith("2022") and not block.get("password"):
        key_bytes = protocols.SS2022_KEY_BYTES.get(method, 16)
        block["password"] = base64.b64encode(os.urandom(key_bytes)).decode("ascii")
    return block


@router.get("", response_model=list[InboundOut])
def list_inbounds(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[InboundOut]:
    return [_to_out(ib) for ib in db.query(Inbound).order_by(Inbound.id.asc()).all()]


@router.post("", response_model=InboundOut, status_code=status.HTTP_201_CREATED)
def create_inbound(
    body: InboundCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> InboundOut:
    try:
        spec = protocols.spec(body.protocol)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if body.core != spec.core:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{spec.label} runs on the '{spec.core}' core")

    if db.query(Inbound).filter(Inbound.tag == body.tag).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Inbound tag already exists")
    if db.query(Inbound).filter(Inbound.port == body.port).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Port {body.port} already in use by another inbound")

    settings_block = body.settings or dict(spec.default_settings)
    stream = body.stream_settings or {}
    if body.security == "reality" and body.auto_reality:
        stream = _seed_reality(stream)
    if body.protocol == "shadowsocks":
        settings_block = _seed_ss_key(settings_block)

    ib = Inbound(
        tag=body.tag,
        remark=body.remark,
        enabled=body.enabled,
        core=body.core,
        protocol=body.protocol,
        listen=body.listen,
        port=body.port,
        network=body.network,
        security=body.security,
        settings=settings_block,
        stream_settings=stream,
        sniffing=body.sniffing,
    )
    db.add(ib)
    db.commit()
    db.refresh(ib)
    _apply_core(db, ib.core)
    return _to_out(ib)


@router.get("/{inbound_id}", response_model=InboundOut)
def get_inbound(
    inbound_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> InboundOut:
    ib = db.get(Inbound, inbound_id)
    if ib is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    return _to_out(ib)


@router.patch("/{inbound_id}", response_model=InboundOut)
def update_inbound(
    inbound_id: int,
    body: InboundUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> InboundOut:
    ib = db.get(Inbound, inbound_id)
    if ib is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")

    data = body.model_dump(exclude_unset=True)
    if "port" in data and data["port"] != ib.port:
        clash = db.query(Inbound).filter(Inbound.port == data["port"], Inbound.id != ib.id).first()
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Port {data['port']} already in use")
    for field, value in data.items():
        setattr(ib, field, value)
    db.commit()
    db.refresh(ib)
    _apply_core(db, ib.core)
    return _to_out(ib)


@router.post("/{inbound_id}/toggle", response_model=InboundOut)
def toggle_inbound(
    inbound_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> InboundOut:
    ib = db.get(Inbound, inbound_id)
    if ib is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    ib.enabled = not ib.enabled
    db.commit()
    db.refresh(ib)
    _apply_core(db, ib.core)
    return _to_out(ib)


@router.delete("/{inbound_id}")
def delete_inbound(
    inbound_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> dict:
    ib = db.get(Inbound, inbound_id)
    if ib is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    core = ib.core
    db.delete(ib)
    db.commit()
    _apply_core(db, core)
    return {"ok": True}


@router.post("/apply/all", response_model=list[ApplyResult])
def apply_all(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[ApplyResult]:
    """Force-regenerate and reload both cores."""
    return [_apply_core(db, "xray"), _apply_core(db, "singbox")]
