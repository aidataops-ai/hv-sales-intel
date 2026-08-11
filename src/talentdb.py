"""Talent-DB inbound Lead webhook — the "Import Lead" push.

One-way, fire-and-forget: we map a practice (+ its linked job posting) into a
Salesforce-style Lead envelope, HMAC-sign the exact bytes, and POST them to the
Talent-DB webhook. See docs/specs/2026-08-11-talentdb-lead-webhook-design.md.

Contract shape (only what we send — the receiver accepts more):

    { "objectType": "Lead", "operation": "upsert", "fields": { ... } }

We deliberately omit `eventId` / `salesforceId` / `salesforceUpdatedAt`; dedup
is done on our side (see lead_store.mark_lead_exported), not via an upsert key.
Keys we have no value for are omitted rather than sent as ""/null. Native JSON
types are preserved (numbers, bools, [], {}).
"""

import hashlib
import hmac
import json
import logging

import httpx

from src.settings import settings

log = logging.getLogger("hvsi.talentdb")


def is_configured() -> bool:
    """True when both the webhook URL and signing secret are set."""
    return bool(settings.talentdb_webhook_url and settings.talentdb_webhook_secret)


# Job-board source -> Lead_Type__c slug. Anything else (including no linked
# posting) falls back to the generic slug.
_SOURCE_SLUGS = {
    "indeed": "hv-sales-intel-indeed",
    "linkedin": "hv-sales-intel-linkedin",
}


def _lead_type_slug(source: str | None) -> str:
    return _SOURCE_SLUGS.get(source or "", "hv-sales-intel")


def _coerce_json(value):
    """Return real JSON for a column that may be stored as a JSON string.

    Dicts/lists/numbers/bools/None pass through; a JSON string is parsed; an
    unparseable string becomes None (dropped by _omit_missing).
    """
    if value is None or isinstance(value, (list, dict, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _split_owner_name(owner_name: str | None) -> tuple[str | None, str | None]:
    """Split the enriched contact's full name into (FirstName, LastName).

    Last whitespace token is the last name, everything before it the first name.
    A single token becomes the last name (no first name). Empty → (None, None),
    so both keys are omitted — there is no company-name fallback.
    """
    name = (owner_name or "").strip()
    if not name:
        return None, None
    if " " in name:
        first, last = name.rsplit(" ", 1)
        return (first.strip() or None), (last.strip() or None)
    return None, name


def _omit_missing(fields: dict) -> dict:
    """Drop keys with no value. None and "" are "no value"; 0 / False / [] / {}
    are real values we keep (they carry meaning in the sample)."""
    out = {}
    for k, v in fields.items():
        if v is None or v == "":
            continue
        out[k] = v
    return out


def build_fields(practice: dict | None, posting: dict | None) -> dict:
    """Map a practice (+ its linked posting) onto the Talent-DB `fields` object.

    Either argument may be None: signals leads without a bank practice fall back
    to the posting's employer; practices with no linked posting send no
    `posting_*`. Missing values are omitted.
    """
    p = practice or {}
    pg = posting or {}
    source = pg.get("source")
    company = p.get("name") or pg.get("employer_name")
    pid = p.get("id")
    first_name, last_name = _split_owner_name(p.get("owner_name"))

    fields = {
        # Core (Salesforce-aliased)
        "Company": company,
        "FirstName": first_name,          # from owner_name; omitted when none
        # LastName is required by the receiver, so it falls back to the company
        # name when there is no enriched owner_name.
        "LastName": last_name or company,
        "Email": p.get("owner_email") or p.get("email"),
        "Phone": p.get("owner_phone") or p.get("phone"),
        "Website": p.get("website"),
        "City": p.get("city") or pg.get("city"),
        "State": p.get("state") or pg.get("state"),
        "Rating": p.get("rating"),
        "Status": "New",
        "Lead_Type__c": _lead_type_slug(source),   # slug; always present

        # Linked job posting (raw DB values)
        "posting_source": source,
        "posting_url": pg.get("url"),
        "role_title": pg.get("title"),
        "posted_at": pg.get("posted_at"),
        "board_remote": pg.get("board_remote_flag"),
        "posting_description": pg.get("description"),
        "search_term": pg.get("search_term"),
        "search_location": pg.get("search_location"),
        "first_seen_at": pg.get("first_seen_at"),
        "last_seen_at": pg.get("last_seen_at"),
        "source_practice_id": str(pid) if pid is not None else None,
        "match_confidence": pg.get("match_confidence"),
        "match_status": pg.get("match_status"),
        "matched_at": pg.get("matched_at"),

        # Practice scoring / meta / CRM
        "urgency_score": p.get("urgency_score"),
        "hiring_signal_score": p.get("hiring_signal_score"),
        "icp_tier": p.get("icp_tier"),
        "icp_breakdown": _coerce_json(p.get("icp_breakdown")),
        "enrichment_status": p.get("enrichment_status"),
        "lat": p.get("lat"),
        "lng": p.get("lng"),
        "opening_hours": p.get("opening_hours"),
        "category": p.get("category"),
        "review_count": p.get("review_count"),
        "call_script": _coerce_json(p.get("call_script")),
        "email_draft": p.get("email_draft"),
        "email_draft_updated_at": p.get("email_draft_updated_at"),
        "tags": p.get("tags"),
        "source_assigned_at": p.get("assigned_at"),
        "source_assigned_by": p.get("assigned_by"),
        "last_touched_by": p.get("last_touched_by"),
        "last_touched_at": p.get("last_touched_at"),
        "export_count": p.get("export_count"),
        "last_exported_at": p.get("last_exported_at"),
        "last_exported_by": p.get("last_exported_by"),
        "salesforce_owner_id": p.get("salesforce_owner_id"),
        "salesforce_owner_name": p.get("salesforce_owner_name"),
        "salesforce_lead_url": p.get("salesforce_lead_url"),
        "summary": p.get("summary"),
        "sales_angles": _coerce_json(p.get("sales_angles")),
        "website_contacts": _coerce_json(p.get("website_contacts")),
    }
    return _omit_missing(fields)


# Canonical column order for the signals CSV export — the `fields` keys the
# webhook sends (envelope ids are NOT included in the CSV), in build_fields order.
CSV_COLUMNS = [
    # Core (Salesforce-aliased)
    "Company", "LastName", "Email", "FirstName", "Phone", "Website",
    "City", "State", "Rating", "Status", "Lead_Type__c",
    # Linked job posting
    "posting_source", "posting_url", "role_title", "posted_at", "board_remote",
    "posting_description", "search_term", "search_location", "first_seen_at",
    "last_seen_at", "source_practice_id", "match_confidence", "match_status",
    "matched_at",
    # Practice scoring / meta / CRM
    "urgency_score", "hiring_signal_score", "icp_tier", "icp_breakdown",
    "enrichment_status", "lat", "lng", "opening_hours", "category",
    "review_count", "call_script", "email_draft", "email_draft_updated_at",
    "tags", "source_assigned_at", "source_assigned_by", "last_touched_by",
    "last_touched_at", "export_count", "last_exported_at", "last_exported_by",
    "salesforce_owner_id", "salesforce_owner_name", "salesforce_lead_url",
    "summary", "sales_angles", "website_contacts",
]


def build_envelope(practice: dict | None, posting: dict | None) -> dict:
    """The full request body: `objectType` + `operation` + `fields`.

    The app-origin webhook (`/api/sales-intel/webhook`) mints its own record, so
    it takes no `salesforceId`, `salesforceUpdatedAt`, or `eventId` — we send
    none of them. Dedup is handled on our side via the export marker.
    """
    return {
        "objectType": "Lead",
        "operation": "upsert",
        "fields": build_fields(practice, posting),
    }


def _serialize(envelope: dict) -> bytes:
    """Serialize once to the exact bytes we both sign and send."""
    return json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")


def _sign(raw: bytes) -> str:
    """HMAC-SHA256 over the raw body, hex, prefixed `sha256=`."""
    digest = hmac.new(
        settings.talentdb_webhook_secret.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


async def import_lead(practice: dict | None, posting: dict | None) -> dict:
    """POST one signed Lead to Talent-DB. Fail-soft: never raises.

    Returns a normalized dict: {ok, status, message, local_entity_id,
    http_status}. `ok` is True only when the receiver accepted the record;
    callers use it to decide whether to set the export marker.
    """
    if not is_configured():
        log.warning("[talentdb.skip] not_configured url=%s secret=%s",
                    bool(settings.talentdb_webhook_url),
                    bool(settings.talentdb_webhook_secret))
        return {"ok": False, "status": "not_configured",
                "message": "Talent-DB webhook is not configured."}

    envelope = build_envelope(practice, posting)
    raw = _serialize(envelope)
    headers = {
        "Content-Type": "application/json",
        "X-HV-Signature": _sign(raw),
    }
    log.info("[talentdb.request] company=%r fields=%d bytes=%d",
             envelope["fields"].get("Company"), len(envelope["fields"]), len(raw))

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            # Send the SAME bytes we signed — not a re-serialized dict.
            resp = await client.post(
                settings.talentdb_webhook_url, headers=headers, content=raw
            )
        except httpx.HTTPError as e:
            log.error("[talentdb.network_error] err=%r", e)
            return {"ok": False, "status": "network_error", "message": str(e)}

    try:
        data = resp.json()
    except Exception:
        data = {}
    ok = bool(data.get("ok"))
    # Surface the receiver's complaint on any non-success so the warning is
    # actionable (schema validation errors, missing fields, etc.).
    message = data.get("message")
    if not ok and not message:
        message = (resp.text or "").strip()[:500] or None
    if not ok:
        log.warning("[talentdb.response] http=%s ok=%s status=%s body=%s",
                    resp.status_code, data.get("ok"), data.get("status"),
                    (resp.text or "")[:800])
    else:
        log.info("[talentdb.response] http=%s ok=%s status=%s",
                 resp.status_code, ok, data.get("status"))

    return {
        "ok": ok,
        "status": data.get("status") or ("ok" if resp.is_success else "error"),
        "message": message,
        "local_entity_id": data.get("localEntityId"),
        "http_status": resp.status_code,
    }
