"""Talent-DB "Import Lead" push — envelope mapping, signing, and fail-soft POST."""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src import talentdb

client = TestClient(app)


def test_import_endpoints_require_auth():
    assert client.post("/api/practices/abc/import-lead").status_code == 401
    assert client.post("/api/leads/1/import").status_code == 401


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _practice(**overrides) -> dict:
    base = {
        "id": 1024,
        "name": "Acme Dental",
        "owner_name": "Jane Doe",
        "owner_email": "jane@acme.com",
        "owner_phone": "+13120000000",
        "phone": "+13125550100",
        "website": "https://acme.com",
        "city": "Austin",
        "state": "TX",
        "rating": 4.6,
        "review_count": 212,
        "urgency_score": 0,          # a real 0 — must be kept
        "icp_tier": "A",
        "icp_breakdown": {},         # a real empty object — must be kept
        "tags": [],                  # a real empty array — must be kept
        "summary": "Independent practice",
        "enrichment_status": None,   # missing — must be omitted
        "email": "front@acme.com",
        "sales_angles": '["angle one"]',       # JSON string — must be parsed
        "website_contacts": None,
        "call_script": None,
    }
    base.update(overrides)
    return base


def _posting(**overrides) -> dict:
    base = {
        "id": 5567,
        "source": "indeed",
        "url": "https://indeed.com/viewjob?jk=x",
        "title": "Dental Assistant",
        "posted_at": "2026-08-02T00:00:00Z",
        "board_remote_flag": False,   # a real False — must be kept
        "description": "Full posting body",
        "search_term": "dental assistant",
        "search_location": "Austin, TX",
        "first_seen_at": "2026-08-02T06:00:00Z",
        "last_seen_at": "2026-08-10T06:00:00Z",
        "match_confidence": 0.94,
        "match_status": "auto",
        "matched_at": "2026-08-02T07:00:00Z",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# is_configured
# --------------------------------------------------------------------------- #

def test_is_configured_requires_both_url_and_secret():
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_url, s.talentdb_webhook_secret = "https://x", ""
        assert talentdb.is_configured() is False
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_url, s.talentdb_webhook_secret = "", "sec"
        assert talentdb.is_configured() is False
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_url, s.talentdb_webhook_secret = "https://x", "sec"
        assert talentdb.is_configured() is True


# --------------------------------------------------------------------------- #
# Slug (Lead_Type__c) vs raw posting_source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source,slug", [
    ("indeed", "hv-sales-intel-indeed"),
    ("linkedin", "hv-sales-intel-linkedin"),
    (None, "hv-sales-intel"),
    ("something-else", "hv-sales-intel"),
])
def test_lead_type_is_slug_posting_source_is_raw(source, slug):
    fields = talentdb.build_fields(_practice(), _posting(source=source))
    assert fields["Lead_Type__c"] == slug
    if source:
        assert fields["posting_source"] == source      # raw, not slugged
    else:
        assert "posting_source" not in fields           # None → omitted


# --------------------------------------------------------------------------- #
# Core field mapping
# --------------------------------------------------------------------------- #

def test_company_is_practice_name():
    fields = talentdb.build_fields(_practice(), _posting())
    assert fields["Company"] == "Acme Dental"


def test_first_last_name_split_from_owner_name():
    fields = talentdb.build_fields(_practice(owner_name="Jane Doe"), _posting())
    assert fields["FirstName"] == "Jane"
    assert fields["LastName"] == "Doe"
    # Multi-word: last token is the last name, the rest the first name.
    fields = talentdb.build_fields(_practice(owner_name="Jane Ann Doe"), _posting())
    assert fields["FirstName"] == "Jane Ann"
    assert fields["LastName"] == "Doe"


def test_single_token_owner_name_is_lastname_only():
    fields = talentdb.build_fields(_practice(owner_name="Cher"), _posting())
    assert fields["LastName"] == "Cher"
    assert "FirstName" not in fields


def test_no_owner_name_lastname_falls_back_to_company():
    """The receiver requires LastName, so it falls back to the company name when
    there's no owner_name. FirstName is not required → omitted."""
    fields = talentdb.build_fields(_practice(owner_name=None), _posting())
    assert "FirstName" not in fields
    assert fields["LastName"] == "Acme Dental"
    assert fields["Company"] == "Acme Dental"


def test_email_prefers_owner_then_practice():
    assert talentdb.build_fields(_practice(), None)["Email"] == "jane@acme.com"
    fields = talentdb.build_fields(_practice(owner_email=None), None)
    assert fields["Email"] == "front@acme.com"
    fields = talentdb.build_fields(_practice(owner_email=None, email=None), None)
    assert "Email" not in fields              # neither → omitted


def test_phone_prefers_owner_then_practice():
    assert talentdb.build_fields(_practice(), None)["Phone"] == "+13120000000"
    fields = talentdb.build_fields(_practice(owner_phone=None), None)
    assert fields["Phone"] == "+13125550100"


def test_country_is_hardcoded_usa():
    assert talentdb.build_fields(_practice(), _posting())["country"] == "USA"


def test_organization_size_from_practice():
    fields = talentdb.build_fields(_practice(organization_size=42), _posting())
    assert fields["organization_size"] == 42
    # Omitted when the practice has no value.
    assert "organization_size" not in talentdb.build_fields(_practice(), _posting())


@pytest.mark.parametrize("hint,industry", [
    ("Virtual Medical Assistant", "Medical"),
    ("Virtual Medical Scheduler", "Medical"),
    ("Virtual Dental Assistant", "Dental"),
    ("Virtual Chiropractic Assistant", "Chiropractor"),
    ("Virtual Home Health Operations Coordinator", "Home Health"),
    ("Virtual Legal Assistant", "Legal"),
    ("Virtual Assisted Living Coordinator", "Assisted Living"),
])
def test_industry_mapped_from_track(hint, industry):
    fields = talentdb.build_fields(_practice(), _posting(service_line_hint=hint))
    assert fields["industry"] == industry


def test_industry_omitted_when_track_unmapped():
    fields = talentdb.build_fields(_practice(), _posting(service_line_hint="Something Else"))
    assert "industry" not in fields


def test_source_practice_id_is_stringified():
    fields = talentdb.build_fields(_practice(), _posting())
    assert fields["source_practice_id"] == "1024"


# --------------------------------------------------------------------------- #
# Omit-missing but keep falsy real values + native types
# --------------------------------------------------------------------------- #

def test_missing_values_omitted_but_falsy_reals_kept():
    fields = talentdb.build_fields(_practice(), _posting())
    # Omitted (None / "")
    assert "enrichment_status" not in fields
    assert "call_script" not in fields
    assert "website_contacts" not in fields
    # Kept falsy reals — carry meaning
    assert fields["urgency_score"] == 0
    assert fields["board_remote"] is False
    assert fields["icp_breakdown"] == {}
    assert fields["tags"] == []
    assert fields["review_count"] == 212


def test_native_types_preserved_through_serialization():
    env = talentdb.build_envelope(_practice(), _posting())
    raw = talentdb._serialize(env)
    parsed = json.loads(raw)["fields"]
    assert parsed["Rating"] == 4.6                # number, not "4.6"
    assert parsed["board_remote"] is False        # bool
    assert parsed["match_confidence"] == 0.94
    assert parsed["urgency_score"] == 0


def test_json_string_columns_are_parsed():
    fields = talentdb.build_fields(_practice(), _posting())
    assert fields["sales_angles"] == ["angle one"]   # parsed from JSON string
    # An already-structured dict passes through untouched.
    fields = talentdb.build_fields(_practice(icp_breakdown={"vertical": 9}), _posting())
    assert fields["icp_breakdown"] == {"vertical": 9}
    # An unparseable string becomes None → omitted.
    fields = talentdb.build_fields(_practice(sales_angles="not json"), _posting())
    assert "sales_angles" not in fields


# --------------------------------------------------------------------------- #
# No-posting case
# --------------------------------------------------------------------------- #

def test_no_posting_omits_posting_fields_and_falls_back_slug():
    fields = talentdb.build_fields(_practice(), None)
    assert fields["Lead_Type__c"] == "hv-sales-intel"
    for key in ("posting_source", "posting_url", "role_title", "posted_at",
                "match_confidence", "match_status", "matched_at"):
        assert key not in fields
    # Practice data still rides along.
    assert fields["Company"] == "Acme Dental"


def test_no_practice_falls_back_to_employer_name():
    fields = talentdb.build_fields(None, _posting(employer_name="Board Co"))
    assert fields["Company"] == "Board Co"     # Company from employer
    assert fields["LastName"] == "Board Co"    # LastName falls back to company
    assert "FirstName" not in fields           # first name not required
    assert fields["posting_source"] == "indeed"


# --------------------------------------------------------------------------- #
# Envelope shape — objectType + operation + fields only
# --------------------------------------------------------------------------- #

def test_envelope_is_objecttype_operation_fields_only():
    env = talentdb.build_envelope(_practice(), _posting())
    assert set(env) == {"objectType", "operation", "fields"}
    assert env["objectType"] == "Lead"
    assert env["operation"] == "upsert"
    # The app-origin webhook mints its own record — we send no ids/timestamp.
    for absent in ("salesforceId", "salesforceUpdatedAt", "eventId"):
        assert absent not in env


# --------------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------------- #

def test_sign_is_hmac_sha256_hex_prefixed():
    raw = b'{"objectType":"Lead"}'
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_secret = "topsecret"
        sig = talentdb._sign(raw)
    expected = hmac.new(b"topsecret", raw, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


def test_signature_covers_the_exact_bytes_sent():
    """The signed bytes must be identical to the serialized envelope bytes."""
    env = talentdb.build_envelope(_practice(), _posting())
    raw = talentdb._serialize(env)
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_secret = "topsecret"
        sig = talentdb._sign(raw)
    expected = "sha256=" + hmac.new(b"topsecret", raw, hashlib.sha256).hexdigest()
    assert sig == expected


# --------------------------------------------------------------------------- #
# import_lead — fail-soft
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_import_lead_not_configured_is_soft():
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_url, s.talentdb_webhook_secret = "", ""
        result = await talentdb.import_lead(_practice(), _posting())
    assert result["ok"] is False
    assert result["status"] == "not_configured"


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.is_success = 200 <= status < 300
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.sent = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, content=None):
        self.sent = {"url": url, "headers": headers, "content": content}
        return self._resp


@pytest.mark.asyncio
async def test_import_lead_success_returns_ok_and_signs_sent_bytes():
    fake = _FakeClient(_FakeResp({"ok": True, "status": "ok", "localEntityId": 482}))
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_url = "https://x/api/salesforce/webhook"
        s.talentdb_webhook_secret = "topsecret"
        with patch("src.talentdb.httpx.AsyncClient", return_value=fake):
            result = await talentdb.import_lead(_practice(), _posting())
    assert result["ok"] is True
    assert result["local_entity_id"] == 482
    # The signature header matches the exact bytes posted.
    sent = fake.sent
    expected = "sha256=" + hmac.new(
        b"topsecret", sent["content"], hashlib.sha256
    ).hexdigest()
    assert sent["headers"]["X-HV-Signature"] == expected


@pytest.mark.asyncio
async def test_import_lead_receiver_error_is_soft_not_ok():
    fake = _FakeClient(_FakeResp({"ok": False, "status": "error",
                                  "message": "Cannot create lead."}))
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_url = "https://x/api/salesforce/webhook"
        s.talentdb_webhook_secret = "topsecret"
        with patch("src.talentdb.httpx.AsyncClient", return_value=fake):
            result = await talentdb.import_lead(_practice(), None)
    assert result["ok"] is False
    assert result["status"] == "error"
    assert "Cannot create lead." in result["message"]
