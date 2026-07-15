"""SSH accounts: username validation (unit) + account lifecycle (needs root).

Creating a real account shells out to ``useradd``/``chpasswd``, which need a
Linux box and root (or the zeta-privileged wrapper). Those tests skip cleanly
off-Linux or when not root; the input-validation tests run everywhere because
they're rejected before any OS call. The full end-to-end SSH stack is verified
on the live server, not here.
"""

import os

import pytest

from tests.conftest import BASE

_can_useradd = os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0
needs_root = pytest.mark.skipif(not _can_useradd, reason="needs Linux + root for useradd")


def test_control_char_password_rejected(client, auth):
    # newline in password would let chpasswd rewrite another account.
    r = client.post(f"{BASE}/api/ssh", headers=auth, json={
        "username": "sshbad", "password": "x\ny", "expiry_days": 1})
    assert r.status_code in (400, 422)


def test_short_username_rejected(client, auth):
    r = client.post(f"{BASE}/api/ssh", headers=auth, json={
        "username": "ab", "password": "Secret123", "expiry_days": 1})
    assert r.status_code in (400, 422)


@needs_root
def test_create_and_password_is_returned(client, auth):
    r = client.post(f"{BASE}/api/ssh", headers=auth, json={
        "username": "sshuser1", "password": "Secret123", "max_login": 2, "expiry_days": 7})
    assert r.status_code == 201, r.text
    assert r.json()["password"] == "Secret123"  # recoverable for the owner
    lst = client.get(f"{BASE}/api/ssh", headers=auth).json()
    assert any(a["username"] == "sshuser1" and a["password"] == "Secret123" for a in lst)


@needs_root
def test_renew_extends_expiry(client, auth):
    r = client.post(f"{BASE}/api/ssh", headers=auth, json={
        "username": "sshrenew", "password": "Secret123", "expiry_days": 10})
    aid = r.json()["id"]
    before = r.json()["expiry_date"]
    rn = client.post(f"{BASE}/api/ssh/{aid}/renew?days=30", headers=auth)
    assert rn.status_code == 200, rn.text
    assert rn.json()["expiry_date"] > before
