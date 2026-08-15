from unittest.mock import MagicMock, patch

from src.storage import add_tags


def _fake_client_with_existing_tags(existing: list[str]):
    """Build a Supabase client mock returning a row with the given tags."""
    client = MagicMock()
    select_chain = MagicMock()
    select_chain.execute.return_value.data = {"tags": existing}
    client.table.return_value.select.return_value.eq.return_value.maybe_single.return_value = select_chain

    update_chain = MagicMock()
    update_chain.execute.return_value.data = [{"tags": existing + ["NEW"]}]
    client.table.return_value.update.return_value.eq.return_value = update_chain
    return client


def test_add_tags_appends_when_absent():
    fake = _fake_client_with_existing_tags(["RESEARCHED"])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", ["SCRIPT_READY"])
    update_args = fake.table.return_value.update.call_args.args[0]
    assert sorted(update_args["tags"]) == ["RESEARCHED", "SCRIPT_READY"]


def test_add_tags_dedupes_existing():
    fake = _fake_client_with_existing_tags(["RESEARCHED", "SCRIPT_READY"])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", ["RESEARCHED"])
    fake.table.return_value.update.assert_not_called()


def test_add_tags_handles_empty_existing():
    fake = _fake_client_with_existing_tags([])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", ["RESEARCHED", "ENRICHED"])
    update_args = fake.table.return_value.update.call_args.args[0]
    assert sorted(update_args["tags"]) == ["ENRICHED", "RESEARCHED"]


def test_add_tags_noop_when_no_new_tags():
    fake = _fake_client_with_existing_tags(["RESEARCHED"])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", [])
    fake.table.return_value.update.assert_not_called()


def test_add_tags_skips_when_client_unconfigured():
    with patch("src.storage._get_client", return_value=None):
        add_tags("place-1", ["RESEARCHED"])  # must not raise


# --------------------------------------------------------------------------
# The per-company mirror's practice id. `add_tags` runs from ~8 mutation
# endpoints, and the mirror used to re-resolve `place_id -> id` every time
# even though the caller — and this function's own read — already had it.
# --------------------------------------------------------------------------


def test_add_tags_reads_the_id_alongside_the_tags():
    """Same query, one more column: the mirror never has to ask separately."""
    fake = _fake_client_with_existing_tags(["RESEARCHED"])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", ["SCRIPT_READY"])
    assert fake.table.return_value.select.call_args.args[0] == "id,tags"


def test_add_tags_mirrors_against_the_id_from_its_own_read():
    fake = _fake_client_with_existing_tags(["RESEARCHED"])
    fake.table.return_value.select.return_value.eq.return_value.maybe_single \
        .return_value.execute.return_value.data = {"id": 42, "tags": ["RESEARCHED"]}

    def boom(place_id):
        raise AssertionError("re-resolved an id the tags read already carried")

    with patch("src.storage._get_client", return_value=fake), \
         patch("src.storage._practice_id_by_place", side_effect=boom), \
         patch("src.storage._per_company_upsert") as mirror:
        add_tags("place-1", ["SCRIPT_READY"], company_id="co-1")

    assert mirror.call_args.args[2] == 42


def test_add_tags_prefers_an_id_the_caller_already_holds():
    """Every route that tags does so right after reading or writing the
    practice, so the id is normally in hand before we get here."""
    fake = _fake_client_with_existing_tags(["RESEARCHED"])

    def boom(place_id):
        raise AssertionError("looked up an id the caller supplied")

    with patch("src.storage._get_client", return_value=fake), \
         patch("src.storage._practice_id_by_place", side_effect=boom), \
         patch("src.storage._per_company_upsert") as mirror:
        add_tags("place-1", ["SCRIPT_READY"], company_id="co-1", practice_id=99)

    assert mirror.call_args.args[2] == 99
