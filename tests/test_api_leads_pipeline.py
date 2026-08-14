"""The admin pipeline pause/resume endpoints that flip the GitHub workflow.

"Stop" disables the scheduled workflow and cancels queued/in-progress runs;
"resume" re-enables it; the state read collapses GitHub's workflow `state`
into active/paused for the topbar toggle. These tests pin the auth gate, the
not-configured 503, both happy paths, and a GitHub rejection — without ever
reaching GitHub.
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


@pytest.fixture
def github_configured(monkeypatch):
    monkeypatch.setattr(app_settings, "github_token", "tok")
    monkeypatch.setattr(app_settings, "github_repo", "acme/repo")
    monkeypatch.setattr(app_settings, "github_leads_workflow", "leads.yml")
    # Most tests pin the single-workflow shape; the multi-workflow sweep has
    # its own test below.
    monkeypatch.setattr(app_settings, "github_leads_scheduled_workflows", "leads.yml")


class _FakeResp:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        return self._body


class _FakeClient:
    """Stands in for httpx.AsyncClient; records every request it receives.

    Routes are matched on URL suffix so each test declares just the responses
    it cares about; anything unrouted gets a 404 to fail loudly.
    """

    calls: list = []
    routes: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _respond(self, method, url, **kwargs):
        _FakeClient.calls.append({"method": method, "url": url, **kwargs})
        for suffix, resp in _FakeClient.routes.items():
            if url.endswith(suffix):
                return resp
        return _FakeResp(404)

    async def get(self, url, headers=None, params=None):
        return await self._respond("GET", url, headers=headers, params=params)

    async def put(self, url, headers=None):
        return await self._respond("PUT", url, headers=headers)

    async def post(self, url, headers=None, json=None):
        return await self._respond("POST", url, headers=headers, json=json)


@pytest.fixture
def fake_github(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.routes = {}
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    return _FakeClient


def test_pipeline_endpoints_require_admin():
    """No override -> the real require_admin runs and rejects anonymous calls."""
    client = TestClient(app)
    assert client.get("/api/admin/leads/pipeline").status_code == 401
    assert client.post("/api/admin/leads/pipeline/stop").status_code == 401
    assert client.post("/api/admin/leads/pipeline/resume").status_code == 401


def test_pipeline_503_when_token_unset(sample_admin_profile, monkeypatch):
    _override_admin(sample_admin_profile)
    monkeypatch.setattr(app_settings, "github_token", "")
    for call in (
        lambda c: c.get("/api/admin/leads/pipeline"),
        lambda c: c.post("/api/admin/leads/pipeline/stop"),
        lambda c: c.post("/api/admin/leads/pipeline/resume"),
    ):
        resp = call(TestClient(app))
        assert resp.status_code == 503
        assert "actions:write" in resp.json()["detail"]


def test_state_collapses_github_state(
    sample_admin_profile, github_configured, fake_github
):
    _override_admin(sample_admin_profile)
    fake_github.routes = {
        "/workflows/leads.yml": _FakeResp(200, {"state": "disabled_manually"})
    }

    resp = TestClient(app).get("/api/admin/leads/pipeline")
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "paused",
        "workflows": {"leads.yml": "disabled_manually"},
    }
    assert fake_github.calls[0]["url"] == (
        "https://api.github.com/repos/acme/repo/actions/workflows/leads.yml"
    )
    assert fake_github.calls[0]["headers"]["Authorization"] == "Bearer tok"

    fake_github.routes = {"/workflows/leads.yml": _FakeResp(200, {"state": "active"})}
    assert TestClient(app).get("/api/admin/leads/pipeline").json()["state"] == "active"


def test_stop_disables_workflow_and_cancels_runs(
    sample_admin_profile, github_configured, fake_github
):
    _override_admin(sample_admin_profile)
    fake_github.routes = {
        "/workflows/leads.yml/disable": _FakeResp(204),
        "/workflows/leads.yml/runs": _FakeResp(
            200, {"workflow_runs": [{"id": 111}]}
        ),
        "/runs/111/cancel": _FakeResp(202),
    }

    resp = TestClient(app).post("/api/admin/leads/pipeline/stop")
    assert resp.status_code == 200
    # One run per status query (queued + in_progress both return run 111 here).
    assert resp.json() == {
        "ok": True, "state": "paused", "workflows": ["leads.yml"],
        "cancelled_runs": 2,
    }

    urls = [(c["method"], c["url"]) for c in fake_github.calls]
    assert urls[0] == (
        "PUT",
        "https://api.github.com/repos/acme/repo/actions/workflows/leads.yml/disable",
    )
    assert ("POST", "https://api.github.com/repos/acme/repo/actions/runs/111/cancel") in urls
    statuses = [c["params"]["status"] for c in fake_github.calls if c["method"] == "GET"]
    assert statuses == ["queued", "in_progress"]


def test_stop_surfaces_a_github_rejection(
    sample_admin_profile, github_configured, fake_github
):
    _override_admin(sample_admin_profile)
    fake_github.routes = {
        "/workflows/leads.yml/disable": _FakeResp(403, text="forbidden")
    }

    resp = TestClient(app).post("/api/admin/leads/pipeline/stop")
    assert resp.status_code == 502
    assert "403" in resp.json()["detail"]


def test_resume_enables_workflow(
    sample_admin_profile, github_configured, fake_github
):
    _override_admin(sample_admin_profile)
    fake_github.routes = {"/workflows/leads.yml/enable": _FakeResp(204)}

    resp = TestClient(app).post("/api/admin/leads/pipeline/resume")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "state": "active", "workflows": ["leads.yml"]}
    assert fake_github.calls[0]["method"] == "PUT"
    assert fake_github.calls[0]["url"].endswith("/workflows/leads.yml/enable")


def test_pipeline_controls_span_every_scheduled_workflow(
    sample_admin_profile, github_configured, fake_github, monkeypatch
):
    """The toggle controls the source-split scheduled PAIR: state is active if
    ANY workflow is active, stop disables each one, and a workflow GitHub
    hasn't registered yet (404 — file not on the default branch) is skipped
    rather than failing the whole request."""
    monkeypatch.setattr(
        app_settings, "github_leads_scheduled_workflows",
        "leads-indeed.yml,leads-linkedin.yml,leads-unborn.yml",
    )
    _override_admin(sample_admin_profile)

    fake_github.routes = {
        "/workflows/leads-indeed.yml": _FakeResp(200, {"state": "disabled_manually"}),
        "/workflows/leads-linkedin.yml": _FakeResp(200, {"state": "active"}),
        # leads-unborn.yml is unrouted -> the fake's 404, which must be skipped
    }
    resp = TestClient(app).get("/api/admin/leads/pipeline")
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "active",
        "workflows": {
            "leads-indeed.yml": "disabled_manually",
            "leads-linkedin.yml": "active",
        },
    }

    fake_github.calls = []
    fake_github.routes = {
        "/workflows/leads-indeed.yml/disable": _FakeResp(204),
        "/workflows/leads-linkedin.yml/disable": _FakeResp(204),
        "/workflows/leads-indeed.yml/runs": _FakeResp(200, {"workflow_runs": []}),
        "/workflows/leads-linkedin.yml/runs": _FakeResp(
            200, {"workflow_runs": [{"id": 9}]}
        ),
        "/runs/9/cancel": _FakeResp(202),
    }
    resp = TestClient(app).post("/api/admin/leads/pipeline/stop")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True, "state": "paused",
        "workflows": ["leads-indeed.yml", "leads-linkedin.yml"],
        "cancelled_runs": 2,  # queued + in_progress both return run 9
    }
    disables = [c["url"] for c in fake_github.calls if c["url"].endswith("/disable")]
    assert disables == [
        "https://api.github.com/repos/acme/repo/actions/workflows/leads-indeed.yml/disable",
        "https://api.github.com/repos/acme/repo/actions/workflows/leads-linkedin.yml/disable",
        "https://api.github.com/repos/acme/repo/actions/workflows/leads-unborn.yml/disable",
    ]

    fake_github.calls = []
    fake_github.routes = {
        "/workflows/leads-indeed.yml/enable": _FakeResp(204),
        "/workflows/leads-linkedin.yml/enable": _FakeResp(204),
    }
    resp = TestClient(app).post("/api/admin/leads/pipeline/resume")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True, "state": "active",
        "workflows": ["leads-indeed.yml", "leads-linkedin.yml"],
    }
