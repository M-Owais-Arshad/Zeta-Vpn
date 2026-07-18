"""Tiny Telegram Bot API client — long-polling, stdlib only.

Deliberately NOT pyrogram/telethon: the Bot API needs only the bot token (which
already lives in the panel's Settings), no api_id/api_hash, and no heavy
dependency — so the bot ships with zero extra install footprint.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request


class TelegramError(RuntimeError):
    pass


class Bot:
    def __init__(self, token: str, timeout: int = 30) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout
        self._offset = 0

    def _call(self, method: str, **params) -> dict:
        data = urllib.parse.urlencode(
            {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
             for k, v in params.items() if v is not None}
        ).encode()
        req = urllib.request.Request(f"{self.base}/{method}", data=data)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout + 10) as resp:
                out = json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            raise TelegramError(f"{method} failed: {exc}") from exc
        if not out.get("ok"):
            raise TelegramError(f"{method}: {out.get('description')}")
        return out.get("result", {})

    def get_updates(self) -> list[dict]:
        res = self._call("getUpdates", offset=self._offset, timeout=self.timeout,
                         allowed_updates=["message", "callback_query"])
        if res:
            self._offset = res[-1]["update_id"] + 1
        return res

    def send(self, chat_id: int, text: str, keyboard: list | None = None) -> dict:
        return self._call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                          disable_web_page_preview=True,
                          reply_markup={"inline_keyboard": keyboard} if keyboard else None)

    def edit(self, chat_id: int, message_id: int, text: str, keyboard: list | None = None) -> None:
        try:
            self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                       parse_mode="HTML", disable_web_page_preview=True,
                       reply_markup={"inline_keyboard": keyboard} if keyboard else None)
        except TelegramError:
            pass  # "message is not modified" etc. are harmless

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except TelegramError:
            pass

    def me(self) -> dict:
        return self._call("getMe")


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}
