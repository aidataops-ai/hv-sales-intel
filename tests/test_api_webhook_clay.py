import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.index import app


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def capture_file(tmp_path, monkeypatch):
    """Redirect the raw-body capture at a temp file.

    Every authenticated callback appends to `clay-webhook-captures.jsonl` at
    the repo root; a test run would otherwise bury the live Clay payloads that
    file exists to collect. Yielded so a test can read back what was captured
    — the real write path runs, only the destination moves."""
    from api import index

    path = tmp_path / "clay-webhook-captures.jsonl"
    monkeypatch.setattr(index, "_CLAY_CAPTURE_PATH", path)
    return path


def _captured_bodies(path):
    if not path.exists():
        return []
    return [json.loads(line)["body"]
            for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_webhook_rejects_missing_secret():
    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        client = TestClient(app)
        resp = client.post(
            "/api/webhooks/clay",
            json={"place_id": "abc", "owner_name": "Jane"},
        )
    assert resp.status_code == 401


def test_webhook_rejects_wrong_secret():
    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        client = TestClient(app)
        resp = client.post(
            "/api/webhooks/clay",
            json={"place_id": "abc", "owner_name": "Jane"},
            headers={"X-Clay-Secret": "wrong"},
        )
    assert resp.status_code == 401


def test_webhook_returns_404_when_practice_missing():
    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=None):
            client = TestClient(app)
            resp = client.post(
                "/api/webhooks/clay",
                json={"place_id": "missing", "owner_name": "Jane"},
                headers={"X-Clay-Secret": "shhh"},
            )
    assert resp.status_code == 404


def test_webhook_happy_path_writes_owner_fields_and_sets_enriched():
    existing = {"place_id": "abc", "name": "Test"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        captured["_place_id"] = place_id
        return {**existing, **fields}

    payload = {
        "place_id": "abc",
        "owner_name": "Jane Smith",
        "owner_title": "Practice Manager",
        "owner_email": "jane@hfd.com",
        "owner_phone": "+17135559999",
        "owner_linkedin": "https://linkedin.com/in/janesmith",
    }

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json=payload,
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert captured["owner_name"] == "Jane Smith"
    assert captured["owner_title"] == "Practice Manager"
    assert captured["owner_email"] == "jane@hfd.com"
    assert captured["owner_phone"] == "+17135559999"
    assert captured["owner_linkedin"] == "https://linkedin.com/in/janesmith"
    assert captured["enrichment_status"] == "enriched"
    assert "enriched_at" in captured


def test_webhook_maps_and_coerces_organization_size():
    """Clay's org-size (int or messy string) is coerced to an int and written."""
    from api.index import _coerce_org_size

    assert _coerce_org_size("1,200") == 1200
    assert _coerce_org_size("50-100 employees") == 50
    assert _coerce_org_size(42) == 42
    assert _coerce_org_size("N/A") is None
    assert _coerce_org_size(0) is None

    existing = {"place_id": "abc", "name": "Test"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json={"place_id": "abc", "owner_name": "Jane",
                          "organization_size": "1,200"},
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert captured["organization_size"] == 1200


def test_webhook_omits_organization_size_when_unparseable():
    existing = {"place_id": "abc", "name": "Test"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json={"place_id": "abc", "owner_name": "Jane",
                          "organization_size": "unknown"},
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert "organization_size" not in captured


def test_webhook_flips_to_failed_when_no_owner_fields():
    existing = {"place_id": "abc", "name": "Test"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json={"place_id": "abc"},
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert captured["enrichment_status"] == "failed"
    assert "owner_name" not in captured


def test_webhook_partial_payload_only_writes_present_fields():
    existing = {"place_id": "abc", "name": "Test", "owner_phone": "+17130000000"}
    captured = {}

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing):
            with patch("api.index.update_practice_fields", side_effect=fake_update):
                client = TestClient(app)
                resp = client.post(
                    "/api/webhooks/clay",
                    json={"place_id": "abc", "owner_name": "Jane"},
                    headers={"X-Clay-Secret": "shhh"},
                )

    assert resp.status_code == 200
    assert captured["owner_name"] == "Jane"
    assert "owner_phone" not in captured
    assert captured["enrichment_status"] == "enriched"


# ---------------------------------------------------------------------------
# Per-contact shape — Clay now POSTs once per person to the same webhook.
#
# The contact rows live in `practice_contacts` (src/contacts.py); the primary
# one is mirrored back onto `practices.owner_*` so nothing downstream had to
# change. Only the two database calls are faked below — the pure helpers
# (clean/pick_primary/should_mirror/owner_mirror_fields) run for real, because
# what they decide *is* what this endpoint is being tested for.
# ---------------------------------------------------------------------------


def _fake_contact_store(saved=None, listed=None):
    from src import contacts as real_contacts

    store = MagicMock()
    store.clean_contact.side_effect = real_contacts.clean_contact
    store.pick_primary.side_effect = real_contacts.pick_primary
    store.should_mirror.side_effect = real_contacts.should_mirror
    store.owner_mirror_fields.side_effect = real_contacts.owner_mirror_fields
    store.upsert_contact.return_value = saved
    store.list_contacts_for_practice.return_value = listed if listed is not None else []
    return store


def _post_contact(payload, existing, store, secret="shhh"):
    """POST one per-person callback; return (response, captured fields, tags)."""
    captured: dict = {}
    tagged: list = []

    def fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        captured["_place_id"] = place_id
        return {**existing, **fields}

    with patch("api.index.app_settings") as s:
        s.clay_inbound_secret = "shhh"
        with patch("api.index.get_practice", return_value=existing or None), \
             patch("api.index.update_practice_fields", side_effect=fake_update), \
             patch("api.index.add_tags", side_effect=lambda pid, tags: tagged.append((pid, tags))), \
             patch("api.index.contact_store", store):
            client = TestClient(app)
            resp = client.post(
                "/api/webhooks/clay",
                json=payload,
                headers={"X-Clay-Secret": secret},
            )
    return resp, captured, tagged


def test_is_new_shape_detects_any_per_person_field():
    from api.index import ClayWebhookPayload, _is_new_shape

    assert _is_new_shape(ClayWebhookPayload(place_id="abc", title="Office Manager"))
    assert _is_new_shape(ClayWebhookPayload(place_id="abc", first_name="Jane"))
    assert _is_new_shape(ClayWebhookPayload(place_id="abc", url="linkedin.com/in/x"))
    assert not _is_new_shape(ClayWebhookPayload(place_id="abc", owner_name="Jane"))
    assert not _is_new_shape(ClayWebhookPayload(place_id="abc"))
    # Whitespace is not a field.
    assert not _is_new_shape(ClayWebhookPayload(place_id="abc", first_name="   "))


def test_webhook_new_shape_stores_contact_and_mirrors_owner_columns():
    existing = {"place_id": "abc", "id": 7, "name": "Test"}
    stored = {
        "id": 1, "practice_id": 7,
        "first_name": "Jane", "last_name": "Smith", "title": "Office Manager",
        "linkedin_url": "https://linkedin.com/in/janesmith",
        "work_email": "jane@hfd.com", "personal_email": "jane@gmail.com",
        "phone": "+1 727 555 0134",
    }
    store = _fake_contact_store(saved=stored, listed=[stored])

    resp, captured, tagged = _post_contact(
        {
            "place_id": "abc",
            "first_name": "Jane",
            "last_name": "Smith",
            "title": "Office Manager",
            "url": "https://linkedin.com/in/janesmith",
            "work_email": "jane@hfd.com",
            "personal_email": "jane@gmail.com",
            "phone": "+1 727 555 0134",
        },
        existing,
        store,
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Stored against the practice's numeric id, with Clay's `url` mapped onto
    # the table's `linkedin_url` and no stray `place_id` in the row.
    store.upsert_contact.assert_called_once()
    practice_id, contact = store.upsert_contact.call_args.args
    assert practice_id == 7
    assert contact == {
        "first_name": "Jane", "last_name": "Smith", "title": "Office Manager",
        "linkedin_url": "https://linkedin.com/in/janesmith",
        "work_email": "jane@hfd.com", "personal_email": "jane@gmail.com",
        "phone": "+1 727 555 0134",
    }

    # Mirrored onto the legacy columns every other consumer still reads.
    assert captured["owner_name"] == "Jane Smith"
    assert captured["owner_title"] == "Office Manager"
    assert captured["owner_linkedin"] == "https://linkedin.com/in/janesmith"
    assert captured["owner_email"] == "jane@hfd.com"  # work beats personal
    assert captured["owner_phone"] == "+1 727 555 0134"  # direct dial mirrors too
    assert captured["enrichment_status"] == "enriched"
    assert "enriched_at" in captured
    assert tagged == [("abc", ["ENRICHED"])]


def test_webhook_new_shape_mirrors_primary_across_all_contacts():
    """The mirror reflects `pick_primary` over every contact on the practice,
    not just the person in this callback."""
    existing = {"place_id": "abc", "id": 7, "name": "Test"}
    first = {"id": 1, "practice_id": 7, "first_name": "Ann", "last_name": "Owner",
             "title": "Owner", "work_email": "ann@hfd.com", "personal_email": None,
             "linkedin_url": None}
    second = {"id": 2, "practice_id": 7, "first_name": "Bob", "last_name": "Newguy",
              "title": "Coordinator", "work_email": None,
              "personal_email": "bob@gmail.com", "linkedin_url": None}
    store = _fake_contact_store(saved=second, listed=[first, second])

    resp, captured, _ = _post_contact(
        {"place_id": "abc", "first_name": "Bob", "last_name": "Newguy",
         "title": "Coordinator", "personal_email": "bob@gmail.com"},
        existing,
        store,
    )

    assert resp.status_code == 200
    assert captured["owner_name"] == "Ann Owner"
    assert captured["owner_email"] == "ann@hfd.com"


def test_webhook_hybrid_payload_takes_the_contact_branch():
    """owner_* alongside first_name: the per-person branch wins, and the old
    branch's straight-through writes (owner_phone above all) never happen."""
    existing = {"place_id": "abc", "id": 7, "name": "Test"}
    stored = {"id": 1, "practice_id": 7, "first_name": "Jane", "last_name": "Smith",
              "title": None, "linkedin_url": None,
              "work_email": "jane@hfd.com", "personal_email": None}
    store = _fake_contact_store(saved=stored, listed=[stored])

    resp, captured, _ = _post_contact(
        {
            "place_id": "abc",
            "owner_name": "Legacy Owner",
            "owner_phone": "+17135559999",
            "first_name": "Jane",
            "last_name": "Smith",
            "work_email": "jane@hfd.com",
        },
        existing,
        store,
    )

    assert resp.status_code == 200
    store.upsert_contact.assert_called_once()
    assert captured["owner_name"] == "Jane Smith"  # not "Legacy Owner"
    assert "owner_phone" not in captured
    assert captured["enrichment_status"] == "enriched"


def test_webhook_old_shape_never_touches_the_contacts_table():
    existing = {"place_id": "abc", "name": "Test"}
    store = _fake_contact_store()

    resp, captured, _ = _post_contact(
        {"place_id": "abc", "owner_name": "Jane", "owner_phone": "+17135559999"},
        existing,
        store,
    )

    assert resp.status_code == 200
    store.upsert_contact.assert_not_called()
    store.list_contacts_for_practice.assert_not_called()
    assert captured["owner_name"] == "Jane"
    assert captured["owner_phone"] == "+17135559999"
    assert captured["enrichment_status"] == "enriched"


def test_webhook_personal_only_contact_does_not_clobber_real_owner_email():
    existing = {"place_id": "abc", "id": 7, "name": "Test",
                "owner_name": "Ann Owner", "owner_email": "ann@hfd.com"}
    stored = {"id": 1, "practice_id": 7, "first_name": "Bob", "last_name": "Newguy",
              "title": "Coordinator", "linkedin_url": None,
              "work_email": None, "personal_email": "bob@gmail.com"}
    store = _fake_contact_store(saved=stored, listed=[stored])

    resp, captured, _ = _post_contact(
        {"place_id": "abc", "first_name": "Bob", "last_name": "Newguy",
         "title": "Coordinator", "personal_email": "bob@gmail.com"},
        existing,
        store,
    )

    assert resp.status_code == 200
    # Stored as a contact, but no owner_* key is written — a gmail address must
    # not overwrite a verified work address.
    store.upsert_contact.assert_called_once()
    assert not [k for k in captured if k.startswith("owner_")]
    assert captured["enrichment_status"] == "enriched"


def test_webhook_new_shape_falls_back_to_the_payload_when_the_table_is_missing():
    """Unapplied migration: upsert returns None and the list is empty, but the
    practice still gets its owner_* columns off the payload in hand."""
    existing = {"place_id": "abc", "id": 7, "name": "Test"}
    store = _fake_contact_store(saved=None, listed=[])

    resp, captured, tagged = _post_contact(
        {"place_id": "abc", "first_name": "Jane", "last_name": "Smith",
         "work_email": "jane@hfd.com"},
        existing,
        store,
    )

    assert resp.status_code == 200
    assert captured["owner_name"] == "Jane Smith"
    assert captured["owner_email"] == "jane@hfd.com"
    assert captured["enrichment_status"] == "enriched"
    assert tagged == [("abc", ["ENRICHED"])]


def test_webhook_contactless_callback_does_not_downgrade_an_enriched_practice():
    """A title with nobody attached is new-shape but carries no contact. On an
    already-enriched practice the status must stay "enriched"."""
    existing = {"place_id": "abc", "id": 7, "name": "Test",
                "enrichment_status": "enriched"}
    store = _fake_contact_store(saved=None, listed=[])

    resp, captured, tagged = _post_contact(
        {"place_id": "abc", "title": "Office Manager"}, existing, store,
    )

    assert resp.status_code == 200
    store.upsert_contact.assert_not_called()
    assert captured["enrichment_status"] == "enriched"
    assert tagged == [("abc", ["ENRICHED"])]


def test_webhook_contactless_callback_fails_a_never_enriched_practice():
    existing = {"place_id": "abc", "id": 7, "name": "Test",
                "enrichment_status": "pending"}
    store = _fake_contact_store(saved=None, listed=[])

    resp, captured, tagged = _post_contact(
        {"place_id": "abc", "title": "Office Manager"}, existing, store,
    )

    assert resp.status_code == 200
    assert captured["enrichment_status"] == "failed"
    assert tagged == []


def test_webhook_new_shape_coerces_organization_size():
    existing = {"place_id": "abc", "id": 7, "name": "Test"}
    stored = {"id": 1, "practice_id": 7, "first_name": "Jane", "last_name": None,
              "title": None, "linkedin_url": None,
              "work_email": "jane@hfd.com", "personal_email": None}
    store = _fake_contact_store(saved=stored, listed=[stored])

    resp, captured, _ = _post_contact(
        {"place_id": "abc", "first_name": "Jane", "work_email": "jane@hfd.com",
         "organization_size": "50-100 employees"},
        existing,
        store,
    )

    assert resp.status_code == 200
    assert captured["organization_size"] == 50


def test_webhook_new_shape_returns_404_when_practice_missing():
    store = _fake_contact_store()
    resp, captured, _ = _post_contact(
        {"place_id": "missing", "first_name": "Jane"}, None, store,
    )
    assert resp.status_code == 404
    store.upsert_contact.assert_not_called()
    assert captured == {}


def test_webhook_new_shape_rejects_wrong_secret():
    store = _fake_contact_store()
    resp, _, _ = _post_contact(
        {"place_id": "abc", "first_name": "Jane"},
        {"place_id": "abc", "id": 7},
        store,
        secret="wrong",
    )
    assert resp.status_code == 401
    store.upsert_contact.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/practices/{place_id} — the one view that shows every contact.
# Lives here because it is the read side of the same feature.
# ---------------------------------------------------------------------------


def test_practice_detail_returns_contacts_list():
    from src.auth import get_current_user

    profile = {"id": "u-1", "email": "sdr@example.com", "role": "sdr",
               "company_id": "co-1"}
    app.dependency_overrides[get_current_user] = lambda: profile

    row = {"place_id": "abc", "id": 7, "name": "Test"}
    contacts = [{"id": 1, "practice_id": 7, "first_name": "Jane",
                 "last_name": "Smith", "work_email": "jane@hfd.com"}]
    store = _fake_contact_store(listed=contacts)

    with patch("api.index.get_practice", return_value=row), \
         patch("api.index._practice_exported", return_value=False), \
         patch("api.index.contact_store", store):
        client = TestClient(app)
        resp = client.get("/api/practices/abc")

    assert resp.status_code == 200
    assert resp.json()["contacts"] == contacts
    store.list_contacts_for_practice.assert_called_once_with(7)


def test_practice_detail_contacts_defaults_to_empty_list():
    from src.auth import get_current_user

    profile = {"id": "u-1", "email": "sdr@example.com", "role": "sdr",
               "company_id": "co-1"}
    app.dependency_overrides[get_current_user] = lambda: profile

    row = {"place_id": "abc", "name": "Test"}  # no numeric id yet
    store = _fake_contact_store()

    with patch("api.index.get_practice", return_value=row), \
         patch("api.index._practice_exported", return_value=False), \
         patch("api.index.contact_store", store):
        client = TestClient(app)
        resp = client.get("/api/practices/abc")

    assert resp.status_code == 200
    assert resp.json()["contacts"] == []
    store.list_contacts_for_practice.assert_not_called()


# ---------------------------------------------------------------------------
# Raw-body capture — Clay's real column names are only knowable from what it
# actually posts, so the endpoint takes a dict, logs it and appends it to
# `clay-webhook-captures.jsonl` before any of it is validated away.
# ---------------------------------------------------------------------------


def test_webhook_accepts_unknown_clay_field_names(capture_file):
    """A body full of names our model has never heard of must not 500 — the
    point of the exercise is to see those names in the capture."""
    existing = {"place_id": "abc", "id": 7, "name": "Test"}
    raw = {"place_id": "abc", "First Name": "Jane", "Work Email": "jane@hfd.com",
           "bogus": 1}

    resp, captured, tagged = _post_contact(raw, existing, _fake_contact_store())

    assert resp.status_code == 200
    # Nothing the model recognizes -> old shape, and no contact found.
    assert captured["enrichment_status"] == "failed"
    assert tagged == []
    # The unknown keys survive into the capture, which is what we are after.
    assert _captured_bodies(capture_file) == [raw]


def test_webhook_returns_422_when_place_id_missing(capture_file):
    resp, captured, _ = _post_contact(
        {"first_name": "Jane"}, {"place_id": "abc", "id": 7}, _fake_contact_store(),
    )
    assert resp.status_code == 422
    assert "Invalid payload" in resp.json()["detail"]
    assert captured == {}
    # Captured anyway — a body we cannot parse is the most interesting one.
    assert _captured_bodies(capture_file) == [{"first_name": "Jane"}]


def test_webhook_never_captures_an_unauthenticated_body(capture_file):
    resp, _, _ = _post_contact(
        {"place_id": "abc", "first_name": "Jane"},
        {"place_id": "abc", "id": 7},
        _fake_contact_store(),
        secret="wrong",
    )
    assert resp.status_code == 401
    assert _captured_bodies(capture_file) == []


def test_capture_appends_one_jsonl_line_per_callback(capture_file):
    from api import index

    index._capture_clay_body({"place_id": "abc", "First Name": "Jane"})
    index._capture_clay_body({"place_id": "def"})

    lines = capture_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["body"] == {"place_id": "abc", "First Name": "Jane"}
    assert first["ts"].startswith("20")
    assert json.loads(lines[1])["body"] == {"place_id": "def"}


def test_capture_swallows_write_errors(tmp_path, monkeypatch):
    """An unwritable capture path must never cost us an enrichment."""
    from api import index

    monkeypatch.setattr(index, "_CLAY_CAPTURE_PATH", tmp_path)  # a directory
    index._capture_clay_body({"place_id": "abc"})  # does not raise
