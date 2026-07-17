"""Generate nginx's per-inbound WS-family proxy include and reload it.

WS/HTTPUpgrade/XHTTP inbounds are always publicly reachable on the same
port nginx already owns (:80, see core/protocols.WS_FAMILY_NETWORKS) —
xray binds a private 127.0.0.1:<internal_port> for each one instead, and
this file is the set of nginx `location` blocks that route each inbound's
distinct path to its own internal port. install_nginx.sh wires
`include` for this file into the port-80 (and, once a domain is set, the
TLS) server block; this module owns the file's contents from then on.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Inbound
from . import protocols, services

# Deliberately NOT under /etc/nginx/conf.d/: nginx.conf's default
# `include conf.d/*.conf;` (http-context) would ALSO pick up any *.conf file
# placed there, but this file is just bare `location {}` blocks — only valid
# inside a `server {}`. Living outside conf.d/ means the ONLY place it's
# included is the explicit `include` install_nginx.sh puts inside the
# server block, in the right context. (The bug this fixes: nginx refused to
# start with "location directive is not allowed here" once this file had
# any content, because it was being parsed twice, once in http{} context.)
INCLUDE_PATH = Path("/etc/nginx/zeta-inbounds.conf")

_LOCATION_TEMPLATE = """    location {path} {{
        proxy_pass http://127.0.0.1:{internal_port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
        proxy_request_buffering off;
        tcp_nodelay on;
    }}"""


def _path_for(inbound: Inbound) -> str:
    return ((inbound.stream_settings or {}).get(inbound.network, {}) or {}).get("path") or "/"


def generate(db: Session) -> str:
    blocks = []
    inbounds = (
        db.query(Inbound)
        .filter(Inbound.enabled.is_(True), Inbound.internal_port.isnot(None))
        .order_by(Inbound.id.asc())
        .all()
    )
    for ib in inbounds:
        if not protocols.is_ws_family(ib.network):
            continue
        blocks.append(_LOCATION_TEMPLATE.format(path=_path_for(ib), internal_port=ib.internal_port))
    header = "# Managed by ZetaVPN — regenerated whenever a WS-family inbound changes.\n"
    return header + ("\n".join(blocks) + "\n" if blocks else "")


def sync(db: Session) -> services.CommandResult:
    """Regenerate the include file and reload nginx to pick it up.

    Writes in place rather than the usual write-tmp-then-rename dance:
    install.sh chowns this one file to 'zetavpn' (not the whole
    /etc/nginx/conf.d/, which is otherwise root-owned), and renaming into a
    directory the writer doesn't own fails even when the target file itself
    is writable — POSIX rename() needs write access on the *directory*, not
    just the file. In-place writes are safe here: we're the only writer, and
    nginx only reads this file when explicitly reloaded, right after.

    Best-effort: nginx not being installed/running (dev box) shouldn't block
    inbound CRUD — callers should log a failure here, not treat it as fatal.
    """
    content = generate(db)
    try:
        INCLUDE_PATH.write_text(content, encoding="utf-8")
    except OSError as exc:
        return services.CommandResult(False, 1, "", str(exc))
    return services.reload_or_restart("nginx")
