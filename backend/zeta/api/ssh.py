"""SSH / tunnelling account management endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import auth as auth_lib
from ..db import get_db
from ..deps import require_admin
from ..models import SSHAccount, User
from ..core import ssh_manager
from ..schemas import SSHAccountCreate, SSHAccountOut

router = APIRouter()


def _to_out(
    acc: SSHAccount,
    online_counts: dict[str, int] | None = None,
    online_sessions: dict[str, list[str]] | None = None,
) -> SSHAccountOut:
    out = SSHAccountOut.model_validate(acc)
    counts = online_counts if online_counts is not None else ssh_manager.online_counts()
    out.online = counts.get(acc.username, 0)
    sessions = online_sessions if online_sessions is not None else ssh_manager.online_sessions()
    out.online_ips = sessions.get(acc.username, [])
    return out


@router.get("", response_model=list[SSHAccountOut])
def list_accounts(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[SSHAccountOut]:
    accounts = db.query(SSHAccount).order_by(SSHAccount.id).all()
    # One `ps` + one privileged `ss` for the whole list, not per account.
    counts = ssh_manager.online_counts()
    sessions = ssh_manager.online_sessions()
    return [_to_out(a, counts, sessions) for a in accounts]


@router.post("/refresh-traffic", response_model=list[SSHAccountOut])
def refresh_traffic(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[SSHAccountOut]:
    """Force an immediate SSH-traffic poll (read the per-account cgroup byte
    counters right now and fold them into each account's running total) instead
    of waiting for the ~5s background poller, then return the freshened list.
    Serialized with the poller by ssh_manager._traffic_lock so the read-and-zero
    counters are never double-read. Runs in FastAPI's threadpool (sync def), so
    the brief privileged read never blocks the event loop."""
    from ..tasks import _accumulate_ssh_traffic  # lazy: avoid import cycle at load

    _accumulate_ssh_traffic(db)
    accounts = db.query(SSHAccount).order_by(SSHAccount.id).all()
    counts = ssh_manager.online_counts()
    sessions = ssh_manager.online_sessions()
    return [_to_out(a, counts, sessions) for a in accounts]


@router.post("", response_model=SSHAccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    body: SSHAccountCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> SSHAccountOut:
    try:
        ssh_manager.validate_username(body.username)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    if db.query(SSHAccount).filter(SSHAccount.username == body.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")

    expiry = datetime.now(timezone.utc) + timedelta(days=body.expiry_days) if body.expiry_days else None
    res = ssh_manager.create_account(body.username, body.password, expiry)
    if not res.ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"useradd failed: {res.stderr}")

    acc = SSHAccount(
        username=body.username,
        password_hash=auth_lib.hash_password(body.password),
        password=body.password,  # stored so the owner can view/copy it later
        max_login=body.max_login,
        expiry_date=expiry,
        comment=body.comment,
        enabled=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    # A just-created system user has no sessions — skip the ps/ss online scans.
    return _to_out(acc, {}, {})


@router.post("/{account_id}/lock", response_model=SSHAccountOut)
def lock_account(
    account_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> SSHAccountOut:
    acc = db.get(SSHAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    ssh_manager.lock(acc.username)
    ssh_manager.kill_sessions(acc.username)
    acc.enabled = False
    db.commit()
    db.refresh(acc)
    # Sessions were just killed — no live IPs to scan for.
    return _to_out(acc, {}, {})


@router.post("/{account_id}/unlock", response_model=SSHAccountOut)
def unlock_account(
    account_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> SSHAccountOut:
    acc = db.get(SSHAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    ssh_manager.unlock(acc.username)
    acc.enabled = True
    db.commit()
    db.refresh(acc)
    return _to_out(acc)


@router.post("/{account_id}/renew", response_model=SSHAccountOut)
def renew_account(
    account_id: int, days: int = 30, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> SSHAccountOut:
    acc = db.get(SSHAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    base = acc.expiry_date or datetime.now(timezone.utc)
    if base < datetime.now(timezone.utc):
        base = datetime.now(timezone.utc)
    acc.expiry_date = base + timedelta(days=days)
    ssh_manager.set_expiry(acc.username, acc.expiry_date)
    db.commit()
    db.refresh(acc)
    return _to_out(acc)


@router.post("/{account_id}/reset-traffic", response_model=SSHAccountOut)
def reset_traffic(
    account_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> SSHAccountOut:
    acc = db.get(SSHAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    acc.used_bytes = 0
    db.commit()
    db.refresh(acc)
    return _to_out(acc)


@router.delete("/{account_id}")
def delete_account(
    account_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> dict:
    acc = db.get(SSHAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    ssh_manager.kill_sessions(acc.username)
    ssh_manager.delete_account(acc.username)
    db.delete(acc)
    db.commit()
    return {"ok": True}
