"""`GET /api/session` — the app shell's three boot requests, merged into one.

The shell used to call `/api/me`, `/api/me/companies` and `/api/me/credits`
in sequence, and every one of them separately resolved the user through
`get_current_user`'s auth round trips. These tests pin the two properties
that make the merge safe to migrate to: the bodies are the SAME shapes the
three routes return (so the client can move a field at a time), and the
three routes still exist untouched.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import get_current_user

client = TestClient(app)


def _override_user(user: dict):
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


class _FakeAdminClient:
    """Answers the two reads `/api/session` fans out to: the membership join
    and the credits pair (company row + transactions)."""

    def __init__(self, memberships, company_row, transactions):
        self._by_table = {
            "company_members": memberships,
            "companies": company_row,
            "credit_transactions": transactions,
        }
        self.tables: list[str] = []

    def table(self, name):
        self.tables.append(name)
        return _FakeAdminQuery(self._by_table.get(name, []))


class _FakeAdminQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _fake_admin(company_id="co-1"):
    return _FakeAdminClient(
        memberships=[{
            "role": "admin",
            "company": {
                "id": company_id, "slug": "acme", "name": "Acme",
                "branding": None, "icp_parsed": {"verticals_in_scope": ["dental"]},
                "archived_at": None,
            },
        }],
        company_row=[{
            "credit_balance": 12.5, "credits_purchased": 20, "credits_consumed": 7.5,
        }],
        transactions=[{"id": 1, "kind": "topup", "delta": 20}],
    )


def test_session_returns_user_companies_and_credits(sample_admin_profile):
    user = {**sample_admin_profile, "company_id": "co-1"}
    _override_user(user)

    with patch("api.index.get_admin_client", return_value=_fake_admin()), \
         patch("src.auth.settings") as s:
        s.bootstrap_admin_email = None
        body = client.get("/api/session").json()

    assert set(body) == {"user", "companies", "credits"}
    assert body["user"]["email"] == sample_admin_profile["email"]
    assert body["user"]["is_bootstrap_admin"] is False
    assert body["companies"]["current_company_id"] == "co-1"
    assert body["companies"]["companies"][0]["name"] == "Acme"
    assert body["companies"]["companies"][0]["is_current"] is True
    assert body["credits"]["balance"] == 12.5


def test_session_bodies_match_the_three_routes_they_replace(sample_admin_profile):
    """The whole point of the merge: a client can read `session.companies`
    where it read `/api/me/companies`, without translating anything."""
    user = {**sample_admin_profile, "company_id": "co-1"}
    _override_user(user)

    with patch("api.index.get_admin_client", side_effect=lambda: _fake_admin()), \
         patch("src.auth.settings") as s:
        s.bootstrap_admin_email = None
        session = client.get("/api/session").json()
        me = client.get("/api/me").json()
        companies = client.get("/api/me/companies").json()
        credits = client.get("/api/me/credits").json()

    assert session["user"] == me
    assert session["companies"] == companies
    assert session["credits"] == credits


def test_session_requires_auth():
    assert client.get("/api/session").status_code == 401


def test_session_handles_a_user_with_no_company(sample_sdr_profile):
    """No active company is a real state (a fresh invite): credits answer
    zeroes rather than erroring, same as the standalone route does."""
    _override_user({**sample_sdr_profile, "company_id": None})

    with patch("api.index.get_admin_client", return_value=_fake_admin()), \
         patch("src.auth.settings") as s:
        s.bootstrap_admin_email = None
        body = client.get("/api/session").json()

    assert body["credits"]["balance"] == 0
    assert body["credits"]["transactions"] == []
