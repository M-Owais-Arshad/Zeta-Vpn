"""Start/stop/status for the Telegram bot service (zeta-bot), toggled from the
dashboard. Mirrors tuning.py / tgproxy.py: mutating actions go through the
zeta-privileged systemctl wrapper; status is an unprivileged read.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

from . import services
from ..bot import config as botcfg

UNIT = "zeta-bot"


def status() -> dict:
    st = services.status(UNIT)
    return {
        "active": st["running"],
        "state": st["active"],
        "configured": bool(botcfg.bot_token()),
        "admins": len(botcfg.admin_ids()),
    }


def start() -> dict:
    if not botcfg.bot_token():
        return {"ok": False, "detail": "Set a Telegram bot token in Settings first.", "active": False}
    services.run_privileged(["systemctl", "enable", UNIT], ["systemctl", "enable", UNIT])
    res = services.restart(UNIT)
    return {"ok": res.ok, "detail": (res.stderr or res.stdout).strip(), "active": res.ok}


def stop() -> dict:
    services.run_privileged(["systemctl", "disable", UNIT], ["systemctl", "disable", UNIT])
    res = services.systemctl("stop", UNIT)
    return {"ok": res.ok, "detail": (res.stderr or res.stdout).strip(), "active": not res.ok}
