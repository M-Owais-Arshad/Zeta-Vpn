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
from .models import Client, Inbound, TrafficSnapshot
from .core import access_log, singbox, system_stats, xray

log = logging.getLogger("zeta.tasks")

# Clients we've already cut off, so we don't reload the core repeatedly.
_cut_clients: set[int] = set()

# Hysteresis for limit_ip: client_id -> monotonic-ish time it first went back
# under its IP cap. The flag is only cleared after IP_LIMIT_CLEAR_COOLDOWN of
# continuous under-limit, so a flapping client can't oscillate the core.
_ip_ok_since: dict[int, float] = {}
IP_LIMIT_CLEAR_COOLDOWN = 60.0

# The stats poll runs frequently (for a responsive "online" badge), but the
# dashboard throughput chart wants a longer history — so record a DB snapshot on
# a fixed wall-clock cadence, decoupled from the poll rate.
_SNAPSHOT_EVERY_SECONDS = 30.0
_last_snapshot_at = 0.0


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
        db.commit()

        # Clients with a non-zero delta this poll are still actively
        # transferring data even if Xray's access log logged their
        # connection minutes ago and never again (see access_log.py) —
        # used to keep genuinely-active clients from "going offline".
        active_emails = {email for email, rec in xray_stats["users"].items() if rec["up"] or rec["down"]}
        active_emails |= {email for email, rec in singbox_stats["users"].items() if rec["up"] or rec["down"]}
        _update_ip_limits(db, active_emails)

        # Always enforce, even when this poll gathered zero fresh stats (a
        # quiet Xray period, sing-box-only deployment, or Clash API briefly
        # unreachable must not skip cutting off already-expired/over-quota
        # clients — expiry/quota are evaluated against data already in the
        # DB, not against this poll's deltas).
        _enforce_limits(db)
        _enforce_ssh_logins(db)
        _accumulate_ssh_traffic(db)
    finally:
        db.close()

    _maybe_record_snapshot()


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


def _maybe_record_snapshot() -> None:
    """Record a throughput snapshot at most every _SNAPSHOT_EVERY_SECONDS,
    regardless of how often the stats loop polls — so speeding up the poll for a
    snappy online badge doesn't shrink the dashboard chart's time span."""
    global _last_snapshot_at
    now = time.time()
    if now - _last_snapshot_at >= _SNAPSHOT_EVERY_SECONDS:
        _last_snapshot_at = now
        _record_snapshot()


def _record_snapshot() -> None:
    try:
        snap = system_stats.snapshot()["net"]
    except Exception:  # noqa: BLE001
        return
    db = SessionLocal()
    try:
        db.add(TrafficSnapshot(rx_bytes=snap["rx_bytes"], tx_bytes=snap["tx_bytes"]))
        # Keep only the most recent ~2000 samples.
        count = db.query(func.count(TrafficSnapshot.id)).scalar() or 0
        if count > 2000:
            oldest = (
                db.query(TrafficSnapshot.id)
                .order_by(TrafficSnapshot.id.asc())
                .limit(count - 2000)
                .all()
            )
            ids = [row[0] for row in oldest]
            if ids:
                db.query(TrafficSnapshot).filter(TrafficSnapshot.id.in_(ids)).delete(
                    synchronize_session=False
                )
        db.commit()
    finally:
        db.close()


async def stats_loop() -> None:
    log.info("Traffic stats poller started (every %ss)", settings.stats_poll_seconds)
    while True:
        try:
            await asyncio.to_thread(_accumulate_once)
            auth_lib.login_guard.sweep_stale()
        except Exception as exc:  # noqa: BLE001
            log.warning("stats poll error: %s", exc)
        await asyncio.sleep(settings.stats_poll_seconds)
