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
        source=None, states="FL,GA", salary=None, search=None,
    )
    assert filters["cities"] == ["Miami", "Tampa"]
    assert filters["tracks"] == ["Virtual Dental Assistant"]
    assert filters["states"] == ["FL", "GA"]


def test_empty_multi_select_params_produce_no_filter():
    filters = _lead_filters(
        cities="", tracks=",,", disposition=None, band=None, decision=None,
        work_mode=None, source=None, states=None, salary=None,
        search=None,
    )
    assert filters["cities"] == []
    assert filters["tracks"] == []
    assert filters["states"] == []


def test_the_feed_and_the_export_build_filters_the_same_way():
    """Exporting the whole table from a filtered view is the obvious trap.
    Both endpoints call this one builder, so they cannot drift."""
    import inspect

    from api.index import export_leads_csv, list_leads_endpoint

    feed_params = set(inspect.signature(list_leads_endpoint).parameters)
    export_params = set(inspect.signature(export_leads_csv).parameters)
    filter_params = {
        "cities", "tracks", "disposition", "band", "decision", "work_mode",
        "source", "states", "salary", "search",
    }
    assert filter_params <= feed_params
    assert filter_params <= export_params


def test_export_columns_are_the_talentdb_field_mapping():
    """The CSV columns are the Talent-DB `fields` keys (the receiver's schema),
    so an exported CSV round-trips into a Talent-DB CSV import."""
    from src import talentdb

    cols = talentdb.CSV_COLUMNS
    assert cols[0] == "source_practice_id"
    # No-source / envelope-only fields are NOT columns.
    for absent in ("salesforceId", "salesforceUpdatedAt",
                   "hiring_timeline", "locations_count"):
        assert absent not in cols
    assert "Industry" not in cols               # not sent / not evaluated
    for key in ("source_practice_id", "Company", "LastName", "Email", "Lead_Type__c",
                "No_of_Providers__c", "source", "interested_tracks",
                "organization_size", "alternate_phone", "practice_notes", "pain_points",
                "lead_role", "posting_source", "posting_url", "role_title", "icp_tier",
                "summary", "sales_angles"):
        assert key in cols
    assert len(cols) == len(set(cols))          # no duplicate columns


def test_posting_from_lead_maps_flattened_keys_back():
    """`leads_for_export` flattens the posting onto the lead; the export undoes
    that so talentdb's builders see a raw posting dict."""
    from api.index import _posting_from_lead

    posting = _posting_from_lead({
        "posting_id": 5567, "source": "indeed", "url": "u", "title": "MA",
        "board_remote_flag": False, "posting_created_at": "2026-08-02T06:00:00Z",
        "last_seen_at": "2026-08-10T06:00:00Z", "match_status": "auto",
        "employer_name": "Board Co",
    })
    assert posting["id"] == 5567
    assert posting["source"] == "indeed"
    assert posting["first_seen_at"] == "2026-08-02T06:00:00Z"   # renamed back
    assert posting["employer_name"] == "Board Co"


def test_export_row_maps_practice_and_posting_to_talentdb_keys():
    """A full practice + reconstructed posting + lead produce the camelCase keys
    the webhook sends — owner email, company name, source slug, providers."""
    from api.index import _posting_from_lead
    from src import talentdb

    lead = {"posting_id": 5567, "source": "linkedin", "title": "RN",
            "employer_name": "Fallback Co", "provider_count": 6,
            "service_line": "Virtual Dental Assistant"}
    practice = {"id": 1024, "place_id": "ChIJx", "name": "Bright Smile Dental",
                "owner_name": "Jane Doe", "owner_email": "jane@brightsmile.com",
                "phone": "305-555-0100"}
    row = talentdb.build_fields(practice, _posting_from_lead(lead), lead)
    assert row["Company"] == "Bright Smile Dental"
    assert row["FirstName"] == "Jane"
    assert row["LastName"] == "Doe"                      # from owner_name, not company
    assert row["Email"] == "jane@brightsmile.com"
    assert row["source"] == "hv-sales-intel-linkedin"    # slug
    assert row["posting_source"] == "linkedin"           # raw
    assert row["source_practice_id"] == "1024"
    assert row["No_of_Providers__c"] == 6
    assert "Industry" not in row                         # not sent
    assert row["interested_tracks"] == ["88bcb836-c0aa-11f0-a242-325255367c63"]  # UUID
    assert "salesforceId" not in row


def test_export_row_uses_employer_name_when_no_practice():
    """An unmatched posting gets Company (and the required LastName) from the
    posting's employer; FirstName stays omitted (no owner)."""
    from api.index import _posting_from_lead
    from src import talentdb

    lead = {"posting_id": 1, "source": "indeed", "employer_name": "Board Only Co"}
    row = talentdb.build_fields(None, _posting_from_lead(lead), lead)
    assert row["Company"] == "Board Only Co"
    assert row["LastName"] == "Board Only Co"
    assert "FirstName" not in row


def test_patch_only_exposes_workflow_fields():
    """The API surface itself must not offer a way to overwrite a verdict."""
    from api.index import PatchLeadRequest

    assert set(PatchLeadRequest.model_fields) <= lead_store.WORKFLOW_COLUMNS


def test_the_feed_defaults_to_keeps_only():
    """Most postings are discards — systems, DSOs, clinical roles. Showing them
    by default buries the handful of real leads."""
    filters = _lead_filters(
        cities=None, tracks=None, disposition=None, band=None, decision=None,
        work_mode=None, source=None, states=None, salary=None,
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
            work_mode=None, source=None, states=None, salary=None,
            search=None,
        )
