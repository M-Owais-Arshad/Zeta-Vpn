"""Inbound creation: per-protocol, validation, REALITY + SS-2022 seeding."""

import base64
from tests.conftest import BASE


def _mk(client, auth, **over):
    body = {"tag": over.pop("tag"), "core": "xray", "protocol": "vless",
            "network": "tcp", "security": "none"}
    body.update(over)
    return client.post(f"{BASE}/api/inbounds", headers=auth, json=body)


def test_create_vless_reality_seeds_defaults(client, auth):
    r = _mk(client, auth, tag="t-vless-reality", protocol="vless",
            port=18443, security="reality")
    assert r.status_code == 201, r.text
    reality = r.json()["stream_settings"].get("reality", {})
    assert reality.get("shortIds")            # shortId seeded (Python-side)
    # apple.com is the working default dest — microsoft breaks the REALITY
    # handshake. This is the regression this test guards.
    assert reality.get("dest") == "www.apple.com:443"
    assert reality.get("serverNames") == ["www.apple.com"]
    # (privateKey/publicKey come from the xray binary — asserted in live/integration
    #  runs where xray is installed, not in this unit environment.)


def test_bad_protocol_is_400_not_500(client, auth):
    r = _mk(client, auth, tag="t-bad", protocol="vles", port=19001)
    assert r.status_code == 400


def test_shadowsocks_2022_psk_length(client, auth):
    r = _mk(client, auth, tag="t-ss", protocol="shadowsocks", port=18388)
    assert r.status_code == 201, r.text
    assert len(base64.b64decode(r.json()["settings"]["password"])) == 16


def test_vmess_tcp_defaults_to_443_not_80(client, auth):
    # No explicit port -> must not auto-pick 80 (nginx) and 409.
    r = _mk(client, auth, tag="t-vmess-auto", protocol="vmess", network="tcp")
    assert r.status_code == 201, r.text
    assert r.json()["port"] == 443


def test_ws_family_forced_to_80_with_internal_port(client, auth):
    r = _mk(client, auth, tag="t-vless-ws", protocol="vless", network="ws",
            stream_settings={"ws": {"path": "/t-ws"}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["port"] == 80
    assert body["internal_port"] and body["internal_port"] >= 20000
