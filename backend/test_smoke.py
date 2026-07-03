"""Throwaway end-to-end smoke test for the ZetaVPN panel (run on the dev box).

Boots the app under a secret base path and exercises the happy path plus the
specific cases the code review flagged: base-path routing, SS-2022 key format,
global email uniqueness, bad-protocol handling, and multi-format subscriptions.
"""
import base64
import os
import tempfile

TMP = tempfile.mkdtemp(prefix="zeta-smoke-")
os.environ.update(
    ZETA_DATA_DIR=os.path.join(TMP, "data"),
    ZETA_XRAY_CONFIG=os.path.join(TMP, "xray.json"),
    ZETA_SINGBOX_CONFIG=os.path.join(TMP, "singbox.json"),
    ZETA_SERVICE_MANAGER="none",
    ZETA_WEB_BASE_PATH="zeta-test",          # exercise the secret base path
    ZETA_ADMIN_USERNAME="admin",
    ZETA_ADMIN_PASSWORD="testpass123",
    ZETA_SERVER_ADDRESS="203.0.113.10",
    ZETA_SERVER_DOMAIN="vpn.example.com",
)

from fastapi.testclient import TestClient  # noqa: E402
from zeta.main import app  # noqa: E402

B = "/zeta-test"
FAILS = []


def check(name, cond):
    print(("  ok " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


with TestClient(app) as client:
    check("base-path health", client.get(f"{B}/api/health").status_code == 200)
    check("root health 404 (secret path)", client.get("/api/health").status_code == 404)

    r = client.post(f"{B}/api/auth/login", json={"username": "admin", "password": "testpass123"})
    check("login", r.status_code == 200)
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # --- VLESS + REALITY ---
    r = client.post(f"{B}/api/inbounds", headers=H, json={
        "tag": "vless-reality", "core": "xray", "protocol": "vless",
        "port": 8443, "network": "tcp", "security": "reality"})
    check("create vless inbound", r.status_code == 201)
    vless_id = r.json()["id"]

    # Bad protocol -> 400 (not 500)
    r = client.post(f"{B}/api/inbounds", headers=H, json={
        "tag": "bad", "core": "xray", "protocol": "vles", "port": 9001})
    check("bad protocol -> 400", r.status_code == 400)

    # --- Shadowsocks-2022: client PSK must be valid base64 of the cipher key length ---
    r = client.post(f"{B}/api/inbounds", headers=H, json={
        "tag": "ss2022", "core": "xray", "protocol": "shadowsocks", "port": 8388,
        "network": "tcp", "security": "none"})
    check("create ss2022 inbound", r.status_code == 201)
    ss_id = r.json()["id"]
    check("ss server PSK is 16-byte b64",
          len(base64.b64decode(r.json()["settings"]["password"])) == 16)

    r = client.post(f"{B}/api/inbounds/{ss_id}/clients", headers=H, json={"email": "ssuser"})
    check("create ss client", r.status_code == 201)
    ss_pw = r.json()["password"]
    check("ss client PSK is 16-byte b64", len(base64.b64decode(ss_pw)) == 16)

    # --- global email uniqueness across inbounds ---
    r = client.post(f"{B}/api/inbounds/{vless_id}/clients", headers=H, json={"email": "dupe"})
    check("create client A", r.status_code == 201)
    sub_id = r.json()["sub_id"]
    r = client.post(f"{B}/api/inbounds/{ss_id}/clients", headers=H, json={"email": "dupe"})
    check("duplicate email across inbounds rejected", r.status_code == 409)

    # --- links & multi-format subs (under base path) ---
    r = client.get(f"{B}/api/inbounds/{ss_id}/clients/{[c for c in client.get(f'{B}/api/inbounds/{ss_id}/clients', headers=H).json()][0]['id']}/link", headers=H)
    check("ss client link ss://", r.status_code == 200 and r.json()["link"].startswith("ss://"))

    r = client.get(f"{B}/sub/{sub_id}")
    check("sub base64 under base path", r.status_code == 200)
    r = client.get(f"{B}/sub/{sub_id}?target=clash")
    check("sub clash", r.status_code == 200 and "proxies:" in r.text)
    r = client.get(f"{B}/sub/{sub_id}?target=singbox")
    check("sub singbox", r.status_code == 200 and '"outbounds"' in r.text)

    # --- session revocation on password change ---
    r = client.post(f"{B}/api/auth/change-password", headers=H,
                    json={"current_password": "testpass123", "new_password": "newpass456"})
    check("password changed", r.status_code == 200)
    check("old token revoked", client.get(f"{B}/api/auth/me", headers=H).status_code == 401)

print()
print("RESULT:", "ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
