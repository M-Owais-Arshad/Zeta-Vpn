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


def init_db() -> None:
    """Create all tables. Import models first so they register with the metadata."""
    from . import models  # noqa: F401  (side-effect import registers tables)

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
