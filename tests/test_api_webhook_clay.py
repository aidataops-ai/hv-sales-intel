from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_webhook_rejects_missing_secret():
    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        client = TestClient(app)
        resp = client.post(
            "/api/webhooks/clay",
            json={"place_id": "abc", "owner_name": "Jane"},
        )
    assert resp.status_code == 401


def test_webhook_rejects_wrong_secret():
    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        client = TestClient(app)
        resp = client.post(
            "/api/webhooks/clay",
            json={"place_id": "abc", "owner_name": "Jane"},
            headers={"X-Clay-Secret": "wrong"},
        )
    assert resp.status_code == 401


def test_webhook_returns_404_when_practice_missing():
    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=None):
            client = TestClient(app)
            resp = client.post(
                "/api/webhooks/clay",
                json={"place_id": "missing", "owner_name": "Jane"},
                headers={"X-Clay-Secret": "shhh"},
            )
    assert resp.status_code == 404


def test_webhook_happy_path_writes_owner_fields_and_sets_enriched():
    existing = {"place_id": "abc", "name": "Test"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        captured["_place_id"] = place_id
        return {**existing, **fields}

    payload = {
        "place_id": "abc",
        "owner_name": "Jane Smith",
        "owner_title": "Practice Manager",
        "owner_email": "jane@hfd.com",
        "owner_phone": "+17135559999",
        "owner_linkedin": "https://linkedin.com/in/janesmith",
    }

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json=payload,
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert captured["owner_name"] == "Jane Smith"
    assert captured["owner_title"] == "Practice Manager"
    assert captured["owner_email"] == "jane@hfd.com"
    assert captured["owner_phone"] == "+17135559999"
    assert captured["owner_linkedin"] == "https://linkedin.com/in/janesmith"
    assert captured["enrichment_status"] == "enriched"
    assert "enriched_at" in captured


def test_webhook_maps_and_coerces_organization_size():
    """Clay's org-size (int or messy string) is coerced to an int and written."""
    from api.index import _coerce_org_size

    assert _coerce_org_size("1,200") == 1200
    assert _coerce_org_size("50-100 employees") == 50
    assert _coerce_org_size(42) == 42
    assert _coerce_org_size("N/A") is None
    assert _coerce_org_size(0) is None

    existing = {"place_id": "abc", "name": "Test"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json={"place_id": "abc", "owner_name": "Jane",
                          "organization_size": "1,200"},
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert captured["organization_size"] == 1200


def test_webhook_omits_organization_size_when_unparseable():
    existing = {"place_id": "abc", "name": "Test"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json={"place_id": "abc", "owner_name": "Jane",
                          "organization_size": "unknown"},
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert "organization_size" not in captured


def test_webhook_flips_to_failed_when_no_owner_fields():
    existing = {"place_id": "abc", "name": "Test"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json={"place_id": "abc"},
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert captured["enrichment_status"] == "failed"
    assert "owner_name" not in captured


def test_webhook_partial_payload_only_writes_present_fields():
    existing = {"place_id": "abc", "name": "Test", "owner_phone": "+17130000000"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json={"place_id": "abc", "owner_name": "Jane"},
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert captured["owner_name"] == "Jane"
    assert "owner_phone" not in captured
    assert captured["enrichment_status"] == "enriched"
