"""Multi-format subscription output + the Subscription-Userinfo header."""

import pytest

from tests.conftest import BASE


@pytest.fixture(scope="module")
def sub_id(client, token):
    auth = {"Authorization": f"Bearer {token}"}
    r = client.post(f"{BASE}/api/inbounds", headers=auth, json={
        "tag": "sub-ib", "core": "xray", "protocol": "vless",
        "network": "ws", "security": "none", "stream_settings": {"ws": {"path": "/sub"}}})
    assert r.status_code == 201, r.text
    ibid = r.json()["id"]
    c = client.post(f"{BASE}/api/inbounds/{ibid}/clients", headers=auth,
                    json={"email": "subuser", "total_gb": 10, "expiry_days": 30})
    assert c.status_code == 201, c.text
    return c.json()["sub_id"]


def test_base64_subscription(client, sub_id):
    r = client.get(f"{BASE}/sub/{sub_id}")
    assert r.status_code == 200
    # standard clients read quota/expiry from this header
    assert "subscription-userinfo" in {k.lower() for k in r.headers}


def test_clash_yaml(client, sub_id):
    r = client.get(f"{BASE}/sub/{sub_id}?target=clash")
    assert r.status_code == 200 and "proxies:" in r.text


def test_singbox_json(client, sub_id):
    r = client.get(f"{BASE}/sub/{sub_id}?target=singbox")
    assert r.status_code == 200 and '"outbounds"' in r.text
