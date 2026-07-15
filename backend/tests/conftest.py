"""Pytest fixtures for the ZetaVPN panel test suite.

Env is set at import time (before ``zeta.*`` is imported anywhere) so the
cached settings singleton picks up a throwaway data dir, a secret base path,
and ``service_manager=none`` (no real systemd/cores touched).

ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="zeta-tests-")
os.environ.update(
    ZETA_DATA_DIR=os.path.join(_TMP, "data"),
    ZETA_XRAY_CONFIG=os.path.join(_TMP, "xray.json"),
    ZETA_SINGBOX_CONFIG=os.path.join(_TMP, "singbox.json"),
    ZETA_SERVICE_MANAGER="none",
    ZETA_WEB_BASE_PATH="zeta-test",
    ZETA_ADMIN_USERNAME="admin",
    ZETA_ADMIN_PASSWORD="testpass123",
    ZETA_SERVER_ADDRESS="203.0.113.10",
    ZETA_SERVER_DOMAIN="vpn.example.com",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

BASE = "/zeta-test"


@pytest.fixture(scope="session")
def client():
    from zeta.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def token(client):
    r = client.post(f"{BASE}/api/auth/login",
                    json={"username": "admin", "password": "testpass123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture()
def auth(token):
    return {"Authorization": f"Bearer {token}"}
