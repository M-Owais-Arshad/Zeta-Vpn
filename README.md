<div align="center">

<img src="frontend/assets/img/logo.png" width="90" alt="ZetaVPN"/>

# ZetaVPN

**An all-in-one, self-hosted VPN / proxy panel — every protocol, one command, one portal.**

<sub>**by Muhammad Owais**</sub>

ZetaVPN turns a fresh Debian/Ubuntu VPS into a full proxy server managed from a modern
web dashboard: **Xray-core** + **sing-box** + a complete **SSH tunnelling stack**, wired
together by a FastAPI backend and installed straight from GitHub.

`VLESS · VMess · Trojan · Shadowsocks-2022 · REALITY · Hysteria2 · TUIC · SSH-WS · SSH-SSL · Dropbear`

</div>

---

## ✨ Highlights

- **All the protocols, two cores.** Xray-core serves VLESS (REALITY + XTLS-Vision), VMess,
  Trojan, Shadowsocks-2022, SOCKS and HTTP; sing-box adds the QUIC generation — Hysteria2 and
  TUIC. A native SSH tunnelling stack (OpenSSH, Dropbear, stunnel/SSL, WebSocket) rounds it out.
- **One-command install from GitHub.** `curl | bash` detects your arch, pulls the core binaries
  from their official releases, sets up systemd services, nginx, TLS (acme.sh), BBR and a firewall.
- **A real web portal — no build step.** A clean, dark, responsive dashboard (framework-free,
  zero npm) to manage inbounds, clients, traffic quotas, expiry, SSH accounts and settings, plus a
  self-service **user subscription page**.
- **Smart subscriptions.** One subscription URL serves **base64** (v2rayN/Hiddify),
  **Clash/Mihomo YAML** (Clash Verge) and **sing-box JSON** (NekoBox) — auto-detected per client,
  with the `Subscription-Userinfo` quota/expiry header and per-config QR codes.
- **Secure by default.** Randomised admin credentials and a secret panel path at install, bcrypt
  hashing, TOTP 2FA, login brute-force lockout, session revocation on password change, verified
  binary downloads, fail2ban + ufw. No default passwords, ever.
- **`zeta` CLI.** A terminal menu for status, restarts, updates, backups and quick SSH accounts.

---

## 🚀 Quick install

On a fresh **Debian 11/12** or **Ubuntu 20.04/22.04/24.04** VPS, as **root**:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/M-Owais-Arshad/Zeta-Vpn/main/install.sh)
```

…or clone and run locally:

```bash
git clone https://github.com/M-Owais-Arshad/Zeta-Vpn.git
cd zetavpn
sudo ./install.sh
```

With a domain (recommended — unlocks TLS protocols + HTTPS panel):

```bash
sudo ZETA_DOMAIN=vpn.example.com ./install.sh --yes
```

When it finishes you'll get the **panel URL, username and password**. Open the URL, sign in, set your
domain under **Settings**, then add an inbound and a client.

> The installer also honours `ZETA_REPO=…` if you're installing from a fork.

---

## 🧭 What gets installed

| Component | Where | Service |
|---|---|---|
| Panel (FastAPI) | `/opt/zetavpn` | `zeta-panel` |
| Xray-core | `/usr/local/bin/xray` | `zeta-xray` |
| sing-box | `/usr/local/bin/sing-box` | `zeta-singbox` |
| SSH-over-WebSocket proxy | `/opt/zetavpn/proxies/ws-proxy.py` | `zeta-ws` |
| Dropbear / stunnel | system | `dropbear` / `stunnel4` |
| nginx reverse proxy | `/etc/nginx/conf.d/zeta.conf` | `nginx` |
| CLI manager | `/usr/local/bin/zeta` | — |

Default ports: **panel** `2096` · **SSH** `22`, `143`/`149` (Dropbear), `445` (SSL), `8880` (WS) ·
proxy inbounds on whatever ports you choose in the panel.

---

## 🖥️ Using it

**Panel** — Dashboard (live CPU/RAM/disk + network chart + service health), Inbounds
(create/enable/delete, manage clients), SSH Accounts, Settings (server identity, password, 2FA,
core reload).

**Create a client** → open its **Share** dialog for the config URI, a QR code, the subscription URL
and a link to the user portal.

**User portal** — `https://your.domain/portal?id=<sub_id>` shows a user their configs, QR codes,
usage and expiry, and a one-tap subscription import URL.

**CLI** — run `zeta` for the interactive menu, or:

```bash
zeta status          # service health
zeta restart all     # restart panel + cores + nginx
zeta info            # panel URL & credentials
zeta update          # git pull + deps + restart
zeta backup          # tar up data + .env
```

---

## 📚 Documentation

| Doc | What's in it |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Full install guide, env vars, domains, updating, uninstall |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the panel, cores, DB and services fit together |
| [docs/PROTOCOLS.md](docs/PROTOCOLS.md) | Every protocol, transport, and how to configure it |
| [docs/API.md](docs/API.md) | REST API + subscription endpoint reference |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, hardening defaults, and known CVE classes |

---

## 🗂️ Project layout

```
zetavpn/
├── install.sh              # one-command bootstrap
├── bin/zeta                # CLI manager
├── backend/                # FastAPI panel (Python)
│   └── zeta/
│       ├── api/            # REST routers (auth, inbounds, clients, ssh, system, sub, settings)
│       ├── core/           # xray, singbox, links, clientconf, protocols, ssh_manager, stats
│       ├── models.py schemas.py auth.py deps.py tasks.py bootstrap.py main.py config.py db.py
├── frontend/               # no-build web UI (HTML + vanilla JS + CSS)
├── scripts/                # modular installers (xray, singbox, ssh, nginx, ssl, bbr, firewall)
├── systemd/                # zeta-panel / zeta-xray / zeta-singbox / zeta-ws units
├── proxies/ws-proxy.py     # SSH-over-WebSocket proxy
├── config/templates/       # reference inbound JSON for each protocol
└── docs/                   # documentation
```

---

## 🔒 Security in one line

The panel manages system users and services, so it runs privileged — but ships **no default
credentials**, a **secret URL path**, **2FA**, **brute-force lockout**, **verified downloads**,
**fail2ban** and **ufw** enabled out of the box. Read [docs/SECURITY.md](docs/SECURITY.md) before
exposing it to the internet, and always put it behind a domain + TLS.

## ⚖️ License & responsible use

**ZetaVPN by Muhammad Owais** — licensed under **AGPL-3.0** (see [LICENSE](LICENSE)).
You're free to use, self-host and modify it, but if you run a modified version — including as a
network service — you must **publish your source and keep clear attribution to the original author,
Muhammad Owais**. Forks are welcome under those terms.

ZetaVPN is for running **your own** servers and providing access to users you are authorised to
serve. You are responsible for complying with the laws and terms that apply to you. Built on the
excellent open-source [Xray-core](https://github.com/XTLS/Xray-core) and
[sing-box](https://github.com/SagerNet/sing-box).

<div align="center"><sub><b>ZetaVPN</b> by Muhammad Owais · © 2026 · AGPL-3.0</sub></div>
