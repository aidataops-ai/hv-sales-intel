"""Auth and wiring for the two background stages.

The stages spend model credits unattended, so the interesting cases are the
ones where they should refuse to run at all.
"""

from fastapi.testclient import TestClient

from api.index import app
from src.settings import settings

client = TestClient(app)


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
    """No tenants have targets in this environment, so the sweep is a no-op —
    which is the point: a stage that finds nothing to do must return a clean
    summary, not raise."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.companies_with_targets", lambda: [])
    resp = client.post("/api/cron/leads/collect",
                       headers={"X-Cron-Secret": "s3cret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["companies"] == 0
    assert body["targets"] == 0
    assert body["sources"]


def test_collect_flags_a_sweep_where_every_target_returned_nothing(monkeypatch):
    """The Indeed key-rotation tripwire: zero rows everywhere, no exception."""
    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.companies_with_targets", lambda: ["c1"])
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
    monkeypatch.setattr("src.lead_targets.companies_with_targets", lambda: ["c1"])
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


def test_running_out_of_credits_stops_one_tenant_not_the_sweep(monkeypatch):
    """A tenant that can't pay must not take the other tenants' runs down."""
    from src.credits import InsufficientCreditsError

    monkeypatch.setattr(settings, "lead_cron_secret", "s3cret")
    monkeypatch.setattr("src.lead_targets.companies_with_targets", lambda: ["broke", "paid"])
    monkeypatch.setattr(
        "src.lead_store.claim_unqualified",
        lambda company_id, limit: [{"id": 1, "title": "Dental Receptionist"}],
    )

    def fake_qualify(chunk, company_id=None, user_id=None):
        if company_id == "broke":
            raise InsufficientCreditsError("INSUFFICIENT_CREDITS")
        return ([{"posting_id": 1, "decision": "keep"}], {"keeps": 1, "missing": 0})

    monkeypatch.setattr("src.lead_qualifier.qualify_batch", fake_qualify)
    monkeypatch.setattr("src.lead_store.write_verdicts",
                        lambda company_id, verdicts: len(verdicts))

    body = client.post("/api/cron/leads/qualify",
                       headers={"X-Cron-Secret": "s3cret"}).json()
    assert body["verdicts"] == 1, "the solvent tenant still got qualified"
    assert any("insufficient credits" in e for e in body["errors"])


def test_seed_targets_requires_an_admin():
    assert client.post("/api/admin/leads/seed-targets").status_code == 401
