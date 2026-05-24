from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import require_admin


def _override_admin(profile: dict):
    app.dependency_overrides[require_admin] = lambda: profile


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_create_user_rejects_malformed_email(sample_admin_profile):
    # Domain allowlist removed for the demo — only malformed addresses
    # should be rejected now (off-domain addresses are accepted).
    _override_admin(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/admin/users", json={
        "email": "not-an-email",
        "name": "Rep",
        "password": "Healthy123!",
    })
    assert resp.status_code == 400
    assert "format" in resp.json()["detail"]


def test_create_user_rejects_double_dash_email(sample_admin_profile):
    _override_admin(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/admin/users", json={
        "email": "rep--admin@healthandgroup.com",
        "name": "Rep",
        "password": "Healthy123!",
    })
    assert resp.status_code == 400
    assert "--" in resp.json()["detail"]


def test_create_user_rejects_weak_password(sample_admin_profile):
    _override_admin(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/admin/users", json={
        "email": "rep@healthandgroup.com",
        "name": "Rep",
        "password": "weak",
    })
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Password" in detail


def test_create_user_maps_duplicate_email_to_friendly_message(sample_admin_profile):
    _override_admin(sample_admin_profile)

    fake_admin = MagicMock()
    fake_admin.auth.admin.create_user.side_effect = Exception("User already registered")

    with patch("api.index.get_admin_client", return_value=fake_admin):
        client = TestClient(app)
        resp = client.post("/api/admin/users", json={
            "email": "rep@healthandgroup.com",
            "name": "Rep",
            "password": "Healthy123!",
        })

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Email already in use."


def test_create_user_happy_path(sample_admin_profile):
    _override_admin(sample_admin_profile)

    created_user = MagicMock()
    created_user.user.id = "new-user-id"
    fake_admin = MagicMock()
    fake_admin.auth.admin.create_user.return_value = created_user
    profile_select = MagicMock()
    profile_select.execute.return_value.data = {
        "id": "new-user-id",
        "email": "rep@healthandgroup.com",
        "name": "Rep",
        "role": "sdr",
        "created_at": "2026-04-27T00:00:00Z",
    }
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value = profile_select

    with patch("api.index.get_admin_client", return_value=fake_admin):
        client = TestClient(app)
        resp = client.post("/api/admin/users", json={
            "email": "rep@healthandgroup.com",
            "name": "Rep",
            "password": "Healthy123!",
            "role": "sdr",
        })

    assert resp.status_code == 200
    assert resp.json()["email"] == "rep@healthandgroup.com"


# ---------- reset password ----------


def test_reset_rejects_weak_password(sample_admin_profile):
    _override_admin(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/admin/users/some-id/reset-password", json={
        "new_password": "weak",
    })
    assert resp.status_code == 400
    assert "Password" in resp.json()["detail"]


def test_reset_admin_target_blocked_for_non_bootstrap(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target_admin = {
        "id": "target-admin-id",
        "email": "other@healthandgroup.com",
        "role": "admin",
    }

    fake_admin = MagicMock()
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = target_admin

    with patch("api.index.get_admin_client", return_value=fake_admin):
        with patch("src.auth.settings") as s:
            s.bootstrap_admin_email = "boss@healthandgroup.com"
            client = TestClient(app)
            resp = client.post(
                "/api/admin/users/target-admin-id/reset-password",
                json={"new_password": "Healthy123!"},
            )

    assert resp.status_code == 403
    assert "bootstrap admin" in resp.json()["detail"].lower()


def test_reset_admin_target_allowed_for_bootstrap(sample_admin_profile):
    bootstrap = {**sample_admin_profile, "email": "boss@healthandgroup.com"}
    _override_admin(bootstrap)
    target_admin = {
        "id": "target-admin-id",
        "email": "other@healthandgroup.com",
        "role": "admin",
    }

    fake_admin = MagicMock()
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = target_admin

    with patch("api.index.get_admin_client", return_value=fake_admin):
        with patch("src.auth.settings") as s:
            s.bootstrap_admin_email = "boss@healthandgroup.com"
            client = TestClient(app)
            resp = client.post(
                "/api/admin/users/target-admin-id/reset-password",
                json={"new_password": "Healthy123!"},
            )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_reset_sdr_target_allowed_for_any_admin(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target_sdr = {
        "id": "target-sdr-id",
        "email": "rep@healthandgroup.com",
        "role": "sdr",
    }

    fake_admin = MagicMock()
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = target_sdr

    with patch("api.index.get_admin_client", return_value=fake_admin):
        with patch("src.auth.settings") as s:
            s.bootstrap_admin_email = "boss@healthandgroup.com"
            client = TestClient(app)
            resp = client.post(
                "/api/admin/users/target-sdr-id/reset-password",
                json={"new_password": "Healthy123!"},
            )

    assert resp.status_code == 200


# ---------- PATCH /api/admin/users/{id} (edit + disable) ----------


def _patch_with_target(target: dict, body: dict, fake_admin: MagicMock | None = None):
    if fake_admin is None:
        fake_admin = MagicMock()
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = target

    update_chain = MagicMock()
    update_chain.execute.return_value.data = [{**target, **body}]
    fake_admin.table.return_value.update.return_value.eq.return_value = update_chain

    with patch("api.index.get_admin_client", return_value=fake_admin):
        with patch("src.auth.settings") as s:
            s.bootstrap_admin_email = "boss@healthandgroup.com"
            client = TestClient(app)
            resp = client.patch(
                f"/api/admin/users/{target['id']}",
                json=body,
            )
    return resp, fake_admin


def test_patch_user_404_when_target_missing(sample_admin_profile):
    _override_admin(sample_admin_profile)
    fake_admin = MagicMock()
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = None
    with patch("api.index.get_admin_client", return_value=fake_admin):
        client = TestClient(app)
        resp = client.patch("/api/admin/users/missing", json={"name": "X"})
    assert resp.status_code == 404


def test_patch_user_renames_sdr(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target = {"id": "sdr-1", "email": "sdr@healthandgroup.com", "role": "sdr"}
    resp, fake = _patch_with_target(target, {"name": "New Name"})
    assert resp.status_code == 200
    update_args = fake.table.return_value.update.call_args.args[0]
    assert update_args == {"name": "New Name"}


def test_patch_user_disable_sets_timestamp(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target = {"id": "sdr-1", "email": "sdr@healthandgroup.com", "role": "sdr"}
    resp, fake = _patch_with_target(target, {"disabled": True})
    assert resp.status_code == 200
    update_args = fake.table.return_value.update.call_args.args[0]
    assert update_args["disabled_at"] is not None


def test_patch_user_enable_clears_timestamp(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target = {"id": "sdr-1", "email": "sdr@healthandgroup.com", "role": "sdr"}
    resp, fake = _patch_with_target(target, {"disabled": False})
    assert resp.status_code == 200
    update_args = fake.table.return_value.update.call_args.args[0]
    assert update_args["disabled_at"] is None


def test_patch_user_cannot_disable_self(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target = {**sample_admin_profile, "role": "admin"}
    resp, _ = _patch_with_target(target, {"disabled": True})
    assert resp.status_code == 400
    assert "self" in resp.json()["detail"].lower()


def test_patch_user_blocks_editing_other_admin_for_non_bootstrap(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target = {"id": "admin-2", "email": "other@healthandgroup.com", "role": "admin"}
    resp, _ = _patch_with_target(target, {"name": "Renamed"})
    assert resp.status_code == 403


def test_patch_user_allows_editing_other_admin_for_bootstrap(sample_admin_profile):
    bootstrap = {**sample_admin_profile, "email": "boss@healthandgroup.com"}
    _override_admin(bootstrap)
    target = {"id": "admin-2", "email": "other@healthandgroup.com", "role": "admin"}
    resp, _ = _patch_with_target(target, {"name": "Renamed"})
    assert resp.status_code == 200


def test_patch_user_blocks_promotion_to_admin_for_non_bootstrap(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target = {"id": "sdr-1", "email": "sdr@healthandgroup.com", "role": "sdr"}
    resp, _ = _patch_with_target(target, {"role": "admin"})
    assert resp.status_code == 403


def test_patch_user_rejects_invalid_role(sample_admin_profile):
    bootstrap = {**sample_admin_profile, "email": "boss@healthandgroup.com"}
    _override_admin(bootstrap)
    target = {"id": "sdr-1", "email": "sdr@healthandgroup.com", "role": "sdr"}
    resp, _ = _patch_with_target(target, {"role": "owner"})
    assert resp.status_code == 400


def test_patch_user_rejects_empty_body(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target = {"id": "sdr-1", "email": "sdr@healthandgroup.com", "role": "sdr"}
    resp, _ = _patch_with_target(target, {})
    assert resp.status_code == 400
