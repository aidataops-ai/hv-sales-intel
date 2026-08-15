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
