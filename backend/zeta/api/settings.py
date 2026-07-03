"""Panel/server settings (key-value) and small config utilities."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import settings as app_settings
from ..db import get_db
from ..deps import require_admin
from ..models import Setting, User
from ..core import xray

router = APIRouter()

# Settings that may be read/written through the API and mirrored onto the live
# config object. Anything security-sensitive (secret_key) is deliberately absent.
ALLOWED_KEYS = {
    "server_address",
    "server_domain",
    "brand",
    "telegram_bot_token",
    "telegram_admin_id",
    "sub_domain",
}

# Keys mirrored onto the in-memory settings singleton so links reflect changes now.
_LIVE_MIRROR = {"server_address", "server_domain", "brand"}


def load_into_settings(db: Session) -> None:
    """Apply DB-stored settings onto the runtime settings object (call at startup)."""
    for row in db.query(Setting).all():
        if row.key in _LIVE_MIRROR and row.value:
            setattr(app_settings, row.key, row.value)


@router.get("")
def get_settings(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> dict:
    stored = {row.key: row.value for row in db.query(Setting).all()}
    return {
        "server_address": stored.get("server_address", app_settings.server_address),
        "server_domain": stored.get("server_domain", app_settings.server_domain),
        "brand": stored.get("brand", app_settings.brand),
        "telegram_bot_token": stored.get("telegram_bot_token", ""),
        "telegram_admin_id": stored.get("telegram_admin_id", ""),
        "sub_domain": stored.get("sub_domain", ""),
        "panel_port": app_settings.port,
        "base_path": app_settings.base_path,
    }


@router.put("")
def update_settings(
    body: dict, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> dict:
    for key, value in body.items():
        if key not in ALLOWED_KEYS:
            continue
        row = db.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=str(value))
            db.add(row)
        else:
            row.value = str(value)
        if key in _LIVE_MIRROR:
            setattr(app_settings, key, str(value))
    db.commit()
    return {"ok": True}


@router.post("/reality-keypair")
def reality_keypair(_: User = Depends(require_admin)) -> dict:
    """Generate a fresh X25519 keypair + shortId for a REALITY inbound."""
    keys = xray.gen_reality_keypair()
    keys["shortId"] = xray.gen_short_id()
    return keys


@router.get("/new-uuid")
def new_uuid(_: User = Depends(require_admin)) -> dict:
    return {"uuid": xray.gen_uuid()}
