# ZetaVPN — Architecture

ZetaVPN is a **single-node** panel: one FastAPI process is the source of truth, and it drives three
independent delivery layers — Xray-core, sing-box, and a native SSH stack.

```
                         ┌───────────────────────────────────────────┐
   Browser  ──HTTPS──▶   │  nginx  (reverse proxy, TLS, WS upgrade)   │
   VPN client ─sub──▶    └───────────────┬───────────────────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │      zeta-panel (FastAPI)     │
                          │  REST API · UI · subscriptions│
                          │  auth · stats poller · config │
                          └───┬─────────┬─────────┬───────┘
             writes config    │         │         │   useradd / systemctl
             + reload         ▼         ▼         ▼
                     ┌────────────┐ ┌──────────┐ ┌──────────────────────────┐
                     │ zeta-xray  │ │zeta-sing │ │ SSH stack: sshd, dropbear│
                     │ (Xray-core)│ │  -box    │ │ stunnel, zeta-ws (WS)    │
                     └────────────┘ └──────────┘ └──────────────────────────┘
                          ▲
                          │ gRPC stats/handler API (127.0.0.1:62789)
                          └── traffic counters ─▶ SQLite
```

## Components

- **Panel — `backend/zeta`** (Python 3.11, FastAPI, SQLAlchemy 2, Uvicorn). Serves the REST API,
  the static UI, and the public subscription endpoints; owns the database; runs the background stats
  poller.
- **Xray-core** (`zeta-xray`) — VLESS/VMess/Trojan/Shadowsocks/SOCKS/HTTP, REALITY & TLS, all
  transports. Config at `/usr/local/etc/xray/config.json`.
- **sing-box** (`zeta-singbox`) — Hysteria2 & TUIC (and other modern protocols). Config at
  `/etc/sing-box/config.json`. Only started when it has inbounds.
- **SSH stack** — OpenSSH + Dropbear (extra ports) + stunnel (SSH-over-SSL) + `zeta-ws`
  (SSH-over-WebSocket). Accounts are real Linux users with an expiry date.

## Data model (`models.py`)

| Table | Role |
|---|---|
| `users` | Panel operators; password hash, TOTP, `token_version`. `role` carries `admin` \| `reseller`, but every endpoint currently gates on `role == "admin"` — a `reseller` account can log in but has no scoped permissions yet (deferred post-MVP, see RESEARCH.md §9.7) |
| `inbounds` | A proxy listener: `core`, `protocol`, `port`, `network`, `security`, `settings` + `stream_settings` (JSON), traffic counters |
| `clients` | A credential under an inbound: `uuid`/`password`, `flow`, quota, expiry, `sub_id`, usage |
| `ssh_accounts` | Native SSH accounts mirrored to system users |
| `settings` | Key/value server settings (domain, address, brand…) |
| `traffic_snapshots` | Rolling network samples for the dashboard chart |
| `audit_logs` | Login and admin events |

Protocol-specific data (`settings`, `stream_settings`) is stored as **opaque JSON** and passed
through to the core verbatim — so new core features work without a schema change.

## Config generation & apply

1. An API call mutates the DB (e.g. add a client).
2. `core/xray.py` (or `core/singbox.py`) renders the **entire** config from the DB — only clients
   that are enabled, unexpired and under quota are included.
3. The config is written **atomically** (`tmp` + `os.replace`) and the core is restarted via
   `core/services.py` → `systemctl`.

The generated Xray config always includes an `api` inbound + stats policy so per-client and
per-inbound counters are available over the local gRPC API.

## Traffic accounting & enforcement (`tasks.py`)

An asyncio loop runs every `stats_poll_seconds` (default 30s):

1. `xray api statsquery -reset` returns and zeroes the counters.
2. Deltas are added to each client/inbound in the DB.
3. Any client that has just crossed its **quota** or **expiry** triggers a core reload, which drops
   it from the rendered config — cutting the credential off.
4. A network snapshot is recorded for the dashboard chart.

## Subscriptions (`core/links.py`, `core/clientconf.py`, `api/subscription.py`)

Clients grouped by `sub_id` are exported as share-link URIs and aggregated into:

- **base64** newline list (v2rayN, v2rayNG, Hiddify),
- **Clash/Mihomo YAML** (Clash Verge, FlClash),
- **sing-box JSON** (NekoBox, sing-box).

The format is chosen from `?target=` or the `User-Agent`. Responses carry the
`Subscription-Userinfo` header (upload/download/total/expire) so clients show quota and expiry.

## Front end (`frontend/`)

Deliberately **build-free**: static `index.html` (admin SPA) and `sub.html` (user portal) with
vanilla JS (`api.js` fetch wrapper + `app.js` views) and a hand-written stylesheet. FastAPI mounts
`/{base}/assets` and serves the pages. This keeps installs offline-capable with no Node toolchain.

## Why two cores?

No single core covers everything. Xray owns REALITY/XTLS-Vision and the widest client
compatibility; sing-box owns the QUIC generation (Hysteria2, TUIC). Running both as separate
services — each with its own config and lifecycle — is the same pattern proven by S-UI and Hiddify.
See [RESEARCH.md](RESEARCH.md) §3–4 for the full rationale.
