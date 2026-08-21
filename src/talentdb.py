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
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.settings import settings

log = logging.getLogger("hvsi.talentdb")

# Every push response, appended as JSONL while we pin down the receiver's
# response shape (which field carries their record id, etc.). Local debugging
# aid — gitignored, like clay-webhook-captures.jsonl.
_RESPONSE_CAPTURE_PATH = (
    Path(__file__).resolve().parents[1] / "talentdb-response-captures.jsonl"
)


def _capture_response(http_status: int, body) -> None:
    """Append one response to the capture file. Never raises."""
    try:
        entry = {"ts": datetime.now(timezone.utc).isoformat(),
                 "http": http_status, "body": body}
        with open(_RESPONSE_CAPTURE_PATH, "a") as fp:
            fp.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log.warning("[talentdb.capture.error] %s: %s",
                    type(e).__name__, str(e)[:200])


def is_configured() -> bool:
    """True when both the webhook URL and signing secret are set."""
    return bool(settings.talentdb_webhook_url and settings.talentdb_webhook_secret)


# Job-board source -> `source` slug. Anything else (incl. no linked posting)
# falls back to the generic slug.
_SOURCE_SLUGS = {
    "indeed": "hv-sales-intel-indeed",
    "linkedin": "hv-sales-intel-linkedin",
}


def _source_slug(source: str | None) -> str:
    return _SOURCE_SLUGS.get(source or "", "hv-sales-intel")


# Our track name → the receiver's Tracks UUID code. `interested_tracks` sends
# the CODE (a label/made-up string stores but won't render as a tag). These are
# prod's current codes — if L&D re-creates a track its code changes, so pull
# fresh from the Tracks admin if a tag stops rendering.
_TRACK_CODES = {
    "Virtual Medical Scheduler": "45c76242-e585-11f0-831c-2eb420401434",
    "Virtual Medical Assistant": "dc6dec2a-e58f-11f0-a406-32c63f1d4ac3",
    "Virtual Medical Scribe": "c20ec098-2e91-11f1-bc1e-1adb42af3a6e",
    "Virtual Dental Assistant": "88bcb836-c0aa-11f0-a242-325255367c63",
    "Virtual Chiropractic Assistant": "01b24202-7fa4-11f1-be03-7e3910071e94",
    "Virtual Wellness and Hospitality Assistant": "4bb21e9c-e592-11f0-8817-32c63f1d4ac3",
    "Virtual Assisted Living Coordinator": "d7acd1ee-699f-11f1-b509-5e9236066227",
    "Virtual Legal Assistant": "21364a0a-4e31-11f1-a825-9e198b55c155",
    "Virtual Home Health Operations Coordinator": "7d69fef4-8395-11f1-ab36-7ed30a98e1a8",
}


def _track_code(track: str | None) -> str | None:
    """Our track name → the receiver's Tracks UUID; None if unmapped (omitted)."""
    return _TRACK_CODES.get((track or "").strip())


# Our track name → the industry it serves. Sent as `Industry` so the receiver
# can segment leads by vertical without re-deriving it from the track code.
_TRACK_INDUSTRY = {
    "Virtual Medical Assistant": "medical",
    "Virtual Medical Scheduler": "medical",
    "Virtual Medical Scribe": "medical",
    "Virtual Dental Assistant": "dental",
    "Virtual Chiropractic Assistant": "chiropractor",
    "Virtual Wellness and Hospitality Assistant": "spas",
    "Virtual Assisted Living Coordinator": "assisted_living",
    "Virtual Home Health Operations Coordinator": "home_health",
    "Virtual Legal Assistant": "legal",
}


def _track_industry(track: str | None) -> str | None:
    """Our track name → the industry it serves; None if unmapped (omitted)."""
    return _TRACK_INDUSTRY.get((track or "").strip())


# Placeholder emails the enrichment writes when it finds nothing — these are not
# real addresses and must be omitted rather than sent as the contact's Email.
_EMAIL_PLACEHOLDERS = {"not found", "notfound", "n/a", "na", "none", "null",
                       "unknown", "-", "--"}


def _scrub_email(value):
    """Return None for a placeholder email ("Not Found", …), else the value."""
    if isinstance(value, str) and value.strip().lower() in _EMAIL_PLACEHOLDERS:
        return None
    return value


def _text(value):
    """Trimmed string, or None for anything blank. Non-strings pass through."""
    if isinstance(value, str):
        return value.strip() or None
    return value


def _postable_email(practice: dict | None) -> str | None:
    """The contact email we'd send (owner_email, else email), scrubbed of
    placeholders. None means the lead has no real email — do NOT post it."""
    p = practice or {}
    return _scrub_email(p.get("owner_email") or p.get("email"))


def _org_size_bucket(value) -> str | None:
    """Integer headcount → the receiver's organization_size picklist bucket."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    n = int(value)
    if n <= 0:
        return None
    if n <= 10:
        return "2_10"
    if n <= 25:
        return "11_25"
    if n <= 50:
        return "25_50"
    if n <= 250:
        return "50_250"
    if n <= 500:
        return "250_500"
    return "500_plus"


def _phones(p: dict) -> tuple[str | None, str | None]:
    """(primary, alternate) phone — owner line, then office, then website line,
    deduped so the same number is never sent twice."""
    seen: list[str] = []
    for candidate in (p.get("owner_phone"), p.get("phone"), p.get("website_doctor_phone")):
        val = str(candidate).strip() if candidate is not None else ""
        if val and val not in seen:
            seen.append(val)
    return (seen[0] if seen else None), (seen[1] if len(seen) > 1 else None)


def _painpoints_text(value) -> str | None:
    """pain_points JSON array string → newline-joined textarea text."""
    parsed = _coerce_json(value)
    if isinstance(parsed, list):
        return "\n".join(str(x) for x in parsed if x) or None
    if isinstance(value, str):
        return value.strip() or None
    return None


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
    """Drop keys with no value. None and "" are "no value"; 0 / False / {}
    are real values we keep."""
    out = {}
    for k, v in fields.items():
        if v is None or v == "":
            continue
        out[k] = v
    return out


def _contact_person_fields(contact: dict, practice: dict, company) -> dict:
    """The person block for ONE contact — the per-contact fan-out's whole delta.

    Everything else in the envelope is a fact about the practice/posting and is
    identical across the N leads a practice's N contacts produce; these are the
    keys that differ. Deliberate choices, not oversights:

    * `Email` prefers the contact's **personal_email** and falls back to the
      work address; `work_email` prefers the work address and falls back to
      personal (user decision 2026-08-22). A one-email contact therefore fills
      BOTH keys with that address — the receiver's forms never show an empty
      email slot for a person we could reach.
    * `Phone` is the person's direct line and `alternate_phone` the practice's
      office line, deduped when Clay handed us the office number as the
      person's. The practice's `owner_phone` is NOT consulted: it is a mirror of
      *some* contact, and mixing it in here would put person A's number on
      person B's lead.
    * `LastName` keeps the company fallback the legacy path has — the receiver
      requires it, and a first-name-only contact must still post.
    """
    phone = _text(contact.get("phone"))
    office = _text(practice.get("phone"))
    personal = _scrub_email(_text(contact.get("personal_email")))
    work = _scrub_email(_text(contact.get("work_email")))
    return {
        "FirstName": _text(contact.get("first_name")),
        "LastName": _text(contact.get("last_name")) or company,
        "Title": _text(contact.get("title")),
        "Email": personal or work,
        "work_email": work or personal,
        "linkedin_url": _text(contact.get("linkedin_url")),
        "Phone": phone,
        "alternate_phone": office if office and office != phone else None,
    }


def _td_lead_id_from_response(data: dict) -> str | None:
    """Talent-DB's own record id for the Lead this response just created.

    Carried in the response's `td_lead_id` field (NOT `localEntityId` — a
    different identifier, even when the numbers happen to match). Coerced to
    text since the receiver mints numeric ids; absent or blank → None, so
    nothing is stored and `td_lead_id` stays out of every payload.
    `import_lead` puts this on its result, `talentdb_push` stores it on the
    (lead, contact) marker row, and a later post of the same pair sends it
    back so the receiver updates instead of duplicating. Mirror:
    scripts/talentdb_export.py::_td_lead_id_from_response — keep in sync.
    """
    value = data.get("td_lead_id")
    if value is None:
        return None
    return _text(str(value))


def build_fields(
    practice: dict | None,
    posting: dict | None,
    lead: dict | None = None,
    contact: dict | None = None,
    td_lead_id: str | None = None,
) -> dict:
    """Map a practice (+ its linked posting + lead) onto the Talent-DB `fields`.

    Keys are the receiver's exact accepted field API names (its schema: a mix of
    PascalCase core fields and snake_case posting/scoring fields). NOT sent:
    `hiring_timeline` / `locations_count` (no source in our system). Any of the
    args may be None; missing values are omitted.

    `contact` is one `practice_contacts` row and turns this into the per-contact
    fan-out: the person block comes from that person instead of the practice's
    flat `owner_*` mirror (see `_contact_person_fields`), and everything else is
    untouched, so N contacts produce N identical-but-for-the-person leads. With
    no `contact` the mapping is exactly what it always was — that is the path a
    practice with zero contact rows still takes.

    `td_lead_id` is Talent-DB's own record id for a (lead, contact) pair we have
    posted before. Sending it back is what turns a re-post into an update on
    their side instead of a second record; we have no id for a pair we have
    never sent, and the key is then omitted entirely rather than sent empty.
    """
    p = practice or {}
    pg = posting or {}
    ld = lead or {}
    source = pg.get("source")
    company = p.get("name") or pg.get("employer_name")
    first_name, last_name = _split_owner_name(p.get("owner_name"))
    phone_primary, phone_alt = _phones(p)
    # Track = what the lead IS. The lead's `service_line` is the deterministic
    # posting→specialty track (track_resolver, resolved at qualify time), and it
    # wins. The `service_line_hint` (how we FOUND it — the search term) is only a
    # null-safety fallback; it is NOT what we sell, and shipping it is what put
    # chiro leads under the Dental track. (ADR 2026-08-19-deterministic-track-resolver.)
    track = ld.get("service_line") or pg.get("service_line_hint")
    track_code = _track_code(track)
    pid = p.get("id")

    fields = {
        # Our practice id — the receiver's stable link back to our record.
        "source_practice_id": str(pid) if pid is not None else None,
        # THEIR id for this exact (lead, contact) pair, when we have posted it
        # before — the upsert key we otherwise lack. Omitted when we have none.
        "td_lead_id": td_lead_id or None,

        # --- Contact + company (PascalCase) ---
        "Company": company,                         # required
        "LastName": last_name or company,           # falls back to company
        "FirstName": first_name,                    # from owner_name; omit if none
        "Title": p.get("owner_title"),              # contact's role (Clay enrichment)
        "Email": _postable_email(p),
        # work_email / linkedin_url are the per-contact fan-out's keys. Declared
        # here, as None, purely to hold their place in the field order (and in
        # CSV_COLUMNS) — the legacy path omits them, `contact` fills them in.
        "work_email": None,
        "Phone": phone_primary,
        "alternate_phone": phone_alt,
        "Country": "USA",                           # ISO alpha-3, hardcoded for now
        "City": p.get("city") or pg.get("city"),
        "State": p.get("state") or pg.get("state"),
        "Website": p.get("website"),
        "linkedin_url": None,

        # --- Classification ---
        "Industry": _track_industry(track),          # industry the track serves
        "interested_tracks": [track_code] if track_code else None,   # Tracks UUID(s)
        "organization_size": _org_size_bucket(p.get("organization_size")),  # bucket
        # hiring_timeline / locations_count: no source in our system → omitted.
        "No_of_Providers__c": ld.get("provider_count"),
        "Lead_Type__c": "Outbound",                 # picklist Inbound | Outbound
        "source": _source_slug(source),             # hv-sales-intel-{indeed|linkedin}
        "lead_role": ld.get("lead_role"),           # Company Spokesperson's Role picklist
        "practice_notes": p.get("notes"),
        "pain_points": _painpoints_text(p.get("pain_points")),

        # --- Posting (snake_case) ---
        "role_title": pg.get("title"),
        "posting_source": source,                   # raw indeed | linkedin
        "posting_url": pg.get("url"),
        "posted_at": pg.get("posted_at"),
        "board_remote": pg.get("board_remote_flag"),
        "posting_description": pg.get("description"),
        "search_term": pg.get("search_term"),
        "search_location": pg.get("search_location"),
        "first_seen_at": pg.get("first_seen_at"),
        "last_seen_at": pg.get("last_seen_at"),
        "match_confidence": pg.get("match_confidence"),
        "match_status": pg.get("match_status"),

        # --- Scoring / analysis (snake_case) ---
        "urgency_score": p.get("urgency_score"),
        "hiring_signal_score": p.get("hiring_signal_score"),
        "icp_tier": p.get("icp_tier"),
        "icp_breakdown": _coerce_json(p.get("icp_breakdown")),
        "category": p.get("category"),
        "review_count": p.get("review_count"),
        "opening_hours": p.get("opening_hours"),
        "summary": p.get("summary"),
        "sales_angles": _coerce_json(p.get("sales_angles")),
        # call_script + email_draft go as raw strings (the receiver's target
        # shows them escaped, unlike icp_breakdown / sales_angles which are JSON).
        "call_script": p.get("call_script"),
        "email_draft": p.get("email_draft"),
    }
    # Per-contact fan-out: this person replaces the owner_* person block whole.
    # An override rather than a branch inside the literal above, so the legacy
    # mapping stays one readable block and the delta is one readable block.
    if contact:
        fields.update(_contact_person_fields(contact, p, company))
    return _omit_missing(fields)


# Canonical column order for the signals CSV export — the exact `fields` keys the
# webhook sends, so an exported CSV round-trips into a Talent-DB CSV import.
CSV_COLUMNS = [
    "source_practice_id", "td_lead_id",
    # Contact + company
    "Company", "LastName", "FirstName", "Title", "Email", "work_email", "Phone",
    "alternate_phone", "Country", "City", "State", "Website", "linkedin_url",
    # Classification
    "Industry", "interested_tracks", "organization_size", "No_of_Providers__c",
    "Lead_Type__c", "source", "lead_role", "practice_notes", "pain_points",
    # Posting
    "role_title", "posting_source", "posting_url", "posted_at",
    "board_remote", "posting_description", "search_term", "search_location",
    "first_seen_at", "last_seen_at", "match_confidence", "match_status",
    # Scoring / analysis
    "urgency_score", "hiring_signal_score", "icp_tier", "icp_breakdown",
    "category", "review_count", "opening_hours", "summary", "sales_angles",
    "call_script", "email_draft",
]


def build_envelope(
    practice: dict | None,
    posting: dict | None,
    lead: dict | None = None,
    contact: dict | None = None,
    td_lead_id: str | None = None,
) -> dict:
    """The full request body: `objectType` + `operation` + `fields`.

    The app-origin webhook (`/api/sales-intel/webhook`) mints its own record, so
    it takes no `salesforceId`, `salesforceUpdatedAt`, or `eventId` — we send
    none of them. Dedup is handled on our side via the export markers.
    """
    return {
        "objectType": "Lead",
        "operation": "upsert",
        "fields": build_fields(practice, posting, lead, contact, td_lead_id),
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


async def import_lead(
    practice: dict | None,
    posting: dict | None,
    lead: dict | None = None,
    contact: dict | None = None,
    td_lead_id: str | None = None,
) -> dict:
    """POST one signed Lead to Talent-DB. Fail-soft: never raises.

    Returns a normalized dict: {ok, status, message, local_entity_id,
    td_lead_id, http_status}. `ok` is True only when the receiver accepted the
    record; callers use it to decide whether to set the export marker.

    `contact` sends this person's lead instead of the practice's `owner_*` one.
    One POST per call either way — the fan-out over a practice's contacts lives
    in `src/talentdb_push.py`, which calls this once per person.

    `td_lead_id` in / `td_lead_id` out are the two halves of the same loop: pass
    the id we stored for this (lead, contact) pair and the receiver updates that
    record; the id read off the response (`_td_lead_id_from_response`) is what
    the caller stores for next time.
    """
    if not is_configured():
        log.warning("[talentdb.skip] not_configured url=%s secret=%s",
                    bool(settings.talentdb_webhook_url),
                    bool(settings.talentdb_webhook_secret))
        return {"ok": False, "status": "not_configured",
                "message": "Talent-DB webhook is not configured."}

    # Email guard: a lead with no real contact email is not actionable for sales —
    # don't post it. ok=False so the caller does NOT set the export marker; it can
    # be sent later once an email lands. (Mirror of scripts/talentdb_export.py.)
    #
    # On the contact path the question is about THAT person, not the practice: a
    # practice with a good owner_email still must not post a contact we have no
    # way to reach. Either address counts — `Email` ships the personal one and
    # `work_email` the work one, so a contact with only one of them is postable.
    if contact:
        if not (_scrub_email(_text(contact.get("personal_email")))
                or _scrub_email(_text(contact.get("work_email")))):
            log.info("[talentdb.skip] no_email company=%r contact=%s",
                     (practice or {}).get("name"), contact.get("id"))
            return {"ok": False, "status": "skipped_no_email",
                    "message": "Contact has no email — not posted."}
        # Phone gate (user decision 2026-08-22): a contact the SDRs cannot
        # call is not posted as a lead. The practice office line does not
        # count here — this is about the person's own number.
        if not str(contact.get("phone") or "").strip():
            log.info("[talentdb.skip] no_phone company=%r contact=%s",
                     (practice or {}).get("name"), contact.get("id"))
            return {"ok": False, "status": "skipped_no_phone",
                    "message": "Contact has no phone — not posted."}
    elif not _postable_email(practice):
        log.info("[talentdb.skip] no_email company=%r",
                 (practice or {}).get("name"))
        return {"ok": False, "status": "skipped_no_email",
                "message": "No contact email — not posted."}

    envelope = build_envelope(practice, posting, lead, contact, td_lead_id)
    raw = _serialize(envelope)
    headers = {
        "Content-Type": "application/json",
        "X-HV-Signature": _sign(raw),
    }
    log.info("[talentdb.request] company=%r contact=%s fields=%d bytes=%d",
             envelope["fields"].get("Company"), (contact or {}).get("id"),
             len(envelope["fields"]), len(raw))

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
    _capture_response(resp.status_code, data if data else (resp.text or "")[:2000])
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
        log.info("[talentdb.response] http=%s ok=%s status=%s body=%s",
                 resp.status_code, ok, data.get("status"),
                 (resp.text or "")[:500])

    return {
        "ok": ok,
        "status": data.get("status") or ("ok" if resp.is_success else "error"),
        "message": message,
        "local_entity_id": data.get("localEntityId"),
        # Their id for this record, to store on the (lead, contact) marker and
        # send back next time. None until the source field is named — see
        # `_td_lead_id_from_response`.
        "td_lead_id": _td_lead_id_from_response(data),
        "http_status": resp.status_code,
    }
