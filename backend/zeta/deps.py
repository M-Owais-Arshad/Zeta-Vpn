"""Shared FastAPI dependencies (current user, role gating, client IP)."""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .auth import decode_token
from .db import get_db
from .models import User

bearer_scheme = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    """Best-effort client IP.

    Deliberately does NOT re-parse X-Forwarded-For here: uvicorn's
    ProxyHeadersMiddleware (wired up in main.py with `forwarded_allow_ips`
    limited to `settings.trusted_proxies`) already resolves the real client
    IP into ``request.client`` when the connection came through a trusted
    reverse proxy, and leaves it as the raw TCP peer otherwise. Re-reading the
    header here would let a direct connection spoof its own X-Forwarded-For
    and bypass the login brute-force lockout / falsify audit logs.
    """
    return request.client.host if request.client else "unknown"


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    # Reject tokens issued before the last password change.
    if payload.get("ver", 0) != (user.token_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked — please sign in again")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator privileges required")
    return user
