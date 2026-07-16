"""Background tasks: traffic accounting and quota/expiry/IP-limit enforcement.

Runs inside the panel process as an asyncio task. Every ``stats_poll_seconds`` it
reads (and resets) both cores' traffic counters, accumulates them into the DB,
updates each client's concurrent-IP status from the Xray access log, records a
throughput snapshot for the dashboard chart, and — when a client crosses its
quota, expiry or IP limit — reloads the affected core so the credential stops
working.
"""

from __future__ import annotations

import asyncio
import contextlib
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
    for client in db.query(Client).filter(Client.limit_ip > 0).all():
        exceeded = counts.get(client.email, 0) > client.limit_ip
        if exceeded != client.ip_limit_exceeded:
            client.ip_limit_exceeded = exceeded
    db.commit()


def _enforce_limits(db) -> None:  # noqa: ANN001
    """Reload cores if any enabled client just became unusable (quota/expiry)."""
    enabled = db.query(Client).filter(Client.enabled.is_(True)).all()
    # Prune ids of clients deleted/disabled since we last cut them, so a reused
    # SQLite rowid can never masquerade as a previously-cut client.
    _cut_clients.intersection_update({c.id for c in enabled})

    newly_cut: dict[str, bool] = {}
    for client in enabled:
        if not client.is_usable and client.id not in _cut_clients:
            _cut_clients.add(client.id)
            newly_cut[client.inbound.core] = True
        elif client.is_usable and client.id in _cut_clients:
            _cut_clients.discard(client.id)  # e.g. quota reset / renewed
            newly_cut[client.inbound.core] = True

    if newly_cut.get("xray"):
        log.info("Reloading Xray to enforce client limits")
        xray.apply(db)
    if newly_cut.get("singbox"):
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


@contextlib.asynccontextmanager
async def lifespan_tasks():
    task = asyncio.create_task(stats_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
