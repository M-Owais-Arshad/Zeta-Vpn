"""Bot-owned tables (commerce state the panel has no equivalent for). They use
the panel's declarative Base + engine, so they live in the same SQLite DB and
are created by ``bot.db.init()``.

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BotUser(Base):
    """A Telegram user of the bot, linked to the panel Client they were given."""

    __tablename__ = "bot_users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    client_email: Mapped[str | None] = mapped_column(String(128), nullable=True)  # panel Client.email
    plan: Mapped[str] = mapped_column(String(32), default="none")  # none|trial|premium
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|active|banned
    trial_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class BotPayment(Base):
    """A manual payment submitted by a user, awaiting admin approve/reject."""

    __tablename__ = "bot_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan_days: Mapped[int] = mapped_column(Integer, default=30)
    plan_gb: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    amount: Mapped[int] = mapped_column(Integer, default=0)   # in your currency
    proof: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
