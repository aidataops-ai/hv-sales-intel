"""The Instant Signals config-page admin routes: dimension CRUD on
`search_terms` / `search_locations` / `target_overrides` (instant-signals
refactor, Phase 3). See docs/refactor/instant-signals-targets.md §4.

These pin the auth gate, the request/response shapes, and the validation ->
400 / not-found -> 404 mapping — without touching a real Supabase client.
"""

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src import lead_config, lead_targets
from src.auth import require_admin

client = TestClient(app)


def _override_admin(company_id: str = "c1"):
    app.dependency_overrides[require_admin] = lambda: {
        "company_id": company_id, "id": "u1", "role": "admin",
    }


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Auth gate — every route is admin-only.
# --------------------------------------------------------------------------


def test_get_config_requires_admin():
    assert client.get("/api/admin/leads/config").status_code == 401


def test_add_terms_requires_admin():
    assert client.post("/api/admin/leads/terms", json={"rows": []}).status_code == 401


def test_add_locations_requires_admin():
    assert client.post("/api/admin/leads/locations", json={"rows": []}).status_code == 401


def test_toggle_term_requires_admin():
    assert client.patch(
        "/api/admin/leads/terms/1", json={"enabled": True}
    ).status_code == 401


def test_toggle_location_requires_admin():
    assert client.patch(
        "/api/admin/leads/locations/1", json={"enabled": True}
    ).status_code == 401


def test_set_override_requires_admin():
    assert client.put(
        "/api/admin/leads/overrides",
        json={"term_id": 1, "location_id": 1, "enabled": True},
    ).status_code == 401


# --------------------------------------------------------------------------
# GET /api/admin/leads/config
# --------------------------------------------------------------------------


def test_get_config_returns_the_dimension_shape(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(lead_targets, "catalog", lambda: {"states": [], "tracks": []})
    monkeypatch.setattr(
        lead_targets, "list_config",
        lambda company_id: {
            "terms": [{"id": 1, "term": "RN", "service_line": "nursing", "enabled": True}],
            "locations": [{"id": 2, "location": "Tampa, FL", "state": "FL",
                           "granularity": "city", "enabled": True}],
            "overrides": [],
        },
    )
    monkeypatch.setattr(
        lead_targets, "sweep_status",
        lambda company_id: {"indeed": {"coverage_pct": 90.0}},
    )

    body = client.get("/api/admin/leads/config").json()
    assert body["catalog"] == {"states": [], "tracks": []}
    assert body["terms"][0]["term"] == "RN"
    assert body["locations"][0]["location"] == "Tampa, FL"
    assert body["overrides"] == []
    assert body["sweep"]["indeed"]["coverage_pct"] == 90.0


def test_get_config_surfaces_a_bad_catalog_as_500(monkeypatch):
    _override_admin("c1")

    def boom():
        raise lead_config.LeadConfigError("roles.json is broken")

    monkeypatch.setattr(lead_targets, "catalog", boom)
    resp = client.get("/api/admin/leads/config")
    assert resp.status_code == 500
    assert "roles.json is broken" in resp.json()["detail"]


# --------------------------------------------------------------------------
# POST /api/admin/leads/terms
# --------------------------------------------------------------------------


def test_add_terms_rejects_an_empty_batch():
    _override_admin("c1")
    resp = client.post("/api/admin/leads/terms", json={"rows": []})
    assert resp.status_code == 400


def test_add_terms_maps_validation_error_to_400(monkeypatch):
    _override_admin("c1")

    def boom(company_id, rows):
        raise lead_targets.TargetValidationError("a term row has no `term`")

    monkeypatch.setattr(lead_targets, "add_terms", boom)
    resp = client.post(
        "/api/admin/leads/terms",
        json={"rows": [{"term": "", "service_line": "nursing"}]},
    )
    assert resp.status_code == 400
    assert "no `term`" in resp.json()["detail"]


def test_add_terms_happy_path(monkeypatch):
    _override_admin("c1")
    calls = []

    def fake_add(company_id, rows):
        calls.append((company_id, rows))
        return {"requested": len(rows), "inserted": len(rows)}

    monkeypatch.setattr(lead_targets, "add_terms", fake_add)
    resp = client.post(
        "/api/admin/leads/terms",
        json={"rows": [{"term": "RN", "service_line": "nursing"}]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"requested": 1, "inserted": 1}
    assert calls[0][0] == "c1"
    assert calls[0][1] == [{"term": "RN", "service_line": "nursing", "enabled": True}]


# --------------------------------------------------------------------------
# POST /api/admin/leads/locations
# --------------------------------------------------------------------------


def test_add_locations_rejects_an_empty_batch():
    _override_admin("c1")
    resp = client.post("/api/admin/leads/locations", json={"rows": []})
    assert resp.status_code == 400


def test_add_locations_maps_validation_error_to_400(monkeypatch):
    _override_admin("c1")

    def boom(company_id, rows):
        raise lead_targets.TargetValidationError("location 'X' needs a 2-letter `state`")

    monkeypatch.setattr(lead_targets, "add_locations", boom)
    resp = client.post(
        "/api/admin/leads/locations",
        json={"rows": [{"location": "X", "state": "TEX", "granularity": "city"}]},
    )
    assert resp.status_code == 400
    assert "2-letter" in resp.json()["detail"]


def test_add_locations_happy_path(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(
        lead_targets, "add_locations",
        lambda company_id, rows: {"requested": len(rows), "inserted": len(rows)},
    )
    resp = client.post(
        "/api/admin/leads/locations",
        json={"rows": [{"location": "Tampa, FL", "state": "FL", "granularity": "city"}]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"requested": 1, "inserted": 1}


# --------------------------------------------------------------------------
# PATCH /api/admin/leads/terms/{id} and /locations/{id}
# --------------------------------------------------------------------------


def test_toggle_term_404s_when_not_found(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(lead_targets, "set_term_enabled", lambda c, i, e: None)
    resp = client.patch("/api/admin/leads/terms/99", json={"enabled": False})
    assert resp.status_code == 404


def test_toggle_term_returns_the_updated_row(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(
        lead_targets, "set_term_enabled",
        lambda c, i, e: {"id": i, "term": "RN", "enabled": e},
    )
    resp = client.patch("/api/admin/leads/terms/1", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"id": 1, "term": "RN", "enabled": False}


def test_toggle_location_404s_when_not_found(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(lead_targets, "set_location_enabled", lambda c, i, e: None)
    resp = client.patch("/api/admin/leads/locations/99", json={"enabled": False})
    assert resp.status_code == 404


def test_toggle_location_returns_the_updated_row(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(
        lead_targets, "set_location_enabled",
        lambda c, i, e: {"id": i, "location": "Tampa, FL", "enabled": e},
    )
    resp = client.patch("/api/admin/leads/locations/2", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


# --------------------------------------------------------------------------
# PUT /api/admin/leads/overrides
# --------------------------------------------------------------------------


def test_set_override_pins_a_cell(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(
        lead_targets, "set_override",
        lambda c, t, l, e: {"term_id": t, "location_id": l, "enabled": e},
    )
    resp = client.put(
        "/api/admin/leads/overrides",
        json={"term_id": 1, "location_id": 2, "enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json() == {"override": {"term_id": 1, "location_id": 2, "enabled": False}}


def test_set_override_null_unpins_and_returns_200_not_404(monkeypatch):
    """`enabled: null` is the expected way to delete a pin — `set_override`
    returning None for this case is success, not a scope miss."""
    _override_admin("c1")
    monkeypatch.setattr(lead_targets, "set_override", lambda c, t, l, e: None)
    resp = client.put(
        "/api/admin/leads/overrides",
        json={"term_id": 1, "location_id": 2, "enabled": None},
    )
    assert resp.status_code == 200
    assert resp.json() == {"override": None}


def test_set_override_404s_on_a_genuine_scope_miss(monkeypatch):
    """`enabled` was true/false (a pin attempt) but `set_override` returned
    None — the term/location did not belong to this tenant."""
    _override_admin("c1")
    monkeypatch.setattr(lead_targets, "set_override", lambda c, t, l, e: None)
    resp = client.put(
        "/api/admin/leads/overrides",
        json={"term_id": 1, "location_id": 2, "enabled": True},
    )
    assert resp.status_code == 404
