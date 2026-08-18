"""What actually goes over the wire — column lists, filters, round trips.

These are cost tests, not behaviour tests. The pipeline runs hourly against a
Supabase free tier that caps egress at 5 GB/month, and two reads
(`lead_store.claim_unqualified` and `practice_matcher.load_practices_by_city`)
were between them moving 25-35 GB/month by asking for whole rows and then
throwing ~99% of them away in Python. The write side had the mirror problem:
`increment_export_counts` issued three round trips per exported row, ~15,000
for one CSV, which does not fit inside a serverless invocation.

Nothing here asserts on results the other suites already cover. Each test pins
one of: **which columns were requested**, **which filter reached the server**,
**how many round trips were made**. Those are invisible to an ordinary
behavioural test — every one of these functions returned the right answer
before the fix, just at 1000× the cost — so they need a client that records the
query rather than one that only answers it.
"""

import pytest

from src import lead_store, practice_matcher, storage
from src.models import Practice


# --------------------------------------------------------------------------
# A recording Supabase stand-in.
#
# Unlike the minimal fakes in test_lead_store.py / test_practice_matcher.py,
# this one actually APPLIES eq/in_/order/range and projects the select list,
# because the assertions here are about paging and filtering. A fake that
# ignored `range` would let a paging bug pass, and one that ignored the select
# list could not show that `description` stayed on the server.
# --------------------------------------------------------------------------


def _project(row: dict, columns: str | None) -> dict:
    if not columns or columns.strip() == "*":
        return dict(row)
    wanted = [c.strip() for c in columns.split(",") if c.strip()]
    return {k: v for k, v in row.items() if k in wanted}


class FakeQuery:
    def __init__(self, table, rows, log):
        self.table = table
        self.rows = rows
        self.log = log
        self.op = "select"
        self.columns: str | None = None
        self.payload = None
        self.upsert_kwargs: dict = {}
        self.eqs: dict = {}
        self.ins: dict = {}
        self.order_by: tuple | None = None
        self.window: tuple | None = None

    # -- builders ----------------------------------------------------------

    def select(self, columns="*", **kwargs):
        self.columns = columns
        return self

    def update(self, payload, **kwargs):
        self.op, self.payload = "update", dict(payload)
        return self

    def upsert(self, payload, **kwargs):
        self.op, self.payload, self.upsert_kwargs = "upsert", payload, kwargs
        return self

    def eq(self, key, value):
        self.eqs[key] = value
        return self

    def in_(self, key, values):
        self.ins[key] = list(values)
        return self

    def lte(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def order(self, column, desc=False):
        self.order_by = (column, desc)
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *a, **k):
        return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    # -- the wire ----------------------------------------------------------

    def execute(self):
        self.log.append(self)
        if self.op in ("update", "upsert"):
            written = self.payload if isinstance(self.payload, list) else [self.payload]
            ids = next(iter(self.ins.values()), None)
            if ids is not None:
                written = [dict(self.payload) for _ in ids]
            return type("R", (), {"data": written, "count": len(written)})()

        rows = [r for r in self.rows
                if all(r.get(k) == v for k, v in self.eqs.items())
                and all(r.get(k) in set(vals) for k, vals in self.ins.items())]
        if self.order_by:
            rows = sorted(rows, key=lambda r: r.get(self.order_by[0]),
                          reverse=self.order_by[1])
        if self.window:
            rows = rows[self.window[0]:self.window[1] + 1]
        rows = [_project(r, self.columns) for r in rows]
        return type("R", (), {"data": rows, "count": len(rows)})()


class FakeClient:
    def __init__(self, **by_table):
        self.by_table = by_table
        self.log: list[FakeQuery] = []

    def table(self, name):
        return FakeQuery(name, list(self.by_table.get(name, [])), self.log)

    # -- assertions helpers ------------------------------------------------

    def reads(self, table):
        return [q for q in self.log if q.table == table and q.op == "select"]

    def writes(self, table, op="update"):
        return [q for q in self.log if q.table == table and q.op == op]


# --------------------------------------------------------------------------
# lead_store.claim_unqualified — the single largest egress item in the app.
# --------------------------------------------------------------------------


def _posting(pid: int) -> dict:
    return {
        "id": pid,
        "source": "indeed",
        "external_id": f"e{pid}",
        "url": f"https://example.test/{pid}",
        "title": "Front Office Coordinator",
        "employer_name": "Bay Family Dentistry",
        "employer_name_norm": "bay family dentistry",
        "location_raw": "Tampa, FL, US",
        "city": "Tampa",
        "state": "FL",
        "posted_at": "2026-08-01T00:00:00Z",
        "salary_min": 20,
        "salary_max": 24,
        "salary_interval": "hourly",
        "board_remote_flag": False,
        "service_line_hint": "Virtual Dental Assistant",
        # The 45 MB column. Every assertion below is ultimately about this.
        "description": "x" * 4000,
        "first_seen_at": "2026-08-01T00:00:00Z",
        "last_seen_at": "2026-08-01T00:00:00Z",
    }


@pytest.fixture
def claim_world(monkeypatch):
    """2,500 postings of which only the newest 3 are unqualified — the steady
    state that made this read cost a whole table scan of full rows."""
    postings = [_posting(i) for i in range(1, 2501)]
    qualified = [{"posting_id": i, "company_id": "c1"} for i in range(1, 2498)]
    client = FakeClient(job_postings=postings, company_job_leads=qualified)
    monkeypatch.setattr(lead_store, "_client", lambda: client)
    return client


def test_the_anti_join_scan_asks_only_for_ids(claim_world):
    """The fix, in one assertion. Every scan page must request `id` and
    nothing else — in steady state the scan walks essentially the whole
    `job_postings` table, so a single extra column on it is worth more egress
    than everything else in the codebase combined."""
    lead_store.claim_unqualified("c1", 3)

    scans = [q for q in claim_world.reads("job_postings") if q.window is not None]
    assert scans, "the scan should still page"
    assert {q.columns for q in scans} == {"id"}


def test_only_the_survivors_are_hydrated_with_a_description(claim_world):
    """`description` IS needed — the qualifier prompt excerpts it — so it is
    fetched, but for the handful of rows that survive the anti-join, never for
    the scan. One hydrate call, scoped by id, is the whole budget."""
    postings = lead_store.claim_unqualified("c1", 3)

    hydrates = [q for q in claim_world.reads("job_postings")
                if q.columns and "description" in q.columns]
    assert len(hydrates) == 1
    assert sorted(hydrates[0].ins["id"]) == [2498, 2499, 2500]
    assert all(p["description"] for p in postings)


def test_the_claim_carries_exactly_what_the_qualifier_reads(claim_world):
    """The column list is not a guess: `lead_qualifier._posting_row` renders
    these and `parse_verdict` reads id + service_line_hint. Widening it costs
    egress; narrowing it silently degrades the prompt."""
    postings = lead_store.claim_unqualified("c1", 1)

    assert set(postings[0]) == {
        "id", "title", "employer_name", "location_raw", "board_remote_flag",
        "salary_min", "salary_max", "salary_interval", "service_line_hint",
        "description",
    }


def test_the_claim_is_still_newest_first_and_capped_at_the_limit(claim_world):
    """Contract preserved: same rows, same order, same count as the old
    `select("*")` scan. PostgREST promises no ordering on an `in_` filter, so
    this pins that the caller's batching order did not quietly change."""
    postings = lead_store.claim_unqualified("c1", 3)

    assert [p["id"] for p in postings] == [2500, 2499, 2498]


def test_a_tenant_with_nothing_to_claim_makes_no_hydrate_call(claim_world):
    """The common cron outcome. Nothing survived the anti-join, so the second
    phase must not fire at all."""
    claim_world.by_table["company_job_leads"] = [
        {"posting_id": i, "company_id": "c1"} for i in range(1, 2501)
    ]

    assert lead_store.claim_unqualified("c1", 3) == []
    assert not [q for q in claim_world.reads("job_postings")
                if q.columns and "description" in q.columns]


# --------------------------------------------------------------------------
# practice_matcher.load_practices_by_city — the second egress hog.
# --------------------------------------------------------------------------


def _practice(pid, name, city, state):
    return {"id": pid, "name": name, "city": city, "state": state,
            "service_line": "Virtual Dental Assistant"}


def test_the_city_filter_reaches_the_query_instead_of_python():
    """It used to page the entire service-line-tagged bank (~23 MB) and drop
    the wrong cities client-side, every qualify run."""
    client = FakeClient(practices=[
        _practice(1, "Bay Family Dentistry", "Tampa", "FL"),
        _practice(2, "Peach Dental", "Atlanta", "GA"),
    ])

    by_location, _ = practice_matcher.load_practices_by_city(
        client, only_city_labels={"Tampa"},
    )

    reads = client.reads("practices")
    assert all("city" in q.ins for q in reads), "the filter must be server-side"
    assert "Tampa" in reads[0].ins["city"]
    assert list(by_location) == [practice_matcher.location_key("Tampa", "FL")]


def test_no_filter_still_scans_everything_for_the_bulk_script():
    """`scripts/link_postings.py` does a full pass with posting_ids=None and
    must keep loading the whole universe."""
    client = FakeClient(practices=[
        _practice(1, "Bay Family Dentistry", "Tampa", "FL"),
        _practice(2, "Peach Dental", "Atlanta", "GA"),
    ])

    _, by_city = practice_matcher.load_practices_by_city(client)

    assert all("city" not in q.ins for q in client.reads("practices"))
    assert len(by_city) == 2


def _matcher_world(monkeypatch, posting_city="Tampa"):
    client = FakeClient(
        company_job_leads=[{"posting_id": 1, "company_id": "c1",
                            "decision": "keep", "employer_type": "independent"}],
        job_postings=[{"id": 1, "employer_name_norm": "bay family dentistry",
                       "city": posting_city, "state": "FL"}],
        practices=[
            _practice(1, "Bay Family Dentistry", posting_city, "FL"),
            _practice(2, "Peach Dental", "Atlanta", "GA"),
        ],
    )
    monkeypatch.setattr(practice_matcher, "_client", lambda: client)
    return client


def test_the_cron_scope_pushes_its_cities_into_the_practices_query(monkeypatch):
    """The qualify cron matches one batch at a time, so its city set is tiny
    — that is the case worth pushing server-side."""
    client = _matcher_world(monkeypatch)

    practice_matcher.link_postings("c1", posting_ids=[1], dry_run=True)

    reads = client.reads("practices")
    assert reads and all("city" in q.ins for q in reads)
    assert "Tampa" in reads[0].ins["city"]


def test_the_full_pass_does_not_send_a_city_list(monkeypatch):
    """A full pass spans essentially every city in the universe; an `in_`
    list that long is a worse query than the sequential read it replaces."""
    client = _matcher_world(monkeypatch)

    practice_matcher.link_postings("c1", posting_ids=None, dry_run=True)

    assert all("city" not in q.ins for q in client.reads("practices"))


@pytest.mark.parametrize("label, also_sent", [
    ("St. Petersburg", "Saint Petersburg"),
    ("Saint Petersburg", "St. Petersburg"),
    ("Ft. Myers", "Fort Myers"),
    ("Winston-Salem", "Winston Salem"),
    ("Winston Salem", "Winston-Salem"),
    ("tampa", "Tampa"),
])
def test_every_spelling_that_folds_alike_is_sent(label, also_sent):
    """Each pair folds to one `city_key` but is two distinct strings to
    PostgREST, so both have to be in the `in_` list or the server filter
    stops being a superset of the fold."""
    assert practice_matcher.city_key(label) == practice_matcher.city_key(also_sent)
    assert also_sent in practice_matcher.city_spellings(label)


def test_a_city_starting_with_st_is_not_mangled_into_saint():
    """`\\bSt\\b` and not a bare prefix: 'Staten Island' must survive."""
    assert practice_matcher.city_spellings("Staten Island") == {
        "Staten Island", "Staten-Island",
    }


def test_the_server_filter_is_a_superset_of_the_city_key_fold():
    """`city_key` folds 'St. Petersburg' and 'Saint Petersburg' together;
    PostgREST only does exact matching. A naive `.in_(["St. Petersburg"])`
    would therefore drop practices the old Python filter kept — so the query
    sends every spelling that folds to the same key, and `city_key` still
    decides what survives."""
    client = FakeClient(practices=[
        _practice(1, "Gulf Coast Dental", "Saint Petersburg", "FL"),
    ])

    _, by_city = practice_matcher.load_practices_by_city(
        client, only_city_labels={"St. Petersburg"},
    )

    assert by_city[practice_matcher.city_key("St. Petersburg")]


def test_the_filter_is_on_city_alone_not_city_and_state():
    """`by_city` is the fallback index for postings with no recorded state,
    and it has to stay complete for every city loaded — so the query must not
    narrow by state even though `by_location` keys on city+state."""
    client = FakeClient(practices=[
        _practice(1, "Sunrise Dental", "Greenville", "NC"),
        _practice(2, "Sunrise Dental", "Greenville", "SC"),
    ])

    _, by_city = practice_matcher.load_practices_by_city(
        client, only_city_labels={"Greenville"},
    )

    reads = client.reads("practices")
    assert all("state" not in q.ins and "state" not in q.eqs for q in reads)
    assert len(by_city[practice_matcher.city_key("Greenville")]) == 2


# --------------------------------------------------------------------------
# practice_matcher.link_postings — one UPDATE per matched posting.
# --------------------------------------------------------------------------


def test_matched_postings_sharing_a_payload_are_written_in_one_update(monkeypatch):
    """Several openings at one employer is the normal case, not the
    exception, and they produce byte-identical update payloads."""
    client = FakeClient(
        company_job_leads=[
            {"posting_id": i, "company_id": "c1",
             "decision": "keep", "employer_type": "independent"}
            for i in (1, 2, 3)
        ],
        job_postings=[
            {"id": i, "employer_name_norm": "bay family dentistry",
             "city": "Tampa", "state": "FL"}
            for i in (1, 2, 3)
        ],
        practices=[_practice(7, "Bay Family Dentistry", "Tampa", "FL")],
    )
    monkeypatch.setattr(practice_matcher, "_client", lambda: client)

    stats = practice_matcher.link_postings("c1")

    links = [q for q in client.writes("job_postings")
             if q.payload.get("practice_id") is not None]
    assert len(links) == 1, "three postings, one round trip"
    assert sorted(links[0].ins["id"]) == [1, 2, 3]
    assert stats["linked"] == 3


def test_postings_matching_different_practices_still_get_their_own_write(monkeypatch):
    """Grouping must be on the payload, not on the batch — two practices are
    two different `practice_id`s and must not be collapsed."""
    client = FakeClient(
        company_job_leads=[
            {"posting_id": i, "company_id": "c1",
             "decision": "keep", "employer_type": "independent"}
            for i in (1, 2)
        ],
        job_postings=[
            {"id": 1, "employer_name_norm": "bay family dentistry",
             "city": "Tampa", "state": "FL"},
            {"id": 2, "employer_name_norm": "peach dental",
             "city": "Tampa", "state": "FL"},
        ],
        practices=[
            _practice(7, "Bay Family Dentistry", "Tampa", "FL"),
            _practice(8, "Peach Dental", "Tampa", "FL"),
        ],
    )
    monkeypatch.setattr(practice_matcher, "_client", lambda: client)

    practice_matcher.link_postings("c1")

    links = [q for q in client.writes("job_postings")
             if q.payload.get("practice_id") is not None]
    assert sorted(q.payload["practice_id"] for q in links) == [7, 8]


# --------------------------------------------------------------------------
# storage.increment_export_counts — 1 + 3 writes per exported row.
# --------------------------------------------------------------------------


@pytest.fixture
def export_client(monkeypatch):
    def _build(rows):
        client = FakeClient(practices=rows, company_practice_state=[])
        monkeypatch.setattr(storage, "_get_client", lambda: client)
        return client
    return _build


def test_the_export_count_read_is_chunked_at_500(export_client):
    """A practices CSV export passes up to 50k place_ids. Unchunked, the
    request URL is long enough to be rejected outright — a silent no-op that
    breaks `max_exports=0` dedup on exactly the exports big enough to need
    it."""
    rows = [{"id": i, "place_id": f"p{i}", "export_count": 0}
            for i in range(1200)]
    client = export_client(rows)

    storage.increment_export_counts([f"p{i}" for i in range(1200)])

    reads = client.reads("practices")
    assert len(reads) == 3
    assert all(len(q.ins["place_id"]) <= 500 for q in reads)


def test_export_counts_are_written_one_update_per_distinct_count(export_client):
    """Read-modify-write is unavoidable (no `+= 1` in PostgREST) but per-row
    writes are not: every row sharing a count gets the same new value."""
    rows = (
        [{"id": i, "place_id": f"p{i}", "export_count": 0} for i in range(400)]
        + [{"id": 900 + i, "place_id": f"q{i}", "export_count": 3} for i in range(50)]
    )
    client = export_client(rows)

    storage.increment_export_counts([r["place_id"] for r in rows])

    updates = client.writes("practices")
    assert len(updates) == 2, "450 rows, two distinct counts, two round trips"
    assert sorted(q.payload["export_count"] for q in updates) == [1, 4]
    assert sum(len(q.ins["place_id"]) for q in updates) == 450


def test_the_per_row_practice_id_lookup_is_gone(export_client):
    """`_write_per_company_state` re-resolved place_id → practices.id with its
    own SELECT for every row, even though the read two lines above had the id
    in hand. Selecting `id` deletes that whole round trip."""
    rows = [{"id": i, "place_id": f"p{i}", "export_count": 0} for i in range(1, 11)]
    client = export_client(rows)

    storage.increment_export_counts([r["place_id"] for r in rows], company_id="c1")

    reads = client.reads("practices")
    assert len(reads) == 1, "no per-row id re-lookup"
    assert "id" in reads[0].columns

    mirrors = client.writes("company_practice_state", op="upsert")
    assert len(mirrors) == 1, "one bulk upsert, not one per row"
    assert len(mirrors[0].payload) == 10
    assert mirrors[0].payload[0]["practice_id"] == 1
    assert mirrors[0].payload[0]["export_count"] == 1


def test_lead_export_counts_are_also_grouped(monkeypatch):
    """Same shape on the signals side of the export."""
    rows = (
        [{"id": i, "export_count": 0} for i in range(300)]
        + [{"id": 500 + i, "export_count": 2} for i in range(20)]
    )
    client = FakeClient(company_job_leads=rows)
    monkeypatch.setattr(lead_store, "_client", lambda: client)

    lead_store.increment_export_counts([r["id"] for r in rows], user_id="u1")

    updates = client.writes("company_job_leads")
    assert len(updates) == 2
    assert sorted(q.payload["export_count"] for q in updates) == [1, 3]
    assert all(q.payload["last_exported_by"] == "u1" for q in updates)


# --------------------------------------------------------------------------
# The two latent truncations.
# --------------------------------------------------------------------------


def test_duplicate_detection_chunks_its_name_lookup(monkeypatch):
    """A truncated answer here silently ADMITS a duplicate place_id — the one
    thing this function exists to prevent."""
    incoming = [
        Practice(place_id=f"new{i}", name=f"Practice {i}", address="1 Main St",
                 phone="813-555-0100")
        for i in range(1200)
    ]
    client = FakeClient(practices=[])
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    storage.find_duplicate_place_ids(incoming)

    reads = client.reads("practices")
    assert len(reads) == 3
    assert all(len(q.ins["name"]) <= 500 for q in reads)


def test_state_row_seeding_chunks_its_place_id_lookup(monkeypatch):
    """Unchunked, a >1000-place upsert seeded state rows for the first 1,000
    only, and the rest never appeared in the tenant's sidebar."""
    rows = [{"id": i, "place_id": f"p{i}"} for i in range(1, 1201)]
    client = FakeClient(practices=rows, company_practice_state=[])
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    storage._ensure_state_rows_for_practices("c1", [r["place_id"] for r in rows])

    reads = client.reads("practices")
    assert len(reads) == 3
    assert all(len(q.ins["place_id"]) <= 500 for q in reads)
    seeded = sum(len(q.payload)
                 for q in client.writes("company_practice_state", op="upsert"))
    assert seeded == 1200, "every place must get a state row, not just the first 1,000"
