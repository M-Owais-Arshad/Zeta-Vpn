"""Inbound CRUD + core config application."""

from __future__ import annotations

import base64
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Client, Inbound, User
from ..core import firewall, nginx, portcheck, protocols, singbox, xray
from ..schemas import ApplyResult, InboundCreate, InboundOut, InboundUpdate

router = APIRouter()
log = logging.getLogger("zeta.api.inbounds")

# Internal loopback ports xray uses for nginx-fronted (WS-family) inbounds —
# offset from the inbound's own (unique, autoincrement) id so it's always
# collision-free without needing its own uniqueness check.
_INTERNAL_PORT_BASE = 20000


def _to_out(ib: Inbound, client_count: int | None = None) -> InboundOut:
    out = InboundOut.model_validate(ib)
    out.client_count = len(ib.clients) if client_count is None else client_count
    return out


def _apply_core(db: Session, core: str) -> ApplyResult:
    res = xray.apply(db) if core == "xray" else singbox.apply(db)
    return ApplyResult(ok=res.ok, detail=(res.stderr or res.stdout).strip())


def _apply_or_422(db: Session, core: str) -> None:
    """Apply the pending (flushed but uncommitted) change to `core`.

    If the core rejects the resulting config, roll back so the DB never ends
    up holding an inbound the core actually refused to run, and surface the
    real reason to the caller instead of silently discarding it.
    """
    result = _apply_core(db, core)
    if not result.ok:
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Rejected by the {core} core, no changes were applied: "
            f"{result.detail or 'validation failed'}",
        )


def _ws_path(network: str, stream_settings: dict) -> str:
    return ((stream_settings or {}).get(network) or {}).get("path", "").strip()


def _flush_or_409(db: Session, port: int, tag: str, fronted: bool, ws_path: str | None) -> None:
    """Flush pending changes, translating a unique-port_key/tag race into a 409.

    The pre-checks in create/update_inbound are a TOCTOU race under
    concurrent requests; the DB's unique constraints on `tag` and `port_key`
    are the actual guard, so a violation here still needs to become a normal
    API error (naming the right field), not a raw 500.
    """
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        msg = str(exc.orig).lower()
        if "tag" in msg:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Inbound tag '{tag}' already exists")
        if fronted:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"WS path '{ws_path}' on the shared :80/:443 port is already used by another inbound",
            )
        raise HTTPException(status.HTTP_409_CONFLICT, f"Port {port} already in use by another inbound")


def _panel_direct_ports(db: Session, exclude_id: int | None, family: str) -> set[int]:
    """Every direct port the panel's OTHER inbounds already bind — each
    inbound's own `port` (when it's a direct, non-fronted bind) plus all its
    extra_ports — restricted to the same L4 (tcp/udp) family."""
    q = db.query(Inbound.port, Inbound.protocol, Inbound.internal_port, Inbound.extra_ports)
    if exclude_id is not None:
        q = q.filter(Inbound.id != exclude_id)
    ports: set[int] = set()
    for port, proto, internal, extras in q.all():
        if protocols.l4_family(proto) != family:
            continue
        if internal is None:  # direct primary bind
            ports.add(port)
        ports.update(extras or [])
    return ports


def _validate_ports(
    db: Session, primary_port: int, fronted: bool, protocol: str,
    extra_ports: list[int] | None, exclude_id: int | None,
) -> list[int]:
    """Clean the extra_ports list and reject any direct-port collision — with
    another inbound (primary or extra) or an external service (nginx/SSH/…).
    Returns the de-duplicated extras. Also covers the primary when it's a
    direct bind (fronted primaries live on nginx's shared :80/:443).
    """
    family = protocols.l4_family(protocol)
    cleaned: list[int] = []
    for p in (extra_ports or []):
        if not (1 <= p <= 65535):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Extra port {p} is out of range (1-65535)")
        if p in protocols.FRONTED_PORTS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Port {p} is a shared nginx port — make it the MAIN port to use path-fronting; "
                "extra ports are dedicated direct binds.",
            )
        if p == primary_port or p in cleaned:
            continue
        cleaned.append(p)
    new_direct = cleaned + ([] if fronted else [primary_port])
    owned = _panel_direct_ports(db, exclude_id, family)
    for p in new_direct:
        if p in owned:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Port {p} is already used by another inbound")
    for p in new_direct:
        if portcheck.external_conflict(p, family, owned | set(new_direct)):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Port {p}/{family} is already in use by another service on this server "
                "(nginx, the SSH stack, etc.) — pick a different port.",
            )
    return cleaned


def _seed_reality(stream: dict) -> dict:
    stream = dict(stream or {})
    r = dict(stream.get("reality", {}))
    if not r.get("privateKey"):
        keys = xray.gen_reality_keypair()
        r["privateKey"] = keys["privateKey"]
        r["publicKey"] = keys["publicKey"]
    if not r.get("shortIds"):
        r["shortIds"] = [xray.gen_short_id()]
    # www.microsoft.com does NOT complete the REALITY handshake (its TLS/CDN
    # stack is incompatible — verified: xray logs "handshake did not complete
    # successfully"), which silently breaks every REALITY inbound. apple.com is
    # a stable TLS-1.3 dest with huge collateral (rarely blocked).
    r.setdefault("dest", "www.apple.com:443")
    r.setdefault("serverNames", ["www.apple.com"])
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
    inbounds = db.query(Inbound).order_by(Inbound.id.asc()).all()
    # One grouped COUNT instead of a lazy-loaded `.clients` query per inbound.
    counts = dict(
        db.query(Client.inbound_id, func.count(Client.id)).group_by(Client.inbound_id).all()
    )
    return [_to_out(ib, counts.get(ib.id, 0)) for ib in inbounds]


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

    settings_block = body.settings or dict(spec.default_settings)
    stream = body.stream_settings or {}
    if body.security == "reality" and body.auto_reality:
        stream = _seed_reality(stream)
    if body.protocol == "shadowsocks":
        settings_block = _seed_ss_key(settings_block)
        method = settings_block.get("method", "")
        try:
            protocols.validate_ss2022_password(method, settings_block.get("password", ""))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    is_ws = protocols.is_ws_family(body.network)
    # Auto-infer the mode from the port: a WS inbound on a port nginx owns
    # (:80/:443) is nginx-fronted (shared, path-routed); on ANY other port it
    # binds that port directly (xray listens) — this is what makes VLESS-WS on
    # :8080, or several WS inbounds on different ports, possible.
    if body.port is not None:
        port = body.port
    elif is_ws:
        port = 80  # default WS => fronted on the shared :80 (CDN-friendly)
    else:
        port = protocols.default_port(body.protocol, body.network)
    fronted = protocols.is_fronted(body.network, port)

    ws_path = None
    if is_ws:
        ws_path = _ws_path(body.network, stream)
        if fronted and not ws_path.startswith("/"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "A WS inbound sharing the :80/:443 port needs a path starting with '/' "
                "(stream_settings.<network>.path). Give it its own dedicated port instead "
                "for a free or empty path.",
            )
    extra_ports = _validate_ports(db, port, fronted, body.protocol, body.extra_ports, exclude_id=None)

    port_key = protocols.compute_port_key(port, body.protocol, body.network, stream)
    if db.query(Inbound).filter(Inbound.port_key == port_key).first():
        if fronted:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"WS path '{ws_path}' on the shared :80/:443 port is already used by another inbound",
            )
        raise HTTPException(status.HTTP_409_CONFLICT, f"Port {port} already in use by another inbound")

    ib = Inbound(
        tag=body.tag,
        remark=body.remark,
        enabled=body.enabled,
        core=body.core,
        protocol=body.protocol,
        listen=body.listen,
        port=port,
        network=body.network,
        security=body.security,
        settings=settings_block,
        stream_settings=stream,
        sniffing=body.sniffing,
        port_key=port_key,
        extra_ports=extra_ports,
    )
    db.add(ib)
    _flush_or_409(db, port, body.tag, fronted, ws_path)
    if fronted:
        # Needs ib.id, which only exists after the flush above; xray.apply()
        # (next) must see this set or it'd bind the public :80/:443 directly and
        # collide with nginx. A DIRECT WS inbound keeps internal_port NULL and
        # binds `port` itself (see core/xray.build_inbound).
        ib.internal_port = _INTERNAL_PORT_BASE + ib.id
        db.flush()
    _apply_or_422(db, ib.core)
    db.commit()
    db.refresh(ib)

    if fronted:
        res = nginx.sync(db)
        if not res.ok:
            log.warning("nginx sync failed after creating inbound %s: %s", ib.id, res.stderr or res.stdout)
    else:
        # Direct primary (incl. a direct WS inbound on its own custom port).
        firewall.allow(ib.port, ib.protocol)
    # Extra ports are always direct binds, regardless of the primary's mode.
    for ep in ib.extra_ports or []:
        firewall.allow(ep, ib.protocol)
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
    old_port = ib.port
    old_fronted = protocols.is_fronted(ib.network, ib.port)
    old_direct = set(ib.extra_ports or []) | (set() if old_fronted else {ib.port})

    if ib.protocol == "shadowsocks" and "settings" in data:
        method = data["settings"].get("method", ib.settings.get("method", ""))
        try:
            protocols.validate_ss2022_password(method, data["settings"].get("password", ""))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    for field, value in data.items():
        setattr(ib, field, value)

    is_ws = protocols.is_ws_family(ib.network)
    fronted = protocols.is_fronted(ib.network, ib.port)
    ws_path = None
    if is_ws:
        ws_path = _ws_path(ib.network, ib.stream_settings)
        if fronted and not ws_path.startswith("/"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "A WS inbound sharing the :80/:443 port needs a path starting with '/' "
                "(stream_settings.<network>.path). Give it its own dedicated port instead "
                "for a free or empty path.",
            )
    if not fronted:
        ib.internal_port = None
    ib.extra_ports = _validate_ports(db, ib.port, fronted, ib.protocol, ib.extra_ports, exclude_id=ib.id)

    new_port_key = protocols.compute_port_key(ib.port, ib.protocol, ib.network, ib.stream_settings)
    if new_port_key != ib.port_key:
        clash = db.query(Inbound).filter(Inbound.port_key == new_port_key, Inbound.id != ib.id).first()
        if clash:
            if fronted:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"WS path '{ws_path}' on the shared :80/:443 port is already used by another inbound",
                )
            raise HTTPException(status.HTTP_409_CONFLICT, f"Port {ib.port} already in use by another inbound")
        ib.port_key = new_port_key

    _flush_or_409(db, ib.port, ib.tag, fronted, ws_path)
    if fronted and ib.internal_port is None:
        ib.internal_port = _INTERNAL_PORT_BASE + ib.id
        db.flush()
    _apply_or_422(db, ib.core)
    db.commit()
    db.refresh(ib)

    # The nginx include is the set of fronted inbounds — resync if this one is
    # or was fronted. Direct inbounds (incl. direct WS) own a real port the
    # firewall must open/close.
    if old_fronted or fronted:
        nginx.sync(db)
    # Reconcile the firewall to the exact set of direct ports this inbound now
    # binds (primary-if-direct + extras), opening/closing only what changed.
    new_direct = set(ib.extra_ports or []) | (set() if fronted else {ib.port})
    for p in old_direct - new_direct:
        firewall.revoke(p, ib.protocol)
    for p in new_direct - old_direct:
        firewall.allow(p, ib.protocol)
    return _to_out(ib)


@router.post("/{inbound_id}/toggle", response_model=InboundOut)
def toggle_inbound(
    inbound_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> InboundOut:
    ib = db.get(Inbound, inbound_id)
    if ib is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    ib.enabled = not ib.enabled
    db.flush()
    _apply_or_422(db, ib.core)
    db.commit()
    db.refresh(ib)
    if protocols.is_fronted(ib.network, ib.port):
        nginx.sync(db)
    return _to_out(ib)


@router.delete("/{inbound_id}")
def delete_inbound(
    inbound_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> dict:
    ib = db.get(Inbound, inbound_id)
    if ib is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    core = ib.core
    protocol = ib.protocol
    fronted = protocols.is_fronted(ib.network, ib.port)
    # Every direct port this inbound bound (primary-if-direct + extras).
    direct_ports = list(ib.extra_ports or []) + ([] if fronted else [ib.port])
    db.delete(ib)
    db.flush()
    _apply_or_422(db, core)
    db.commit()
    if fronted:
        nginx.sync(db)
    for p in direct_ports:
        firewall.revoke(p, protocol)
    return {"ok": True}


@router.post("/apply/all", response_model=list[ApplyResult])
def apply_all(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[ApplyResult]:
    """Force-regenerate and reload both cores."""
    return [_apply_core(db, "xray"), _apply_core(db, "singbox")]
