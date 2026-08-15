from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src import storage
from src.storage import update_practice_fields


def _mock_supabase_update_returning(row):
    client = MagicMock()
    table = MagicMock()
    table.update.return_value = table
    table.eq.return_value = table
    table.execute.return_value = MagicMock(data=[row])
    client.table.return_value = table
    return client, table


def test_update_practice_fields_stamps_touched_by():
    client, table = _mock_supabase_update_returning({"place_id": "p1"})
    with patch("src.storage._get_client", return_value=client):
        update_practice_fields("p1", {"status": "CONTACTED"}, touched_by="user-1")
    call_args = table.update.call_args.args[0]
    assert call_args["status"] == "CONTACTED"
    assert call_args["last_touched_by"] == "user-1"
    assert "last_touched_at" in call_args


def test_update_practice_fields_no_stamp_when_touched_by_none():
    client, table = _mock_supabase_update_returning({"place_id": "p1"})
    with patch("src.storage._get_client", return_value=client):
        update_practice_fields("p1", {"status": "CONTACTED"})
    call_args = table.update.call_args.args[0]
    assert "last_touched_by" not in call_args
    assert "last_touched_at" not in call_args


# --------------------------- client memoization -----------------------------


@pytest.fixture
def fake_create_client(monkeypatch):
    """Isolate the module-level client cache and stub out client construction.

    monkeypatch restores the cache globals after each test, so these tests
    never leak a fake client into modules that call the real _get_client().
    """
    monkeypatch.setattr(storage, "_client", None)
    monkeypatch.setattr(storage, "_client_creds", None)

    calls = []

    def _fake(url, key, options=None):
        calls.append({"url": url, "key": key, "options": options})
        return MagicMock(name=f"client-{len(calls)}")

    monkeypatch.setattr(storage, "create_client", _fake)
    return calls


def _configure(monkeypatch, url="https://proj.supabase.co", service_key="service-key"):
    monkeypatch.setattr(storage.settings, "supabase_url", url)
    monkeypatch.setattr(storage.settings, "supabase_service_role_key", service_key)
    monkeypatch.setattr(storage.settings, "supabase_key", "anon-key")


def test_get_client_reuses_one_client_across_calls(monkeypatch, fake_create_client):
    _configure(monkeypatch)

    first = storage._get_client()
    second = storage._get_client()

    assert first is second
    assert len(fake_create_client) == 1, "client should be built once, then reused"


def test_get_client_rebuilds_when_credentials_change(monkeypatch, fake_create_client):
    _configure(monkeypatch)
    first = storage._get_client()

    monkeypatch.setattr(storage.settings, "supabase_service_role_key", "rotated-key")
    second = storage._get_client()

    assert first is not second, "changed creds must not return the stale client"
    assert len(fake_create_client) == 2
    assert fake_create_client[1]["key"] == "rotated-key"


def test_get_client_rebuilds_when_url_changes(monkeypatch, fake_create_client):
    _configure(monkeypatch)
    first = storage._get_client()

    monkeypatch.setattr(storage.settings, "supabase_url", "https://other.supabase.co")
    second = storage._get_client()

    assert first is not second
    assert fake_create_client[1]["url"] == "https://other.supabase.co"


@pytest.mark.parametrize(
    "url,service_key,anon_key",
    [
        ("", "service-key", "anon-key"),   # no url
        ("https://proj.supabase.co", "", ""),  # no key of either kind
    ],
)
def test_get_client_returns_none_when_unconfigured_and_does_not_cache(
    monkeypatch, fake_create_client, url, service_key, anon_key
):
    monkeypatch.setattr(storage.settings, "supabase_url", url)
    monkeypatch.setattr(storage.settings, "supabase_service_role_key", service_key)
    monkeypatch.setattr(storage.settings, "supabase_key", anon_key)

    assert storage._get_client() is None
    assert fake_create_client == [], "must not build a client when unconfigured"
    # The failure path must leave the cache empty, so a later configured call
    # builds a real client instead of returning a memoized None.
    assert storage._client is None
    assert storage._client_creds is None

    _configure(monkeypatch)
    assert storage._get_client() is not None


def test_get_client_falls_back_to_anon_key_and_caches_on_it(
    monkeypatch, fake_create_client
):
    monkeypatch.setattr(storage.settings, "supabase_url", "https://proj.supabase.co")
    monkeypatch.setattr(storage.settings, "supabase_service_role_key", "")
    monkeypatch.setattr(storage.settings, "supabase_key", "anon-key")

    first = storage._get_client()
    assert fake_create_client[0]["key"] == "anon-key"

    # A service-role key appearing later outranks the anon key, so the cache
    # keyed on the resolved key must rebuild rather than serve the anon client.
    monkeypatch.setattr(storage.settings, "supabase_service_role_key", "service-key")
    second = storage._get_client()

    assert first is not second
    assert fake_create_client[1]["key"] == "service-key"


def test_get_client_sets_explicit_postgrest_timeout(monkeypatch, fake_create_client):
    _configure(monkeypatch)
    storage._get_client()

    options = fake_create_client[0]["options"]
    assert options is not None, "must not inherit PostgREST's 120s default timeout"
    assert options.postgrest_client_timeout == storage.POSTGREST_TIMEOUT_SECONDS
    assert storage.POSTGREST_TIMEOUT_SECONDS == 15


# ------------------- practices list: column diet + pager --------------------
#
# Two things are under test here, and they pull in opposite directions: the
# list must stop shipping columns nobody renders, and it must keep shipping
# every column something does. The second is the one that bites — a dropped
# column doesn't fail, it renders as a blank card field for every lead.


def _top_level_columns(select: str) -> set[str]:
    """Column names a PostgREST select string asks for, embeds collapsed to
    their alias (so `x:t(a,b)` contributes `x`, not `a` / `b`)."""
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


class _FakePracticesQuery:
    """Enough of the PostgREST builder for query_practices_page. Filters and
    ordering are no-ops — these tests are about which columns are asked for,
    and how the returned window drives the total."""

    def __init__(self, rows, call, planned, raise_on_count):
        self._rows = rows
        self._call = call
        self._planned = planned
        self._raise_on_count = raise_on_count

    def or_(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def overlaps(self, *a, **k): return self
    def order(self, *a, **k): return self

    def range(self, start, end):
        self._call["range"] = (start, end)
        return self

    def limit(self, n):
        self._call["limit"] = n
        return self

    def execute(self):
        if self._call["count"]:
            if self._raise_on_count:
                raise RuntimeError("count blew up")
            return SimpleNamespace(data=[], count=self._planned)
        start, end = self._call["range"]
        return SimpleNamespace(data=self._rows[start:end + 1], count=None)


def _fake_practices_client(monkeypatch, rows, planned=None, raise_on_count=False):
    calls: list[dict] = []

    def _table(name):
        assert name == "practices"

        def _select(columns, count=None):
            call = {"columns": columns, "count": count, "range": None, "limit": None}
            calls.append(call)
            return _FakePracticesQuery(rows, call, planned, raise_on_count)

        return SimpleNamespace(select=_select)

    monkeypatch.setattr(storage, "_get_client", lambda: SimpleNamespace(table=_table))
    return calls


def _practice_rows(n: int) -> list[dict]:
    return [{"place_id": f"p{i}", "name": f"Practice {i}"} for i in range(n)]


def test_practices_list_select_drops_the_columns_the_list_never_renders():
    """The list page paints a card and a map pin. `select("*")` also shipped
    the call script, the email draft and the raw analysis inputs — hundreds of
    KB a page of text nothing on that screen reads."""
    selected = _top_level_columns(storage._PRACTICE_LIST_SELECT)
    for dropped in ("call_script", "email_draft", "email_draft_updated_at",
                    "notes", "call_notes", "website_contacts",
                    "analysis_input_hash", "opening_hours", "email",
                    "assigned_to", "assigned_by", "export_count"):
        assert dropped not in selected, f"{dropped} is back on the list payload"


def test_practices_list_select_keeps_every_field_the_card_and_map_render():
    """A column dropped here renders as an empty card field, not an error, so
    the audit of web/components/practice-card.tsx + map-view.tsx is pinned
    rather than left to be re-done by hand."""
    selected = _top_level_columns(storage._PRACTICE_LIST_SELECT)
    rendered = {
        # card header, address block, badges
        "place_id", "name", "address", "status", "lead_score", "tags",
        "rating", "review_count", "category", "phone", "website",
        # the expanded analysis panel — the card DOES render these
        "summary", "pain_points", "sales_angles", "icp_breakdown",
        # attribution + call / Salesforce line
        "last_touched_at", "call_count", "salesforce_synced_at",
        "salesforce_owner_name", "salesforce_lead_id", "salesforce_lead_url",
        # owner mini-card + enrich button
        "owner_name", "owner_email", "owner_phone", "owner_title",
        "owner_linkedin", "enrichment_status",
        # website-extracted doctor line
        "website_doctor_name", "website_doctor_phone",
        # map pins
        "lat", "lng",
    }
    assert rendered <= selected, f"list stopped fetching {sorted(rendered - selected)}"
    # last_touched_by_name is the flattened profile embed, not a column.
    assert "last_touched_by_profile" in selected


def test_detail_and_export_keep_the_full_select():
    """Only the list is on a diet. `get_practice` feeds the detail page and
    `query_for_export` feeds the CSV — both read the columns it drops."""
    import inspect

    assert storage.PROFILE_JOIN_SELECT.startswith("*")
    for fn in (storage.get_practice, storage.query_for_export):
        assert "PROFILE_JOIN_SELECT" in inspect.getsource(fn)


def test_practices_page_uses_the_list_select(monkeypatch):
    calls = _fake_practices_client(monkeypatch, _practice_rows(3))
    storage.query_practices_page(limit=10)
    assert calls[0]["columns"] == storage._PRACTICE_LIST_SELECT


def test_last_page_total_is_exact_and_costs_no_count_query(monkeypatch):
    """A page that runs off the end of the result set knows the true total
    from its own short read — so it skips the count round trip entirely."""
    calls = _fake_practices_client(monkeypatch, _practice_rows(3), planned=999_999)
    rows, total = storage.query_practices_page(limit=10)

    assert len(rows) == 3
    assert total == 3, "a short read is the exact total, not an estimate"
    assert len(calls) == 1, "no count query on the last page"


def test_full_page_pops_the_probe_row_and_reports_more_to_come(monkeypatch):
    """One row past the page is fetched to answer "is there another page?"
    from data. It must never reach the caller."""
    calls = _fake_practices_client(monkeypatch, _practice_rows(25), planned=25)
    rows, total = storage.query_practices_page(limit=10)

    assert calls[0]["range"] == (0, 10), "must fetch limit+1 rows"
    assert len(rows) == 10, "the probe row leaked into the page"
    assert rows[-1]["place_id"] == "p9"
    # api/index.py derives has_more from exactly this comparison.
    assert 0 + len(rows) < total


def test_the_count_query_is_planned_not_exact(monkeypatch):
    """`count="exact"` re-scanned 23k rows on every page of every list load,
    on a shared-CPU instance, to render one number."""
    calls = _fake_practices_client(monkeypatch, _practice_rows(25), planned=23_000)
    _, total = storage.query_practices_page(limit=10)

    count_calls = [c for c in calls if c["count"]]
    assert [c["count"] for c in count_calls] == ["planned"]
    assert count_calls[0]["columns"] == "place_id", "count must stay embed-free"
    assert total == 23_000


def test_paging_survives_an_estimate_that_understates_the_table(monkeypatch):
    """A planner estimate can be stale or plain wrong. When it undershoots,
    `has_more` must still be driven by the row we actually fetched, or the
    pager would stop dead on page 1 with rows left unseen."""
    _fake_practices_client(monkeypatch, _practice_rows(25), planned=4)
    rows, total = storage.query_practices_page(limit=10)

    assert len(rows) == 10
    assert total > len(rows), "an understated estimate would end paging early"


def test_a_failed_count_still_returns_a_usable_page(monkeypatch):
    """The count query has always been allowed to fail without taking the
    page down with it."""
    _fake_practices_client(monkeypatch, _practice_rows(25), raise_on_count=True)
    rows, total = storage.query_practices_page(limit=10)

    assert len(rows) == 10
    assert total > len(rows), "has_more must survive a count that never answered"


def test_a_middle_page_offsets_the_probe_and_the_total(monkeypatch):
    calls = _fake_practices_client(monkeypatch, _practice_rows(25), planned=25)
    rows, total = storage.query_practices_page(offset=20, limit=10)

    assert calls[0]["range"] == (20, 30)
    assert len(rows) == 5
    assert total == 25, "the tail page still resolves to the exact total"
    assert 20 + len(rows) == total, "has_more is False on the last page"
