"""Auth and wiring for the two background stages.

The stages spend model credits unattended, so the interesting cases are the
ones where they should refuse to run at all.
"""

from fastapi.testclient import TestClient

from api.index import app
from src import lead_targets
from src.settings import settings

client = TestClient(app)

# ensure_targets is a no-op in these tests — seeding is covered separately.
_NO_SEED = {"config": 0, "existing": 1, "inserted": 0}


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
    monkeypatch.setattr("src.lead_targets.claim_targets", lambda c, n: [])
    resp = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company_id"] == "c1"
    assert body["targets"] == 0
    assert body["sources"]


def test_collect_flags_a_sweep_where_every_target_returned_nothing(monkeypatch):
    """The Indeed key-rotation tripwire: zero rows everywhere, no exception."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    monkeypatch.setattr(
        "src.lead_targets.claim_targets",
        lambda company_id, limit: [
            {"id": 1, "term": "dental receptionist", "location": "Tampa, FL",
             "state": "FL", "service_line": "Virtual Dental Assistant"},
        ],
    )
    monkeypatch.setattr("src.lead_targets.record_target_result", lambda *a: None)
    monkeypatch.setattr("src.job_boards.search_jobs",
                        lambda *a, **k: ([], {"indeed": {"rows": 0, "error": None}}))

    body = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"}).json()
    assert body["zero_row_targets"] == 1
    assert "Indeed" in body["alert"]


def test_a_productive_sweep_raises_no_alert(monkeypatch):
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    monkeypatch.setattr(
        "src.lead_targets.claim_targets",
        lambda company_id, limit: [
            {"id": 1, "term": "dental receptionist", "location": "Tampa, FL",
             "state": "FL", "service_line": "Virtual Dental Assistant"},
        ],
    )
    monkeypatch.setattr("src.lead_targets.record_target_result", lambda *a: None)
    monkeypatch.setattr(
        "src.job_boards.search_jobs",
        lambda *a, **k: ([{"source": "indeed", "external_id": "x", "title": "T"}],
                         {"indeed": {"rows": 1, "error": None}}),
    )
    monkeypatch.setattr("src.lead_store.upsert_postings", lambda rows: len(rows))

    body = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"}).json()
    assert body["rows"] == 1
    assert "alert" not in body


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
                        lambda c: {"config": 434, "existing": 0, "inserted": 434})
    monkeypatch.setattr("src.lead_targets.claim_targets", lambda c, n: [])

    body = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"}).json()
    assert body["seeded"] == 434
    assert body["company_id"] == "c1"


def test_seed_targets_requires_an_admin():
    assert client.post("/api/admin/leads/seed-targets").status_code == 401


def test_vercel_cron_bearer_header_is_accepted(monkeypatch):
    """Vercel's scheduler sends `Authorization: Bearer $CRON_SECRET` and cannot
    be configured to send anything else."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.resolve_company_id", lambda: "c1")
    monkeypatch.setattr("src.lead_targets.ensure_targets", lambda c: _NO_SEED)
    monkeypatch.setattr("src.lead_targets.claim_targets", lambda c, n: [])
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
    monkeypatch.setattr("src.lead_targets.claim_targets", lambda c, n: [])
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
