"""Authentication endpoints: login, profile, password change, 2FA."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import auth as auth_lib
from ..db import get_db
from ..deps import client_ip, get_current_user
from ..models import AuditLog, User
from ..schemas import (
    ChangePassword,
    LoginRequest,
    TokenResponse,
    TotpSetup,
    TotpVerify,
    UserOut,
)
from ..config import settings

router = APIRouter()


def _audit(db: Session, actor: str, action: str, detail: str = "", ip: str = "") -> None:
    db.add(AuditLog(actor=actor, action=action, detail=detail, ip=ip))
    db.commit()


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    ip = client_ip(request)
    guard_key = f"{ip}:{body.username}"

    allowed, retry_after = auth_lib.login_guard.check(guard_key)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Try again in {retry_after}s.",
        )

    user = db.query(User).filter(User.username == body.username).first()
    # Always run a bcrypt verify, even for an unknown username (against a
    # fixed dummy hash) — otherwise a missing user short-circuits in
    # microseconds while a real one takes ~100ms, letting an attacker
    # enumerate valid usernames purely from response timing.
    password_ok = auth_lib.verify_password(
        body.password, user.password_hash if user else auth_lib.DUMMY_PASSWORD_HASH
    )
    if user is None or not password_ok:
        auth_lib.login_guard.record_failure(guard_key)
        _audit(db, body.username, "login_failed", "bad credentials", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    if user.totp_enabled:
        if not body.totp or not auth_lib.verify_totp(user.totp_secret or "", body.totp):
            auth_lib.login_guard.record_failure(guard_key)
            _audit(db, user.username, "login_failed", "bad 2FA", ip)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid 2FA code")

    auth_lib.login_guard.record_success(guard_key)
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    _audit(db, user.username, "login_ok", "", ip)

    token = auth_lib.create_access_token(user.username, user.role, extra={"ver": user.token_version})
    return TokenResponse(
        access_token=token,
        role=user.role,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/change-password")
def change_password(
    body: ChangePassword,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not auth_lib.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = auth_lib.hash_password(body.new_password)
    user.token_version = (user.token_version or 0) + 1  # invalidate existing sessions
    db.commit()
    _audit(db, user.username, "password_changed", "", client_ip(request))
    return {"ok": True}


@router.post("/totp/setup", response_model=TotpSetup)
def totp_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TotpSetup:
    from ..core.links import qr_data_url

    # Refuse to re-provision while 2FA is already enabled: login authenticates
    # against user.totp_secret whenever totp_enabled is set, so overwriting it here
    # would make an UNVERIFIED new secret the live login secret immediately and can
    # lock the admin out. Disable 2FA first to re-provision.
    if user.totp_enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "2FA is already enabled — disable it first to set up a new authenticator.",
        )
    secret = auth_lib.new_totp_secret()
    user.totp_secret = secret  # stored but not active until verified/enabled
    db.commit()
    uri = auth_lib.totp_uri(secret, user.username)
    return TotpSetup(secret=secret, uri=uri, qr=qr_data_url(uri))


@router.post("/totp/enable")
def totp_enable(
    body: TotpVerify, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    if not user.totp_secret or not auth_lib.verify_totp(user.totp_secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid 2FA code")
    user.totp_enabled = True
    db.commit()
    return {"ok": True}


@router.post("/totp/disable")
def totp_disable(
    body: TotpVerify, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    if user.totp_enabled and not auth_lib.verify_totp(user.totp_secret or "", body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid 2FA code")
    user.totp_enabled = False
    user.totp_secret = None
    db.commit()
    return {"ok": True}
