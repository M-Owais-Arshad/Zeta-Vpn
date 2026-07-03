"""Dashboard metrics, protocol registry and service control."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import require_admin
from ..models import Client, Inbound, SSHAccount, User
from ..core import protocols, services, system_stats
from ..schemas import ProtocolInfo

router = APIRouter()

# Only these units may be controlled through the panel.
_CONTROLLABLE = {
    "zeta-panel", settings.xray_service, settings.singbox_service,
    "nginx", "ssh", "dropbear", "stunnel4", "zeta-ws",
}


@router.get("/stats")
def stats(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> dict:
    snap = system_stats.snapshot()
    inbound_count = db.query(func.count(Inbound.id)).scalar() or 0
    active_inbounds = db.query(func.count(Inbound.id)).filter(Inbound.enabled.is_(True)).scalar() or 0
    client_count = db.query(func.count(Client.id)).scalar() or 0
    ssh_count = db.query(func.count(SSHAccount.id)).scalar() or 0
    total_up = db.query(func.coalesce(func.sum(Inbound.up), 0)).scalar() or 0
    total_down = db.query(func.coalesce(func.sum(Inbound.down), 0)).scalar() or 0

    snap.update(
        {
            "counts": {
                "inbounds": inbound_count,
                "active_inbounds": active_inbounds,
                "clients": client_count,
                "ssh_accounts": ssh_count,
            },
            "proxy_traffic": {"up": int(total_up), "down": int(total_down)},
            "services": system_stats.services_health(),
        }
    )
    return snap


@router.get("/throughput")
def throughput(_: User = Depends(require_admin)) -> dict:
    return system_stats.net_throughput(1.0)


@router.get("/protocols", response_model=list[ProtocolInfo])
def list_protocols(_: User = Depends(require_admin)) -> list[ProtocolInfo]:
    return [
        ProtocolInfo(
            key=s.key,
            label=s.label,
            core=s.core,
            credential=s.credential,
            transports=list(s.transports),
            securities=list(s.securities),
            default_transport=s.default_transport,
            default_security=s.default_security,
            udp=s.udp,
            supports_flow=s.supports_flow,
            notes=s.notes,
        )
        for s in protocols.all_specs()
    ]


@router.get("/cores")
def cores_status(_: User = Depends(require_admin)) -> dict:
    return {
        "xray": services.status(settings.xray_service),
        "singbox": services.status(settings.singbox_service),
    }


@router.post("/services/{unit}/restart")
def restart_service(unit: str, _: User = Depends(require_admin)) -> dict:
    if unit not in _CONTROLLABLE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Service not managed by ZetaVPN")
    res = services.restart(unit)
    return {"ok": res.ok, "detail": (res.stderr or res.stdout).strip()}
