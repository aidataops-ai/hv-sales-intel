"""Tests for lead persistence.

The column-split tests are the point of this file. ADR-04 puts the
qualification verdict and the SDR workflow in one row for read speed, and the
price of that is a rule no type system enforces: each writer touches only its
own half. A regression here is silent — the leads still render correctly, and
nobody notices until a rep asks where their approvals went.
"""

import pytest

from src import lead_store


# --------------------------------------------------------------------------
# A recording stand-in for the Supabase client. Only the calls lead_store
# actually makes are implemented; anything else raises loudly rather than
# quietly returning a mock that makes a broken test pass.
# --------------------------------------------------------------------------


class FakeQuery:
    def __init__(self, table, log, rows):
        self.table = table
        self.log = log
        self.rows = rows

    def upsert(self, payload, **kwargs):
        self.log.append(("upsert", self.table, payload, kwargs))
        return self

    def update(self, payload, **kwargs):
        self.log.append(("update", self.table, payload, kwargs))
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return type("Result", (), {"data": self.rows, "count": len(self.rows or [])})()


class FakeClient:
    """`rows` answers every `.table()` call the same way — fine while a test
    only touches one table. `by_table` overrides that per table name, for
    tests (like collector_health's) that query more than one table and need
    each to answer differently."""

    def __init__(self, rows=None, by_table=None):
        self.log = []
        self.rows = rows if rows is not None else []
        self.by_table = by_table or {}

    def table(self, name):
        return FakeQuery(name, self.log, self.by_table.get(name, self.rows))


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(lead_store, "_client", lambda: client)
    return client


def _written(client, op):
    return [entry[2] for entry in client.log if entry[0] == op]


# --------------------------------------------------------------------------
# The column split
# --------------------------------------------------------------------------


def test_write_verdicts_never_writes_workflow_columns(fake):
    """The regression that would reset every SDR's pipeline.

    A re-qualification pass carrying a stale `disposition` must not be able to
    overwrite one an operator set — so the payload is filtered, not trusted.
    """
    lead_store.write_verdicts("company-1", [{
        "posting_id": 42,
        "decision": "keep",
        "confidence": 0.91,
        "confidence_band": "ready",
        "band_rank": 1,
        # A caller bug: these belong to the operator, not the qualifier.
        "disposition": "approved",
        "notes": "clobbered",
        "reject_reason": "wrong",
        "contacted_at": "2026-08-01T00:00:00Z",
    }])

    payload = _written(fake, "upsert")[0][0]
    for column in lead_store.WORKFLOW_COLUMNS:
        assert column not in payload, f"{column} leaked into a verdict write"
    assert payload["decision"] == "keep"
    assert payload["company_id"] == "company-1"
    assert payload["posting_id"] == 42


def test_write_verdicts_stamps_qualified_at(fake):
    lead_store.write_verdicts("company-1", [{"posting_id": 1, "decision": "discard"}])
    assert _written(fake, "upsert")[0][0]["qualified_at"]


def test_write_verdicts_upserts_on_the_tenant_posting_pair(fake):
    """`unique (company_id, posting_id)` is what stops a posting being
    re-qualified — and so re-billed — for the same tenant."""
    lead_store.write_verdicts("company-1", [{"posting_id": 1, "decision": "keep"}])
    kwargs = [e[3] for e in fake.log if e[0] == "upsert"][0]
    assert kwargs["on_conflict"] == "company_id,posting_id"


def test_write_verdicts_skips_rows_with_no_posting_id(fake):
    written = lead_store.write_verdicts("company-1", [{"decision": "keep"}])
    assert written == 0
    assert not _written(fake, "upsert")


def test_update_lead_workflow_never_writes_verdict_columns(fake):
    """The mirror image: an operator action must not rewrite the reason the
    lead was surfaced."""
    lead_store.update_lead_workflow("company-1", 7, {
        "disposition": "approved",
        "notes": "called, left voicemail",
        # A caller bug in the other direction.
        "decision": "discard",
        "confidence": 0.1,
        "reason": "rewritten",
        "draft": "rewritten",
    })

    payload = _written(fake, "update")[0]
    for column in lead_store.VERDICT_COLUMNS:
        assert column not in payload, f"{column} leaked into a workflow write"
    assert payload["disposition"] == "approved"


def test_the_two_column_groups_do_not_overlap():
    assert not (lead_store.VERDICT_COLUMNS & lead_store.WORKFLOW_COLUMNS)


def test_workflow_write_records_who_touched_it(fake):
    lead_store.update_lead_workflow("company-1", 7, {"disposition": "approved"},
                                    user_id="user-9")
    payload = _written(fake, "update")[0]
    assert payload["last_touched_by"] == "user-9"
    assert payload["last_touched_at"]


# --------------------------------------------------------------------------
# Bands (ADR-07)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("confidence,band", [
    (1.0, "ready"),
    (0.85, "ready"),
    (0.849, "check"),
    (0.70, "check"),
    (0.699, "decide"),
    (0.0, "decide"),
    (None, "decide"),
])
def test_confidence_bands(confidence, band):
    assert lead_store.band_for(confidence)[0] == band


def test_band_rank_orders_ready_before_check_before_decide():
    """Alphabetically these words sort check, decide, ready — which is why the
    feed sorts on the rank and not the label."""
    ranks = [lead_store.band_for(c)[1] for c in (0.95, 0.8, 0.5)]
    assert ranks == sorted(ranks)
    assert ranks[0] < ranks[1] < ranks[2]


def test_an_unqualified_lead_lands_in_the_review_queue():
    """A missing confidence must not read as a confident keep."""
    assert lead_store.band_for(None) == ("decide", lead_store.BAND_RANK["decide"])


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_flatten_lifts_the_posting_onto_the_lead():
    row = lead_store._flatten({
        "id": 3, "disposition": "undecided", "service_line": "Virtual Dental Assistant",
        "posting": {"id": 99, "title": "Dental Receptionist", "city": "Tampa",
                    "first_seen_at": "2026-08-01T00:00:00Z"},
    })
    assert row["id"] == 3, "the lead id must survive the merge"
    assert row["title"] == "Dental Receptionist"
    assert row["city"] == "Tampa"
    assert row["posting_created_at"] == "2026-08-01T00:00:00Z"
    assert "posting" not in row


def test_flatten_never_lets_a_posting_field_shadow_a_lead_field():
    row = lead_store._flatten({
        "id": 3, "disposition": "approved", "created_at": "2026-08-05T00:00:00Z",
        "posting": {"id": 99, "disposition": "ignored", "created_at": "1999-01-01T00:00:00Z"},
    })
    assert row["disposition"] == "approved"
    assert row["created_at"] == "2026-08-05T00:00:00Z"


def test_the_feed_select_joins_rather_than_nests():
    """Without `!inner`, a filter on a posting column blanks the embedded
    object instead of dropping the lead — the city filter would silently
    return every lead with an empty posting."""
    assert "!inner" in lead_store.LEAD_SELECT


def test_sort_keys_are_whitelisted():
    """A bad ?sort= must not reach the query builder."""
    column, _ = lead_store._SORT_COLUMNS.get("'; drop table --", ("band_rank", False))
    assert column == "band_rank"


def test_upsert_postings_refreshes_last_seen(fake):
    """first_seen_at keeps its insert default; the gap between the two is how
    long a posting has stayed open."""
    lead_store.upsert_postings([{"source": "indeed", "external_id": "abc", "title": "X"}])
    payload = _written(fake, "upsert")[0][0]
    assert payload["last_seen_at"]
    assert "first_seen_at" not in payload


def test_upsert_postings_is_not_company_scoped(fake):
    """Postings are shared across tenants (ADR-04) — a company_id here would
    store the same posting once per tenant."""
    lead_store.upsert_postings([{"source": "indeed", "external_id": "abc", "title": "X"}])
    assert "company_id" not in _written(fake, "upsert")[0][0]


def test_existing_external_ids_returns_what_the_fake_table_has(monkeypatch):
    """`upsert_postings`'s own return count can't answer "how many were new"
    — PostgREST's upsert returns every affected row, inserted or updated
    alike. `existing_external_ids` is the pre-check the collector uses
    instead, so novelty is a real not-already-in-job_postings diff."""
    client = FakeClient(rows=[{"external_id": "abc"}, {"external_id": "def"}])
    monkeypatch.setattr(lead_store, "_client", lambda: client)
    assert lead_store.existing_external_ids("indeed", ["abc", "def", "xyz"]) == {"abc", "def"}


def test_existing_external_ids_is_empty_for_an_empty_batch(monkeypatch):
    monkeypatch.setattr(lead_store, "_client", lambda: FakeClient())
    assert lead_store.existing_external_ids("indeed", []) == set()


# --------------------------------------------------------------------------
# Collector health — the zero-row tripwire
#
# Rewritten for the dimension model (instant-signals refactor, Phase 2):
# `collector_health` now reads `search_locations` (per-source cursors +
# zero-streaks) and `target_runs` (crash-mid-sweep detection) instead of the
# retired `company_search_targets` matrix. `FakeClient(by_table=...)` answers
# each table separately since this function queries three of them.
# --------------------------------------------------------------------------


def test_all_swept_locations_returning_zero_rows_raises_the_indeed_alert(monkeypatch):
    locations = [
        {"id": 1, "last_indeed_at": "2026-08-05T10:00:00Z", "last_linkedin_at": None,
         "indeed_zero_streak": 1, "linkedin_zero_streak": 0},
        {"id": 2, "last_indeed_at": "2026-08-05T10:01:00Z", "last_linkedin_at": None,
         "indeed_zero_streak": 2, "linkedin_zero_streak": 0},
    ]
    client = FakeClient(by_table={"search_locations": locations, "target_runs": []})
    monkeypatch.setattr(lead_store, "_client", lambda: client)
    health = lead_store.collector_health("company-1")
    assert health["zero_row_locations"] == 2
    assert "Indeed" in health["alert"]


def test_a_healthy_sweep_raises_nothing(monkeypatch):
    locations = [
        {"id": 1, "last_indeed_at": "2026-08-05T10:00:00Z", "last_linkedin_at": None,
         "indeed_zero_streak": 0, "linkedin_zero_streak": 0},
        {"id": 2, "last_indeed_at": "2026-08-05T10:01:00Z", "last_linkedin_at": None,
         "indeed_zero_streak": 0, "linkedin_zero_streak": 0},
    ]
    client = FakeClient(by_table={"search_locations": locations, "target_runs": []})
    monkeypatch.setattr(lead_store, "_client", lambda: client)
    assert lead_store.collector_health("company-1")["alert"] is None


def test_an_unswept_location_set_is_not_an_alert(monkeypatch):
    """Before the first collect run every location has both cursors null.
    That is a cold start, not a board outage."""
    locations = [
        {"id": 1, "last_indeed_at": None, "last_linkedin_at": None,
         "indeed_zero_streak": 0, "linkedin_zero_streak": 0},
    ]
    client = FakeClient(by_table={"search_locations": locations, "target_runs": []})
    monkeypatch.setattr(lead_store, "_client", lambda: client)
    assert lead_store.collector_health("company-1")["alert"] is None


def test_a_bulk_verdict_write_has_uniform_keys(fake):
    """PostgREST rejects a bulk upsert whose rows carry different keys. One
    verdict that omitted `draft` would 400 the whole batch — 20 postings the
    tenant has already been billed for."""
    lead_store.write_verdicts("company-1", [
        {"posting_id": 1, "decision": "keep", "draft": "hello", "confidence": 0.9},
        {"posting_id": 2, "decision": "discard"},
    ])
    rows = _written(fake, "upsert")[0]
    assert len(rows) == 2
    assert set(rows[0]) == set(rows[1])
    assert rows[1]["draft"] is None


def test_a_started_but_unstamped_location_is_unfinished_not_zero_row(monkeypatch):
    """`stamp_location` only fires once a location's FULL sweep finishes, so
    an interrupted run leaves individual `target_runs` cells recorded but no
    cursor stamped on the location. Counting that as zero-row would fire the
    Indeed alert every time a sweep was killed mid-flight."""
    locations = [
        # completed sweep, found rows
        {"id": 1, "last_indeed_at": "2026-08-05T10:00:00Z", "last_linkedin_at": None,
         "indeed_zero_streak": 0, "linkedin_zero_streak": 0},
        # some cells recorded, but the run was killed before the location as
        # a whole was stamped
        {"id": 2, "last_indeed_at": None, "last_linkedin_at": None,
         "indeed_zero_streak": 0, "linkedin_zero_streak": 0},
    ]
    runs = [{"location_id": 2}]
    client = FakeClient(by_table={"search_locations": locations, "target_runs": runs})
    monkeypatch.setattr(lead_store, "_client", lambda: client)
    health = lead_store.collector_health("company-1")
    assert health["swept"] == 1
    assert health["unfinished"] == 1
    assert health["zero_row_locations"] == 0
    assert health["alert"] is None


def test_band_distribution_counts_keeps_only(monkeypatch):
    """The band measures confidence in the VERDICT, not lead quality — a
    confidently-rejected hospital system also scores 'ready'. Since discards
    outnumber keeps ~9:1, counting everything makes the chart read as a pile
    of strong leads when it is mostly confident rejections."""
    rows = (
        [{"decision": "discard", "confidence_band": "ready", "disposition": "undecided",
          "created_at": "2026-08-05T00:00:00Z", "posting": {"source": "indeed"}}] * 9
        + [{"decision": "keep", "confidence_band": "check", "disposition": "undecided",
            "created_at": "2026-08-05T00:00:00Z", "posting": {"source": "indeed"}}]
    )
    client = FakeClient(rows=rows)
    monkeypatch.setattr(lead_store, "_client", lambda: client)
    result = lead_store.lead_analytics("company-1")

    assert result["bands"] == {"check": 1}, "discards must not inflate the bands"
    assert result["total"] == 10, "the total still counts everything qualified"
    assert result["keep_rate"] == 0.1
