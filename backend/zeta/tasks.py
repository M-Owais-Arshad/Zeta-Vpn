"""Background tasks: traffic accounting and quota/expiry/IP-limit enforcement.

Runs inside the panel process as an asyncio task. Every ``stats_poll_seconds`` it
reads (and resets) both cores' traffic counters, accumulates them into the DB,
updates each client's concurrent-IP status from the Xray access log, records a
throughput snapshot for the dashboard chart, and — when a client crosses its
quota, expiry or IP limit — cuts that one credential on the LIVE Xray core via
the HandlerService API (no restart, so no other tunnel drops), falling back to a
full core reload only for protocols/cores without a live user API.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import func

from . import auth as auth_lib
from .config import settings
from .db import SessionLocal
from .models import Client, Inbound
from .core import access_log, singbox, xray

log = logging.getLogger("zeta.tasks")

# Clients we've already cut off, so we don't reload the core repeatedly.
_cut_clients: set[int] = set()

# SSH accounts we've already locked for exceeding their data cap, so we don't
# re-run usermod/pkill every poll. Mirrors _cut_clients — but an SSH account is a
# real OS user with no live-credential API, so enforcement is an explicit
# lock + kill rather than removing one credential from a running core.
_ssh_cut: set[int] = set()

# Rewrite the per-account SSH banner files (data-used / days-left) at most this
# often. A tiny write per account, but no need to churn every 5s. A 1-element
# list (not a rebindable global) so the poll body needs no `global` declaration.
_banner_refresh_at = [0.0]
BANNER_REFRESH_INTERVAL = 30.0

# Hysteresis for limit_ip: client_id -> monotonic-ish time it first went back
# under its IP cap. The flag is only cleared after IP_LIMIT_CLEAR_COOLDOWN of
# continuous under-limit, so a flapping client can't oscillate the core.
_ip_ok_since: dict[int, float] = {}
IP_LIMIT_CLEAR_COOLDOWN = 60.0


def _accumulate_once() -> None:
    # Xray has a gRPC StatsService; sing-box only exposes live connections via
    # its Clash API. Both return the same {"users": {...}, "inbounds": {...}}
    # shape so they can be merged and processed identically.
    xray_stats = xray.query_stats(reset=True)
    singbox_stats = singbox.query_stats(reset=True)

    db = SessionLocal()
    try:
        # Batch the per-poll lookups into two IN() queries instead of one SELECT
        # per active email + per inbound tag (the poll runs every few seconds
        # regardless of whether a dashboard is open). Extra-port listeners are
        # tagged "<tag>@<port>" (see core/xray.build_inbounds), so fold their
        # traffic back into the base inbound; only strip a trailing "@<digits>"
        # (a port) — real tags may legitimately contain '@'.
        emails = set(xray_stats["users"]) | set(singbox_stats["users"])
        clients = {
            c.email: c for c in db.query(Client).filter(Client.email.in_(emails)).all()
        } if emails else {}
        tags = {_base_tag(t) for st in (xray_stats, singbox_stats) for t in st["inbounds"]}
        inbounds = {
            ib.tag: ib for ib in db.query(Inbound).filter(Inbound.tag.in_(tags)).all()
        } if tags else {}
        for stats in (xray_stats, singbox_stats):
            for email, rec in stats["users"].items():
                client = clients.get(email)
                if client:
                    client.up = (client.up or 0) + rec["up"]
                    client.down = (client.down or 0) + rec["down"]
            for tag, rec in stats["inbounds"].items():
                ib = inbounds.get(_base_tag(tag))
                if ib:
                    ib.up = (ib.up or 0) + rec["up"]
                    ib.down = (ib.down or 0) + rec["down"]

        # sing-box's Clash API can't attribute QUIC (Hysteria2/TUIC) traffic to a
        # specific user, so singbox_stats["users"] is empty. But for the common
        # ONE-client-per-inbound case we can bill the whole inbound's delta to that
        # single client, so its data-limit still enforces. (Multi-client QUIC
        # inbounds stay per-inbound-only — a sing-box API limitation.)
        singbox_active: set[str] = set()
        for tag, rec in singbox_stats["inbounds"].items():
            ib = inbounds.get(_base_tag(tag))
            if not (ib and ib.core == "singbox") or not (rec["up"] or rec["down"]):
                continue
            solo = [c for c in ib.clients if c.enabled]
            if len(solo) == 1:
                c = solo[0]
                c.up = (c.up or 0) + rec["up"]
                c.down = (c.down or 0) + rec["down"]
                singbox_active.add(c.email)
        db.commit()

        # Clients with a non-zero delta this poll are still actively
        # transferring data even if Xray's access log logged their
        # connection minutes ago and never again (see access_log.py) —
        # used to keep genuinely-active clients from "going offline".
        active_emails = {email for email, rec in xray_stats["users"].items() if rec["up"] or rec["down"]}
        active_emails |= {email for email, rec in singbox_stats["users"].items() if rec["up"] or rec["down"]}
        active_emails |= singbox_active  # single-client QUIC inbounds that moved data this poll
        _update_ip_limits(db, active_emails)

        # Always enforce, even when this poll gathered zero fresh stats (a
        # quiet Xray period, sing-box-only deployment, or Clash API briefly
        # unreachable must not skip cutting off already-expired/over-quota
        # clients — expiry/quota are evaluated against data already in the
        # DB, not against this poll's deltas).
        _enforce_limits(db)
        _enforce_ssh_logins(db)
        _accumulate_ssh_traffic(db)
        _enforce_ssh_quota(db)  # after accumulation, so used_bytes is this poll's freshest
        # Keep the post-auth banner files fresh (data-used / days-left), throttled.
        now = time.monotonic()
        if now - _banner_refresh_at[0] >= BANNER_REFRESH_INTERVAL:
            from .core import ssh_info

            ssh_info.write_all(db)
            _banner_refresh_at[0] = now
    finally:
        db.close()


def _base_tag(tag: str) -> str:
    """Strip a trailing "@<port>" extra-listener suffix to the base inbound tag."""
    base, sep, suffix = tag.rpartition("@")
    return base if sep and suffix.isdigit() else tag


def _enforce_ssh_logins(db) -> None:  # noqa: ANN001
    """Enforce SSHAccount.max_login by terminating the NEWEST excess tunnel
    sessions (the oldest max_login sessions are kept, so a legitimate user is
    never thrashed — only the over-limit devices keep getting kicked). Reuses
    the same per-user session count already trusted for the online badge."""
    from .core import ssh_manager
    from .models import SSHAccount

    accounts = (
        db.query(SSHAccount)
        .filter(SSHAccount.max_login > 0, SSHAccount.enabled.is_(True))
        .all()
    )
    if not accounts:
        return
    counts = ssh_manager.online_counts()
    for acc in accounts:
        if counts.get(acc.username, 0) > acc.max_login:
            ssh_manager.enforce_max_login(acc.username, acc.max_login)


def _accumulate_ssh_traffic(db) -> None:  # noqa: ANN001
    """Add each SSH account's byte delta (per-uid cgroup user-slice counter) to its
    running ``used_bytes`` total — the SSH equivalent of the Xray/sing-box
    per-user stats accumulation above. Best-effort: silently no-ops on a
    non-Linux/dev box (no `pwd`) or if the privileged counter read fails."""
    try:
        import pwd
    except ImportError:
        return
    from .core import ssh_manager
    from .models import SSHAccount

    accounts = db.query(SSHAccount).filter(SSHAccount.enabled.is_(True)).all()
    uid_to_acc = {}
    for acc in accounts:
        try:
            uid_to_acc[pwd.getpwnam(acc.username).pw_uid] = acc
        except KeyError:
            continue  # account row exists but the system user is gone
    if not uid_to_acc:
        return
    changed = False
    for uid, delta in ssh_manager.traffic_deltas(list(uid_to_acc)).items():
        acc = uid_to_acc.get(uid)
        if acc and delta > 0:
            # Atomic DB-side increment (not a Python read-modify-write): the 5s
            # poller and the /ssh/refresh-traffic endpoint run on separate threads
            # with separate sessions, and each already captured a DISJOINT delta
            # under _traffic_lock — but a stale-snapshot "used_bytes = X + delta"
            # from one would clobber the other's commit, permanently dropping an
            # interval's bytes. Letting the DB compute the new value avoids that.
            db.query(SSHAccount).filter(SSHAccount.id == acc.id).update(
                {SSHAccount.used_bytes: func.coalesce(SSHAccount.used_bytes, 0) + delta},
                synchronize_session=False,
            )
            changed = True
    if changed:
        db.commit()
        for acc in uid_to_acc.values():
            db.refresh(acc)  # expire_on_commit=False, so refresh for an accurate response


def _enforce_ssh_quota(db) -> None:  # noqa: ANN001
    """Lock + drop any enabled SSH account that has crossed its data cap
    (``total_bytes``), and unlock it again once it's back under the cap (traffic
    reset or the cap raised). ``enabled`` stays the admin's on/off intent; the OS
    lock is a separate quota-driven layer, exactly like a client's live cut.

    Restart-safe: the in-memory ``_ssh_cut`` memo only suppresses repeat
    usermod/pkill calls — the actual heal (unlock) also happens synchronously at
    the two action points that can move an account from over->under cap
    (reset-traffic and raising the cap in the PATCH edit), so a memo emptied by a
    panel restart can never leave an account stuck locked. Best-effort:
    ssh_manager.* no-ops on a non-root/dev box."""
    from .core import ssh_manager
    from .models import SSHAccount

    capped = (
        db.query(SSHAccount)
        .filter(SSHAccount.total_bytes > 0, SSHAccount.enabled.is_(True))
        .all()
    )
    # Forget memo entries for accounts disabled/deleted/uncapped since we cut
    # them, so a reused SQLite rowid can't masquerade as a still-cut account.
    _ssh_cut.intersection_update({a.id for a in capped})
    for acc in capped:
        # Re-read this row so the lock decision uses the freshest usage: without
        # it, a reset-traffic / cap-raise that committed on the request thread
        # AFTER the query above (but before this check) would be read stale and
        # could transiently re-lock + kill a just-freed account for one poll.
        db.refresh(acc)
        over = acc.is_quota_exceeded
        if over and acc.id not in _ssh_cut:
            ssh_manager.lock(acc.username)
            ssh_manager.kill_sessions(acc.username)
            _ssh_cut.add(acc.id)
            log.info("SSH account %s locked: data cap reached", acc.username)
        elif not over and acc.id in _ssh_cut:
            ssh_manager.unlock(acc.username)
            _ssh_cut.discard(acc.id)
            log.info("SSH account %s unlocked: back under data cap", acc.username)


def _update_ip_limits(db, active_emails: set[str]) -> None:  # noqa: ANN001
    """Flag/unflag clients currently exceeding their concurrent-IP cap.

    Only touches ``ip_limit_exceeded`` (a transient flag, unlike
    enabled/expiry/quota); ``_enforce_limits()`` picks up the resulting
    ``is_usable`` change the same way it already does for quota/expiry.
    """
    counts = access_log.poll_concurrent_ips(active_emails)
    # Merge sing-box's per-user IP snapshot (Hysteria2/TUIC) — its clients are
    # invisible to Xray's access log, so without this their limit_ip cap would
    # be a silent no-op. singbox.client_activity() is the same read-only,
    # window-filtered {email: [ip,...]} shape.
    for email, ips in singbox.client_activity().items():
        counts[email] = max(counts.get(email, 0), len(ips))
    now = time.time()
    # Self-heal: the loop below only visits limit_ip > 0 rows, so a client that was
    # flagged over-limit and then had its cap lifted (limit_ip set to 0 = unlimited)
    # would never get the flag cleared and would stay cut off FOREVER — enabled,
    # unexpired, under quota, uncapped, yet is_usable=False. Clear it here.
    db.query(Client).filter(
        Client.limit_ip == 0, Client.ip_limit_exceeded.is_(True)
    ).update({Client.ip_limit_exceeded: False}, synchronize_session=False)
    for client in db.query(Client).filter(Client.limit_ip > 0).all():
        exceeded = counts.get(client.email, 0) > client.limit_ip
        # HYSTERESIS: flag immediately on exceed, but only CLEAR after the client
        # has stayed under its cap for a sustained cooldown. Without this, a
        # client that persistently exceeds limit_ip (a phone on WiFi+cellular /
        # IPv4+IPv6 / CGNAT) oscillates every ~10s — cut, IPs age out, re-added,
        # reconnects, over-limit, cut — and each flip made _enforce_limits do a
        # full `systemctl restart` of the core, dropping EVERY user's tunnel:
        # the reported mid-transfer speed sawtooth. Now an over-limit client is
        # cut once and stays cut until it is genuinely back under the cap.
        if exceeded:
            _ip_ok_since.pop(client.id, None)
            if not client.ip_limit_exceeded:
                client.ip_limit_exceeded = True
        elif client.ip_limit_exceeded:
            first_ok = _ip_ok_since.setdefault(client.id, now)
            if now - first_ok >= IP_LIMIT_CLEAR_COOLDOWN:
                client.ip_limit_exceeded = False
                _ip_ok_since.pop(client.id, None)
        else:
            _ip_ok_since.pop(client.id, None)
    db.commit()


def _enforce_limits(db) -> None:  # noqa: ANN001
    """Bring the cores in line when a client just became unusable/usable
    (quota / expiry / ip-limit). Xray VLESS/VMess/Trojan transitions are applied
    to the LIVE process via HandlerService — that one user is added/removed with
    NO restart, so no OTHER tunnel is dropped (this is what stopped a single
    over-limit client from restart-storming the whole core). Anything the live
    API can't do (sing-box, legacy single-user protocols, or a failed live op)
    falls back to a full restart."""
    enabled = db.query(Client).filter(Client.enabled.is_(True)).all()
    # Prune ids of clients deleted/disabled since we last cut them, so a reused
    # SQLite rowid can never masquerade as a previously-cut client.
    live_ids = {c.id for c in enabled}
    _cut_clients.intersection_update(live_ids)
    for _gone in [cid for cid in _ip_ok_since if cid not in live_ids]:
        _ip_ok_since.pop(_gone, None)

    xray_restart = False   # a transition the live API couldn't do -> full apply
    xray_live = False      # at least one live add/remove landed -> persist config
    singbox_restart = False
    for client in enabled:
        newly = None
        if not client.is_usable and client.id not in _cut_clients:
            _cut_clients.add(client.id); newly = "cut"
        elif client.is_usable and client.id in _cut_clients:
            _cut_clients.discard(client.id); newly = "restore"  # quota reset / renewed
        if newly is None:
            continue
        ib = client.inbound
        if xray.supports_live_user_ops(ib):
            res = (xray.remove_user_live(ib, client.email) if newly == "cut"
                   else xray.add_user_live(ib, client))
            if res.ok:
                xray_live = True
                continue
            xray_restart = True   # live op failed -> fall back to a restart
        elif ib.core == "singbox":
            singbox_restart = True
        else:
            xray_restart = True   # non-live xray protocol (legacy SS / socks / http)

    if xray_restart:
        log.info("Reloading Xray to enforce client limits")
        xray.apply(db)                 # full write + restart (rebuilds all state)
    elif xray_live:
        xray.persist_config(db)        # sync the on-disk config, NO restart
    if singbox_restart:
        log.info("Reloading sing-box to enforce client limits")
        singbox.apply(db)


async def stats_loop() -> None:
    log.info("Traffic stats poller started (every %ss)", settings.stats_poll_seconds)
    while True:
        try:
            await asyncio.to_thread(_accumulate_once)
            auth_lib.login_guard.sweep_stale()
        except Exception as exc:  # noqa: BLE001
            log.warning("stats poll error: %s", exc)
        await asyncio.sleep(settings.stats_poll_seconds)
