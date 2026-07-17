"""Runtime configuration for the ZetaVPN panel.

Settings are read from environment variables (prefix ``ZETA_``) and an optional
``.env`` file in ``ZETA_HOME``. Everything has a sane default so the panel boots
on a fresh install with no configuration at all — the installer writes a ``.env``
with a generated secret key and admin credentials.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Root of the deployment. Overridable so the same code runs from /opt/zetavpn on
# the server or from a checkout during development.
ZETA_HOME = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Central settings object, cached as a singleton via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="ZETA_",
        env_file=str(Path(ZETA_HOME, ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Identity / branding -------------------------------------------------
    brand: str = "ZetaVPN"
    version: str = "1.3.1"

    # --- Filesystem layout ---------------------------------------------------
    home: Path = ZETA_HOME
    data_dir: Path = Field(default=Path(ZETA_HOME, "data"))
    frontend_dir: Path = Field(default=Path(ZETA_HOME, "frontend"))

    # --- Panel HTTP server ---------------------------------------------------
    # Loopback-only: install.sh always puts nginx in front (with TLS when a
    # domain is configured, plain HTTP on :80 otherwise) — the panel itself
    # never needs to be reachable directly. Override ZETA_HOST if you're
    # intentionally running without the bundled nginx.
    host: str = "127.0.0.1"
    port: int = 2096
    # Obscured base path for the whole panel, e.g. "/zeta-a1b2c3". Empty = root.
    web_base_path: str = ""
    # Comma-separated allowed CORS origins. Empty = same-origin only (the safe
    # default; the UI is served from the panel itself and auth uses Bearer tokens,
    # so no cross-origin access is needed). Never ship "*".
    cors_origins: str = ""

    # --- Security ------------------------------------------------------------
    # HMAC secret for signing JWTs. MUST be overridden in production (.env).
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12  # 12h admin sessions
    # Login brute-force guard.
    login_max_attempts: int = 8
    login_lockout_seconds: int = 300

    # --- Reverse proxy trust --------------------------------------------------
    # Comma-separated IPs allowed to set X-Forwarded-For/-Proto (uvicorn's
    # ProxyHeadersMiddleware only honours these headers when the *direct* TCP
    # peer is in this list — e.g. the local nginx). Never "*": that lets any
    # direct connection spoof its source IP and bypass the login brute-force
    # lockout (keyed by IP) and falsify audit-log IPs.
    trusted_proxies: str = "127.0.0.1,::1"

    # --- Public server identity (used when building client links) ------------
    # The address clients connect to. Defaults to the server's own IP at runtime.
    server_address: str = ""
    server_domain: str = ""
    # Where the panel's TLS cert lives (real Let's Encrypt cert if a domain was
    # configured, else a self-signed one). Used as the default cert for a
    # direct-bind (non-nginx-fronted) TLS inbound so it works exactly like 3x-ui
    # — xray terminates TLS itself — without the admin hand-entering cert paths.
    cert_dir: Path = Path("/etc/zetavpn/certs")

    # --- Xray-core -----------------------------------------------------------
    xray_bin: Path = Path("/usr/local/bin/xray")
    xray_config: Path = Path("/usr/local/etc/xray/config.json")
    xray_assets: Path = Path("/usr/local/share/xray")
    xray_service: str = "zeta-xray"
    # Local gRPC API endpoint Xray exposes for stats/handler control.
    xray_api_host: str = "127.0.0.1"
    xray_api_port: int = 62789

    # --- sing-box ------------------------------------------------------------
    singbox_bin: Path = Path("/usr/local/bin/sing-box")
    singbox_config: Path = Path("/etc/sing-box/config.json")
    singbox_service: str = "zeta-singbox"
    # sing-box has no gRPC stats API like Xray; traffic is read from its
    # loopback-only Clash API (/connections), which the generated config
    # always enables. The secret is generated once per process and never
    # exposed outside the panel (nginx doesn't proxy this port).
    singbox_clash_api_host: str = "127.0.0.1"
    singbox_clash_api_port: int = 9190
    singbox_clash_api_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(24))

    # --- Service control -----------------------------------------------------
    # "systemd" on a real server; "none" disables service control (dev/testing).
    service_manager: str = "systemd"

    # --- SSH ------------------------------------------------------------------
    # Pre-auth banner shown to every SSH client on connect (OpenSSH `Banner` /
    # Dropbear `-b`). The admin sets the text from the panel; the panel writes it
    # here and sshd/dropbear read it fresh per connection, so no reload is needed
    # to change the message. Lives under data_dir so the unprivileged panel can
    # write it while root-run sshd can read it. install_ssh_stack.sh points the
    # daemons at this exact path.
    ssh_banner_file: Path = Field(default=Path(ZETA_HOME, "data", "ssh-banner.txt"))

    # --- Traffic accounting --------------------------------------------------
    # How often the cores' stats + access log are polled. Kept short so the
    # "online" badge reacts near-real-time: a proxy client drops to offline
    # within ~one poll + online_window of disconnecting. (The DB throughput
    # snapshot is time-gated separately in tasks.py, so a fast poll doesn't
    # shorten the dashboard chart history.)
    stats_poll_seconds: int = 5
    # Xray's access log is the only source of per-connection source IPs (the
    # gRPC stats API only gives byte counters) — used to enforce Client.limit_ip.
    xray_access_log: Path = Path("/var/log/zetavpn/xray-access.log")
    # How long since last activity before a source IP is dropped from the
    # concurrent-IP set that enforces Client.limit_ip. Backed by real traffic,
    # not just connection age — see access_log.py. Kept short (10s) so a
    # client's IP frees up almost immediately after they drop off, instead of a
    # stale/changed IP hogging the limit_ip slot for minutes.
    ip_limit_window_seconds: int = 10
    # Window for the UI "online" badge (client_activity()). IP last-seen stamps
    # refresh only at poll time, so this must stay >= the poll interval or a
    # still-connected client flaps offline between polls — client_activity()
    # enforces that floor (max with 2x the poll interval). At 12s (with a 5s
    # poll) a client drops to offline ~12s after it disconnects: about as snappy
    # as polling allows without a live per-connection query, which xray doesn't
    # expose per user the way `ss` does for SSH.
    online_window_seconds: int = 12

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir, "zeta.db")

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def base_path(self) -> str:
        """Normalised base path: '' or '/something' (no trailing slash)."""
        bp = (self.web_base_path or "").strip().strip("/")
        return f"/{bp}" if bp else ""

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
