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
from ..schemas import SSHAccountCreate, SSHAccountOut, SSHAccountUpdate

router = APIRouter()

_GB = 1024 ** 3  # gigabyte in bytes — same convention as clients (api/clients.py)


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


def _drop_quota_cut(account_id: int) -> None:
    """Forget a data-cap lock memo entry (in tasks._ssh_cut) after we've manually
    unlocked an account here, so the background poller doesn't believe it's still
    cut. Lazy import avoids a load-time cycle; best-effort."""
    try:
        from ..tasks import _ssh_cut

        _ssh_cut.discard(account_id)
    except Exception:  # noqa: BLE001
        pass


def _add_quota_cut(account_id: int) -> None:
    """Record that we've OS-locked this account for exceeding its cap, matching
    the poller's memo (tasks._ssh_cut) so it won't repeat the lock. Best-effort."""
    try:
        from ..tasks import _ssh_cut

        _ssh_cut.add(account_id)
    except Exception:  # noqa: BLE001
        pass


def _reconcile_os_lock(acc) -> None:  # noqa: ANN001
    """Bring the OS user's lock state (and the quota memo) in line with the
    account's intended state after a mutation: it may authenticate ONLY when it is
    enabled AND under its data cap — anything else means ``usermod -L`` + drop live
    sessions. Re-asserting here in ONE place is essential because a password change
    (``chpasswd``) rewrites the shadow field and thereby clears a ``usermod -L``
    lock, and because unlocking an over-cap account must not strand a stale memo
    entry that would stop the poller re-locking it."""
    if (not acc.enabled) or acc.is_quota_exceeded:
        ssh_manager.lock(acc.username)
        ssh_manager.kill_sessions(acc.username)
        # Enabled-but-over-cap is the poller's "cut" state; keep the memo in sync
        # so _enforce_ssh_quota doesn't redundantly re-lock every poll.
        if acc.enabled and acc.is_quota_exceeded:
            _add_quota_cut(acc.id)
        else:
            _drop_quota_cut(acc.id)
    else:
        ssh_manager.unlock(acc.username)
        _drop_quota_cut(acc.id)


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
        total_bytes=int(body.total_gb * _GB),  # data cap (0 = unlimited)
        comment=body.comment,
        enabled=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    # A just-created system user has no sessions — skip the ps/ss online scans.
    return _to_out(acc, {}, {})


@router.patch("/{account_id}", response_model=SSHAccountOut)
def update_account(
    account_id: int,
    body: SSHAccountUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> SSHAccountOut:
    """Edit an existing SSH account in one place (mirrors clients' PATCH). Only
    the fields the admin actually changed are applied, and each change is kept in
    sync with the OS user: password -> chpasswd, expiry -> chage, enable/disable
    -> usermod -U/-L (+ kill sessions on disable). The GB cap / max_login /
    comment are plain column updates; the stats poller enforces them."""
    acc = db.get(SSHAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    data = body.model_dump(exclude_unset=True)

    # Password -> reset the OS user's password, refresh hash + stored plaintext.
    if data.get("password") is not None:
        pw = data.pop("password")
        res = ssh_manager.set_password(acc.username, pw)
        if not res.ok:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"password change failed: {res.stderr}")
        acc.password_hash = auth_lib.hash_password(pw)
        acc.password = pw
    else:
        data.pop("password", None)

    # GB cap -> bytes (0 = unlimited).
    if "total_gb" in data:
        acc.total_bytes = int((data.pop("total_gb") or 0) * _GB)

    # Expiry -> re-anchor N days from now (0 = never), and keep the OS user in
    # sync so a lapsed date actually blocks login. Mirrors client edit semantics
    # (absolute re-anchor), distinct from the additive /renew endpoint.
    if "expiry_days" in data:
        days = data.pop("expiry_days")
        acc.expiry_date = (datetime.now(timezone.utc) + timedelta(days=days)) if days else None
        ssh_manager.set_expiry(acc.username, acc.expiry_date)

    # Enable/disable is applied as a flag here; the actual OS lock is reconciled
    # once at the end (a password change above cleared any usermod -L, so the lock
    # state must be re-asserted in a single place after ALL fields are applied).
    if "enabled" in data:
        acc.enabled = data.pop("enabled")

    # Remaining plain columns: max_login, comment. Skip an explicit null so a
    # `{"comment": null}` body can't hit the NOT NULL column (500) — an unset
    # field simply keeps its current value.
    for key, value in data.items():
        if value is not None:
            setattr(acc, key, value)

    db.commit()
    db.refresh(acc)
    # Single source of truth for the OS lock: enabled AND under-cap -> unlock,
    # else lock + drop sessions. Fixes the chpasswd-clears-lock bypass and keeps
    # the quota memo consistent regardless of which fields changed.
    _reconcile_os_lock(acc)
    return _to_out(acc)


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
    acc.enabled = True
    db.commit()
    db.refresh(acc)
    _reconcile_os_lock(acc)  # unlock, or re-lock immediately if still over the cap
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
    # Under cap again -> reconcile lifts any data-cap lock right away (unless the
    # admin has the account disabled), instead of waiting for the next poll.
    _reconcile_os_lock(acc)
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
