"""Database engine, session factory and declarative base.

Uses SQLite via SQLAlchemy 2.x. SQLite is more than enough for a single-node
panel managing thousands of clients, and keeps the install dependency-free.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
    """Enable WAL + foreign keys for concurrency and referential integrity."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns() -> None:
    """Additive migration: create_all() only creates missing *tables*, never
    adds columns to an existing one. Add any column introduced after a DB was
    first created (SQLite supports ALTER TABLE ADD COLUMN)."""
    from sqlalchemy import inspect, text

    wanted = {
        "ssh_accounts": [("password", "VARCHAR(128)")],
        "inbounds": [("extra_ports", "JSON DEFAULT '[]'")],
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    _migrate_inbound_port_key(insp)


def _migrate_inbound_port_key(insp) -> None:  # noqa: ANN001
    """The port redesign added inbounds.port_key (unique) + internal_port, which
    a plain ALTER can't add with NOT NULL/UNIQUE on a populated SQLite table —
    so add them nullable, backfill port_key from existing rows, then create the
    unique index (skipping it if the old non-unique `port` produced duplicates).
    Idempotent: a fresh install already has both columns (create_all made them),
    so this whole function is a no-op there. Best-effort: never crash init_db."""
    import json as _json
    import logging

    from sqlalchemy import text

    log = logging.getLogger("zeta.db")
    if not insp.has_table("inbounds"):
        return
    existing = {c["name"] for c in insp.get_columns("inbounds")}
    if "port_key" in existing and "internal_port" in existing:
        return  # fresh/already-migrated schema
    from .core import protocols  # local import avoids an import cycle at load

    try:
        with engine.begin() as conn:
            if "internal_port" not in existing:
                conn.execute(text("ALTER TABLE inbounds ADD COLUMN internal_port INTEGER"))
            if "port_key" not in existing:
                conn.execute(text("ALTER TABLE inbounds ADD COLUMN port_key VARCHAR(80)"))
                rows = conn.execute(
                    text("SELECT id, port, protocol, network, stream_settings FROM inbounds")
                ).fetchall()
                seen: set[str] = set()
                dupes = False
                for rid, port, proto, network, stream in rows:
                    try:
                        ss = stream if isinstance(stream, dict) else _json.loads(stream or "{}")
                    except (ValueError, TypeError):
                        ss = {}
                    pk = protocols.compute_port_key(port, proto, network, ss)
                    if pk in seen:
                        dupes = True
                    seen.add(pk)
                    conn.execute(
                        text("UPDATE inbounds SET port_key = :pk WHERE id = :id"),
                        {"pk": pk, "id": rid},
                    )
                if dupes:
                    log.warning("port_key backfill found duplicates; skipping UNIQUE index")
                else:
                    conn.execute(
                        text("CREATE UNIQUE INDEX IF NOT EXISTS ix_inbounds_port_key "
                             "ON inbounds(port_key)")
                    )
    except Exception as exc:  # noqa: BLE001
        log.error("inbounds port_key migration failed: %s", exc)


def init_db() -> None:
    """Create all tables. Import models first so they register with the metadata."""
    from . import models  # noqa: F401  (side-effect import registers tables)

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
