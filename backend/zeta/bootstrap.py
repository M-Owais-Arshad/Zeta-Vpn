"""First-run bootstrap: ensure an admin user exists and seed default settings."""

from __future__ import annotations

import logging
import os
import secrets
import stat

from sqlalchemy.orm import Session

from .auth import hash_password
from .config import settings
from .models import Setting, User

log = logging.getLogger("zeta.bootstrap")


def ensure_admin(db: Session) -> None:
    if db.query(User).count() > 0:
        return

    username = os.getenv("ZETA_ADMIN_USERNAME", "admin")
    password = os.getenv("ZETA_ADMIN_PASSWORD")
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True

    db.add(User(username=username, password_hash=hash_password(password), role="admin"))
    db.commit()

    if generated:
        # Persist the one-time credentials somewhere only root can read.
        cred_file = settings.data_dir / "initial_admin.txt"
        cred_file.write_text(
            f"ZetaVPN initial admin credentials\nusername: {username}\npassword: {password}\n",
            encoding="utf-8",
        )
        try:
            cred_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
        log.warning(
            "Created admin '%s' with a generated password — see %s", username, cred_file
        )
    else:
        log.info("Created admin '%s' from environment", username)


def seed_settings(db: Session) -> None:
    defaults = {
        "server_address": settings.server_address,
        "server_domain": settings.server_domain,
        "brand": settings.brand,
    }
    for key, value in defaults.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value or ""))
    db.commit()
