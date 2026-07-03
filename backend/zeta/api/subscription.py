"""Public subscription endpoints (no auth; keyed by an unguessable sub_id).

Serves the base64 aggregate subscription consumed by VPN clients, with the
standard ``Subscription-Userinfo`` header (upload/download/total/expire) so
clients can display quota and expiry. Also exposes a JSON endpoint that backs
the self-service user portal page.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Client, Inbound
from ..core import clientconf, links

router = APIRouter(prefix="/sub", tags=["subscription"])


def _clients_for(db: Session, sub_id: str) -> list[tuple[Inbound, Client]]:
    rows = (
        db.query(Client, Inbound)
        .join(Inbound, Client.inbound_id == Inbound.id)
        .filter(Client.sub_id == sub_id, Client.enabled.is_(True), Inbound.enabled.is_(True))
        .all()
    )
    return [(ib, c) for c, ib in rows]


def _userinfo_header(clients: list[tuple[Inbound, Client]]) -> str:
    up = sum(c.up or 0 for _, c in clients)
    down = sum(c.down or 0 for _, c in clients)
    totals = [c.total_bytes for _, c in clients if c.total_bytes]
    total = sum(totals) if totals and len(totals) == len(clients) else 0
    expiries = [c.expiry_time for _, c in clients if c.expiry_time]
    expire = int(min(expiries) / 1000) if expiries else 0
    return f"upload={up}; download={down}; total={total}; expire={expire}"


@router.get("/{sub_id}")
def subscription(
    sub_id: str,
    request: Request,
    target: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    clients = _clients_for(db, sub_id)
    if not clients:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")

    fmt = clientconf.detect_format(target, request.headers.get("user-agent", ""))
    address = settings.server_domain or settings.server_address or "127.0.0.1"
    headers = {
        "Subscription-Userinfo": _userinfo_header(clients),
        "Profile-Title": settings.brand,
        "Profile-Update-Interval": "12",
    }

    if fmt == "clash":
        body = clientconf.to_clash_yaml(clients, address)
        media = "text/yaml; charset=utf-8"
    elif fmt == "singbox":
        body = clientconf.to_singbox_json(clients, address)
        media = "application/json; charset=utf-8"
    else:
        body = links.subscription_for(clients)
        media = "text/plain; charset=utf-8"

    headers["Content-Disposition"] = f'attachment; filename="{settings.brand}-{sub_id}"'
    return Response(content=body, media_type=media, headers=headers)


@router.get("/{sub_id}/info")
def subscription_info(sub_id: str, db: Session = Depends(get_db)) -> dict:
    clients = _clients_for(db, sub_id)
    if not clients:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")

    items = []
    for ib, c in clients:
        link = links.client_link(ib, c)
        items.append(
            {
                "protocol": ib.protocol,
                "remark": ib.remark or ib.tag,
                "email": c.email,
                "link": link,
                "qr": links.qr_data_url(link) if link else None,
                "up": c.up,
                "down": c.down,
                "total": c.total_bytes,
                "expiry": c.expiry_time,
            }
        )
    first = clients[0][1]
    return {
        "brand": settings.brand,
        "sub_id": sub_id,
        "email": first.email,
        "used": sum(c.used_bytes for _, c in clients),
        "total": first.total_bytes,
        "expiry": first.expiry_time,
        "configs": items,
    }
