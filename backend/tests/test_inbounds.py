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


def test_ws_defaults_to_80_fronted(client, auth):
    # No port -> WS defaults to the shared :80 (nginx-fronted, xray on loopback).
    r = _mk(client, auth, tag="t-vless-ws", protocol="vless", network="ws",
            stream_settings={"ws": {"path": "/t-ws"}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["port"] == 80
    assert body["internal_port"] and body["internal_port"] >= 20000


def test_ws_custom_port_binds_directly(client, auth):
    # VLESS-WS on a non-80/443 port -> xray binds it directly (no nginx/loopback).
    r = _mk(client, auth, tag="t-ws-direct", protocol="vless", network="ws",
            port=28080, stream_settings={"ws": {"path": "/anything"}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["port"] == 28080
    assert body["internal_port"] is None


def test_ws_direct_port_allows_empty_path(client, auth):
    # On its own port the WS path is optional/free — no 400 for a missing path.
    r = _mk(client, auth, tag="t-ws-nopath", protocol="vmess", network="ws", port=28081)
    assert r.status_code == 201, r.text
    assert r.json()["internal_port"] is None


def test_ws_fronted_requires_path(client, auth):
    # On the shared :80 a path is still required (nginx routes by it).
    r = _mk(client, auth, tag="t-ws-nopath80", protocol="vmess", network="ws", port=80)
    assert r.status_code == 400, r.text


def test_multiple_ws_inbounds_on_different_ports(client, auth):
    r1 = _mk(client, auth, tag="t-ws-a", protocol="vless", network="ws", port=28082,
             stream_settings={"ws": {"path": "/a"}})
    r2 = _mk(client, auth, tag="t-ws-b", protocol="vless", network="ws", port=28083,
             stream_settings={"ws": {"path": "/b"}})
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)


def test_ws_direct_same_port_conflicts(client, auth):
    r1 = _mk(client, auth, tag="t-ws-c1", protocol="vless", network="ws", port=28084,
             stream_settings={"ws": {"path": "/c"}})
    r2 = _mk(client, auth, tag="t-ws-c2", protocol="vmess", network="ws", port=28084,
             stream_settings={"ws": {"path": "/d"}})
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text


def test_fronted_ws_same_path_conflicts(client, auth):
    r1 = _mk(client, auth, tag="t-ws-p1", protocol="vless", network="ws", port=80,
             stream_settings={"ws": {"path": "/shared-x"}})
    r2 = _mk(client, auth, tag="t-ws-p2", protocol="vmess", network="ws", port=80,
             stream_settings={"ws": {"path": "/shared-x"}})
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text


def test_extra_ports_stored(client, auth):
    r = _mk(client, auth, tag="t-multi", protocol="vless", network="tcp",
            port=28090, extra_ports=[28091, 28092])
    assert r.status_code == 201, r.text
    assert r.json()["extra_ports"] == [28091, 28092]


def test_extra_port_collision_with_other_primary(client, auth):
    r1 = _mk(client, auth, tag="t-mp-a", protocol="vless", network="tcp", port=28093)
    r2 = _mk(client, auth, tag="t-mp-b", protocol="vless", network="tcp", port=28094,
             extra_ports=[28093])  # 28093 is r1's primary port
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text


def test_extra_port_cannot_be_shared_nginx_port(client, auth):
    r = _mk(client, auth, tag="t-mp-80", protocol="vless", network="tcp", port=28095,
            extra_ports=[80])
    assert r.status_code == 400, r.text


def test_multi_port_emits_one_xray_listener_per_port(client, auth):
    from zeta.core import xray
    from zeta.models import Inbound
    ib = Inbound(tag="mp", protocol="vless", listen="0.0.0.0", port=28096, network="tcp",
                 security="none", internal_port=None, stream_settings={},
                 settings={"decryption": "none"}, extra_ports=[28097, 28098])
    outs = xray.build_inbounds(ib)
    assert [o["port"] for o in outs] == [28096, 28097, 28098]
    assert outs[1]["tag"] == "mp@28097"
