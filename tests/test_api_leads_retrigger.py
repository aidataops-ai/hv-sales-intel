"""The admin 'Run pipeline' endpoint that dispatches the GitHub workflow.

It runs the sweep on a GitHub runner rather than in the API process, so the
endpoint's whole job is to POST a workflow_dispatch for the full sweep. These
tests pin the auth gate, the not-configured 503, the happy path, and a GitHub
rejection — without ever reaching GitHub.
"""

import pytest
from fastapi.testclient import TestClient

from api.index import app, app_settings
from src.auth import require_admin


def _override_admin(profile: dict):
    app.dependency_overrides[require_admin] = lambda: profile


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


class _FakeResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Stands in for httpx.AsyncClient; records the one POST it receives."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResp(204)


def test_retrigger_requires_admin():
    """No override -> the real require_admin runs and rejects the anonymous call."""
    resp = TestClient(app).post("/api/admin/leads/retrigger")
    assert resp.status_code == 401


def test_retrigger_503_when_token_unset(sample_admin_profile, monkeypatch):
    _override_admin(sample_admin_profile)
    monkeypatch.setattr(app_settings, "github_token", "")
    resp = TestClient(app).post("/api/admin/leads/retrigger")
    assert resp.status_code == 503
    assert "actions:write" in resp.json()["detail"]


def test_retrigger_dispatches_the_full_sweep(sample_admin_profile, monkeypatch):
    _override_admin(sample_admin_profile)
    monkeypatch.setattr(app_settings, "github_token", "tok")
    monkeypatch.setattr(app_settings, "github_repo", "acme/repo")
    monkeypatch.setattr(app_settings, "github_leads_workflow", "leads.yml")
    monkeypatch.setattr(app_settings, "github_workflow_ref", "main")
    _FakeClient.calls = []
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    resp = TestClient(app).post("/api/admin/leads/retrigger")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    assert len(_FakeClient.calls) == 1
    call = _FakeClient.calls[0]
    assert call["url"] == (
        "https://api.github.com/repos/acme/repo/actions/workflows/leads.yml/dispatches"
    )
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["json"]["ref"] == "main"
    # Always the full sweep; the workflow's own defaults set the batch size.
    assert call["json"]["inputs"] == {"stage": "both"}


def test_retrigger_surfaces_a_github_rejection(sample_admin_profile, monkeypatch):
    _override_admin(sample_admin_profile)
    monkeypatch.setattr(app_settings, "github_token", "tok")

    class _RejectingClient(_FakeClient):
        async def post(self, url, headers=None, json=None):
            return _FakeResp(422, "no ref")

    monkeypatch.setattr("httpx.AsyncClient", _RejectingClient)
    resp = TestClient(app).post("/api/admin/leads/retrigger")
    assert resp.status_code == 502
    assert "422" in resp.json()["detail"]
