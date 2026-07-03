# ZetaVPN — Security

This is a summary of ZetaVPN's threat model and hardening. The full analysis, including the
published CVE classes for panels in this category, is in [RESEARCH.md](RESEARCH.md) §8.

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

Because ZetaVPN manages **system SSH users** (`useradd`/`chage`) and **services** (`systemctl`), the
panel process runs privileged, like 3x-ui and the AutoScript panels it draws on. This is a
deliberate trade-off for a single-node personal panel. To reduce the blast radius:

- The proxy **cores** run as hardened units (`NoNewPrivileges`, scoped `AmbientCapabilities`,
  `ProtectSystem`), *not* with broad privileges.
- Always deploy **behind a domain + TLS** and keep the secret path private.
- Restrict who can reach the panel port (bind nginx to the domain; consider a firewall allow-list or
  binding the panel to `127.0.0.1` behind nginx).
- Keep the box patched (the installer enables base updates); run `zeta update` regularly.

If you don't need SSH-account management, you can bind the panel to localhost and put it entirely
behind nginx + client-certificate or IP allow-listing.

## Known CVE classes to avoid (and how ZetaVPN does)

| Class | Seen in | ZetaVPN mitigation |
|---|---|---|
| Default credentials | x-ui/S-UI `admin/admin` | Random/required password, no defaults |
| Insecure update fetch | panel auto-updaters | SHA256-verified downloads; `zeta update` uses git |
| Authenticated config-injection → RCE | 3x-ui advisory | Configs rendered from typed DB fields; JSON passed to cores, not shelled |

## Hardening you should still do

1. Change the admin password on first login and **enable 2FA**.
2. Point a **domain** at the server and issue **TLS** (`ZETA_DOMAIN=… ./install.sh`).
3. Keep `data/initial_admin.txt` private and delete it once you've stored the password.
4. Take periodic backups (`zeta backup`) and store them off-box.
5. Review `audit_logs` (login events) via the DB if you suspect abuse.

## Reporting

Found a security issue? Open a private security advisory on the GitHub repository rather than a
public issue.
