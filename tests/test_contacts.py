from unittest.mock import MagicMock, patch

import pytest

from src import contacts
from src.contacts import (
    clean_contact,
    contact_dedupe_key,
    contact_email,
    list_contact_exports,
    list_contacts_for_practice,
    list_exported_contact_ids,
    mark_contact_exported,
    owner_mirror_fields,
    pick_primary,
    should_mirror,
    upsert_contact,
)


def _mock_client(rows=None, raises=None):
    """A supabase-shaped stub: every builder call returns the same table mock,
    so any chain of .select/.eq/.order/.range/.upsert ends at .execute()."""
    client = MagicMock()
    table = MagicMock()
    for method in ("select", "eq", "order", "range", "upsert", "insert", "update"):
        getattr(table, method).return_value = table
    if raises is not None:
        table.execute.side_effect = raises
    else:
        table.execute.return_value = MagicMock(data=rows if rows is not None else [])
    client.table.return_value = table
    return client, table


# ---------------------------------------------------------------------------
# clean_contact
# ---------------------------------------------------------------------------


def test_clean_contact_strips_whitespace_and_blanks_to_none():
    cleaned = clean_contact({
        "first_name": "  Ada  ",
        "last_name": "   ",
        "title": "\tOffice Manager\n",
        "linkedin_url": "",
        "work_email": " ADA@clinic.com ",
        "personal_email": None,
    })
    assert cleaned["first_name"] == "Ada"
    assert cleaned["last_name"] is None
    assert cleaned["title"] == "Office Manager"
    assert cleaned["linkedin_url"] is None
    assert cleaned["work_email"] == "ADA@clinic.com"
    assert cleaned["personal_email"] is None


def test_clean_contact_scrubs_placeholder_emails():
    """'Not Found' is one of talentdb._EMAIL_PLACEHOLDERS — it is Clay's
    'nothing here', not an address."""
    cleaned = clean_contact({
        "work_email": "Not Found",
        "personal_email": "  N/A  ",
    })
    assert cleaned["work_email"] is None
    assert cleaned["personal_email"] is None


def test_clean_contact_drops_unknown_keys_and_always_has_every_field():
    cleaned = clean_contact({
        "first_name": "Ada",
        "place_id": "ChIJ-abc",
        "company_size": 40,
        "some_clay_column": "junk",
    })
    assert set(cleaned) == set(contacts.CONTACT_FIELDS)
    assert "place_id" not in cleaned
    assert "some_clay_column" not in cleaned


def test_clean_contact_tolerates_empty_payload():
    assert clean_contact({}) == {f: None for f in contacts.CONTACT_FIELDS}


# ---------------------------------------------------------------------------
# contact_dedupe_key
# ---------------------------------------------------------------------------


def test_dedupe_key_prefers_linkedin_over_everything():
    key = contact_dedupe_key({
        "linkedin_url": "https://www.linkedin.com/in/ada/",
        "work_email": "ada@clinic.com",
        "personal_email": "ada@gmail.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
    })
    assert key == "li:linkedin.com/in/ada"


def test_dedupe_key_falls_back_to_work_email_then_personal_then_name():
    base = {"first_name": "Ada", "last_name": "Lovelace"}

    assert contact_dedupe_key({
        **base, "work_email": "ADA@Clinic.com", "personal_email": "ada@gmail.com",
    }) == "we:ada@clinic.com"

    assert contact_dedupe_key({
        **base, "personal_email": "Ada@Gmail.com",
    }) == "pe:ada@gmail.com"

    assert contact_dedupe_key(base) == "nm:ada lovelace"


def test_dedupe_key_ignores_placeholder_emails():
    """A 'Not Found' work email must not beat a real personal one."""
    assert contact_dedupe_key({
        "work_email": "Not Found", "personal_email": "ada@gmail.com",
    }) == "pe:ada@gmail.com"


def test_dedupe_key_linkedin_normalization_is_spelling_insensitive():
    spellings = [
        "https://www.linkedin.com/in/x/",
        "linkedin.com/in/x",
        "HTTP://WWW.LINKEDIN.COM/in/x",
        "  http://linkedin.com/in/x/  ",
    ]
    keys = {contact_dedupe_key({"linkedin_url": s}) for s in spellings}
    assert keys == {"li:linkedin.com/in/x"}


def test_dedupe_key_collapses_name_whitespace_and_case():
    assert contact_dedupe_key({
        "first_name": "  ADA ", "last_name": " Lovelace  ",
    }) == "nm:ada lovelace"


def test_dedupe_key_of_an_empty_contact_is_a_single_placeholder():
    """Garbage in, one garbage row — not one per Clay retry."""
    assert contact_dedupe_key({}) == "nm:"
    assert contact_dedupe_key({"title": "Owner"}) == "nm:"


# ---------------------------------------------------------------------------
# contact_email
# ---------------------------------------------------------------------------


def test_contact_email_prefers_work_and_scrubs_placeholders():
    assert contact_email({
        "work_email": "ada@clinic.com", "personal_email": "ada@gmail.com",
    }) == "ada@clinic.com"
    assert contact_email({
        "work_email": "Not Found", "personal_email": "ada@gmail.com",
    }) == "ada@gmail.com"
    assert contact_email({"work_email": "  ", "personal_email": None}) is None
    assert contact_email({}) is None


# ---------------------------------------------------------------------------
# pick_primary
# ---------------------------------------------------------------------------


def test_pick_primary_prefers_a_work_email_over_an_earlier_personal_only():
    first = {"id": 1, "first_name": "Ada", "personal_email": "ada@gmail.com"}
    second = {"id": 2, "first_name": "Bo", "work_email": "bo@clinic.com"}
    assert pick_primary([first, second]) is second


def test_pick_primary_takes_the_earliest_work_email():
    a = {"id": 1, "work_email": "a@clinic.com"}
    b = {"id": 2, "work_email": "b@clinic.com"}
    assert pick_primary([a, b]) is a


def test_pick_primary_falls_back_to_the_first_contact():
    first = {"id": 1, "first_name": "Ada", "personal_email": "ada@gmail.com"}
    second = {"id": 2, "first_name": "Bo"}
    assert pick_primary([first, second]) is first


def test_pick_primary_ignores_placeholder_work_emails():
    first = {"id": 1, "work_email": "Not Found", "personal_email": "ada@gmail.com"}
    second = {"id": 2, "work_email": "bo@clinic.com"}
    assert pick_primary([first, second]) is second


def test_pick_primary_of_empty_list_is_none():
    assert pick_primary([]) is None
    assert pick_primary(None) is None


# ---------------------------------------------------------------------------
# owner_mirror_fields
# ---------------------------------------------------------------------------


def test_owner_mirror_fields_maps_a_full_contact():
    fields = owner_mirror_fields({
        "first_name": "Ada",
        "last_name": "Lovelace",
        "title": "Office Manager",
        "linkedin_url": "https://linkedin.com/in/ada",
        "work_email": "ada@clinic.com",
        "personal_email": "ada@gmail.com",
    })
    assert fields == {
        "owner_name": "Ada Lovelace",
        "owner_title": "Office Manager",
        "owner_linkedin": "https://linkedin.com/in/ada",
        "owner_email": "ada@clinic.com",
    }


def test_owner_mirror_fields_builds_the_name_from_whatever_half_exists():
    assert owner_mirror_fields({"first_name": "Ada"})["owner_name"] == "Ada"
    assert owner_mirror_fields({"last_name": " Lovelace "})["owner_name"] == "Lovelace"


def test_owner_mirror_fields_omits_keys_it_has_no_value_for():
    fields = owner_mirror_fields({"first_name": "Ada", "work_email": "Not Found"})
    assert fields == {"owner_name": "Ada"}
    assert "owner_email" not in fields
    assert owner_mirror_fields({}) == {}


def test_owner_mirror_fields_falls_back_to_the_personal_email():
    fields = owner_mirror_fields({"first_name": "Ada", "personal_email": "ada@gmail.com"})
    assert fields["owner_email"] == "ada@gmail.com"


def test_owner_mirror_fields_mirrors_contact_phone_into_owner_phone():
    fields = owner_mirror_fields({
        "first_name": "Ada", "work_email": "ada@clinic.com",
        "phone": "555-0100",
    })
    assert fields["owner_phone"] == "555-0100"
    assert "phone" not in fields  # only owner_* keys come back


def test_owner_mirror_fields_omits_owner_phone_when_contact_has_none():
    fields = owner_mirror_fields({
        "first_name": "Ada", "work_email": "ada@clinic.com",
    })
    assert "owner_phone" not in fields


# ---------------------------------------------------------------------------
# should_mirror
# ---------------------------------------------------------------------------


def test_should_mirror_true_for_a_work_email_even_over_an_existing_owner_email():
    assert should_mirror(
        {"work_email": "ada@clinic.com"},
        {"owner_email": "front.desk@clinic.com"},
    ) is True


def test_should_mirror_false_when_personal_only_would_clobber_a_real_owner_email():
    assert should_mirror(
        {"personal_email": "ada@gmail.com"},
        {"owner_email": "front.desk@clinic.com"},
    ) is False


def test_should_mirror_true_when_the_existing_owner_email_is_empty_or_placeholder():
    personal_only = {"personal_email": "ada@gmail.com"}
    assert should_mirror(personal_only, {}) is True
    assert should_mirror(personal_only, {"owner_email": None}) is True
    assert should_mirror(personal_only, {"owner_email": "  "}) is True
    assert should_mirror(personal_only, {"owner_email": "Not Found"}) is True
    assert should_mirror(personal_only, None) is True


def test_should_mirror_false_without_a_primary():
    assert should_mirror(None, {}) is False
    assert should_mirror({}, {}) is False


# ---------------------------------------------------------------------------
# upsert_contact
# ---------------------------------------------------------------------------


def test_upsert_contact_stamps_the_key_and_conflict_target():
    stored = {"id": 7, "practice_id": 42}
    client, table = _mock_client(rows=[stored])
    with patch("src.contacts._client", return_value=client):
        result = upsert_contact(42, {
            "first_name": " Ada ",
            "last_name": "Lovelace",
            "work_email": " ada@clinic.com ",
            "place_id": "ChIJ-abc",
        })

    assert result == stored
    assert client.table.call_args.args[0] == "practice_contacts"
    row = table.upsert.call_args.args[0]
    assert row["practice_id"] == 42
    assert row["first_name"] == "Ada"
    assert row["work_email"] == "ada@clinic.com"
    assert row["dedupe_key"] == "we:ada@clinic.com"
    assert row["source"] == "clay"
    assert row["updated_at"]
    assert "place_id" not in row, "unknown Clay keys must not reach the insert"
    assert table.upsert.call_args.kwargs["on_conflict"] == "practice_id,dedupe_key"


def test_upsert_contact_keeps_an_explicit_source():
    client, table = _mock_client(rows=[{"id": 1}])
    with patch("src.contacts._client", return_value=client):
        upsert_contact(42, {"first_name": "Ada", "source": "manual"})
    assert table.upsert.call_args.args[0]["source"] == "manual"


def test_upsert_contact_is_fail_soft_when_the_table_is_missing():
    """An unapplied migration must never 500 the Clay webhook."""
    client, _ = _mock_client(raises=Exception('relation "practice_contacts" does not exist'))
    with patch("src.contacts._client", return_value=client):
        assert upsert_contact(42, {"first_name": "Ada"}) is None


def test_upsert_contact_returns_none_without_a_client_or_practice():
    client, _ = _mock_client(rows=[{"id": 1}])
    with patch("src.contacts._client", return_value=None):
        assert upsert_contact(42, {"first_name": "Ada"}) is None
    with patch("src.contacts._client", return_value=client):
        assert upsert_contact(None, {"first_name": "Ada"}) is None


def test_upsert_contact_returns_none_when_nothing_came_back():
    client, _ = _mock_client(rows=[])
    with patch("src.contacts._client", return_value=client):
        assert upsert_contact(42, {"first_name": "Ada"}) is None


# ---------------------------------------------------------------------------
# list_contacts_for_practice
# ---------------------------------------------------------------------------


def test_list_contacts_orders_by_created_at_then_id():
    rows = [{"id": 1}, {"id": 2}]
    client, table = _mock_client(rows=rows)
    with patch("src.contacts._client", return_value=client):
        assert list_contacts_for_practice(42) == rows

    table.eq.assert_called_once_with("practice_id", 42)
    assert [c.args[0] for c in table.order.call_args_list] == ["created_at", "id"]
    assert table.range.call_args.args == (0, 999)


def test_list_contacts_paginates_past_the_postgrest_ceiling():
    page1 = [{"id": i} for i in range(1000)]
    page2 = [{"id": 1000}, {"id": 1001}]
    client, table = _mock_client()
    table.execute.side_effect = [MagicMock(data=page1), MagicMock(data=page2)]

    with patch("src.contacts._client", return_value=client):
        rows = list_contacts_for_practice(42)

    assert len(rows) == 1002
    assert [c.args for c in table.range.call_args_list] == [(0, 999), (1000, 1999)]


def test_list_contacts_stops_on_an_exactly_empty_trailing_page():
    """A practice with exactly 1000 contacts reads a second, empty page and
    then stops — the loop must terminate, not spin."""
    client, table = _mock_client()
    table.execute.side_effect = [
        MagicMock(data=[{"id": i} for i in range(1000)]),
        MagicMock(data=[]),
    ]
    with patch("src.contacts._client", return_value=client):
        assert len(list_contacts_for_practice(42)) == 1000
    assert table.execute.call_count == 2


def test_list_contacts_is_fail_soft():
    client, _ = _mock_client(raises=Exception('relation "practice_contacts" does not exist'))
    with patch("src.contacts._client", return_value=client):
        assert list_contacts_for_practice(42) == []
    with patch("src.contacts._client", return_value=None):
        assert list_contacts_for_practice(42) == []


def test_list_contacts_then_pick_primary_round_trip():
    rows = [
        {"id": 1, "first_name": "Ada", "personal_email": "ada@gmail.com"},
        {"id": 2, "first_name": "Bo", "last_name": "Chen",
         "title": "Owner", "work_email": "bo@clinic.com"},
    ]
    client, _ = _mock_client(rows=rows)
    with patch("src.contacts._client", return_value=client):
        primary = pick_primary(list_contacts_for_practice(42))

    assert owner_mirror_fields(primary) == {
        "owner_name": "Bo Chen",
        "owner_title": "Owner",
        "owner_email": "bo@clinic.com",
    }


# ---------------------------------------------------------------------------
# Talent-DB per-contact export markers
# ---------------------------------------------------------------------------


def test_list_contact_exports_maps_contact_id_to_td_lead_id():
    rows = [{"contact_id": 7, "td_lead_id": "TD-123"},
            {"contact_id": 9, "td_lead_id": None}]
    client, table = _mock_client(rows=rows)
    with patch("src.contacts._client", return_value=client):
        assert list_contact_exports(900) == {7: "TD-123", 9: None}

    assert client.table.call_args.args[0] == "talentdb_contact_exports"
    table.eq.assert_called_once_with("lead_id", 900)
    assert table.select.call_args.args[0] == "contact_id, td_lead_id"


def test_list_exported_contact_ids_is_just_the_keys():
    """The skip list and the id map are one read — a lead's contacts are few."""
    rows = [{"contact_id": 7, "td_lead_id": "TD-123"},
            {"contact_id": 9, "td_lead_id": None}]
    client, _ = _mock_client(rows=rows)
    with patch("src.contacts._client", return_value=client):
        assert list_exported_contact_ids(900) == {7, 9}


def test_list_contact_exports_is_fail_soft():
    """An unapplied migration degrades the fan-out to "send everyone", which is
    a duplicate at worst — never a blocked push."""
    client, _ = _mock_client(
        raises=Exception('relation "talentdb_contact_exports" does not exist'))
    with patch("src.contacts._client", return_value=client):
        assert list_contact_exports(900) == {}
        assert list_exported_contact_ids(900) == set()
    with patch("src.contacts._client", return_value=None):
        assert list_contact_exports(900) == {}
    client, _ = _mock_client(rows=[{"contact_id": 7}])
    with patch("src.contacts._client", return_value=client):
        assert list_contact_exports(None) == {}


def test_mark_contact_exported_upserts_on_the_pair():
    client, table = _mock_client(rows=[{"id": 1}])
    with patch("src.contacts._client", return_value=client):
        mark_contact_exported(900, 7)

    row = table.upsert.call_args.args[0]
    assert row["lead_id"] == 900
    assert row["contact_id"] == 7
    assert row["exported_at"]
    assert table.upsert.call_args.kwargs["on_conflict"] == "lead_id,contact_id"


def test_mark_contact_exported_stores_a_td_lead_id_when_given():
    client, table = _mock_client(rows=[{"id": 1}])
    with patch("src.contacts._client", return_value=client):
        mark_contact_exported(900, 7, td_lead_id="TD-123")
    assert table.upsert.call_args.args[0]["td_lead_id"] == "TD-123"


@pytest.mark.parametrize("empty", [None, "", 0])
def test_mark_contact_exported_never_blanks_a_stored_td_lead_id(empty):
    """A later marker write that captured no id must leave the key out of the
    upsert entirely — sending NULL would erase the only thing that lets a
    re-post update the receiver's record instead of duplicating it."""
    client, table = _mock_client(rows=[{"id": 1}])
    with patch("src.contacts._client", return_value=client):
        mark_contact_exported(900, 7, td_lead_id=empty)
    assert "td_lead_id" not in table.upsert.call_args.args[0]


def test_mark_contact_exported_is_fail_soft_and_needs_both_ids():
    client, _ = _mock_client(
        raises=Exception('relation "talentdb_contact_exports" does not exist'))
    with patch("src.contacts._client", return_value=client):
        mark_contact_exported(900, 7)          # must not raise
    client, table = _mock_client(rows=[{"id": 1}])
    with patch("src.contacts._client", return_value=client):
        mark_contact_exported(None, 7)
        mark_contact_exported(900, None)
    table.upsert.assert_not_called()
