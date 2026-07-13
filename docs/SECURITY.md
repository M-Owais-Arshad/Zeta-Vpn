# ZetaVPN — Security

This document covers ZetaVPN's threat model, hardening defaults, and the known classes of
vulnerability it is designed to avoid.

## Secure-by-default checklist (shipped on)

- **No default credentials.** The admin password is either supplied by you or randomly generated at
  install and saved root-only to `data/initial_admin.txt`. There is no `admin/admin`.
- **Secret panel path.** The panel is served under a random base path (`/zeta-xxxxxxxx`) so it isn't
  discoverable by scanning the port.
- **Password hashing** with bcrypt (cost 12), constant-time verification.
- **TOTP 2FA** available for every operator (Settings → Two-factor).
- **Brute-force lockout** — per IP+username; N failures trigger a timed lockout
  (`login_max_attempts` / `login_lockout_seconds`). Pair with fail2ban (installed) for persistence.
- **Session revocation.** Changing your password bumps a `token_version` embedded in issued JWTs,
  invalidating every existing session.
- **Same-origin by default.** CORS is off unless you configure `ZETA_CORS_ORIGINS`; the API uses
  bearer tokens, not cookies (no CSRF surface).
- **Security headers** on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
- **Verified downloads.** Core binaries are checked against their published SHA256 where available;
  a mismatch aborts the install.
- **Firewall + fail2ban** (`ufw` default-deny inbound, sshd jail) enabled by the installer.
- **Atomic config writes** and `xray -test` / `sing-box check` validation helpers prevent a
  half-written config from taking a core down.
- **Auditable scripts.** Every installer is plaintext bash + systemd — no obfuscated/compiled blobs.

## The privilege trade-off (read this)

ZetaVPN manages **system SSH users** (`useradd`/`chage`) and **services** (`systemctl`), but the
panel process itself does **not** run as root:

- All four units (`zeta-panel`, `zeta-xray`, `zeta-singbox`, `zeta-ws`) run as a dedicated,
  unprivileged `zetavpn` system user (`install.sh` creates it and `chown`s the directories each
  service needs — `ZETA_HOME`, `/usr/local/etc/xray`, `/etc/sing-box`, the cert dir).
- The panel's two genuinely-privileged jobs — SSH account lifecycle
  (`useradd`/`userdel`/`usermod`/`chpasswd`/`chage`) and reloading proxy/SSH-stack services
  (`systemctl restart/stop`) — are delegated through a narrowly scoped **NOPASSWD sudoers rule**
  (`/etc/sudoers.d/zetavpn-panel`, regenerated on every install/update) instead of the whole
  HTTP-facing app running as root. A bug in the API surface can no longer be turned into an
  arbitrary-file-write-as-root the way it could when the process itself was root (a well-known
  RCE class in root-running admin panels).
- The proxy **cores** (`zeta-xray`, `zeta-singbox`) additionally run with `ProtectSystem=strict`,
  `ProtectHome=true` and scoped `AmbientCapabilities` (`CAP_NET_BIND_SERVICE` to bind low ports
  without root) — they never need `sudo` and get the tightest sandbox of the four units.
- `zeta-panel` keeps `NoNewPrivileges=false` — this is required for `sudo` (a setuid binary) to
  work, not a sign it's unrestricted; its actual privileges are whatever the sudoers rule grants,
  nothing more.

Residual risk: the sudoers rule's argument wildcards (needed since usernames/dates are dynamic)
mean a *full remote-code-execution* bug in the panel process could still be leveraged to run those
specific commands with unintended arguments — sudoers command-matching can't validate semantics,
only shape. It cannot be used to run anything outside that fixed command list, and it's a large
reduction from "the entire process is root" either way.

Beyond that:

- Always deploy **behind a domain + TLS** and keep the secret path private.
- Restrict who can reach the panel port (bind nginx to the domain; consider a firewall allow-list or
  binding the panel to `127.0.0.1` behind nginx).
- Keep the box patched (the installer enables base updates); run `zeta update` regularly.

If you don't need SSH-account management, you can bind the panel to localhost and put it entirely
behind nginx + client-certificate or IP allow-listing.

## Known CVE classes to avoid (and how ZetaVPN does)

| Class | Typical cause | ZetaVPN mitigation |
|---|---|---|
| Default credentials | shipped `admin/admin` logins | Random/required password, no defaults |
| Insecure update fetch | unverified auto-updaters | SHA256-verified downloads; `zeta update` uses git |
| Authenticated config-injection → RCE | string-built core configs run as root | Configs rendered from typed DB fields; JSON passed to cores, not shelled; cores run non-root |

## Hardening you should still do

1. Change the admin password on first login and **enable 2FA**.
2. Point a **domain** at the server and issue **TLS** (`ZETA_DOMAIN=… ./install.sh`).
3. Keep `data/initial_admin.txt` private and delete it once you've stored the password.
4. Take periodic backups (`zeta backup`) and store them off-box.
5. Review `audit_logs` (login events) via the DB if you suspect abuse.

## Reporting

Found a security issue? Open a private security advisory on the GitHub repository rather than a
public issue.
