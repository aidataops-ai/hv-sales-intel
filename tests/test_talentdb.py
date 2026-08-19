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


def _lead(**overrides) -> dict:
    base = {"provider_count": 4, "service_line": "Virtual Dental Assistant"}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# source (slug) + posting_source (raw) + source_practice_id
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source,slug", [
    ("indeed", "hv-sales-intel-indeed"),
    ("linkedin", "hv-sales-intel-linkedin"),
    (None, "hv-sales-intel"),
])
def test_source_slug_and_raw_posting_source(source, slug):
    fields = talentdb.build_fields(_practice(), _posting(source=source))
    assert fields["source"] == slug                  # slug — now sent
    if source:
        assert fields["posting_source"] == source    # raw, not slugged


def test_source_practice_id_is_stringified():
    fields = talentdb.build_fields(_practice(), _posting())
    assert fields["source_practice_id"] == "1024"    # str(practice.id)
    assert "source_practice_id" not in talentdb.build_fields(None, _posting())


# --------------------------------------------------------------------------- #
# Core field mapping (schema keys: PascalCase core + snake_case posting/scoring)
# --------------------------------------------------------------------------- #

def test_company_is_practice_name():
    fields = talentdb.build_fields(_practice(), _posting())
    assert fields["Company"] == "Acme Dental"


def test_first_last_name_split_from_owner_name():
    fields = talentdb.build_fields(_practice(owner_name="Jane Doe"), _posting())
    assert fields["FirstName"] == "Jane"
    assert fields["LastName"] == "Doe"
    fields = talentdb.build_fields(_practice(owner_name="Jane Ann Doe"), _posting())
    assert fields["FirstName"] == "Jane Ann"
    assert fields["LastName"] == "Doe"


def test_single_token_owner_name_is_lastname_only():
    fields = talentdb.build_fields(_practice(owner_name="Cher"), _posting())
    assert fields["LastName"] == "Cher"
    assert "FirstName" not in fields


def test_no_owner_name_lastname_falls_back_to_company():
    fields = talentdb.build_fields(_practice(owner_name=None), _posting())
    assert "FirstName" not in fields
    assert fields["LastName"] == "Acme Dental"
    assert fields["Company"] == "Acme Dental"


def test_email_prefers_owner_then_practice():
    assert talentdb.build_fields(_practice(), None)["Email"] == "jane@acme.com"
    fields = talentdb.build_fields(_practice(owner_email=None), None)
    assert fields["Email"] == "front@acme.com"
    fields = talentdb.build_fields(_practice(owner_email=None, email=None), None)
    assert "Email" not in fields


def test_phone_prefers_owner_then_practice():
    assert talentdb.build_fields(_practice(), None)["Phone"] == "+13120000000"
    fields = talentdb.build_fields(_practice(owner_phone=None), None)
    assert fields["Phone"] == "+13125550100"


def test_country_is_hardcoded_usa():
    assert talentdb.build_fields(_practice(), _posting())["Country"] == "USA"


def test_lead_type_is_outbound_constant():
    assert talentdb.build_fields(_practice(), _posting())["Lead_Type__c"] == "Outbound"


def test_no_of_providers_from_lead():
    fields = talentdb.build_fields(_practice(), _posting(), _lead(provider_count=7))
    assert fields["No_of_Providers__c"] == 7
    # No lead → omitted.
    assert "No_of_Providers__c" not in talentdb.build_fields(_practice(), _posting())


def test_industry_track_code_org_bucket_and_notes():
    fields = talentdb.build_fields(
        _practice(organization_size=120, notes="called twice",
                  pain_points='["long waits","turnover"]'),
        _posting(), _lead(service_line="Virtual Medical Scheduler"))
    assert fields["Industry"] == "medical"                       # derived from the track
    # interested_tracks sends the Tracks UUID code, not a label/slug.
    assert fields["interested_tracks"] == ["45c76242-e585-11f0-831c-2eb420401434"]
    assert fields["organization_size"] == "50_250"               # bucket
    assert fields["practice_notes"] == "called twice"
    assert fields["pain_points"] == "long waits\nturnover"       # list → text


def test_interested_tracks_omitted_when_track_unmapped():
    """An unknown track has no UUID → omitted (a made-up string won't render)."""
    fields = talentdb.build_fields(_practice(), _posting(),
                                   _lead(service_line="Made Up Track"))
    assert "interested_tracks" not in fields
    assert "Industry" not in fields             # unmapped track → no industry either


def test_track_prefers_lead_service_line_over_posting_hint():
    """The lead's resolved service_line (what-it-IS, from track_resolver) wins; the
    search-term hint (how we FOUND it) is only a null-safety fallback.
    (ADR 2026-08-19-deterministic-track-resolver.)"""
    fields = talentdb.build_fields(
        _practice(),
        _posting(service_line_hint="Virtual Dental Assistant"),
        _lead(service_line="Virtual Medical Scheduler"))
    # the lead's scheduler track wins over the search-term dental hint.
    assert fields["interested_tracks"] == ["45c76242-e585-11f0-831c-2eb420401434"]
    assert fields["Industry"] == "medical"
    # With no lead service_line, it falls back to the posting hint.
    fallback = talentdb.build_fields(
        _practice(), _posting(service_line_hint="Virtual Dental Assistant"),
        _lead(service_line=None))
    assert fallback["interested_tracks"] == ["88bcb836-c0aa-11f0-a242-325255367c63"]
    assert fallback["Industry"] == "dental"


def test_email_placeholder_is_scrubbed():
    """A "Not Found" placeholder is dropped rather than sent as the contact email."""
    assert "Email" not in talentdb.build_fields(
        _practice(owner_email="Not Found", email=None), None)
    # case/space-insensitive; other placeholders too.
    assert "Email" not in talentdb.build_fields(
        _practice(owner_email="  N/A ", email=None), None)
    # A real address still passes through untouched.
    assert talentdb.build_fields(
        _practice(owner_email="ceo@acme.com"), None)["Email"] == "ceo@acme.com"


def test_title_from_owner_title():
    """The contact's role (Clay owner_title) is sent as `Title`."""
    fields = talentdb.build_fields(_practice(owner_title="Practice Manager"), _posting())
    assert fields["Title"] == "Practice Manager"
    # Omitted when Clay hasn't set it.
    assert "Title" not in talentdb.build_fields(_practice(owner_title=None), _posting())


def test_lead_role_from_lead():
    fields = talentdb.build_fields(_practice(), _posting(),
                                   _lead(lead_role="Decision_Maker"))
    assert fields["lead_role"] == "Decision_Maker"
    # No value → omitted.
    assert "lead_role" not in talentdb.build_fields(_practice(), _posting(), _lead())


def test_alternate_phone_is_second_distinct_number():
    fields = talentdb.build_fields(_practice(), _posting())       # owner + office
    assert fields["Phone"] == "+13120000000"
    assert fields["alternate_phone"] == "+13125550100"
    # Only one number → no alternate.
    fields = talentdb.build_fields(_practice(phone="+13120000000"), _posting())
    assert "alternate_phone" not in fields


def test_fields_with_no_source_are_omitted():
    """hiring_timeline / locations_count have no source → never sent."""
    fields = talentdb.build_fields(_practice(), _posting(), _lead())
    for absent in ("hiring_timeline", "locations_count"):
        assert absent not in fields


# --------------------------------------------------------------------------- #
# Omit-missing but keep falsy real values + native types
# --------------------------------------------------------------------------- #

def test_missing_values_omitted_but_falsy_reals_kept():
    fields = talentdb.build_fields(_practice(), _posting())
    assert "call_script" not in fields               # None → omitted
    assert fields["urgency_score"] == 0              # real 0 kept
    assert fields["board_remote"] is False           # real False kept
    assert fields["icp_breakdown"] == {}             # real empty dict kept
    assert fields["review_count"] == 212


def test_native_types_preserved_through_serialization():
    env = talentdb.build_envelope(_practice(), _posting())
    parsed = json.loads(talentdb._serialize(env))["fields"]
    assert parsed["board_remote"] is False
    assert parsed["match_confidence"] == 0.94
    assert parsed["urgency_score"] == 0


def test_json_string_columns_are_parsed():
    fields = talentdb.build_fields(_practice(), _posting())
    assert fields["sales_angles"] == ["angle one"]   # parsed from JSON string
    fields = talentdb.build_fields(_practice(icp_breakdown={"vertical": 9}), _posting())
    assert fields["icp_breakdown"] == {"vertical": 9}
    fields = talentdb.build_fields(_practice(sales_angles="not json"), _posting())
    assert "sales_angles" not in fields              # unparseable → omitted


def test_call_script_is_sent_as_raw_string():
    """call_script + email_draft go as raw strings (receiver's target shows them
    escaped), unlike icp_breakdown / sales_angles which are real JSON."""
    raw = '{"sections": [{"title": "Opening"}]}'
    fields = talentdb.build_fields(_practice(call_script=raw), _posting())
    assert fields["call_script"] == raw              # string, not parsed
    assert isinstance(fields["call_script"], str)


# --------------------------------------------------------------------------- #
# No-posting case
# --------------------------------------------------------------------------- #

def test_no_posting_omits_posting_fields():
    fields = talentdb.build_fields(_practice(), None)
    for key in ("posting_source", "posting_url", "role_title", "posted_at",
                "match_confidence", "match_status"):
        assert key not in fields
    assert fields["Company"] == "Acme Dental"


def test_no_practice_falls_back_to_employer_name():
    fields = talentdb.build_fields(None, _posting(employer_name="Board Co"))
    assert fields["Company"] == "Board Co"
    assert fields["LastName"] == "Board Co"          # falls back to company
    assert "FirstName" not in fields
    assert fields["posting_source"] == "indeed"


# --------------------------------------------------------------------------- #
# Envelope shape — objectType + operation + fields, no ids, no shim
# --------------------------------------------------------------------------- #

def test_envelope_is_objecttype_operation_fields_only():
    env = talentdb.build_envelope(_practice(), _posting())
    assert set(env) == {"objectType", "operation", "fields"}
    assert env["objectType"] == "Lead"
    assert env["operation"] == "upsert"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("no_email", [
    {"owner_email": None, "email": None},          # nothing at all
    {"owner_email": "Not Found", "email": None},   # placeholder scrubs to None
])
async def test_import_lead_skips_and_never_posts_when_no_email(no_email):
    fake = _FakeClient(_FakeResp({"ok": True, "status": "ok", "localEntityId": 1}))
    with patch("src.talentdb.settings") as s:
        s.talentdb_webhook_url = "https://x/api/salesforce/webhook"
        s.talentdb_webhook_secret = "topsecret"
        with patch("src.talentdb.httpx.AsyncClient", return_value=fake):
            result = await talentdb.import_lead(_practice(**no_email), _posting())
    assert result["ok"] is False
    assert result["status"] == "skipped_no_email"
    assert fake.sent == {}  # the guard fires BEFORE any POST
