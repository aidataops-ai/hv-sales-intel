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


def test_delete_term_requires_admin():
    assert client.delete("/api/admin/leads/terms/1").status_code == 401


def test_toggle_location_requires_admin():
    assert client.patch(
        "/api/admin/leads/locations/1", json={"enabled": True}
    ).status_code == 401


def test_delete_location_requires_admin():
    assert client.delete("/api/admin/leads/locations/1").status_code == 401


def test_set_override_requires_admin():
    assert client.put(
        "/api/admin/leads/overrides",
        json={"term_id": 1, "location_id": 1, "enabled": True},
    ).status_code == 401


def test_bulk_toggle_terms_requires_admin():
    assert client.patch(
        "/api/admin/leads/terms/bulk", json={"ids": [1], "enabled": True},
    ).status_code == 401


def test_bulk_toggle_locations_requires_admin():
    assert client.patch(
        "/api/admin/leads/locations/bulk", json={"ids": [1], "enabled": True},
    ).status_code == 401


# --------------------------------------------------------------------------
# PATCH /api/admin/leads/{terms,locations}/bulk
#
# The config page's state switches fanned out one PATCH per row — ~64 requests
# (and 64 sets of auth round trips) to enable a state. These take the whole
# set in one call.
# --------------------------------------------------------------------------


def test_bulk_route_is_not_swallowed_by_the_id_route():
    """`/terms/bulk` must be declared before `/terms/{term_id}`. If the
    parameter route wins, "bulk" fails int validation with a 422 and the
    bulk toggle silently breaks — the same trap `/api/leads/export.csv` has."""
    for path in ("/api/admin/leads/terms/bulk", "/api/admin/leads/locations/bulk"):
        resp = client.patch(path, json={"ids": [1], "enabled": True})
        assert resp.status_code == 401, f"{path} reached the id route ({resp.status_code})"


def test_bulk_toggle_terms_returns_the_updated_rows(monkeypatch):
    _override_admin("c1")
    seen = {}

    def fake_bulk(company_id, ids, enabled):
        seen.update(company_id=company_id, ids=ids, enabled=enabled)
        return [{"id": i, "term": "RN", "enabled": enabled} for i in ids]

    monkeypatch.setattr(lead_targets, "set_terms_enabled", fake_bulk)
    resp = client.patch(
        "/api/admin/leads/terms/bulk", json={"ids": [1, 2, 3], "enabled": False},
    )

    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["updated"]] == [1, 2, 3]
    assert all(r["enabled"] is False for r in resp.json()["updated"])
    # Tenant scope comes from the admin session, never from the body.
    assert seen == {"company_id": "c1", "ids": [1, 2, 3], "enabled": False}


def test_bulk_toggle_locations_returns_the_updated_rows(monkeypatch):
    _override_admin("c1")
    seen = {}

    def fake_bulk(company_id, ids, enabled):
        seen.update(company_id=company_id, ids=ids, enabled=enabled)
        return [{"id": i, "location": "Tampa, FL", "enabled": enabled} for i in ids]

    monkeypatch.setattr(lead_targets, "set_locations_enabled", fake_bulk)
    resp = client.patch(
        "/api/admin/leads/locations/bulk", json={"ids": [7, 8], "enabled": True},
    )

    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["updated"]] == [7, 8]
    assert seen == {"company_id": "c1", "ids": [7, 8], "enabled": True}


def test_bulk_toggle_reports_an_empty_update_rather_than_404ing(monkeypatch):
    """Ids that aren't this tenant's simply don't come back. There is no
    partial-failure story to tell: the tenant filter lives in the UPDATE, so
    a miss and a foreign row are indistinguishable by construction."""
    _override_admin("c1")
    monkeypatch.setattr(lead_targets, "set_terms_enabled", lambda c, i, e: [])
    resp = client.patch(
        "/api/admin/leads/terms/bulk", json={"ids": [999], "enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": []}


# --------------------------------------------------------------------------
# GET /api/admin/leads/config
# --------------------------------------------------------------------------


def test_get_config_returns_the_dimension_shape(monkeypatch):
    _override_admin("c1")
    locations = [{"id": 2, "location": "Tampa, FL", "state": "FL",
                  "granularity": "city", "enabled": True}]
    monkeypatch.setattr(lead_targets, "catalog", lambda: {"states": [], "tracks": []})
    monkeypatch.setattr(
        lead_targets, "list_config",
        lambda company_id: {
            "terms": [{"id": 1, "term": "RN", "service_line": "nursing", "enabled": True}],
            "locations": locations,
            "overrides": [],
        },
    )
    monkeypatch.setattr(
        lead_targets, "sweep_status",
        lambda company_id, locations=None: {"indeed": {"coverage_pct": 90.0}},
    )

    body = client.get("/api/admin/leads/config").json()
    assert body["catalog"] == {"states": [], "tracks": []}
    assert body["terms"][0]["term"] == "RN"
    assert body["locations"][0]["location"] == "Tampa, FL"
    assert body["overrides"] == []
    assert body["sweep"]["indeed"]["coverage_pct"] == 90.0


def test_get_config_hands_sweep_status_the_rows_it_already_read(monkeypatch):
    """`sweep_status` computes coverage from `search_locations` — the same
    table `list_config` just read. The route passes those rows in so the page
    load selects it once, not twice."""
    _override_admin("c1")
    locations = [{"id": 2, "location": "Tampa, FL", "state": "FL",
                  "granularity": "city", "enabled": True}]
    seen: dict = {}

    monkeypatch.setattr(lead_targets, "catalog", lambda: {"states": [], "tracks": []})
    monkeypatch.setattr(
        lead_targets, "list_config",
        lambda company_id: {"terms": [], "locations": locations, "overrides": []},
    )

    def fake_sweep(company_id, locations=None):
        seen["company_id"] = company_id
        seen["locations"] = locations
        return {}

    monkeypatch.setattr(lead_targets, "sweep_status", fake_sweep)

    assert client.get("/api/admin/leads/config").status_code == 200
    assert seen["company_id"] == "c1"
    assert seen["locations"] == locations


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
# DELETE /api/admin/leads/terms/{id} and /locations/{id} (Phase 5)
#
# `ensure_targets` now diff-seeds from the checked-in catalog on every
# collect run, so a DELETEd catalog row would just be resurrected next run.
# The server refuses those with 409 rather than silently no-op-ing or
# building a tombstone — see `lead_targets.CatalogProtectedError`.
# --------------------------------------------------------------------------


def test_delete_term_returns_409_for_a_catalog_row(monkeypatch):
    _override_admin("c1")

    def boom(company_id, term_id):
        raise lead_targets.CatalogProtectedError(
            "'medical assistant' is in the checked-in catalog — disable it instead"
        )

    monkeypatch.setattr(lead_targets, "delete_term", boom)
    resp = client.delete("/api/admin/leads/terms/1")
    assert resp.status_code == 409
    assert "disable it instead" in resp.json()["detail"]


def test_delete_term_404s_when_not_found(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(lead_targets, "delete_term", lambda c, i: None)
    resp = client.delete("/api/admin/leads/terms/99")
    assert resp.status_code == 404


def test_delete_term_removes_a_hand_added_row(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(
        lead_targets, "delete_term",
        lambda c, i: {"id": i, "term": "made-up keyword", "service_line": "X"},
    )
    resp = client.delete("/api/admin/leads/terms/5")
    assert resp.status_code == 200
    assert resp.json()["term"] == "made-up keyword"


def test_delete_location_returns_409_for_a_catalog_row(monkeypatch):
    _override_admin("c1")

    def boom(company_id, location_id):
        raise lead_targets.CatalogProtectedError(
            "'Tampa, FL' is in the checked-in catalog — disable it instead"
        )

    monkeypatch.setattr(lead_targets, "delete_location", boom)
    resp = client.delete("/api/admin/leads/locations/1")
    assert resp.status_code == 409
    assert "disable it instead" in resp.json()["detail"]


def test_delete_location_404s_when_not_found(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(lead_targets, "delete_location", lambda c, i: None)
    resp = client.delete("/api/admin/leads/locations/99")
    assert resp.status_code == 404


def test_delete_location_removes_a_hand_added_row(monkeypatch):
    _override_admin("c1")
    monkeypatch.setattr(
        lead_targets, "delete_location",
        lambda c, i: {"id": i, "location": "Ocala, FL", "state": "FL"},
    )
    resp = client.delete("/api/admin/leads/locations/7")
    assert resp.status_code == 200
    assert resp.json()["location"] == "Ocala, FL"


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
