"""Bot DB init + small session helper. Reuses the panel's engine/session so the
bot and panel share one SQLite file (single source of truth).

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

from contextlib import contextmanager

from ..db import Base, SessionLocal, engine


def init() -> None:
    """Create the bot's own tables (idempotent)."""
    from . import models  # noqa: F401  (register tables on the shared Base)
    Base.metadata.create_all(bind=engine)


@contextmanager
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
