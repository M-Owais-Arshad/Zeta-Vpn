# ZetaVPN — Installation Guide

## Requirements

- A VPS running **Debian 11/12** or **Ubuntu 20.04 / 22.04 / 24.04** (x86-64 or ARM64).
- **Root** access.
- (Recommended) a **domain name** pointed at the server's IP — required for TLS-based protocols
  (VLESS-WS-TLS, Trojan-TLS, VMess-WS-TLS, Hysteria2, TUIC) and for serving the panel over HTTPS.

## Install methods

### 1. One-command (from GitHub)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/zetavpn/main/install.sh)
```

### 2. From a local clone

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/zetavpn.git
cd zetavpn
sudo ./install.sh
```

### 3. Non-interactive (automation)

```bash
sudo ZETA_DOMAIN=vpn.example.com \
     ZETA_ADMIN_USERNAME=admin \
     ZETA_ADMIN_PASSWORD='a-strong-password' \
     ./install.sh --yes
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ZETA_REPO` | placeholder | Git URL to clone when bootstrapping via `curl` |
| `ZETA_BRANCH` | `main` | Branch to install |
| `ZETA_HOME` | `/opt/zetavpn` | Install directory |
| `ZETA_DOMAIN` | *(empty)* | Domain for TLS + HTTPS panel; triggers acme.sh cert issuance |
| `ZETA_SSL_EMAIL` | `admin@<domain>` | Email for the ACME account |
| `PANEL_PORT` | `2096` | Panel HTTP port |
| `ZETA_WEB_BASE_PATH` | random `zeta-xxxx` | Secret URL path for the panel |
| `ZETA_ADMIN_USERNAME` | `admin` | Initial admin username |
| `ZETA_ADMIN_PASSWORD` | random | Initial admin password (generated + saved if unset) |
| `XRAY_VERSION` / `SINGBOX_VERSION` | latest | Pin specific core versions |

These are written to `/opt/zetavpn/.env` (mode `0600`) on first run and reused afterward. Delete the
file to regenerate credentials.

## What the installer does

1. Installs base packages (`python3`, `git`, `curl`, `nginx`, `openssl`, …).
2. Copies/clones the source to `/opt/zetavpn`, creates a Python venv, installs panel deps.
3. Generates `.env` (secret key, admin credentials, secret panel path, server IP).
4. Downloads and installs **Xray-core** and **sing-box** (SHA256-verified where published).
5. Sets up the **SSH stack** (OpenSSH tuning, Dropbear, stunnel/SSL, WS proxy).
6. Applies **BBR** + kernel tuning, configures **nginx**, issues **TLS** (if a domain is set),
   enables **ufw** + **fail2ban**.
7. Installs and starts systemd units and the `zeta` CLI, adds an expiry cron job.

## After install

The installer prints your **panel URL, username and password**. If the password was auto-generated
it's also saved to `/opt/zetavpn/data/initial_admin.txt` (root-only) and retrievable via `zeta info`.

1. Open the panel URL and sign in.
2. **Settings → Server identity**: set your domain (used in client links).
3. Enable **2FA** under Settings.
4. **Inbounds → Add inbound** (start with VLESS-REALITY — no cert needed), then add a client.
5. Share the client's link / QR / subscription URL.

## Updating

```bash
zeta update      # git pull + reinstall deps + copy units + restart
```

Or re-run `install.sh`; it's idempotent and keeps your `.env` and data.

## Backup / restore

```bash
zeta backup                       # -> /root/zeta-backup-YYYYmmdd-HHMMSS.tar.gz  (data + .env)
# restore:
tar -xzf zeta-backup-*.tar.gz -C /opt/zetavpn && zeta restart all
```

## Uninstall

```bash
sudo /opt/zetavpn/scripts/uninstall.sh          # removes services, keeps data
sudo /opt/zetavpn/scripts/uninstall.sh --purge  # removes everything incl. cores + data
```

## Troubleshooting

- **Panel won't load** — `zeta status`, then `journalctl -u zeta-panel -n 100`.
- **A core won't start** — validate the generated config: `xray run -test -config /usr/local/etc/xray/config.json`
  or `sing-box check -c /etc/sing-box/config.json`.
- **TLS issuance failed** — ensure DNS points to the server and port 80 is free; re-run
  `ZETA_DOMAIN=… /opt/zetavpn/scripts/install_ssl.sh`.
- **BBR not active** — needs kernel ≥ 4.9; reboot after install on older images.
