"""ZetaVPN Telegram bot — panel-integrated (stdlib Bot API, no heavy deps).

Runs as its own ``zeta-bot`` process (``python -m zeta.bot``) and shares the
panel's SQLite DB + provisioning code, so every account it creates appears in
the dashboard identically and stays in sync — the bot never edits xray configs
directly.

Modules:
  api.py        — tiny Telegram Bot API client (long-poll, urllib only)
  config.py     — token / admin ids / brand from the panel Settings table
  models.py     — bot-owned tables (bot_users, bot_payments)
  db.py         — bot DB init + session helper (shared engine)
  provision.py  — bridge to core.provisioning (the sync guarantee)
  handlers.py   — /start, free trial, buy funnel, my account, admin tools
  runner.py     — poll loop + dispatch

Configure it from the dashboard (Settings -> Telegram: bot token + admin id),
then toggle it on from Boost & Tuning.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""
