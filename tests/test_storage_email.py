from unittest.mock import MagicMock, patch

from src.storage import (
    insert_email_message,
    insert_email_messages,
    list_email_messages,
    list_outbound_message_ids,
)


def _make_client_with(insert_data=None, select_data=None):
    client = MagicMock()
    table = MagicMock()
    table.insert.return_value = table
    table.select.return_value = table
    table.eq.return_value = table
    table.order.return_value = table
    table.execute.return_value = MagicMock(
        data=insert_data if insert_data is not None else select_data
    )
    client.table.return_value = table
    return client, table


def test_insert_email_message_happy_path():
    client, table = _make_client_with(insert_data=[{"id": 1, "practice_id": 5}])
    with patch("src.storage._get_client", return_value=client):
        result = insert_email_message(
            practice_id=5,
            user_id="user-uuid",
            direction="out",
            subject="Hello",
            body="...",
            message_id="<m@h>",
            in_reply_to=None,
            error=None,
        )
    assert result == {"id": 1, "practice_id": 5}
    # The single-row helper delegates to the batch one, so the payload is a
    # one-element list. Keeping one insert path is what stops the single and
    # bulk writers drifting in what columns they write.
    batch = table.insert.call_args.args[0]
    assert isinstance(batch, list) and len(batch) == 1
    insert_arg = batch[0]
    assert insert_arg["practice_id"] == 5
    assert insert_arg["direction"] == "out"
    assert insert_arg["message_id"] == "<m@h>"


def test_insert_email_messages_writes_one_batch_per_table():
    """A reply poll that found four messages used to pay eight round trips —
    a legacy insert plus a per-company mirror insert per reply."""
    client, table = _make_client_with(insert_data=[{"id": 1}, {"id": 2}, {"id": 3}])
    with patch("src.storage._get_client", return_value=client):
        result = insert_email_messages(
            practice_id=5,
            user_id=None,
            direction="in",
            messages=[
                {"message_id": "<a>", "subject": "s1", "body": "b1"},
                {"message_id": "<b>", "subject": "s2", "body": "b2"},
                {"message_id": "<c>", "subject": "s3", "body": "b3"},
            ],
            company_id="co-1",
        )

    assert len(result) == 3
    # Two inserts total: `email_messages` and its `company_email_messages`
    # mirror — not two per message.
    assert table.insert.call_count == 2
    tables = [c.args[0] for c in client.table.call_args_list]
    assert tables == ["email_messages", "company_email_messages"]

    legacy_batch = table.insert.call_args_list[0].args[0]
    assert [r["message_id"] for r in legacy_batch] == ["<a>", "<b>", "<c>"]
    assert all(r["practice_id"] == 5 and r["direction"] == "in" for r in legacy_batch)
    assert all("company_id" not in r for r in legacy_batch)

    mirror_batch = table.insert.call_args_list[1].args[0]
    assert all(r["company_id"] == "co-1" for r in mirror_batch)


def test_insert_email_messages_skips_the_mirror_without_a_company():
    client, table = _make_client_with(insert_data=[{"id": 1}])
    with patch("src.storage._get_client", return_value=client):
        insert_email_messages(
            practice_id=5, user_id=None, direction="in",
            messages=[{"message_id": "<a>"}],
        )
    assert table.insert.call_count == 1


def test_insert_email_messages_survives_a_failed_mirror():
    """The legacy table is the source of truth until Phase 4 swaps the reads;
    a mirror failure must never lose a real message."""
    client, table = _make_client_with(insert_data=[{"id": 1}])
    calls = {"n": 0}

    def flaky_execute():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("company_email_messages does not exist here")
        return MagicMock(data=[{"id": 1}])

    table.execute.side_effect = flaky_execute

    with patch("src.storage._get_client", return_value=client):
        result = insert_email_messages(
            practice_id=5, user_id=None, direction="in",
            messages=[{"message_id": "<a>"}], company_id="co-1",
        )

    assert result == [{"id": 1}]


def test_insert_email_messages_writes_nothing_for_an_empty_batch():
    client, table = _make_client_with(insert_data=[])
    with patch("src.storage._get_client", return_value=client):
        assert insert_email_messages(
            practice_id=5, user_id=None, direction="in", messages=[],
        ) == []
    table.insert.assert_not_called()


def test_list_email_messages_returns_rows():
    rows = [{"id": 1, "direction": "out"}, {"id": 2, "direction": "in"}]
    client, _ = _make_client_with(select_data=rows)
    with patch("src.storage._get_client", return_value=client):
        result = list_email_messages(5)
    assert result == rows


def test_list_outbound_message_ids():
    rows = [{"message_id": "<a>"}, {"message_id": "<b>"}, {"message_id": None}]
    client, _ = _make_client_with(select_data=rows)
    with patch("src.storage._get_client", return_value=client):
        result = list_outbound_message_ids(5)
    assert result == ["<a>", "<b>"]
