import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

from src.auth import (
    CURRENT_COMPANY_COOKIE,
    _read_supabase_token,
    get_current_user,
    require_admin,
)
from src.settings import settings


def _mock_request(cookies: dict):
    req = MagicMock()
    req.cookies = cookies
    return req


def test_read_token_returns_none_when_no_cookies():
    assert _read_supabase_token(_mock_request({})) is None


def test_read_token_reads_single_auth_cookie():
    token_payload = '{"access_token":"abc.def.ghi"}'
    req = _mock_request({"sb-proj-auth-token": token_payload})
    assert _read_supabase_token(req) == "abc.def.ghi"


def test_read_token_reassembles_chunked_cookies():
    part0 = '{"access_token":"abc.de'
    part1 = 'f.ghi","refresh_token":"r"}'
    req = _mock_request({
        "sb-proj-auth-token.0": part0,
        "sb-proj-auth-token.1": part1,
    })
    assert _read_supabase_token(req) == "abc.def.ghi"


def test_read_token_returns_none_on_malformed_cookie():
    req = _mock_request({"sb-proj-auth-token": "not json"})
    assert _read_supabase_token(req) is None


def test_read_token_decodes_base64_prefixed_cookie():
    import base64
    payload = '{"access_token":"abc.def.ghi","refresh_token":"r"}'
    encoded = "base64-" + base64.b64encode(payload.encode()).decode()
    req = _mock_request({"sb-proj-auth-token": encoded})
    assert _read_supabase_token(req) == "abc.def.ghi"


# ---------------------------------------------------------------------------
# get_current_user — a fake Supabase client that counts round trips
# ---------------------------------------------------------------------------

# Length matches a real Supabase JWT secret (>=32 bytes for HS256).
TEST_JWT_SECRET = "test-jwt-secret-not-a-real-one-0123456789abcdef"
OTHER_JWT_SECRET = "another-secret-entirely-0123456789abcdef"


class _FakeQuery:
    """Chainable stand-in for a postgrest query builder."""

    def __init__(self, data):
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self._data)


class _FakeClient:
    """Serves canned rows per table and records every table touched, so a
    test can assert how many round trips auth actually costs."""

    def __init__(self, *, profile=None, memberships=None, auth_user_id=None):
        self._rows = {"profiles": profile, "company_members": memberships or []}
        self.tables_queried: list[str] = []
        self.auth = MagicMock()
        self.auth.get_user.return_value = SimpleNamespace(
            user=SimpleNamespace(id=auth_user_id),
        )

    def table(self, name):
        self.tables_queried.append(name)
        return _FakeQuery(self._rows.get(name))


def _mint(sub, *, secret=TEST_JWT_SECRET, expires_in=3600, aud="authenticated"):
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "aud": aud, "iat": now, "exp": now + expires_in},
        secret,
        algorithm="HS256",
    )


def _request(token, *, company_cookie=None):
    cookies = {"sb-proj-auth-token": json.dumps({"access_token": token})}
    if company_cookie:
        cookies[CURRENT_COMPANY_COOKIE] = company_cookie
    return _mock_request(cookies)


def _member(company_id, role="sdr"):
    return {"company_id": company_id, "role": role}


def test_get_current_user_401_when_no_token():
    with pytest.raises(HTTPException) as exc:
        get_current_user(_mock_request({}))
    assert exc.value.status_code == 401


def test_get_current_user_returns_profile_with_company(sample_sdr_profile):
    client = _FakeClient(
        profile=sample_sdr_profile,
        memberships=[_member("company-a", "admin")],
        auth_user_id=sample_sdr_profile["id"],
    )
    with patch("src.auth.get_admin_client", return_value=client):
        result = get_current_user(_request("abc.def.ghi"))

    assert result["id"] == sample_sdr_profile["id"]
    assert result["email"] == sample_sdr_profile["email"]
    assert result["company_id"] == "company-a"
    assert result["company_role"] == "admin"
    # The per-company role overrides the legacy global one...
    assert result["role"] == "admin"
    # ...which stays reachable for the few callers that need it.
    assert result["global_role"] == "sdr"


def test_get_current_user_costs_exactly_two_queries(sample_sdr_profile):
    """Profile + memberships, and nothing more — the merge is the point."""
    client = _FakeClient(
        profile=sample_sdr_profile,
        memberships=[_member("company-a"), _member("company-b")],
        auth_user_id=sample_sdr_profile["id"],
    )
    with patch("src.auth.get_admin_client", return_value=client):
        get_current_user(_request("abc.def.ghi", company_cookie="company-b"))

    assert client.tables_queried == ["profiles", "company_members"]


def test_get_current_user_401_on_invalid_token():
    client = _FakeClient()
    client.auth.get_user.side_effect = Exception("invalid")
    with patch("src.auth.get_admin_client", return_value=client):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_request("bad"))
    assert exc.value.status_code == 401


def test_get_current_user_403_when_profile_missing():
    client = _FakeClient(profile=None, auth_user_id="missing")
    with patch("src.auth.get_admin_client", return_value=client):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_request("abc"))
    assert exc.value.status_code == 403


def test_get_current_user_401_when_account_disabled(sample_sdr_profile):
    disabled = {**sample_sdr_profile, "disabled_at": "2026-08-01T00:00:00Z"}
    client = _FakeClient(
        profile=disabled,
        memberships=[_member("company-a")],
        auth_user_id=disabled["id"],
    )
    with patch("src.auth.get_admin_client", return_value=client):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_request("abc"))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Company resolution: cookie wins if still a member, else oldest membership
# ---------------------------------------------------------------------------

def test_cookie_company_wins_when_still_a_member(sample_sdr_profile):
    client = _FakeClient(
        profile=sample_sdr_profile,
        # Oldest first, as the joined_at ordering delivers them.
        memberships=[_member("oldest", "sdr"), _member("chosen", "admin")],
        auth_user_id=sample_sdr_profile["id"],
    )
    with patch("src.auth.get_admin_client", return_value=client):
        result = get_current_user(_request("t", company_cookie="chosen"))

    assert result["company_id"] == "chosen"
    assert result["company_role"] == "admin"


def test_cookie_company_ignored_when_not_a_member(sample_sdr_profile):
    """Stale cookie (left a company, or someone forged one) falls back to
    the oldest membership rather than granting access to the named one."""
    client = _FakeClient(
        profile=sample_sdr_profile,
        memberships=[_member("oldest", "sdr"), _member("other", "admin")],
        auth_user_id=sample_sdr_profile["id"],
    )
    with patch("src.auth.get_admin_client", return_value=client):
        result = get_current_user(_request("t", company_cookie="not-mine"))

    assert result["company_id"] == "oldest"
    assert result["company_role"] == "sdr"


def test_oldest_membership_used_without_a_cookie(sample_sdr_profile):
    client = _FakeClient(
        profile=sample_sdr_profile,
        memberships=[_member("oldest", "admin"), _member("newer", "sdr")],
        auth_user_id=sample_sdr_profile["id"],
    )
    with patch("src.auth.get_admin_client", return_value=client):
        result = get_current_user(_request("t"))

    assert result["company_id"] == "oldest"
    assert result["company_role"] == "admin"


def test_403_when_user_belongs_to_no_company(sample_sdr_profile):
    client = _FakeClient(
        profile=sample_sdr_profile,
        memberships=[],
        auth_user_id=sample_sdr_profile["id"],
    )
    with patch("src.auth.get_admin_client", return_value=client):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_request("t", company_cookie="anything"))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Local JWT verification (supabase_jwt_secret set) vs GoTrue fallback
# ---------------------------------------------------------------------------

def test_local_verify_skips_the_gotrue_round_trip(sample_sdr_profile):
    client = _FakeClient(
        profile=sample_sdr_profile,
        memberships=[_member("company-a")],
        auth_user_id="should-not-be-used",
    )
    token = _mint(sample_sdr_profile["id"])
    with patch.object(settings, "supabase_jwt_secret", TEST_JWT_SECRET):
        with patch("src.auth.get_admin_client", return_value=client):
            result = get_current_user(_request(token))

    assert result["id"] == sample_sdr_profile["id"]
    client.auth.get_user.assert_not_called()


def test_local_verify_401_on_expired_token(sample_sdr_profile):
    client = _FakeClient(profile=sample_sdr_profile)
    token = _mint(sample_sdr_profile["id"], expires_in=-3600)
    with patch.object(settings, "supabase_jwt_secret", TEST_JWT_SECRET):
        with patch("src.auth.get_admin_client", return_value=client):
            with pytest.raises(HTTPException) as exc:
                get_current_user(_request(token))
    assert exc.value.status_code == 401
    assert client.tables_queried == []


def test_local_verify_tolerates_small_clock_skew(sample_sdr_profile):
    """A token that expired seconds ago is drift, not a rejection."""
    client = _FakeClient(
        profile=sample_sdr_profile,
        memberships=[_member("company-a")],
    )
    token = _mint(sample_sdr_profile["id"], expires_in=-5)
    with patch.object(settings, "supabase_jwt_secret", TEST_JWT_SECRET):
        with patch("src.auth.get_admin_client", return_value=client):
            result = get_current_user(_request(token))
    assert result["company_id"] == "company-a"


def test_local_verify_401_on_wrong_signature(sample_sdr_profile):
    client = _FakeClient(profile=sample_sdr_profile)
    token = _mint(sample_sdr_profile["id"], secret=OTHER_JWT_SECRET)
    with patch.object(settings, "supabase_jwt_secret", TEST_JWT_SECRET):
        with patch("src.auth.get_admin_client", return_value=client):
            with pytest.raises(HTTPException) as exc:
                get_current_user(_request(token))
    assert exc.value.status_code == 401


def test_local_verify_401_on_wrong_audience(sample_sdr_profile):
    client = _FakeClient(profile=sample_sdr_profile)
    token = _mint(sample_sdr_profile["id"], aud="anon")
    with patch.object(settings, "supabase_jwt_secret", TEST_JWT_SECRET):
        with patch("src.auth.get_admin_client", return_value=client):
            with pytest.raises(HTTPException) as exc:
                get_current_user(_request(token))
    assert exc.value.status_code == 401


def test_local_verify_401_on_malformed_token():
    """Garbage that survives cookie parsing must not surface as a 500."""
    client = _FakeClient()
    with patch.object(settings, "supabase_jwt_secret", TEST_JWT_SECRET):
        with patch("src.auth.get_admin_client", return_value=client):
            with pytest.raises(HTTPException) as exc:
                get_current_user(_request("not.a.jwt"))
    assert exc.value.status_code == 401


def test_falls_back_to_gotrue_when_secret_unset(sample_sdr_profile):
    """Unset secret -> the old `auth.get_user` path, byte for byte."""
    client = _FakeClient(
        profile=sample_sdr_profile,
        memberships=[_member("company-a")],
        auth_user_id=sample_sdr_profile["id"],
    )
    # A token that local verification would have accepted, to prove the
    # secret being empty is what routes this through GoTrue.
    token = _mint(sample_sdr_profile["id"])
    with patch.object(settings, "supabase_jwt_secret", ""):
        with patch("src.auth.get_admin_client", return_value=client):
            result = get_current_user(_request(token))

    client.auth.get_user.assert_called_once_with(token)
    assert result["id"] == sample_sdr_profile["id"]


def test_gotrue_fallback_401_when_response_has_no_user():
    client = _FakeClient()
    client.auth.get_user.return_value = SimpleNamespace(user=None)
    with patch.object(settings, "supabase_jwt_secret", ""):
        with patch("src.auth.get_admin_client", return_value=client):
            with pytest.raises(HTTPException) as exc:
                get_current_user(_request("abc"))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# require_admin
# ---------------------------------------------------------------------------

def test_require_admin_passes_for_admin(sample_admin_profile):
    assert require_admin(sample_admin_profile) == sample_admin_profile


def test_require_admin_403_for_rep(sample_sdr_profile):
    with pytest.raises(HTTPException) as exc:
        require_admin(sample_sdr_profile)
    assert exc.value.status_code == 403
