"""Command + callback handlers. Business logic only — the poll loop in
runner.py dispatches here. Provisioning always goes through provision.py (which
uses the panel's own services), so bot actions mirror the dashboard exactly.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

import time

from .api import Bot, btn, url_btn
from . import config, provision
from .db import session
from .models import BotPayment, BotUser

# (label, days, gb[0=unlimited], price) — edit freely; price is your currency.
PLANS = [
    ("30 days · 10 GB", 30, 10, 150),
    ("30 days · 50 GB", 30, 50, 250),
    ("30 days · Unlimited", 30, 0, 400),
    ("90 days · Unlimited", 90, 0, 1000),
]
TRIAL_DAYS, TRIAL_GB = 1, 1
PAY_INSTRUCTIONS = "Send payment to <b>EasyPaisa/JazzCash 0300-0000000</b>, then reply here with your transaction ID."


def _fmt_bytes(n: int) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def _is_admin(uid: int) -> bool:
    return uid in config.admin_ids()


def _main_menu() -> list:
    return [
        [btn("🎁 Free trial", "trial"), btn("💎 Buy premium", "buy")],
        [btn("👤 My account", "account")],
    ]


def _account_text(uid: int) -> str:
    s = provision.account_summary(uid)
    if not s.get("ok"):
        return "You don't have an active config yet. Tap <b>Free trial</b> or <b>Buy premium</b>."
    total = "∞" if not s["total"] else _fmt_bytes(s["total"])
    exp = "Never" if not s["expiry_ms"] else time.strftime("%Y-%m-%d", time.gmtime(s["expiry_ms"] / 1000))
    return (f"<b>Your account</b>\nPlan: {s['plan']}\nUsage: {_fmt_bytes(s['used'])} / {total}\n"
            f"Expires: {exp}\nStatus: {'active' if s['enabled'] else 'disabled'}\n\n"
            f"<b>Config:</b>\n<code>{s['link']}</code>")


# ---------------- messages ----------------

def handle_message(bot: Bot, msg: dict) -> None:
    chat = msg["chat"]["id"]
    uid = msg["from"]["id"]
    uname = msg["from"].get("username", "")
    text = (msg.get("text") or "").strip()
    provision.ensure_user(uid, uname)

    if text.startswith("/start"):
        bot.send(chat, f"👋 Welcome to <b>{config.brand()}</b>!\nGet a fast, secure connection in seconds.",
                 _main_menu())
    elif text.startswith("/account"):
        bot.send(chat, _account_text(uid))
    elif text.startswith("/admin") and _is_admin(uid):
        _admin_panel(bot, chat)
    elif text.startswith("/newvless") and _is_admin(uid):
        _admin_newvless(bot, chat, text)
    elif text.startswith("/newssh") and _is_admin(uid):
        _admin_newssh(bot, chat, text)
    elif text.startswith("/broadcast") and _is_admin(uid):
        _admin_broadcast(bot, chat, text[len("/broadcast"):].strip())
    elif not text.startswith("/"):
        # treat any non-command text as a payment transaction id
        _record_proof(bot, chat, uid, uname, text)
    else:
        bot.send(chat, "Use the menu below.", _main_menu())


# ---------------- callbacks ----------------

def handle_callback(bot: Bot, cb: dict) -> None:
    data = cb.get("data", "")
    chat = cb["message"]["chat"]["id"]
    mid = cb["message"]["message_id"]
    uid = cb["from"]["id"]
    uname = cb["from"].get("username", "")
    bot.answer_callback(cb["id"])

    if data == "trial":
        u = provision.ensure_user(uid, uname)
        if u.trial_used:
            bot.edit(chat, mid, "You've already used your free trial. Tap <b>Buy premium</b> for more.", _main_menu())
            return
        r = provision.provision_for(uid, uname, days=TRIAL_DAYS, gb=TRIAL_GB, plan="trial", limit_ip=1)
        if not r["ok"]:
            bot.edit(chat, mid, f"⚠️ {r['error']}", _main_menu()); return
        with session() as db:
            bu = db.get(BotUser, uid); bu.trial_used = 1; db.commit()
        bot.edit(chat, mid, f"🎁 <b>Free trial ready</b> ({TRIAL_GB} GB / {TRIAL_DAYS} day)\n\n<code>{r['link']}</code>")
    elif data == "buy":
        kb = [[btn(p[0], f"pay:{i}")] for i, p in enumerate(PLANS)] + [[btn("« Back", "home")]]
        bot.edit(chat, mid, "💎 <b>Choose a plan</b>", kb)
    elif data.startswith("pay:"):
        idx = int(data.split(":")[1]); label, days, gb, amount = PLANS[idx]
        with session() as db:
            db.add(BotPayment(telegram_id=uid, plan_days=days, plan_gb=gb, amount=amount)); db.commit()
        bot.edit(chat, mid, f"🧾 <b>{label}</b> — {amount}\n\n{PAY_INSTRUCTIONS}")
    elif data == "account":
        bot.edit(chat, mid, _account_text(uid), [[btn("« Back", "home")]])
    elif data == "home":
        bot.edit(chat, mid, "Main menu:", _main_menu())
    elif data.startswith("approve:") and _is_admin(uid):
        _approve(bot, chat, mid, int(data.split(":")[1]))
    elif data.startswith("reject:") and _is_admin(uid):
        _reject(bot, chat, mid, int(data.split(":")[1]))


def _record_proof(bot: Bot, chat: int, uid: int, uname: str, proof: str) -> None:
    with session() as db:
        p = (db.query(BotPayment).filter(BotPayment.telegram_id == uid, BotPayment.status == "pending")
               .order_by(BotPayment.id.desc()).first())
        if not p:
            bot.send(chat, "No pending order. Tap <b>Buy premium</b> first.", _main_menu()); return
        p.proof = proof[:250]; db.commit()
        pid, days, gb, amount = p.id, p.plan_days, p.plan_gb, p.amount
    bot.send(chat, "✅ Proof received — an admin will approve it shortly.")
    kb = [[btn("✅ Approve", f"approve:{pid}"), btn("❌ Reject", f"reject:{pid}")]]
    for admin in config.admin_ids():
        bot.send(admin, f"💰 <b>Payment #{pid}</b>\nUser: @{uname} ({uid})\nPlan: {days}d / "
                        f"{'∞' if not gb else str(gb)+'GB'} — {amount}\nTxn: <code>{proof[:80]}</code>", kb)


def _approve(bot: Bot, chat: int, mid: int, pid: int) -> None:
    with session() as db:
        p = db.get(BotPayment, pid)
        if not p or p.status != "pending":
            bot.edit(chat, mid, "Already handled."); return
        p.status = "approved"; db.commit()
        tid, days, gb = p.telegram_id, p.plan_days, p.plan_gb
    r = provision.provision_for(tid, "", days=days, gb=gb, plan="premium", limit_ip=2)
    if not r["ok"]:
        bot.edit(chat, mid, f"⚠️ Provisioning failed: {r['error']}"); return
    bot.edit(chat, mid, f"✅ Payment #{pid} approved & provisioned.")
    bot.send(tid, f"🎉 <b>Payment approved!</b> Your premium config:\n\n<code>{r['link']}</code>")


def _reject(bot: Bot, chat: int, mid: int, pid: int) -> None:
    with session() as db:
        p = db.get(BotPayment, pid)
        if p and p.status == "pending":
            p.status = "rejected"; tid = p.telegram_id; db.commit()
            bot.send(tid, "❌ Your payment could not be verified. Please contact support.")
    bot.edit(chat, mid, f"❌ Payment #{pid} rejected.")


# ---------------- admin ----------------

def _admin_panel(bot: Bot, chat: int) -> None:
    from ..models import Client, Inbound, SSHAccount
    with session() as db:
        users = db.query(BotUser).count()
        clients = db.query(Client).count()
        ssh = db.query(SSHAccount).count()
        pending = db.query(BotPayment).filter(BotPayment.status == "pending").count()
        inbounds = db.query(Inbound).filter(Inbound.enabled.is_(True)).count()
    bot.send(chat, f"🛠 <b>Admin</b>\nBot users: {users}\nClients: {clients}\nSSH: {ssh}\n"
                   f"Enabled inbounds: {inbounds}\nPending payments: {pending}\n\n"
                   f"<b>Commands</b>\n/newvless &lt;inbound_id&gt; &lt;email&gt;\n"
                   f"/newssh &lt;user&gt; &lt;pass&gt; &lt;days&gt;\n/broadcast &lt;text&gt;")


def _admin_newvless(bot: Bot, chat: int, text: str) -> None:
    from ..core import links, provisioning
    from ..core.provisioning import ProvisionError
    from ..models import Inbound
    parts = text.split()
    if len(parts) < 3:
        bot.send(chat, "Usage: /newvless &lt;inbound_id&gt; &lt;email&gt;"); return
    try:
        ib_id, email = int(parts[1]), parts[2]
    except ValueError:
        bot.send(chat, "inbound_id must be a number."); return
    with session() as db:
        ib = db.get(Inbound, ib_id)
        if not ib:
            bot.send(chat, "No such inbound."); return
        try:
            c = provisioning.create_client(db, ib, email=email)
        except ProvisionError as exc:
            bot.send(chat, f"⚠️ {exc.detail}"); return
        bot.send(chat, f"✅ Created <b>{email}</b>\n\n<code>{links.client_link(ib, c)}</code>")


def _admin_newssh(bot: Bot, chat: int, text: str) -> None:
    from ..core import provisioning
    from ..core.provisioning import ProvisionError
    parts = text.split()
    if len(parts) < 4:
        bot.send(chat, "Usage: /newssh &lt;user&gt; &lt;pass&gt; &lt;days&gt;"); return
    user, pw = parts[1], parts[2]
    try:
        days = int(parts[3])
    except ValueError:
        bot.send(chat, "days must be a number."); return
    with session() as db:
        try:
            acc = provisioning.create_ssh_account(db, username=user, password=pw, expiry_days=days)
        except ProvisionError as exc:
            bot.send(chat, f"⚠️ {exc.detail}"); return
    bot.send(chat, f"✅ SSH account <b>{user}</b> created ({days}d). Password: <code>{pw}</code>")


def _admin_broadcast(bot: Bot, chat: int, message: str) -> None:
    if not message:
        bot.send(chat, "Usage: /broadcast &lt;text&gt;"); return
    with session() as db:
        ids = [u.telegram_id for u in db.query(BotUser).all()]
    sent = 0
    for tid in ids:
        try:
            bot.send(tid, message); sent += 1
        except Exception:  # noqa: BLE001
            pass
    bot.send(chat, f"📣 Broadcast sent to {sent}/{len(ids)} users.")
