"""Per-account SSH POST-auth banner files.

After a tunnel account authenticates, its login shell (scripts/zeta-tunnel-shell)
prints ``<settings.ssh_info_dir>/<username>.txt`` — a live snapshot of THAT
account's status (data used / cap / remaining, expiry, days left, policy). The
panel is the only writer; the wrapper only ever reads its own file.

Everything here is best-effort: a write failure must never break account
provisioning or the stats poller. Writes are atomic (temp + ``os.replace``) so a
tunnel user reading concurrently never sees a half-written file, and so the panel
(running as ``zetavpn``) can overwrite a file first seeded by ``root`` at install
— ``rename`` only needs write permission on the directory, which ``zetavpn`` owns.
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings

_GB = 1024 ** 3
_MB = 1024 ** 2
_KB = 1024


def _fmt_bytes(n: int) -> str:
    n = n or 0
    if n >= _GB:
        return f"{n / _GB:.2f} GB"
    if n >= _MB:
        return f"{n / _MB:.1f} MB"
    if n >= _KB:
        return f"{n / _KB:.0f} KB"
    return f"{n} B"


def _days_left(expiry: datetime | None) -> str:
    if expiry is None:
        return "never"
    # expiry_date is stored tz-aware (UTCDateTime); guard a naive value anyway so
    # the subtraction can never raise the naive-vs-aware TypeError.
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    secs = (expiry - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        return "expired"
    # Round UP to match the panel UI (app.js uses Math.ceil), so a freshly-created
    # 30-day account's banner shows "30", not "29".
    return str(math.ceil(secs / 86400))


def render_banner(acc, brand: str = "ZetaVPN") -> str:
    """Render one account's post-auth banner text (HTTP-Custom style)."""
    used = acc.used_bytes or 0
    cap = acc.total_bytes or 0
    if cap:
        data_line = f"{_fmt_bytes(used)} / {_fmt_bytes(cap)}"
        remaining = _fmt_bytes(max(0, cap - used))
    else:
        data_line = f"{_fmt_bytes(used)} / unlimited"
        remaining = "unlimited"

    if not acc.enabled:
        status = "locked"
    elif acc.is_quota_exceeded:
        status = "data limit reached"
    elif acc.expiry_date is not None and _days_left(acc.expiry_date) == "expired":
        status = "expired"
    else:
        status = "active"

    expires = acc.expiry_date.strftime("%Y-%m-%d") if acc.expiry_date else "never"
    login = "unlimited" if not acc.max_login else f"{acc.max_login} device(s)"

    line = "=" * 42
    sep = "-" * 42

    def row(label: str, value: str) -> str:
        return f"  {label:<10}: {value}"

    return "\n".join([
        "",
        line,
        f"  {brand}",
        line,
        row("Username", str(acc.username)),
        row("Status", status),
        row("Login", login),
        row("Data used", data_line),
        row("Remaining", remaining),
        row("Expires", expires),
        row("Days left", _days_left(acc.expiry_date)),
        sep,
        "  No spam  |  No abuse  |  No DDoS",
        "  Extra logins beyond the limit are dropped",
        line,
        "",
    ]) + "\n"


def _path(username: str) -> Path:
    return Path(settings.ssh_info_dir) / f"{username}.txt"


def write_info(acc, brand: str = "ZetaVPN") -> None:
    """Render + atomically write one account's banner file. Best-effort."""
    try:
        d = Path(settings.ssh_info_dir)
        d.mkdir(parents=True, exist_ok=True)
        text = render_banner(acc, brand)
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=f".{acc.username}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, 0o644)  # world-readable: the wrapper reads it as the tunnel user
            os.replace(tmp, str(_path(acc.username)))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001
        pass


def remove_info(username: str) -> None:
    """Delete an account's banner file (on account deletion). Best-effort."""
    try:
        _path(username).unlink()
    except OSError:
        pass


def _brand(db) -> str:  # noqa: ANN001
    """The admin's brand name (Settings), defaulting to ZetaVPN. Best-effort."""
    try:
        from ..models import Setting

        s = db.get(Setting, "brand")
        if s and s.value:
            return s.value
    except Exception:  # noqa: BLE001
        pass
    return "ZetaVPN"


def write_account(db, acc) -> None:  # noqa: ANN001
    """Refresh one account's banner file with the current brand. Best-effort."""
    write_info(acc, _brand(db))


def write_all(db) -> None:  # noqa: ANN001
    """Seed / refresh every account's banner file (startup + each stats poll).
    Best-effort: one bad account never stops the rest."""
    from ..models import SSHAccount

    try:
        brand = _brand(db)
        for acc in db.query(SSHAccount).all():
            write_info(acc, brand)
    except Exception:  # noqa: BLE001
        pass
