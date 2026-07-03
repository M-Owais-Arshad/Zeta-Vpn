"""Authentication primitives: password hashing, JWT, TOTP, brute-force guard."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import pyotp

from .config import settings

# bcrypt hard-caps the password at 72 bytes; hashing more silently truncates.
_BCRYPT_MAX = 72


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_BCRYPT_MAX]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, role: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
        "iss": settings.brand,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decode & validate a JWT. Raises jwt.PyJWTError on any problem."""
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "sub"]},
    )


# --- TOTP (2FA) --------------------------------------------------------------

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=settings.brand)


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


# --- Brute-force / rate-limit guard -----------------------------------------

@dataclass
class _Bucket:
    fails: int = 0
    locked_until: float = 0.0
    stamps: list[float] = field(default_factory=list)


class LoginGuard:
    """In-memory, per-identity login throttle. Resets on restart (acceptable for
    a single-node panel; pair with fail2ban at the OS level for persistence)."""

    def __init__(self, max_attempts: int, lockout_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._buckets: dict[str, _Bucket] = {}

    def _bucket(self, key: str) -> _Bucket:
        return self._buckets.setdefault(key, _Bucket())

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        b = self._bucket(key)
        now = time.monotonic()
        if b.locked_until > now:
            return False, int(b.locked_until - now)
        return True, 0

    def record_failure(self, key: str) -> None:
        b = self._bucket(key)
        b.fails += 1
        if b.fails >= self.max_attempts:
            b.locked_until = time.monotonic() + self.lockout_seconds
            b.fails = 0

    def record_success(self, key: str) -> None:
        self._buckets.pop(key, None)


login_guard = LoginGuard(settings.login_max_attempts, settings.login_lockout_seconds)
