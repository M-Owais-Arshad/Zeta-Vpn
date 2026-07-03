# ZetaVPN — REST API

Base URL: `http(s)://<host>[:<port>]/<base-path>/api`
Interactive docs (opt-in): `…/api/docs` · OpenAPI: `…/api/openapi.json`

All `/api/*` endpoints except `POST /auth/login` require a bearer token:

```
Authorization: Bearer <access_token>
```

Subscription endpoints (`/sub/*`) are **public**, authenticated only by the unguessable `sub_id`.

## Auth

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/auth/login` | `{username, password, totp?}` | Returns `{access_token, role, expires_in}` |
| GET | `/auth/me` | — | Current user |
| POST | `/auth/change-password` | `{current_password, new_password}` | Bumps `token_version` → logs out all sessions |
| POST | `/auth/totp/setup` | — | Returns `{secret, uri, qr}` |
| POST | `/auth/totp/enable` | `{code}` | Activates 2FA |
| POST | `/auth/totp/disable` | `{code}` | Deactivates 2FA |

```bash
curl -sX POST https://vpn.example.com/zeta-ab12/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}'
```

## Inbounds

| Method | Path | Notes |
|---|---|---|
| GET | `/inbounds` | List inbounds (with client counts) |
| POST | `/inbounds` | Create. `auto_reality: true` generates REALITY keys/shortId |
| GET | `/inbounds/{id}` | Fetch one |
| PATCH | `/inbounds/{id}` | Update fields |
| POST | `/inbounds/{id}/toggle` | Enable/disable |
| DELETE | `/inbounds/{id}` | Delete (cascades to clients) |
| POST | `/inbounds/apply/all` | Regenerate + reload both cores |

```jsonc
// POST /inbounds  — VLESS + REALITY (no domain needed)
{ "tag": "vless-reality", "remark": "Main", "core": "xray", "protocol": "vless",
  "port": 8443, "network": "tcp", "security": "reality" }
```

## Clients (under an inbound)

| Method | Path | Notes |
|---|---|---|
| GET | `/inbounds/{id}/clients` | List |
| POST | `/inbounds/{id}/clients` | Create. `total_gb` / `expiry_days` convenience fields; UUID/password auto-generated |
| PATCH | `/inbounds/{id}/clients/{cid}` | Update |
| DELETE | `/inbounds/{id}/clients/{cid}` | Delete |
| POST | `/inbounds/{id}/clients/{cid}/reset-traffic` | Zero usage |
| GET | `/inbounds/{id}/clients/{cid}/link?qr=true` | `{email, link, qr}` share link + QR data-URL |

## SSH accounts

| Method | Path | Notes |
|---|---|---|
| GET | `/ssh` | List (with live online count) |
| POST | `/ssh` | Create Linux user `{username, password, max_login, expiry_days}` |
| POST | `/ssh/{id}/lock` · `/unlock` | Lock/unlock + kill sessions |
| POST | `/ssh/{id}/renew?days=30` | Extend expiry |
| DELETE | `/ssh/{id}` | Remove account + system user |

## System & settings

| Method | Path | Notes |
|---|---|---|
| GET | `/system/stats` | CPU/RAM/disk/net/uptime + counts + service health |
| GET | `/system/throughput` | Instantaneous RX/TX bytes/s (1s sample) |
| GET | `/system/protocols` | Protocol registry (drives the UI) |
| GET | `/system/cores` | Xray/sing-box service status |
| POST | `/system/services/{unit}/restart` | Restart a managed unit |
| GET/PUT | `/settings` | Server identity + branding |
| POST | `/settings/reality-keypair` | Generate an X25519 keypair + shortId |
| GET | `/settings/new-uuid` | Generate a UUID |

## Subscription (public)

| Method | Path | Returns |
|---|---|---|
| GET | `/sub/{sub_id}` | base64 / Clash / sing-box (by `?target=` or User-Agent) + `Subscription-Userinfo` header |
| GET | `/sub/{sub_id}/info` | JSON for the user portal (configs, QR, usage, expiry) |

```bash
curl -H 'User-Agent: clash-verge' https://vpn.example.com/zeta-ab12/sub/<sub_id>   # -> Clash YAML
curl 'https://vpn.example.com/zeta-ab12/sub/<sub_id>?target=singbox'               # -> sing-box JSON
```
