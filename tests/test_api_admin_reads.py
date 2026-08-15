"""The two admin read routes, against a client that truncates like PostgREST.

Both routes used to ask for one big page and aggregate whatever came back:
`/api/admin/usage` with `.limit(5000)` and `/api/admin/users` with a bare
`practices.select("last_touched_by")`. PostgREST caps every response at 1,000
rows regardless of what the limit says, so both were reporting confident
numbers computed over a slice — under-stated spend on one page, wrong
per-user counts on the other.

`FakeSupabase` below enforces that cap. A regression to a single unpaged
request fails these tests instead of quietly shipping short numbers again.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import require_admin

POSTGREST_MAX_ROWS = 1000


class FakeQuery:
    def __init__(self, table, store, calls):
        self.table = table
        self.store = store
        self.calls = calls
        self.selected = None
        self.count_mode = None
        self.head = False
        self.filters: dict = {}
        self.ranges: list[tuple[int, int]] = []
        self.limit_value: int | None = None
        self.range_value: tuple[int, int] | None = None

    def select(self, columns="*", count=None, head=False):
        self.selected = columns
        self.count_mode = count
        self.head = head
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def gte(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self.limit_value = n
        return self

    def range(self, start, end):
        self.range_value = (start, end)
        self.ranges.append((start, end))
        return self

    def _matching(self):
        rows = list(self.store.get(self.table, []))
        for column, value in self.filters.items():
            rows = [r for r in rows if r.get(column) == value]
        return rows

    def execute(self):
        rows = self._matching()
        total = len(rows)
        if self.head:
            # A HEAD count request: the number, no body. The whole point of
            # using one is that the rows never cross the wire.
            return type("Result", (), {"data": [], "count": total})()
        if self.range_value is not None:
            start, end = self.range_value
            width = min(end - start + 1, POSTGREST_MAX_ROWS)
            rows = rows[start:start + width]
        else:
            # The silent ceiling. `.limit(5000)` does not lift it.
            rows = rows[:min(self.limit_value or POSTGREST_MAX_ROWS,
                             POSTGREST_MAX_ROWS)]
        return type("Result", (), {"data": rows, "count": total})()


class FakeSupabase:
    def __init__(self, **tables):
        self.store = tables
        self.calls: dict[str, list[FakeQuery]] = {}

    def table(self, name):
        query = FakeQuery(name, self.store, self.calls)
        self.calls.setdefault(name, []).append(query)
        return query


COMPANY = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def admin_profile(sample_admin_profile):
    return {**sample_admin_profile, "company_id": COMPANY}


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def _call(path, fake, admin_profile):
    app.dependency_overrides[require_admin] = lambda: admin_profile
    with patch("api.index.get_admin_client", return_value=fake):
        return TestClient(app).get(path)


# --------------------------------------------------------------------------
# GET /api/admin/usage
# --------------------------------------------------------------------------


def _usage_event(i):
    return {
        "id": i,
        "company_id": COMPANY,
        "kind": "openai_analyze" if i % 2 else "places_search",
        "model": "gpt-5" if i % 2 else None,
        "input_tokens": 10,
        "output_tokens": 5,
        "calls": 1,
        "cost_cents": 2.0,
        "metadata": {"seq": i},
        "created_at": "2026-08-14T00:00:00Z",
    }


def test_usage_totals_cover_every_page(admin_profile):
    """The number an admin budgets against. With 2,500 events in the window a
    single request saw 1,000 of them, so the page under-reported spend by 60%
    while looking exactly as authoritative."""
    events = [_usage_event(i) for i in range(2500)]
    fake = FakeSupabase(usage_events=events)

    resp = _call("/api/admin/usage?days=30", fake, admin_profile)

    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["events"] == 2500, "a single request would see 1000"
    assert body["totals"]["cost_cents"] == 5000.0
    assert body["totals"]["input_tokens"] == 25_000
    assert body["totals"]["places_calls"] == 1250
    assert body["totals"]["openai_calls"] == 1250

    by_kind = {row["kind"]: row for row in body["by_kind"]}
    assert by_kind["openai_analyze"]["count_events"] == 1250
    assert by_kind["places_search"]["count_events"] == 1250


def test_usage_pages_with_successive_range_calls(admin_profile):
    events = [_usage_event(i) for i in range(2500)]
    fake = FakeSupabase(usage_events=events)

    _call("/api/admin/usage?days=30", fake, admin_profile)

    aggregate = fake.calls["usage_events"][0]
    assert aggregate.ranges == [(0, 999), (1000, 1999), (2000, 2999)]


def test_usage_leaves_the_jsonb_metadata_out_of_the_scan(admin_profile):
    """`metadata` is jsonb and only the newest 50 rows render it. Fetching it
    for the whole window is egress bought for rows nobody looks at — the
    reason full coverage has to come with a column list."""
    events = [_usage_event(i) for i in range(1200)]
    fake = FakeSupabase(usage_events=events)

    _call("/api/admin/usage?days=30", fake, admin_profile)

    aggregate, recent = fake.calls["usage_events"][0], fake.calls["usage_events"][1]
    assert aggregate.selected != "*"
    assert "metadata" not in aggregate.selected
    for needed in ("kind", "model", "input_tokens", "output_tokens",
                   "calls", "cost_cents", "created_at"):
        assert needed in aggregate.selected

    # The rendered tail is a separate, tiny read — that is where metadata rides.
    assert "metadata" in recent.selected
    assert recent.limit_value == 50


def test_usage_response_shape_is_unchanged(admin_profile):
    """The admin usage page consumes this verbatim."""
    events = [_usage_event(i) for i in range(1200)]
    fake = FakeSupabase(usage_events=events)

    body = _call("/api/admin/usage?days=14", fake, admin_profile).json()

    assert set(body) == {"window_days", "by_kind", "by_model",
                         "totals", "recent", "pricing"}
    assert body["window_days"] == 14
    assert len(body["recent"]) == 50
    assert set(body["recent"][0]) == {"created_at", "kind", "model",
                                      "input_tokens", "output_tokens",
                                      "calls", "cost_cents", "metadata"}
    assert body["recent"][0]["metadata"] is not None
    assert set(body["totals"]) == {"events", "places_calls", "openai_calls",
                                   "input_tokens", "output_tokens", "cost_cents",
                                   "places_cost_cents", "openai_cost_cents"}
    assert set(body["pricing"]) == {"openai_per_million_tokens", "places_per_call"}


def test_usage_survives_a_failed_aggregate_read(admin_profile):
    """A broken read must still answer the shape the page expects."""
    class Exploding(FakeSupabase):
        def table(self, name):
            raise RuntimeError("connection reset")

    body = _call("/api/admin/usage", Exploding(), admin_profile).json()
    assert body["totals"]["events"] == 0
    assert body["recent"] == []


# --------------------------------------------------------------------------
# GET /api/admin/users
# --------------------------------------------------------------------------


def _profile(uid, email):
    return {
        "id": uid, "email": email, "name": email.split("@")[0],
        "role": "sdr", "disabled_at": None, "created_at": "2026-04-22T00:00:00Z",
    }


def test_practices_touched_counts_past_the_first_thousand(admin_profile):
    """The count was computed in Python over a 23k-row table read that
    PostgREST clipped to 1,000 rows, so the number was wrong for every user
    and the app paid a table scan to get it wrong."""
    profiles = [_profile("user-a", "a@x.com"), _profile("user-b", "b@x.com")]
    practices = (
        [{"id": i, "last_touched_by": "user-a"} for i in range(2500)]
        + [{"id": 10_000 + i, "last_touched_by": "user-b"} for i in range(30)]
    )
    fake = FakeSupabase(profiles=profiles, practices=practices)

    body = _call("/api/admin/users", fake, admin_profile).json()

    touched = {u["id"]: u["practices_touched"] for u in body["users"]}
    assert touched == {"user-a": 2500, "user-b": 30}


def test_practices_touched_uses_a_head_count_not_a_row_scan(admin_profile):
    """One HEAD per profile (3-10 of them) instead of dragging 23k rows over
    the wire to count them in Python."""
    profiles = [_profile("user-a", "a@x.com"), _profile("user-b", "b@x.com")]
    fake = FakeSupabase(profiles=profiles, practices=[])

    _call("/api/admin/users", fake, admin_profile)

    practice_queries = fake.calls["practices"]
    assert len(practice_queries) == len(profiles)
    for query in practice_queries:
        assert query.head is True and query.count_mode == "exact"
        assert "last_touched_by" in query.filters


def test_users_select_is_narrowed_to_the_columns_returned(admin_profile):
    fake = FakeSupabase(profiles=[_profile("user-a", "a@x.com")], practices=[])

    body = _call("/api/admin/users", fake, admin_profile).json()

    columns = fake.calls["profiles"][0].selected
    assert columns != "*"
    fields = {c.strip() for c in columns.split(",")}
    assert fields == {"id", "email", "name", "role", "disabled_at", "created_at"}
    # ...and the payload the admin page renders is unchanged by the narrowing.
    assert set(body["users"][0]) == fields | {"practices_touched"}


def test_a_failing_count_does_not_take_down_the_user_list(admin_profile):
    class HalfBroken(FakeSupabase):
        def table(self, name):
            if name == "practices":
                raise RuntimeError("statement timeout")
            return super().table(name)

    fake = HalfBroken(profiles=[_profile("user-a", "a@x.com")])

    body = _call("/api/admin/users", fake, admin_profile).json()

    assert body["users"][0]["practices_touched"] == 0
    assert body["users"][0]["email"] == "a@x.com"
