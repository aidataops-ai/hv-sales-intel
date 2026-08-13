"""Auth and wiring for the two background stages.

The stages spend model credits unattended, so the interesting cases are the
ones where they should refuse to run at all.
"""

import time as real_time

from fastapi.testclient import TestClient

from api.index import app
from src import lead_targets
from src.settings import settings

client = TestClient(app)

# ensure_targets is a no-op in these tests — seeding is covered separately.
_NO_SEED = {"terms": 0, "locations": 0}


def _stub_empty_sweep(monkeypatch):
    """Wire the collect loop so every source finds nothing due — the no-op
    baseline most tests build on. `enabled_terms`/`list_config` still need a
    value even when nothing is claimed, since collect() fetches them once up
    front before the claim loop starts."""
    monkeypatch.setattr("src.lead_targets.enabled_terms", lambda c: [
        {"id": 10, "term": "dental receptionist", "service_line": "Virtual Dental Assistant"},
    ])
    monkeypatch.setattr(
        "src.lead_targets.list_config",
        lambda c: {"terms": [], "locations": [], "overrides": []},
    )
    monkeypatch.setattr("src.lead_targets.claim_locations", lambda c, s, limit: [])


def _one_location() -> dict:
    return {
        "id": 1, "location": "Tampa, FL", "state": "FL", "granularity": "city",
        "last_indeed_at": None, "last_linkedin_at": None,
        "indeed_zero_streak": 0, "linkedin_zero_streak": 0,
    }


def test_collect_is_disabled_when_no_secret_is_configured(monkeypatch):
    """An unset secret disables the route rather than leaving it open. A
    background stage that burns credits is not something to leave
    unauthenticated by omission."""
    monkeypatch.setattr(settings, "lead_cron_secret", "")
    assert client.post("/api/cron/leads/collect").status_code == 503


def test_qualify_is_disabled_when_no_secret_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "lead_cron_secret", "")
    assert client.post("/api/cron/leads/qualify").status_code == 503


def test_collect_rejects_a_wrong_secret(monkeypatch):
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    resp = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "wrong"})
    assert resp.status_code == 401


def test_collect_rejects_a_missing_secret_header(monkeypatch):
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    assert client.post("/api/cron/leads/collect").status_code == 401


def test_qualify_rejects_a_wrong_secret(monkeypatch):
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    resp = client.post("/api/cron/leads/qualify",
                       headers={"X-Cron-Secret": "wrong"})
    assert resp.status_code == 401


def test_collect_with_a_valid_secret_runs_and_reports(monkeypatch):
    """Nothing is claimable here, so the sweep is a no-op — which is the point:
    a stage that finds nothing to do must return a clean summary, not raise."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    _stub_empty_sweep(monkeypatch)
    resp = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company_id"] == "c1"
    assert body["locations"] == 0
    assert body["sources"]


def test_collect_stops_claiming_once_a_source_has_nothing_due(monkeypatch):
    """`claim_locations` returning [] must end that source's phase — not loop
    forever, and not be re-polled after the empty result."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    monkeypatch.setattr("src.lead_targets.enabled_terms", lambda c: [
        {"id": 10, "term": "dental receptionist", "service_line": "Virtual Dental Assistant"},
    ])
    monkeypatch.setattr(
        "src.lead_targets.list_config",
        lambda c: {"terms": [], "locations": [], "overrides": []},
    )
    calls = []

    def fake_claim(company_id, source, limit):
        calls.append(source)
        return []

    monkeypatch.setattr("src.lead_targets.claim_locations", fake_claim)

    resp = client.post("/api/cron/leads/collect", headers={"X-Cron-Secret": "s3cret"})
    assert resp.status_code == 200
    assert resp.json()["locations"] == 0
    # One claim attempt per enabled source (indeed, linkedin), each once —
    # an empty result must end that source's phase, not be retried.
    assert len(calls) == len(set(calls))


def test_collect_flags_a_sweep_where_every_swept_location_returned_nothing(monkeypatch):
    """The Indeed key-rotation tripwire: zero rows everywhere, no exception."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    monkeypatch.setattr("src.lead_targets.enabled_terms", lambda c: [
        {"id": 10, "term": "dental receptionist", "service_line": "Virtual Dental Assistant"},
    ])
    monkeypatch.setattr(
        "src.lead_targets.list_config",
        lambda c: {"terms": [], "locations": [], "overrides": []},
    )

    location = _one_location()
    calls = {"n": 0}

    def fake_claim(company_id, source, limit):
        calls["n"] += 1
        return [location] if calls["n"] == 1 else []

    monkeypatch.setattr("src.lead_targets.claim_locations", fake_claim)
    monkeypatch.setattr("src.lead_targets.stamp_location", lambda *a: None)
    monkeypatch.setattr("src.lead_targets.record_target_result", lambda *a: None)
    monkeypatch.setattr("src.lead_targets.record_location_sweep", lambda *a: None)
    monkeypatch.setattr("src.job_boards.search_jobs",
                        lambda *a, **k: ([], {"indeed": {"rows": 0, "error": None}}))

    body = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"}).json()
    assert body["locations"] == 1
    assert "zero rows" in body["alert"]


def test_a_productive_sweep_raises_no_alert(monkeypatch):
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    monkeypatch.setattr("src.lead_targets.enabled_terms", lambda c: [
        {"id": 10, "term": "dental receptionist", "service_line": "Virtual Dental Assistant"},
    ])
    monkeypatch.setattr(
        "src.lead_targets.list_config",
        lambda c: {"terms": [], "locations": [], "overrides": []},
    )

    location = _one_location()
    calls = {"n": 0}

    def fake_claim(company_id, source, limit):
        calls["n"] += 1
        return [location] if calls["n"] == 1 else []

    monkeypatch.setattr("src.lead_targets.claim_locations", fake_claim)
    monkeypatch.setattr("src.lead_targets.stamp_location", lambda *a: None)
    monkeypatch.setattr("src.lead_targets.record_target_result", lambda *a: None)
    monkeypatch.setattr("src.lead_targets.record_location_sweep", lambda *a: None)
    monkeypatch.setattr(
        "src.job_boards.search_jobs",
        lambda *a, **k: ([{"source": "indeed", "external_id": "x", "title": "T"}],
                         {"indeed": {"rows": 1, "error": None}}),
    )
    monkeypatch.setattr("src.lead_store.upsert_postings", lambda rows: len(rows))
    monkeypatch.setattr("src.lead_store.existing_external_ids", lambda source, ids: set())

    body = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"}).json()
    assert body["rows"] == 1
    assert body["new"] == 1
    assert "alert" not in body


def test_an_incomplete_location_is_not_stamped(monkeypatch):
    """A deadline hit mid-location must NOT stamp or record a sweep — the
    crash-safe contract (plan §3) is that only a location whose full term
    list finished gets a fresh cursor; an incomplete one is redone next run."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    monkeypatch.setattr("src.lead_targets.enabled_terms", lambda c: [
        {"id": 10, "term": "dental receptionist", "service_line": "Virtual Dental Assistant"},
        {"id": 11, "term": "medical assistant", "service_line": "Virtual Medical Assistant"},
    ])
    monkeypatch.setattr(
        "src.lead_targets.list_config",
        lambda c: {"terms": [], "locations": [], "overrides": []},
    )
    monkeypatch.setattr(
        "src.lead_targets.claim_locations", lambda c, s, limit: [_one_location()]
    )
    monkeypatch.setattr("src.lead_store.existing_external_ids", lambda source, ids: set())
    monkeypatch.setattr("src.lead_store.upsert_postings", lambda rows: len(rows))
    monkeypatch.setattr("src.lead_targets.record_target_result", lambda *a: None)

    stamp_calls = []
    sweep_calls = []
    monkeypatch.setattr("src.lead_targets.stamp_location",
                        lambda *a: stamp_calls.append(a))
    monkeypatch.setattr("src.lead_targets.record_location_sweep",
                        lambda *a: sweep_calls.append(a))

    # A fake clock that jumps past the deadline the instant the first term's
    # search runs — so the SECOND term's deadline check trips regardless of
    # how many incidental `time.monotonic()` calls happen around it. Far more
    # robust than pinning an exact call count.
    clock = {"t": 1000.0}
    monkeypatch.setattr(real_time, "monotonic", lambda: clock["t"])

    def fake_search(term, location, sources=None, target=None, hours_old=None):
        clock["t"] = 10_000_000.0  # jump well past any budget's deadline
        return (
            [{"source": "indeed", "external_id": f"id-{term}", "title": "T"}],
            {"indeed": {"rows": 1, "error": None}},
        )

    monkeypatch.setattr("src.job_boards.search_jobs", fake_search)

    body = client.post(
        "/api/cron/leads/collect?budget_minutes=1",
        headers={"X-Cron-Secret": "s3cret"},
    ).json()

    assert body["incomplete"] == 1
    assert stamp_calls == [], "an incomplete location must not be stamped"
    assert sweep_calls == [], "an incomplete location must not record a sweep"
    # The first term still ran and was recorded — only the STAMP is withheld.
    assert body["rows"] == 1


def test_running_out_of_credits_stops_the_run_cleanly(monkeypatch):
    """Stop rather than keep calling the model against a balance that can't
    pay. The postings stay unqualified and are re-claimed after a top-up."""
    from src.credits import InsufficientCreditsError

    calls = []

    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr(
        "src.lead_store.claim_unqualified",
        lambda company_id, limit: [{"id": i, "title": "Dental Receptionist"}
                                   for i in range(40)],
    )

    def fake_qualify(chunk, company_id=None, user_id=None):
        calls.append(len(chunk))
        raise InsufficientCreditsError("INSUFFICIENT_CREDITS")

    monkeypatch.setattr("src.lead_qualifier.qualify_batch", fake_qualify)

    body = client.post("/api/cron/leads/qualify",
                       headers={"X-Cron-Secret": "s3cret"}).json()
    assert body["verdicts"] == 0
    assert any("insufficient credits" in e for e in body["errors"])
    assert len(calls) == 1, "should stop after the first failure, not retry every batch"


def test_a_missing_tenant_is_a_503_not_a_silent_no_op(monkeypatch):
    """A run that quietly does nothing looks identical to a board outage."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")

    def boom():
        raise lead_targets.NoLeadCompany("no companies exist")

    monkeypatch.setattr("src.lead_targets.resolve_company_id", boom)
    resp = client.post("/api/cron/leads/collect", headers={"X-Cron-Secret": "s3cret"})
    assert resp.status_code == 503
    assert "no companies exist" in resp.json()["detail"]


def test_collect_seeds_targets_on_a_cold_start(monkeypatch):
    """The first run must work without a separate admin seed step."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets",
                        lambda c: {"terms": 21, "locations": 155})
    _stub_empty_sweep(monkeypatch)

    body = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"}).json()
    assert body["seeded"] == 176
    assert body["company_id"] == "c1"


def test_seed_targets_requires_an_admin():
    assert client.post("/api/admin/leads/seed-targets").status_code == 401


def test_vercel_cron_bearer_header_is_accepted(monkeypatch):
    """Vercel's scheduler sends `Authorization: Bearer $CRON_SECRET` and cannot
    be configured to send anything else."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    _stub_empty_sweep(monkeypatch)
    monkeypatch.setattr("src.lead_store.claim_unqualified", lambda c, n: [])
    resp = client.post("/api/cron/leads/collect",
                       headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200


def test_a_wrong_bearer_token_is_still_rejected(monkeypatch):
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    resp = client.post("/api/cron/leads/collect",
                       headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_both_stages_answer_a_get(monkeypatch):
    """Vercel cron issues a GET, not a POST."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    _stub_empty_sweep(monkeypatch)
    monkeypatch.setattr("src.lead_store.claim_unqualified", lambda c, n: [])
    headers = {"X-Cron-Secret": "s3cret"}
    assert client.get("/api/cron/leads/collect", headers=headers).status_code == 200
    assert client.get("/api/cron/leads/qualify", headers=headers).status_code == 200


def test_the_scheduled_paths_exist_on_the_app():
    """A typo'd path in vercel.json is a cron that silently 404s forever."""
    import json
    import pathlib

    config = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent / "vercel.json").read_text()
    )
    registered = {route.path for route in app.routes if hasattr(route, "path")}
    for job in config.get("crons", []):
        assert job["path"] in registered, job["path"]


def test_the_retired_admin_matrix_routes_answer_501_not_a_crash(monkeypatch):
    """`lead_targets.list_targets`/`add_targets`/`set_target_enabled` are
    deleted (Phase 1). The routes that called them are stubbed to 501 rather
    than left to 500 with an AttributeError — Phase 3 replaces them with the
    dimension-shaped routes from docs/refactor/instant-signals-targets.md §4."""
    from src.auth import require_admin

    app.dependency_overrides[require_admin] = lambda: {"company_id": "c1", "id": "u1"}
    try:
        assert client.get("/api/admin/leads/config").status_code == 501
        assert client.post("/api/admin/leads/targets", json={"rows": []}).status_code == 501
        assert client.patch(
            "/api/admin/leads/targets/1", json={"enabled": True}
        ).status_code == 501
    finally:
        app.dependency_overrides.pop(require_admin, None)
