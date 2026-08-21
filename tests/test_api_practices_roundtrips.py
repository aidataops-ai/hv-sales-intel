"""Round-trip counts on the practices routes (data-layer refactor, Wave 3).

Every one of these pins a number, not a behaviour: how many times a request
goes to Supabase. They are the kind of regression nothing else catches — the
responses stay byte-identical while the page gets slower and the egress bill
goes up — so each test asserts on recorded calls rather than on output.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import get_current_user
from src.models import Practice

client = TestClient(app)


def _override_user(company_id: str = "co-1"):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1", "email": "sdr@example.com", "name": "Test SDR",
        "role": "sdr", "company_id": company_id,
    }


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def _practice(place_id: str, **extra) -> Practice:
    fields = {"name": f"Practice {place_id}", **extra}
    return Practice(place_id=place_id, **fields)


# --------------------------------------------------------------------------
# POST /api/practices/search — one batched read, not one per result.
# --------------------------------------------------------------------------


def test_search_reads_every_result_in_one_batched_query():
    """Was a `get_practice` per Places result. A page is up to 20 rows and
    Bulk Scan runs this ~60 times a sweep, so the loop cost ~1,200 selects —
    each its own TLS handshake — per scan."""
    _override_user()
    found = [_practice("p1"), _practice("p2"), _practice("p3")]
    batched: list[list[str]] = []

    async def fake_search(*a, **k):
        return found

    def fake_batch(place_ids):
        batched.append(list(place_ids))
        return {p: {"place_id": p, "name": f"Practice {p}"} for p in place_ids}

    def boom(place_id):
        raise AssertionError("per-result get_practice is back")

    with patch("api.index.get_cached_search", return_value=None), \
         patch("api.index.get_balance", return_value=1000), \
         patch("api.index.search_places", new=fake_search), \
         patch("api.index.find_duplicate_place_ids", return_value={}), \
         patch("api.index.upsert_practices", return_value=[{"id": 1}]) as upsert, \
         patch("api.index.get_practices_by_place_ids", side_effect=fake_batch), \
         patch("api.index.get_practice", side_effect=boom), \
         patch("api.index.save_search_cache"):
        resp = client.post("/api/practices/search", json={"query": "dentist miami"})

    assert resp.status_code == 200
    assert batched == [["p1", "p2", "p3"]], "one call carrying every place_id"
    assert upsert.call_count == 1
    assert len(resp.json()["practices"]) == 3


def test_search_still_reports_upserted_as_a_count():
    """`upsert_practices` returns rows now; the response field is still the
    count the UI shows."""
    _override_user()

    async def fake_search(*a, **k):
        return [_practice("p1"), _practice("p2")]

    with patch("api.index.get_cached_search", return_value=None), \
         patch("api.index.get_balance", return_value=1000), \
         patch("api.index.search_places", new=fake_search), \
         patch("api.index.find_duplicate_place_ids", return_value={}), \
         patch("api.index.upsert_practices",
               return_value=[{"id": 1, "place_id": "p1"}, {"id": 2, "place_id": "p2"}]), \
         patch("api.index.get_practices_by_place_ids", return_value={}), \
         patch("api.index.save_search_cache"):
        body = client.post("/api/practices/search", json={"query": "q"}).json()

    assert body["upserted"] == 2


def test_search_falls_back_to_the_in_memory_row_when_the_batch_misses():
    """A place the batched read didn't return still renders — it just carries
    the Places payload instead of the stored row."""
    _override_user()

    async def fake_search(*a, **k):
        return [_practice("p1")]

    with patch("api.index.get_cached_search", return_value=None), \
         patch("api.index.get_balance", return_value=1000), \
         patch("api.index.search_places", new=fake_search), \
         patch("api.index.find_duplicate_place_ids", return_value={}), \
         patch("api.index.upsert_practices", return_value=[]), \
         patch("api.index.get_practices_by_place_ids", return_value={}), \
         patch("api.index.save_search_cache"):
        body = client.post("/api/practices/search", json={"query": "q"}).json()

    assert body["practices"][0]["place_id"] == "p1"


# --------------------------------------------------------------------------
# _practice_exported — one query on every practice-detail load.
# --------------------------------------------------------------------------


def test_practice_exported_asks_once(monkeypatch):
    from api.index import _practice_exported
    from src import lead_store

    calls: list[tuple] = []

    def fake(company_id, practice_id):
        calls.append((company_id, practice_id))
        return {"posting_id": 42, "lead": {"id": 9,
                                           "talentdb_exported_at": "2026-08-01T00:00:00Z"}}

    monkeypatch.setattr(lead_store, "newest_lead_for_practice", fake)
    assert _practice_exported("co-1", 7) is True
    assert calls == [("co-1", 7)]


def test_practice_exported_is_false_when_the_newest_posting_has_no_lead(monkeypatch):
    """Postings are shared across tenants; a posting this tenant never
    qualified has nothing to dedup against, so the practice stays sendable."""
    from api.index import _practice_exported
    from src import lead_store

    monkeypatch.setattr(lead_store, "newest_lead_for_practice",
                        lambda c, p: {"posting_id": 42, "lead": None})
    assert _practice_exported("co-1", 7) is False


def test_practice_exported_is_false_for_an_unlinked_practice(monkeypatch):
    from api.index import _practice_exported
    from src import lead_store

    monkeypatch.setattr(lead_store, "newest_lead_for_practice", lambda c, p: None)
    assert _practice_exported("co-1", 7) is False


def test_practice_exported_skips_the_query_entirely_without_an_id(monkeypatch):
    from api.index import _practice_exported
    from src import lead_store

    def boom(*a, **k):
        raise AssertionError("queried without a practice id")

    monkeypatch.setattr(lead_store, "newest_lead_for_practice", boom)
    assert _practice_exported("co-1", None) is False


def test_practice_exported_reads_the_marker_off_the_lead(monkeypatch):
    """An un-exported lead reports False — the marker is the dedup key."""
    from api.index import _practice_exported
    from src import lead_store

    monkeypatch.setattr(lead_store, "newest_lead_for_practice",
                        lambda c, p: {"posting_id": 42,
                                      "lead": {"id": 9, "talentdb_exported_at": None}})
    assert _practice_exported("co-1", 7) is False


# --------------------------------------------------------------------------
# POST /api/practices/{id}/import-lead — the full posting only when sending.
# --------------------------------------------------------------------------


def test_import_lead_skips_the_posting_fetch_when_already_exported(monkeypatch):
    """The dedup answer comes off the single helper, so an already-exported
    practice never reads `job_postings` at all."""
    from src import lead_store

    _override_user()

    def boom(posting_id):
        raise AssertionError("fetched the posting for a deduped export")

    monkeypatch.setattr(lead_store, "get_posting", boom)
    monkeypatch.setattr(
        lead_store, "newest_lead_for_practice",
        lambda c, p: {"posting_id": 42,
                      "lead": {"id": 9, "talentdb_exported_at": "2026-08-01T00:00:00Z"}},
    )

    with patch("api.index.get_practice", return_value={"id": 7, "place_id": "p1"}):
        body = client.post("/api/practices/p1/import-lead").json()

    assert body["talentdb_status"] == "already_exported"


def test_import_lead_fetches_the_full_posting_only_when_it_sends(monkeypatch):
    """The export is the one path that needs `description`, so that is the
    only path that pays for it."""
    from src import lead_store

    _override_user()
    fetched: list[int] = []

    def fake_get_posting(posting_id):
        fetched.append(posting_id)
        return {"id": posting_id, "description": "full body", "source": "indeed"}

    sent: dict = {}

    async def fake_import(practice, posting, lead, contact=None, td_lead_id=None):
        sent["posting"] = posting
        sent["lead"] = lead
        return {"ok": True, "status": "created"}

    # One eligible contact so the fan-out sends — legacy owner_* singles are
    # retired (2026-08-22), a contact-less practice would send nothing.
    monkeypatch.setattr(
        "src.talentdb_push.contacts.list_contacts_for_practice",
        lambda pid: [{"id": 1, "first_name": "Pat", "last_name": "Lee",
                      "work_email": "pat@acme.com", "personal_email": None,
                      "phone": "+13125550001"}])
    monkeypatch.setattr("src.talentdb_push.contacts.list_contact_exports",
                        lambda lid: {})
    monkeypatch.setattr("src.talentdb_push.contacts.mark_contact_exported",
                        lambda lid, cid, td_lead_id=None: None)
    monkeypatch.setattr(lead_store, "get_posting", fake_get_posting)
    monkeypatch.setattr(
        lead_store, "newest_lead_for_practice",
        lambda c, p: {"posting_id": 42,
                      "lead": {"id": 9, "talentdb_exported_at": None,
                               "provider_count": 6}},
    )
    monkeypatch.setattr(lead_store, "mark_lead_exported", lambda c, i: None)
    monkeypatch.setattr("src.talentdb.import_lead", fake_import)

    with patch("api.index.get_practice", return_value={"id": 7, "place_id": "p1"}):
        resp = client.post("/api/practices/p1/import-lead")

    assert resp.status_code == 200
    assert fetched == [42], "exactly one posting fetch, on the send path"
    assert sent["posting"]["description"] == "full body"
    assert sent["lead"]["provider_count"] == 6


def test_import_lead_sends_a_practice_with_no_linked_posting(monkeypatch):
    """Nothing to dedup on — always sendable, and no posting read."""
    from src import lead_store

    _override_user()

    def boom(posting_id):
        raise AssertionError("fetched a posting that does not exist")

    sent: dict = {}

    async def fake_import(practice, posting, lead, contact=None, td_lead_id=None):
        sent["posting"] = posting
        return {"ok": True, "status": "created"}

    # One eligible contact so the fan-out sends — legacy owner_* singles are
    # retired (2026-08-22), a contact-less practice would send nothing.
    monkeypatch.setattr(
        "src.talentdb_push.contacts.list_contacts_for_practice",
        lambda pid: [{"id": 1, "first_name": "Pat", "last_name": "Lee",
                      "work_email": "pat@acme.com", "personal_email": None,
                      "phone": "+13125550001"}])
    monkeypatch.setattr(lead_store, "get_posting", boom)
    monkeypatch.setattr(lead_store, "newest_lead_for_practice", lambda c, p: None)
    monkeypatch.setattr("src.talentdb.import_lead", fake_import)

    with patch("api.index.get_practice", return_value={"id": 7, "place_id": "p1"}):
        resp = client.post("/api/practices/p1/import-lead")

    assert resp.status_code == 200
    assert sent["posting"] is None


# --------------------------------------------------------------------------
# POST /api/practices/{id}/email/poll — one bulk insert, one thread read.
# --------------------------------------------------------------------------


def test_email_poll_inserts_every_reply_in_one_call(monkeypatch):
    """Was an insert per reply (two, counting the per-company mirror) plus a
    second full read of the thread just to count it."""
    _override_user()
    reads: list[int] = []
    inserts: list[list[dict]] = []

    async def fake_poll(**kwargs):
        return [
            {"message_id": "<a>", "subject": "s1", "body": "b1", "in_reply_to": None},
            {"message_id": "<b>", "subject": "s2", "body": "b2", "in_reply_to": None},
        ]

    def fake_list(practice_id):
        reads.append(practice_id)
        return [{"id": 1, "message_id": "<old>"}]

    def fake_insert(practice_id, user_id, direction, messages, company_id=None):
        inserts.append(messages)
        return [{"id": 10 + i, "direction": direction} for i, _ in enumerate(messages)]

    with patch("api.index._email_configured", return_value=True), \
         patch("api.index.get_practice",
               return_value={"id": 7, "place_id": "p1", "email": "x@y.com",
                             "status": "CONTACTED"}), \
         patch("api.index.list_outbound_message_ids", return_value=["<out>"]), \
         patch("api.index.poll_replies", new=fake_poll), \
         patch("api.index.list_email_messages", side_effect=fake_list), \
         patch("api.index.insert_email_messages", side_effect=fake_insert), \
         patch("api.index.update_practice_fields"), \
         patch("api.index.add_tags"):
        body = client.post("/api/practices/p1/email/poll").json()

    assert len(inserts) == 1, "one insert call for the whole batch"
    assert [m["message_id"] for m in inserts[0]] == ["<a>", "<b>"]
    assert len(reads) == 1, "the thread is read once, not again to count it"
    # total = what we read + what we just wrote, no extra query.
    assert body["total"] == 3
    assert len(body["new_messages"]) == 2


def test_email_poll_skips_replies_it_already_stored(monkeypatch):
    _override_user()
    inserts: list[list[dict]] = []

    async def fake_poll(**kwargs):
        return [{"message_id": "<old>", "subject": "s", "body": "b",
                 "in_reply_to": None}]

    def fake_insert(practice_id, user_id, direction, messages, company_id=None):
        inserts.append(messages)
        return []

    with patch("api.index._email_configured", return_value=True), \
         patch("api.index.get_practice",
               return_value={"id": 7, "place_id": "p1", "email": "x@y.com",
                             "status": "CONTACTED"}), \
         patch("api.index.list_outbound_message_ids", return_value=[]), \
         patch("api.index.poll_replies", new=fake_poll), \
         patch("api.index.list_email_messages",
               return_value=[{"id": 1, "message_id": "<old>"}]), \
         patch("api.index.insert_email_messages", side_effect=fake_insert), \
         patch("api.index.update_practice_fields"), \
         patch("api.index.add_tags"):
        body = client.post("/api/practices/p1/email/poll").json()

    assert inserts == [], "nothing new — no write at all"
    assert body["new_messages"] == []
    assert body["total"] == 1


# --------------------------------------------------------------------------
# Upsert-then-refetch.
# --------------------------------------------------------------------------


def test_rescan_answers_from_the_upsert_instead_of_re_reading(monkeypatch):
    """PostgREST returns the stored row from the write, so the `get_practice`
    that used to follow it was re-reading a row already in hand."""
    _override_user()
    stored = {"id": 7, "place_id": "p1", "name": "Fresh Name", "rating": 4.9}

    async def fake_get_place(place_id, **kwargs):
        return _practice("p1", name="Fresh Name")

    reads: list[str] = []

    def counting_get_practice(place_id):
        reads.append(place_id)
        return {"place_id": "p1", "name": "Stale Name"}

    with patch("api.index.get_practice", side_effect=counting_get_practice), \
         patch("api.index.get_place", new=fake_get_place), \
         patch("api.index.upsert_practices", return_value=[dict(stored)]):
        body = client.post("/api/practices/p1/rescan").json()

    assert reads == ["p1"], "only the pre-flight read, not a post-write one"
    assert body["name"] == "Fresh Name"


def test_rescan_stamps_the_actor_as_the_last_toucher(monkeypatch):
    """`last_touched_by_name` is a read-time join, so a written row never
    carries it — but the write just stamped this user, so the name the join
    would resolve is the one already in hand. The detail page renders it."""
    _override_user()

    async def fake_get_place(place_id, **kwargs):
        return _practice("p1")

    with patch("api.index.get_practice",
               return_value={"place_id": "p1", "name": "X"}), \
         patch("api.index.get_place", new=fake_get_place), \
         patch("api.index.upsert_practices",
               return_value=[{"id": 7, "place_id": "p1", "name": "X",
                              "last_touched_by": "u1",
                              "last_touched_by_name": None}]):
        body = client.post("/api/practices/p1/rescan").json()

    assert body["last_touched_by_name"] == "Test SDR"


def test_rescan_falls_back_to_the_places_payload_when_the_write_returns_nothing():
    _override_user()

    async def fake_get_place(place_id, **kwargs):
        return _practice("p1", name="From Places")

    with patch("api.index.get_practice",
               return_value={"place_id": "p1", "name": "X"}), \
         patch("api.index.get_place", new=fake_get_place), \
         patch("api.index.upsert_practices", return_value=[]):
        body = client.post("/api/practices/p1/rescan").json()

    assert body["name"] == "From Places"
