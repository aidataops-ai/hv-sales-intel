from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import get_current_user


def _override_user(user: dict):
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_me_returns_is_bootstrap_admin_true_for_bootstrap(sample_admin_profile):
    bootstrap_user = {**sample_admin_profile, "email": "boss@apexvirtuals.com"}
    _override_user(bootstrap_user)

    with patch("src.auth.settings") as s:
        s.bootstrap_admin_email = "boss@apexvirtuals.com"
        client = TestClient(app)
        resp = client.get("/api/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "boss@apexvirtuals.com"
    assert body["is_bootstrap_admin"] is True


def test_me_returns_is_bootstrap_admin_false_for_other_admin(sample_admin_profile):
    other_admin = {**sample_admin_profile, "email": "other@apexvirtuals.com"}
    _override_user(other_admin)

    with patch("src.auth.settings") as s:
        s.bootstrap_admin_email = "boss@apexvirtuals.com"
        client = TestClient(app)
        resp = client.get("/api/me")

    assert resp.status_code == 200
    assert resp.json()["is_bootstrap_admin"] is False


def test_me_requires_auth():
    client = TestClient(app)
    resp = client.get("/api/me")
    assert resp.status_code == 401


# ---------- /api/me/password ----------


from unittest.mock import MagicMock


def test_me_password_rejects_weak_new_password(sample_admin_profile):
    _override_user(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/me/password", json={
        "current_password": "Whatever1!",
        "new_password": "weak",
    })
    assert resp.status_code == 400
    assert "Password" in resp.json()["detail"]


def test_me_password_rejects_wrong_current(sample_admin_profile):
    _override_user(sample_admin_profile)

    fake_anon = MagicMock()
    fake_anon.auth.sign_in_with_password.side_effect = Exception("Invalid login credentials")

    with patch("api.index._anon_supabase_client", return_value=fake_anon):
        client = TestClient(app)
        resp = client.post("/api/me/password", json={
            "current_password": "WrongPass1!",
            "new_password": "Healthy123!",
        })

    assert resp.status_code == 401
    assert "current password" in resp.json()["detail"].lower()


def test_me_password_happy_path(sample_admin_profile):
    _override_user(sample_admin_profile)

    fake_anon = MagicMock()
    fake_anon.auth.sign_in_with_password.return_value = MagicMock()

    fake_admin = MagicMock()

    with patch("api.index._anon_supabase_client", return_value=fake_anon):
        with patch("api.index.get_admin_client", return_value=fake_admin):
            client = TestClient(app)
            resp = client.post("/api/me/password", json={
                "current_password": "OldPass1!",
                "new_password": "NewHealthy1!",
            })

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    fake_admin.auth.admin.update_user_by_id.assert_called_once()
    args, kwargs = fake_admin.auth.admin.update_user_by_id.call_args
    assert args[0] == sample_admin_profile["id"]
    assert args[1] == {"password": "NewHealthy1!"}
