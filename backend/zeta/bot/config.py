"""Bot configuration — pulled from the panel's own Settings table / env, so the
admin configures the bot from the dashboard (Settings -> Telegram), not a
separate file.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

from ..db import SessionLocal
from ..models import Setting


def _setting(key: str, default: str = "") -> str:
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        return (row.value if row and row.value else default)
    finally:
        db.close()


def bot_token() -> str:
    return _setting("telegram_bot_token").strip()


def admin_ids() -> set[int]:
    raw = _setting("telegram_admin_id")
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def brand() -> str:
    return _setting("brand", "ZetaVPN")


# Which inbound new/trial users are provisioned onto. Empty -> the first
# enabled Xray inbound is used (resolved at runtime in provision.py).
def default_inbound_id() -> int | None:
    val = _setting("bot_default_inbound")
    return int(val) if val.isdigit() else None
