"""Auth: base-path isolation, login, and token revocation on password change."""

from tests.conftest import BASE


def test_base_path_isolation(client):
    assert client.get(f"{BASE}/api/health").status_code == 200
    assert client.get("/api/health").status_code == 404  # secret path only


def test_wrong_password_401(client):
    r = client.post(f"{BASE}/api/auth/login",
                    json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_password_change_revokes_old_token(client):
    # fresh session so we don't poison the shared `token` fixture
    login = client.post(f"{BASE}/api/auth/login",
                        json={"username": "admin", "password": "testpass123"})
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get(f"{BASE}/api/auth/me", headers=h).status_code == 200

    ch = client.post(f"{BASE}/api/auth/change-password", headers=h,
                     json={"current_password": "testpass123", "new_password": "tmp-pass-987"})
    assert ch.status_code == 200
    # token_version bumped -> old bearer is dead
    assert client.get(f"{BASE}/api/auth/me", headers=h).status_code == 401

    # restore so the session admin password stays valid for other tests
    h2 = {"Authorization": f"Bearer {client.post(f'{BASE}/api/auth/login', json={'username': 'admin', 'password': 'tmp-pass-987'}).json()['access_token']}"}
    back = client.post(f"{BASE}/api/auth/change-password", headers=h2,
                       json={"current_password": "tmp-pass-987", "new_password": "testpass123"})
    assert back.status_code == 200
