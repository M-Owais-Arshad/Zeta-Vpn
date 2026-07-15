"""Client lifecycle: global email uniqueness, quota/expiry, links, edit."""

from tests.conftest import BASE


def _inbound(client, auth, tag):
    # WS-family: omit port (the server forces it to 80 and routes via nginx).
    r = client.post(f"{BASE}/api/inbounds", headers=auth, json={
        "tag": tag, "core": "xray", "protocol": "vless",
        "network": "ws", "security": "none",
        "stream_settings": {"ws": {"path": "/" + tag}}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_global_email_uniqueness(client, auth):
    a = _inbound(client, auth, "c-uniq-a")
    b = _inbound(client, auth, "c-uniq-b")
    r1 = client.post(f"{BASE}/api/inbounds/{a}/clients", headers=auth, json={"email": "dupe1"})
    assert r1.status_code == 201
    r2 = client.post(f"{BASE}/api/inbounds/{b}/clients", headers=auth, json={"email": "dupe1"})
    assert r2.status_code == 409  # same email on another inbound is rejected


def test_email_with_gt_rejected(client, auth):
    a = _inbound(client, auth, "c-gt")
    r = client.post(f"{BASE}/api/inbounds/{a}/clients", headers=auth, json={"email": "bad>name"})
    assert r.status_code in (400, 422)  # '>' corrupts xray stats keys


def test_client_link_and_quota(client, auth):
    a = _inbound(client, auth, "c-link")
    r = client.post(f"{BASE}/api/inbounds/{a}/clients", headers=auth,
                    json={"email": "linkuser", "total_gb": 5, "expiry_days": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["total_bytes"] == 5 * 1024 ** 3
    lk = client.get(f"{BASE}/api/inbounds/{a}/clients/{cid}/link", headers=auth)
    assert lk.status_code == 200
    assert lk.json()["link"].startswith("vless://")


def test_client_edit_patch(client, auth):
    a = _inbound(client, auth, "c-edit")
    r = client.post(f"{BASE}/api/inbounds/{a}/clients", headers=auth, json={"email": "edituser"})
    cid = r.json()["id"]
    p = client.patch(f"{BASE}/api/inbounds/{a}/clients/{cid}", headers=auth,
                     json={"limit_ip": 3, "total_gb": 10})
    assert p.status_code == 200, p.text
    assert p.json()["limit_ip"] == 3
    assert p.json()["total_bytes"] == 10 * 1024 ** 3
