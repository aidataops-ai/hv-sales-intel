"""Auth, routing and filter plumbing for the operator-facing lead routes."""

from fastapi.testclient import TestClient

from api.index import _lead_filters, app
from src import lead_store

client = TestClient(app)


def test_every_lead_route_requires_auth():
    assert client.get("/api/leads").status_code == 401
    assert client.get("/api/leads/1").status_code == 401
    assert client.get("/api/leads/filters").status_code == 401
    assert client.get("/api/leads/analytics").status_code == 401
    assert client.get("/api/leads/export.csv").status_code == 401
    assert client.patch("/api/leads/1", json={"disposition": "approved"}).status_code == 401


def test_export_route_is_not_swallowed_by_the_id_route():
    """`/api/leads/export.csv` must be declared before `/api/leads/{lead_id}`.
    If the parameter route wins, "export.csv" fails int validation with a 422
    and the download silently breaks."""
    resp = client.get("/api/leads/export.csv")
    assert resp.status_code == 401, "reached the id route instead of the export route"


def test_analytics_and_filters_routes_are_not_swallowed_either():
    assert client.get("/api/leads/analytics").status_code == 401
    assert client.get("/api/leads/filters").status_code == 401


def test_multi_select_params_split_on_commas():
    filters = _lead_filters(
        cities="Miami,Tampa", tracks="Virtual Dental Assistant",
        disposition=None, band=None, decision=None, work_mode=None,
        source=None, state=None, salary=None, search=None,
    )
    assert filters["cities"] == ["Miami", "Tampa"]
    assert filters["tracks"] == ["Virtual Dental Assistant"]


def test_empty_multi_select_params_produce_no_filter():
    filters = _lead_filters(
        cities="", tracks=",,", disposition=None, band=None, decision=None,
        work_mode=None, source=None, state=None, salary=None,
        search=None,
    )
    assert filters["cities"] == []
    assert filters["tracks"] == []


def test_the_feed_and_the_export_build_filters_the_same_way():
    """Exporting the whole table from a filtered view is the obvious trap.
    Both endpoints call this one builder, so they cannot drift."""
    import inspect

    from api.index import export_leads_csv, list_leads_endpoint

    feed_params = set(inspect.signature(list_leads_endpoint).parameters)
    export_params = set(inspect.signature(export_leads_csv).parameters)
    filter_params = {
        "cities", "tracks", "disposition", "band", "decision", "work_mode",
        "source", "state", "salary", "search",
    }
    assert filter_params <= feed_params
    assert filter_params <= export_params


def test_export_columns_match_the_design():
    from api.index import _LEAD_EXPORT_COLUMNS

    expected = [
        "employer_name", "title", "city", "state", "source", "url", "posted_at",
        "salary_min", "salary_max", "salary_interval", "work_mode",
        "service_line", "employer_type", "provider_count", "confidence",
        "confidence_band", "reason", "draft", "disposition",
        "created_at",
    ]
    assert _LEAD_EXPORT_COLUMNS == expected


def test_patch_only_exposes_workflow_fields():
    """The API surface itself must not offer a way to overwrite a verdict."""
    from api.index import PatchLeadRequest

    assert set(PatchLeadRequest.model_fields) <= lead_store.WORKFLOW_COLUMNS


def test_the_feed_defaults_to_keeps_only():
    """Most postings are discards — systems, DSOs, clinical roles. Showing them
    by default buries the handful of real leads."""
    filters = _lead_filters(
        cities=None, tracks=None, disposition=None, band=None, decision=None,
        work_mode=None, source=None, state=None, salary=None,
        search=None,
    )
    assert filters["decision"] in (None, "", "keep")

    captured = {}

    class Q:
        def __getattr__(self, name):
            def f(*a, **k):
                if name == "eq":
                    captured[a[0]] = a[1]
                return self
            return f

    lead_store._apply_filters(Q(), filters=filters)
    assert captured["decision"] == "keep"


def test_all_is_the_explicit_opt_out():
    captured = {}

    class Q:
        def __getattr__(self, name):
            def f(*a, **k):
                if name == "eq":
                    captured[a[0]] = a[1]
                return self
            return f

    lead_store._apply_filters(Q(), filters={"decision": "all"})
    assert "decision" not in captured


def test_an_unknown_decision_filter_is_rejected():
    from fastapi import HTTPException

    import pytest

    with pytest.raises(HTTPException):
        _lead_filters(
            cities=None, tracks=None, disposition=None, band=None, decision="maybe",
            work_mode=None, source=None, state=None, salary=None,
            search=None,
        )
