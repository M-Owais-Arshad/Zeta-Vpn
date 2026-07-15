"""The bot's bridge to the panel. Every account it creates goes through
core.provisioning (the exact path the dashboard uses), so a client made from
Telegram shows up in the dashboard identically and stays in sync.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

from ..core import links, provisioning
from ..core.provisioning import ProvisionError
from ..models import Client, Inbound
from . import config
from .db import session
from .models import BotUser

__all__ = ["ProvisionError", "ensure_user", "provision_for", "account_summary", "default_inbound_id"]


def default_inbound_id(db) -> int | None:  # noqa: ANN001
    """Configured default inbound, else the first enabled Xray inbound."""
    cid = config.default_inbound_id()
    if cid and db.get(Inbound, cid):
        return cid
    ib = (db.query(Inbound)
            .filter(Inbound.enabled.is_(True), Inbound.core == "xray")
            .order_by(Inbound.id).first())
    return ib.id if ib else None


def ensure_user(telegram_id: int, username: str) -> BotUser:
    with session() as db:
        u = db.get(BotUser, telegram_id)
        if u is None:
            u = BotUser(telegram_id=telegram_id, username=username or "")
            db.add(u)
        elif username and u.username != username:
            u.username = username
        db.commit()
        db.refresh(u)
        return u


def provision_for(telegram_id: int, username: str, *, days: int, gb: float,
                  plan: str, limit_ip: int = 2) -> dict:
    """Create (or replace) this user's panel Client and return its share link."""
    with session() as db:
        ib_id = default_inbound_id(db)
        if ib_id is None:
            return {"ok": False, "error": "No enabled inbound configured on the panel yet."}
        ib = db.get(Inbound, ib_id)

        u = db.get(BotUser, telegram_id) or BotUser(telegram_id=telegram_id, username=username or "")
        db.add(u)

        # One client per bot user: drop the old one first so quotas/links reset.
        if u.client_email:
            old = db.query(Client).filter(Client.email == u.client_email).first()
            if old:
                try:
                    provisioning.delete_client(db, db.get(Inbound, old.inbound_id), old)
                except ProvisionError:
                    pass

        email = f"tg{telegram_id}"
        try:
            client = provisioning.create_client(
                db, ib, email=email, total_gb=gb, expiry_days=days, limit_ip=limit_ip,
                comment=f"bot:{username or telegram_id}",
            )
        except ProvisionError as exc:
            return {"ok": False, "error": exc.detail}

        u.client_email = email
        u.plan = plan
        u.status = "active"
        db.commit()
        return {"ok": True, "link": links.client_link(ib, client), "email": email}


def account_summary(telegram_id: int) -> dict:
    with session() as db:
        u = db.get(BotUser, telegram_id)
        if not u or not u.client_email:
            return {"ok": False}
        c = db.query(Client).filter(Client.email == u.client_email).first()
        if not c:
            return {"ok": False}
        ib = db.get(Inbound, c.inbound_id)
        used = (c.up or 0) + (c.down or 0)
        return {
            "ok": True, "plan": u.plan, "email": c.email,
            "used": used, "total": c.total_bytes or 0,
            "expiry_ms": c.expiry_time or 0, "enabled": c.enabled,
            "link": links.client_link(ib, c) if ib else "",
        }
