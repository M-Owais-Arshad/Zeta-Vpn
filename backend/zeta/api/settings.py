"""Panel/server settings (key-value) and small config utilities."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import settings as app_settings
from ..db import get_db
from ..deps import require_admin
from ..models import Setting, User
from ..core import ssh_manager, xray

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
    "ssh_banner",
}

# Keys mirrored onto the in-memory settings singleton so links reflect changes now.
_LIVE_MIRROR = {"server_address", "server_domain", "brand"}


def _write_ssh_banner(text: str) -> None:
    """Write the admin's SSH banner to the file OpenSSH/Dropbear read on connect.

    sshd/dropbear were pointed at this path at install and re-read it per
    connection, so a new message takes effect immediately — no reload. Normalise
    to LF + a trailing newline so it renders cleanly in a terminal. Best-effort:
    if the file isn't panel-writable right now (an older install), the text is
    still saved in the DB and re-seeded to the file at the next panel startup by
    load_into_settings()."""
    try:
        path = app_settings.ssh_banner_file
        body = text.replace("\r\n", "\n").replace("\r", "\n")
        if body and not body.endswith("\n"):
            body += "\n"
        path.write_text(body, encoding="utf-8")
    except OSError:
        pass


def load_into_settings(db: Session) -> None:
    """Apply DB-stored settings onto the runtime settings object (call at startup)."""
    for row in db.query(Setting).all():
        if row.key in _LIVE_MIRROR and row.value:
            setattr(app_settings, row.key, row.value)
    # Re-seed the on-disk SSH banner from the DB, so the message survives a
    # reinstall / fresh checkout (which recreates an empty banner file) without
    # the admin having to re-save it.
    banner = db.get(Setting, "ssh_banner")
    if banner and banner.value:
        _write_ssh_banner(banner.value)


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
        "ssh_banner": stored.get("ssh_banner", ""),
        "ssh_port": ssh_manager.system_ssh_port(),
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
        if key == "ssh_banner":
            _write_ssh_banner(str(value))
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
