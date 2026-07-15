"""Pure-logic tests for per-protocol default ports and the port-key that keeps
TCP and UDP inbounds on the same number from colliding."""

from zeta.core import protocols


def test_direct_default_ports():
    assert protocols.default_port("vless", "tcp") == 443
    assert protocols.default_port("trojan", "tcp") == 443
    # vmess direct must NOT default to 80 (nginx owns it) — regression guard.
    assert protocols.default_port("vmess", "tcp") == 443
    assert protocols.default_port("shadowsocks", "tcp") == 8388


def test_ws_family_always_port_80():
    for net in ("ws", "httpupgrade", "xhttp"):
        assert protocols.is_ws_family(net)
        assert protocols.default_port("vless", net) == 80
        assert protocols.default_port("vmess", net) == 80


def test_l4_family_split():
    assert protocols.l4_family("vless") == "tcp"
    assert protocols.l4_family("hysteria2") == "udp"
    assert protocols.l4_family("tuic") == "udp"


def test_port_key_tcp_udp_coexist():
    # 443/tcp (REALITY) and 443/udp (Hysteria2) are NOT a collision.
    k_tcp = protocols.compute_port_key(443, "vless", "tcp", {})
    k_udp = protocols.compute_port_key(443, "hysteria2", "udp", {})
    assert k_tcp != k_udp


def test_port_key_ws_keyed_by_path():
    a = protocols.compute_port_key(80, "vless", "ws", {"ws": {"path": "/a"}})
    b = protocols.compute_port_key(80, "vmess", "ws", {"ws": {"path": "/b"}})
    same = protocols.compute_port_key(80, "trojan", "ws", {"ws": {"path": "/a"}})
    assert a != b          # different paths -> different key
    assert a == same       # same path on :80 -> collision, as intended
