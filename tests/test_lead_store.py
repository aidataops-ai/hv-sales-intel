"""Tests for lead persistence.

The column-split tests are the point of this file. ADR-04 puts the
qualification verdict and the SDR workflow in one row for read speed, and the
price of that is a rule no type system enforces: each writer touches only its
own half. A regression here is silent — the leads still render correctly, and
nobody notices until a rep asks where their approvals went.
"""

import re

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
        # The column list this query asked for, so a test can assert the read
        # stays narrow — full coverage is only affordable on thin rows.
        self.selected: tuple = ()

    def upsert(self, payload, **kwargs):
        self.log.append(("upsert", self.table, payload, kwargs))
        return self

    def update(self, payload, **kwargs):
        self.log.append(("update", self.table, payload, kwargs))
        return self

    def select(self, *a, **k):
        self.selected = a
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def gte(self, column, value):
        self.log.append(("gte", self.table, column, value))
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


class RangeAwareQuery(FakeQuery):
    """A `FakeQuery` that answers `.range()` the way PostgREST really does.

    `FakeQuery.range` is a no-op, so a paged read against it looks identical
    to an unpaged one — which is exactly the bug these tests exist to catch.
    This one slices, and refuses to return more than `PAGE_CAP` rows in a
    single response no matter how wide the requested range is: that silent
    cap is what makes `.limit(20_000)` a lie in production.
    """

    PAGE_CAP = 1000

    def __init__(self, table, log, rows):
        super().__init__(table, log, rows)
        self.ranges: list[tuple[int, int]] = []
        self._page: list | None = None

    def range(self, start, end):
        self.ranges.append((start, end))
        width = min(end - start + 1, self.PAGE_CAP)
        self._page = (self.rows or [])[start:start + width]
        return self

    def execute(self):
        data = self.rows if self._page is None else self._page
        return type("Result", (), {"data": data, "count": len(data or [])})()


class RangeAwareClient(FakeClient):
    """`FakeClient` with paging teeth, keeping every query it handed out so a
    test can assert on the `.range()` calls that were actually issued."""

    def __init__(self, rows=None, by_table=None):
        super().__init__(rows=rows, by_table=by_table)
        self.queries: dict[str, list[RangeAwareQuery]] = {}

    def table(self, name):
        query = RangeAwareQuery(name, self.log, self.by_table.get(name, self.rows))
        self.queries.setdefault(name, []).append(query)
        return query


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


# --------------------------------------------------------------------------
# Truncation — PostgREST's silent 1,000-row ceiling
#
# `.limit(20_000)` does not lift it. Every read below used to hand Python a
# clipped row set and let it compute confident-looking totals over the part
# that survived. `RangeAwareClient` reproduces the ceiling so a regression
# back to a single unpaged request fails loudly instead of quietly.
# --------------------------------------------------------------------------


def _analytics_client(leads):
    """A client whose lead table is `leads` and whose collector tables are
    empty — `lead_analytics` calls `collector_health`, which reads three
    other tables and must not fall through to the lead rows."""
    return RangeAwareClient(by_table={
        "company_job_leads": leads,
        "search_locations": [],
        "target_runs": [],
        "job_postings": [],
    })


def _lead_row(i):
    return {
        "decision": "keep" if i % 4 == 0 else "discard",
        "confidence_band": "ready",
        "disposition": "undecided",
        "service_line": "dental",
        "created_at": "2026-08-05T00:00:00Z",
        "posting": {"source": "indeed"},
    }


def test_analytics_counts_every_page_not_just_the_first_thousand(monkeypatch):
    """The bug that made the analytics page lie.

    With 29k lead rows and a single request, PostgREST returned the newest
    1,000 — so `total` read exactly 1000 forever, `keep_rate` was the keep
    ratio of an arbitrary recent window, and the band/disposition/track
    splits described 3% of the tenant's leads while the page presented them
    as all of them.
    """
    leads = [_lead_row(i) for i in range(2500)]
    client = _analytics_client(leads)
    monkeypatch.setattr(lead_store, "_client", lambda: client)

    result = lead_store.lead_analytics("company-1", days=365)

    assert result["total"] == 2500, "a single request would have stopped at 1000"
    assert result["keep_rate"] == 0.25
    assert result["bands"] == {"ready": 625}, "bands count keeps, over ALL pages"
    assert result["dispositions"] == {"undecided": 2500}
    assert result["tracks"] == {"dental": 2500}
    assert result["per_day"] == [{"day": "2026-08-05", "total": 2500, "indeed": 2500}]


def test_analytics_pages_with_successive_range_calls(monkeypatch):
    """The mechanism, asserted directly: three requests walking the offsets,
    stopping on the short read rather than spinning to the limit."""
    leads = [_lead_row(i) for i in range(2500)]
    client = _analytics_client(leads)
    monkeypatch.setattr(lead_store, "_client", lambda: client)

    lead_store.lead_analytics("company-1", days=365)

    ranges = client.queries["company_job_leads"][0].ranges
    assert ranges == [(0, 999), (1000, 1999), (2000, 2999)]


def test_analytics_narrows_the_select_to_the_columns_it_buckets(monkeypatch):
    """Full coverage is only affordable if the rows are thin. `draft` alone is
    ~8KB, and the posting body is larger still — pulling either for 29k rows
    to count dispositions would trade a truncation bug for an egress bill."""
    client = _analytics_client([])
    monkeypatch.setattr(lead_store, "_client", lambda: client)

    lead_store.lead_analytics("company-1")

    columns = client.queries["company_job_leads"][0].selected[0]
    fields = {c.strip() for c in columns.split(",")}

    # `reason` is a distinct column from `reject_reason`, hence the exact
    # token match rather than a substring check.
    assert not fields & {"draft", "reason", "*"}
    for unread in ("description", "posted_at", "practice"):
        assert unread not in columns, f"{unread} is fetched but never read"
    assert {"decision", "confidence_band", "disposition", "reject_reason",
            "service_line", "created_at"} <= fields
    assert "source" in columns, "the per-day chart facets on the posting source"


def test_analytics_pushes_the_days_window_to_the_server(monkeypatch):
    """`days` used to only slice `per_day` after the fact, so `total` and
    `keep_rate` were whole-table figures sitting next to a 30-day chart —
    and the scan was unbounded. A `gte` bounds both."""
    from datetime import datetime, timezone

    client = _analytics_client([])
    monkeypatch.setattr(lead_store, "_client", lambda: client)

    lead_store.lead_analytics("company-1", days=7)

    filters = [e for e in client.log if e[0] == "gte" and e[1] == "company_job_leads"]
    assert filters, "the days window must be a server-side filter"
    column, value = filters[0][2], filters[0][3]
    assert column == "created_at"
    age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(value)).days
    assert age_days == 7


def test_collector_health_pages_the_location_list(monkeypatch):
    """Latent until national scale, then load-bearing: past 1,000 enabled
    locations a single request drops the tail, so `locations`/`swept`
    under-report and the zero-row tripwire stops watching the locations it
    exists to watch."""
    locations = [
        {"id": i, "last_indeed_at": "2026-08-05T10:00:00Z", "last_linkedin_at": None,
         "indeed_zero_streak": 1, "linkedin_zero_streak": 0}
        for i in range(1, 1201)
    ]
    client = RangeAwareClient(by_table={
        "search_locations": locations, "target_runs": [], "job_postings": [],
    })
    monkeypatch.setattr(lead_store, "_client", lambda: client)

    health = lead_store.collector_health("company-1")

    assert health["locations"] == 1200, "a single request would have seen 1000"
    assert health["swept"] == 1200
    assert health["zero_row_locations"] == 1200
    assert "Indeed" in health["alert"]
    assert client.queries["search_locations"][0].ranges == [(0, 999), (1000, 1999)]


# --------------------------------------------------------------------------
# Query diet: which columns each read path asks for, and the feed's pager
# --------------------------------------------------------------------------


def _select_columns(select: str) -> set[str]:
    """Top-level column names in a PostgREST select string; embeds collapse to
    their alias, so `posting:job_postings!inner(...)` contributes `posting`."""
    out: set[str] = set()
    token: list[str] = []
    depth = 0
    for ch in select:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.add("".join(token).strip())
            token = []
            continue
        if depth == 0 and ch not in "()":
            token.append(ch)
    out.add("".join(token).strip())
    return {c.split(":")[0].strip() for c in out if c.strip()}


def test_export_select_leaves_the_draft_behind_but_keeps_the_description():
    """The export used the detail view's select. `draft` is up to 8 KB a lead
    and appears in no CSV column; `description` is the CSV's
    `posting_description` and must survive the diet."""
    lead_cols = _select_columns(lead_store.LEAD_EXPORT_SELECT)
    assert "draft" not in lead_cols
    for workflow_only in ("reason", "notes", "model", "qualified_at",
                          "last_touched_at", "contacted_at", "reject_reason"):
        assert workflow_only not in lead_cols
    assert "description" in lead_store.LEAD_EXPORT_SELECT


def test_export_practice_embed_is_only_the_lookup_key():
    """The export route re-fetches full practice rows by place_id, because the
    embed never carried the analysis columns the Talent-DB mapping needs — so
    every other embedded column was fetched and then dropped on the floor."""
    assert "practice:practices(place_id)" in lead_store.LEAD_EXPORT_SELECT
    for embedded_only_on_the_feed in ("owner_email", "review_count", "address"):
        assert embedded_only_on_the_feed not in lead_store.LEAD_EXPORT_SELECT


def test_newest_posting_select_covers_every_column_talentdb_reads():
    """Two callers, two appetites: the practice-detail page reads only `id`,
    the Import Lead button hands the whole row to `talentdb.build_fields`.
    Narrowing past the union would silently blank fields on the webhook."""
    import inspect

    from src import talentdb

    selected = _select_columns(lead_store._POSTING_TALENTDB_COLS)
    source = inspect.getsource(talentdb.build_fields)
    read_by_talentdb = set(re.findall(r"pg\.get\(\"(\w+)\"\)", source))

    assert read_by_talentdb <= selected, (
        f"build_fields reads {sorted(read_by_talentdb - selected)}, "
        "which newest_posting_for_practice no longer fetches"
    )
    assert "id" in selected, "_practice_exported reads posting['id']"
    for unread in ("external_id", "employer_name_norm", "location_raw",
                   "salary_min", "match_method", "matched_at"):
        assert unread not in selected


class _FeedQuery:
    """Records the select + range the feed issues, and honours the range so a
    limit+1 probe read behaves the way PostgREST would."""

    def __init__(self, rows, call, planned):
        self._rows = rows
        self._call = call
        self._planned = planned

    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def or_(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def order(self, *a, **k): return self

    @property
    def not_(self):
        return self

    def limit(self, n):
        self._call["limit"] = n
        return self

    def range(self, start, end):
        self._call["range"] = (start, end)
        return self

    def execute(self):
        if self._call["count"]:
            return type("R", (), {"data": [], "count": self._planned})()
        start, end = self._call["range"]
        return type("R", (), {"data": self._rows[start:end + 1], "count": None})()


def _fake_feed(monkeypatch, rows, planned=None):
    calls: list[dict] = []

    def _table(name):
        def _select(columns, count=None):
            call = {"columns": columns, "count": count, "range": None, "limit": None}
            calls.append(call)
            return _FeedQuery(rows, call, planned)

        return type("T", (), {"select": staticmethod(_select)})()

    monkeypatch.setattr(
        lead_store, "_client", lambda: type("C", (), {"table": staticmethod(_table)})()
    )
    return calls


def _lead_rows(n: int) -> list[dict]:
    return [{"id": i, "posting": {"city": "Miami"}} for i in range(n)]


def test_feed_counts_planned_not_exact(monkeypatch):
    """`count="exact"` re-counted the whole 29k-row join on every page load,
    on a shared-CPU instance, to render one number."""
    calls = _fake_feed(monkeypatch, _lead_rows(80), planned=29_400)
    rows, total = lead_store.list_leads("company-1", limit=50)

    assert [c["count"] for c in calls if c["count"]] == ["planned"]
    assert len(rows) == 50
    assert total == 29_400


def test_feed_fetches_one_row_past_the_page_and_never_returns_it(monkeypatch):
    calls = _fake_feed(monkeypatch, _lead_rows(80), planned=29_400)
    rows, _ = lead_store.list_leads("company-1", offset=0, limit=50)

    assert calls[0]["range"] == (0, 50), "must fetch limit+1"
    assert calls[0]["columns"] == lead_store.LEAD_LIST_SELECT
    assert len(rows) == 50


def test_feeds_last_page_is_exact_and_skips_the_count(monkeypatch):
    calls = _fake_feed(monkeypatch, _lead_rows(12), planned=29_400)
    rows, total = lead_store.list_leads("company-1", limit=50)

    assert len(rows) == 12
    assert total == 12, "a short read is the exact total"
    assert len(calls) == 1, "no count query when the page ran off the end"


def test_export_reads_the_export_select_not_the_detail_one(monkeypatch):
    calls = _fake_feed(monkeypatch, _lead_rows(3))
    lead_store.leads_for_export("company-1")

    assert calls[0]["columns"] == lead_store.LEAD_EXPORT_SELECT
    assert calls[0]["columns"] != lead_store.LEAD_SELECT


def test_feed_paging_survives_an_estimate_that_undershoots(monkeypatch):
    """A planner estimate below the rows we can actually see would otherwise
    make api/index.py's `has_more` false while a page still waits."""
    _fake_feed(monkeypatch, _lead_rows(80), planned=3)
    rows, total = lead_store.list_leads("company-1", limit=50)

    assert len(rows) == 50
    assert total > len(rows)
