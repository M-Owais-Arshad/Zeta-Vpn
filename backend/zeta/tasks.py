"""Background tasks: traffic accounting and quota/expiry enforcement.

Runs inside the panel process as an asyncio task. Every ``stats_poll_seconds`` it
reads (and resets) Xray's counters, accumulates them into the DB, records a
throughput snapshot for the dashboard chart, and — when a client crosses its
quota or expiry — reloads the affected core so the credential stops working.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import func

from .config import settings
from .db import SessionLocal
from .models import Client, Inbound, TrafficSnapshot
from .core import singbox, system_stats, xray

log = logging.getLogger("zeta.tasks")

# Clients we've already cut off, so we don't reload the core repeatedly.
_cut_clients: set[int] = set()


def _accumulate_once() -> None:
    stats = xray.query_stats(reset=True)
    if not stats["users"] and not stats["inbounds"]:
        _record_snapshot()
        return

    db = SessionLocal()
    try:
        # Per-client usage.
        for email, rec in stats["users"].items():
            for client in db.query(Client).filter(Client.email == email).all():
                client.up = (client.up or 0) + rec["up"]
                client.down = (client.down or 0) + rec["down"]
        # Per-inbound usage.
        for tag, rec in stats["inbounds"].items():
            ib = db.query(Inbound).filter(Inbound.tag == tag).first()
            if ib:
                ib.up = (ib.up or 0) + rec["up"]
                ib.down = (ib.down or 0) + rec["down"]
        db.commit()

        _enforce_limits(db)
    finally:
        db.close()

    _record_snapshot()


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
