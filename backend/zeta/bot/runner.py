"""Bot entrypoint: long-poll Telegram and dispatch to handlers.

Run as its own process (systemd ``zeta-bot`` / ``python -m zeta.bot``) so it
never blocks the panel; it shares the panel's SQLite DB and provisioning code.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

import logging
import time

from . import config, handlers
from .api import Bot, TelegramError
from .db import init as init_bot_db

log = logging.getLogger("zeta.bot")


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = config.bot_token()
    if not token:
        log.error("No bot token set (Settings -> Telegram). Nothing to run.")
        return 1
    init_bot_db()
    bot = Bot(token)
    try:
        me = bot.me()
        log.info("Bot @%s started; %d admin(s)", me.get("username", "?"), len(config.admin_ids()))
    except TelegramError as exc:
        log.error("Invalid bot token: %s", exc)
        return 1

    while True:
        try:
            for upd in bot.get_updates():
                try:
                    if "message" in upd:
                        handlers.handle_message(bot, upd["message"])
                    elif "callback_query" in upd:
                        handlers.handle_callback(bot, upd["callback_query"])
                except Exception:  # noqa: BLE001 — one bad update must not kill the loop
                    log.exception("handler error")
        except TelegramError as exc:
            log.warning("poll error: %s", exc)
            time.sleep(3)
        except KeyboardInterrupt:
            log.info("stopping")
            return 0
        except Exception:  # noqa: BLE001
            log.exception("loop error")
            time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(run())
