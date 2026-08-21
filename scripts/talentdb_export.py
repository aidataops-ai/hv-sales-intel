"""Standalone Talent-DB exporter — the ENTIRE webhook mapping + signing + POST
written out inline, so this one file is the whole truth of what gets sent.

This is a from-scratch replica of the production "Import Lead" webhook push. The
field mapping, Tracks UUID lookup, HMAC signing and request body below are copied
faithfully from the prod path (src/talentdb.py) and reproduced here verbatim, so
you can read exactly what leaves the building without chasing it through modules.
It does NOT import any of the other export scripts.

Contract (only what we send — the receiver accepts more):

    { "objectType": "Lead", "operation": "upsert", "fields": { ... } }

The only id the receiver gets is `source_practice_id` (our practices.id). We omit
salesforceId/eventId, so the receiver mints a fresh record and returns its own
`localEntityId` (captured in the log). Dedup is on OUR side via the
`talentdb_exported_at` marker.

Input: a leadset JSON — an array of rows each carrying `"id"` (a job_postings.id).
Defaults to `leadset-talentdb-push.json`. For every posting it resolves the full
record from Supabase (posting + its linked practice + the (company,posting) lead),
maps it, signs it, and POSTs it.

BILLING GUARD (on by default): any lead whose posting was sourced via a billing
keyword (search_term contains "billing") is SKIPPED and never exported. Override
with --allow-billing.

PER-CONTACT FAN-OUT: a practice with N `practice_contacts` rows becomes N
Talent-DB leads — same company/posting/scoring envelope, a different person
mapped into FirstName/LastName/Title/Email/work_email/linkedin_url/Phone each
time. A practice with no contact rows (or none we can reach) sends NOTHING:
the legacy single lead built from `practices.owner_*` is retired (user
decision 2026-08-22) — pre-contact-era practices are already being worked by
the sales team, and re-posting their owner_* mirror would hand the same
person out twice.

EMAIL GUARD (always on): per contact — a person with neither a work nor a
personal email (missing or a placeholder like "Not Found") is not posted; a
person without a direct phone is not posted either (phone gate, 2026-08-22).

Safety: DRY RUN by default (prints the first envelope, sends nothing); live needs
--yes. Refuses a STAGING destination unless --allow-staging. Already-exported
leads are skipped (resumable) unless --resend; each success stamps
`talentdb_exported_at` unless --no-mark.

TWO MARKERS, because a fan-out can half-succeed. `talentdb_exported_at` on the
lead is stamped only when EVERY eligible contact was accepted, so a lead with a
failed contact stays in the universe and gets retried; each accepted contact is
recorded in `talentdb_contact_exports (lead_id, contact_id)`, and the retry
skips those people so it posts only what is missing. Each such row also carries
`td_lead_id` — Talent-DB's own record id for that (lead, contact) pair, sent
back whenever the pair is posted again so their side updates instead of minting
a second record. Nothing writes a non-NULL id yet: the extraction point,
`_td_lead_id_from_response` below, returns None until the response field that
carries the id is named. That also makes `--resend`
the LATE-ARRIVING-CONTACT tool: it re-enters a finished lead but still skips
everyone already sent, so only the new person goes out.

Usage:
    .venv/bin/python -m scripts.talentdb_export                       # dry run
    .venv/bin/python -m scripts.talentdb_export --yes                 # send the set live
    .venv/bin/python -m scripts.talentdb_export --yes --limit 5       # live smoke test
    .venv/bin/python -m scripts.talentdb_export --leadset my-set.json --yes
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

# Only shared code we use is for reading OUR OWN rows out of Supabase (the
# practice's contacts and the per-contact export markers included). The
# Talent-DB-facing mapping/signing/POST below is all inline.
from src import contacts as contact_store
from src.settings import settings
from src.storage import _get_client, get_practice


# ===========================================================================
# THE MAPPING — verbatim replica of the prod webhook (src/talentdb.py).
# Everything the receiver sees is produced by the functions in this section.
# ===========================================================================

# Job-board source -> `source` slug. Anything else falls back to the generic slug.
_SOURCE_SLUGS = {
    "indeed": "hv-sales-intel-indeed",
    "linkedin": "hv-sales-intel-linkedin",
}


def _source_slug(source: str | None) -> str:
    return _SOURCE_SLUGS.get(source or "", "hv-sales-intel")


# Our track name → the receiver's Tracks UUID code. `interested_tracks` sends the
# CODE. These are prod's current codes — if L&D re-creates a track its code
# changes, so pull fresh from the Tracks admin if a tag stops rendering.
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
    return _TRACK_CODES.get((track or "").strip())


# The industry each track serves (Afnan's Track → Industry column). Sent as the
# `Industry` field, derived from whatever track/interested_tracks we resolve.
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
    return _TRACK_INDUSTRY.get((track or "").strip())


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


def _coerce_json(value):
    """Return real JSON for a column that may be stored as a JSON string."""
    if value is None or isinstance(value, (list, dict, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _painpoints_text(value) -> str | None:
    """pain_points JSON array string → newline-joined textarea text."""
    parsed = _coerce_json(value)
    if isinstance(parsed, list):
        return "\n".join(str(x) for x in parsed if x) or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _split_owner_name(owner_name: str | None) -> tuple[str | None, str | None]:
    """Split the enriched contact's full name into (FirstName, LastName)."""
    name = (owner_name or "").strip()
    if not name:
        return None, None
    if " " in name:
        first, last = name.rsplit(" ", 1)
        return (first.strip() or None), (last.strip() or None)
    return None, name


# Placeholder email values that mean "no email" — scrubbed on the Email field
# only (see build_fields), so "Not Found" is omitted rather than sent as data.
_EMAIL_PLACEHOLDERS = {"not found", "notfound", "n/a", "na", "none", "null", "unknown", "-", "--"}


def _scrub_email(value):
    """Return None for a placeholder email ("Not Found", …), else the value."""
    if isinstance(value, str) and value.strip().lower() in _EMAIL_PLACEHOLDERS:
        return None
    return value


def _postable_email(practice: dict | None) -> str | None:
    """The contact email we'd send (owner_email, else email), scrubbed of
    placeholders. None means the lead has no real email — do NOT post it."""
    p = practice or {}
    return _scrub_email(p.get("owner_email") or p.get("email"))


def _text(value):
    """Trimmed string, or None for anything blank. Non-strings pass through."""
    if isinstance(value, str):
        return value.strip() or None
    return value


def _td_lead_id_from_response(data: dict) -> str | None:
    """Talent-DB's own record id for the Lead this response just created.

    Carried in the response's `td_lead_id` field (NOT `localEntityId`).
    Coerced to text; absent or blank → None (nothing stored, `td_lead_id`
    omitted from payloads). Mirror of
    src/talentdb.py::_td_lead_id_from_response — keep in sync.
    """
    value = data.get("td_lead_id")
    if value is None:
        return None
    return _text(str(value))


def _contact_email(contact: dict | None) -> str | None:
    """Any real address on this contact — the per-person analogue of
    `_postable_email`. None means we cannot reach them: do NOT post them."""
    c = contact or {}
    return (_scrub_email(_text(c.get("work_email")))
            or _scrub_email(_text(c.get("personal_email"))))


def _contact_person_fields(contact: dict, practice: dict, company) -> dict:
    """The person block for ONE contact — the fan-out's whole delta.

    Everything else in the envelope is a fact about the practice/posting and is
    identical across the N leads a practice's N contacts produce. `Email`
    prefers the PERSONAL address and falls back to work; `work_email` prefers
    work and falls back to personal (user decision 2026-08-22) — a one-email
    contact fills both keys. `Phone` is
    the person's direct line and `alternate_phone` the practice's office line,
    deduped. `practices.owner_phone` is deliberately not consulted here: it is a
    mirror of *some* contact, and mixing it in would put person A's number on
    person B's lead. (Mirror of src/talentdb.py::_contact_person_fields; keep in
    sync.)
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


def _omit_missing(fields: dict) -> dict:
    """Drop keys with no value. None and "" are "no value"; 0 / False / {} stay."""
    return {k: v for k, v in fields.items() if v is not None and v != ""}


def build_fields(practice: dict | None, posting: dict | None, lead: dict | None = None,
                 contact: dict | None = None, td_lead_id: str | None = None) -> dict:
    """Map a practice (+ its linked posting + lead) onto the Talent-DB `fields`.

    With a `contact` (one `practice_contacts` row) the person block comes from
    that person instead of the practice's flat `owner_*` mirror, so N contacts
    produce N identical-but-for-the-person leads. With no `contact` the mapping
    is byte-identical to what it always was.

    `td_lead_id` is Talent-DB's own record id for a (lead, contact) pair we have
    posted before; sending it back turns a re-post into an update on their side.
    Omitted entirely when we have none.
    """
    p = practice or {}
    pg = posting or {}
    ld = lead or {}
    source = pg.get("source")
    company = p.get("name") or pg.get("employer_name")
    first_name, last_name = _split_owner_name(p.get("owner_name"))
    phone_primary, phone_alt = _phones(p)
    # Track = the lead's resolved service_line (deterministic posting→specialty
    # track, track_resolver) — what the lead IS. The search-term hint (how we
    # FOUND it) is only a null-safety fallback. (Mirror of src/talentdb.py; keep
    # in sync. ADR 2026-08-19-deterministic-track-resolver.)
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
        "Title": p.get("owner_title"),
        "Email": _postable_email(p),
        # work_email / linkedin_url are the per-contact fan-out's keys. Declared
        # here, as None, purely to hold their place in the field order — the
        # legacy path omits them, `contact` fills them in.
        "work_email": None,
        "Phone": phone_primary,
        "alternate_phone": phone_alt,
        "Country": "USA",                           # ISO alpha-3, hardcoded for now
        "City": p.get("city") or pg.get("city"),
        "State": p.get("state") or pg.get("state"),
        "Website": p.get("website"),
        "linkedin_url": None,

        # --- Classification ---
        "interested_tracks": [track_code] if track_code else None,   # Tracks UUID(s)
        "Industry": _track_industry(track),          # industry the track serves
        "organization_size": _org_size_bucket(p.get("organization_size")),
        "No_of_Providers__c": ld.get("provider_count"),
        "Lead_Type__c": "Outbound",                 # picklist Inbound | Outbound
        "source": _source_slug(source),             # hv-sales-intel-{indeed|linkedin}
        "lead_role": ld.get("lead_role"),
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
        # call_script + email_draft go as raw strings (receiver shows them escaped).
        "call_script": p.get("call_script"),
        "email_draft": p.get("email_draft"),
    }
    # Per-contact fan-out: this person replaces the owner_* person block whole.
    if contact:
        fields.update(_contact_person_fields(contact, p, company))
    return _omit_missing(fields)


def build_envelope(practice: dict | None, posting: dict | None, lead: dict | None = None,
                   contact: dict | None = None, td_lead_id: str | None = None) -> dict:
    """The full request body: objectType + operation + fields."""
    return {
        "objectType": "Lead",
        "operation": "upsert",
        "fields": build_fields(practice, posting, lead, contact, td_lead_id),
    }


def _serialize(envelope: dict) -> bytes:
    """Serialize once to the exact bytes we both sign and send."""
    return json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")


def _sign(raw: bytes, secret: str) -> str:
    """HMAC-SHA256 over the raw body, hex, prefixed sha256=."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


async def post_lead(practice, posting, lead, *, url: str, secret: str,
                    contact: dict | None = None, td_lead_id: str | None = None) -> dict:
    """Sign the exact bytes and POST them. Returns a normalized result dict.

    One POST per call. `contact` sends that person's lead; the fan-out over a
    practice's contacts is the send loop in `run()`. `td_lead_id` in / out are
    the two halves of one loop: pass the id stored for this (lead, contact) pair
    so the receiver updates that record, and store the id off the response for
    next time.
    """
    envelope = build_envelope(practice, posting, lead, contact, td_lead_id)
    raw = _serialize(envelope)
    headers = {"Content-Type": "application/json", "X-HV-Signature": _sign(raw, secret)}
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(url, headers=headers, content=raw)  # SAME bytes we signed
        except httpx.HTTPError as e:
            return {"ok": False, "status": "network_error", "message": str(e),
                    "local_entity_id": None, "td_lead_id": None, "http_status": None}
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {}
    ok = bool(data.get("ok"))
    message = data.get("message") or (None if ok else (resp.text or "").strip()[:500] or None)
    return {"ok": ok, "status": data.get("status") or ("ok" if resp.is_success else "error"),
            "message": message, "local_entity_id": data.get("localEntityId"),
            "td_lead_id": _td_lead_id_from_response(data),
            "http_status": resp.status_code, "fields": len(envelope["fields"])}


# ===========================================================================
# RESOLVE — pull the full record for one posting straight from Supabase,
# exactly the way the prod Import-Lead endpoint does.
# ===========================================================================


# Columns on company_job_leads the mapping actually reads (verified vs schema).
_LEAD_COLS = "id, posting_id, service_line, provider_count, talentdb_exported_at, decision"


def _resolve(company_id: str, posting_id: int):
    """(practice, posting, lead) for one posting — read straight from Supabase.

    posting : the job_postings row (all columns).
    lead    : the (company, posting) company_job_leads row — carries service_line /
              provider_count / the export marker; None if never qualified here.
    practice: the linked practices row via posting.practice_id → place_id.
    """
    client = _get_client()
    posting = (client.table("job_postings").select("*")
               .eq("id", posting_id).maybe_single().execute()).data
    if not posting:
        return None, None, None
    lead = (client.table("company_job_leads").select(_LEAD_COLS)
            .eq("company_id", company_id).eq("posting_id", posting_id)
            .maybe_single().execute()).data
    practice = None
    if posting.get("practice_id"):
        pr = (client.table("practices").select("place_id")
              .eq("id", posting["practice_id"]).maybe_single().execute()).data
        place_id = (pr or {}).get("place_id")
        if place_id:
            practice = get_practice(place_id)
    return practice, posting, lead


def _resolve_retry(company_id: str, posting_id: int, tries: int = 3):
    """_resolve with retry — Supabase reads can time out transiently. Re-raises
    after `tries` so the caller can fail-soft that one lead."""
    delay = 1.0
    for attempt in range(1, tries + 1):
        try:
            return _resolve(company_id, posting_id)
        except Exception:  # noqa: BLE001
            if attempt == tries:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 8.0)


def _mark_exported(company_id: str, lead_id: int) -> None:
    """Stamp talentdb_exported_at so this lead isn't re-sent. Fail-soft."""
    try:
        (_get_client().table("company_job_leads")
         .update({"talentdb_exported_at": datetime.now(timezone.utc).isoformat()})
         .eq("company_id", company_id).eq("id", lead_id).execute())
    except Exception as e:  # noqa: BLE001
        print(f"[export] WARN could not mark lead {lead_id} exported: {e}")


def _is_billing(posting: dict | None) -> bool:
    return "billing" in ((posting or {}).get("search_term") or "").lower()


def _who(contact: dict | None) -> str:
    """Short label for the person one POST is aimed at, for the streamed log."""
    if not contact:
        return "owner_* mirror"
    name = " ".join(x for x in (_text(contact.get("first_name")),
                                _text(contact.get("last_name"))) if x)
    return (f"contact#{contact.get('id')} {name or '(unnamed)'} "
            f"<{_contact_email(contact) or '—'}>")


# ===========================================================================
# EXPORT LOOP
# ===========================================================================


def _load_ids(path: str) -> list[int]:
    try:
        rows = json.load(open(path))
    except Exception as e:  # noqa: BLE001
        sys.exit(f"could not read leadset {path!r}: {e}")
    if not isinstance(rows, list):
        sys.exit("leadset must be a JSON array of rows each with an `id` (job_postings.id).")
    return [r["id"] for r in rows if isinstance(r, dict) and r.get("id")]


async def run(leadset: str, company_id: str, *, dry_run: bool, allow_staging: bool,
              limit: int | None, delay: float, resend: bool, mark: bool,
              allow_billing: bool) -> None:
    url = settings.talentdb_webhook_url
    secret = settings.talentdb_webhook_secret
    if not (url and secret):
        sys.exit("ABORT: Talent-DB webhook not configured (TALENTDB_WEBHOOK_URL / _SECRET).")
    host = urlparse(url).netloc
    is_staging = "staging" in host.lower()
    if is_staging and not allow_staging:
        sys.exit(f"ABORT: destination is STAGING ({host}) — pass --allow-staging to send there.")

    posting_ids = _load_ids(leadset)
    n = len(posting_ids)
    print(f"[export] leadset: {leadset}  ({n} rows)", flush=True)
    print(f"[export] company_id: {company_id}", flush=True)
    print(f"[export] destination: {host}  ({'STAGING' if is_staging else 'PROD'})", flush=True)
    print(f"[export] billing guard: {'OFF (--allow-billing)' if allow_billing else 'ON (billing skipped)'}", flush=True)
    print(f"[export] mode: {'DRY RUN (nothing sent)' if dry_run else 'LIVE'}"
          + (f'  ·  limit {limit}' if limit is not None else ''), flush=True)
    print("[export] STREAMING resolve+send per lead (flushed) ...\n", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    states: Counter = Counter()
    tracks: Counter = Counter()
    results: list[dict] = []
    ok = failed = sent = 0
    preview_shown = False

    # ONE pass: resolve → classify → (send | preview) per lead, so output streams
    # live and `--limit` stops as soon as N are admitted (a canary never walks the
    # whole set). A stall shows on a specific lead instead of a silent freeze.
    for i, pid in enumerate(posting_ids, 1):
        if limit is not None and sent >= limit:
            break
        try:
            practice, posting, lead = _resolve_retry(company_id, pid)
        except Exception as e:  # noqa: BLE001 — a stalled/dropped query must not kill the run
            states["resolve_error"] += 1
            print(f"[export] [—] resolve FAILED posting={pid} — {type(e).__name__}: {e}  (row {i}/{n})", flush=True)
            continue
        if not posting:
            states["missing_posting"] += 1
            continue
        if not allow_billing and _is_billing(posting):
            states["skip_billing"] += 1
            continue
        if not practice:
            states["no_practice"] += 1
            continue
        if lead and lead.get("talentdb_exported_at") and not resend:
            states["already_exported"] += 1
            continue

        # Who this lead goes to. N reachable contacts → N Talent-DB leads.
        contact_rows = contact_store.list_contacts_for_practice(practice.get("id"))
        with_email = [c for c in contact_rows if _contact_email(c)]
        # Phone gate (user decision 2026-08-22): a contact with no direct
        # phone is not posted. (Mirror of src/talentdb_push.eligible_contacts.)
        eligible = [c for c in with_email if str(c.get("phone") or "").strip()]
        states["contacts_no_email"] += len(contact_rows) - len(with_email)
        states["contacts_no_phone"] += len(with_email) - len(eligible)
        # No eligible contact → nothing goes out. The legacy owner_* single
        # lead is retired (user decision 2026-08-22): pre-contact-era
        # practices are already being worked by the sales team, and re-posting
        # their owner_* mirror would hand the same person out twice.
        if not eligible:
            states["no_eligible_contact"] += 1
            continue

        # People already POSTed for this lead, as `{contact_id: td_lead_id}`.
        # The keys are the skip list — consulted even under `--resend`, because
        # re-entering a finished lead to catch a LATE-ARRIVING contact must not
        # duplicate the ones that already shipped. The values are Talent-DB's
        # own id for a pair, sent back whenever a pair IS re-posted so their
        # side updates instead of minting a second record.
        exports: dict = {}
        if lead:
            exports = contact_store.list_contact_exports(lead["id"])
        already: set = set(exports)

        recipients: list = list(eligible)
        base = build_envelope(practice, posting, lead)["fields"]
        company = base.get("Company")
        track = (base.get("interested_tracks") or ["(unmapped)"])[0]
        tracks[track] += 1
        # `--limit` and `sent` count OUR leads, not POSTs: one lead admitted
        # here is len(recipients) Talent-DB leads.
        sent += 1

        if dry_run:
            for c in recipients:
                cid = (c or {}).get("id")
                verb = "would SKIP (already sent)" if cid in already else "would send"
                fields = build_fields(practice, posting, lead, c, exports.get(cid))
                print(f"[export] [{sent}] {verb} {company!r} → {_who(c)} "
                      f"track={track} fields={len(fields)}  (row {i}/{n})", flush=True)
            if not preview_shown:
                preview_shown = True
                print("\n[dry-run] first envelope that WOULD be POSTed:")
                print(json.dumps(build_envelope(practice, posting, lead, recipients[0]),
                                 indent=2, default=str)[:2400], flush=True)
            continue

        lead_all_ok = True
        for c in recipients:
            cid = (c or {}).get("id")
            if cid in already:
                states["contact_already_exported"] += 1
                print(f"[export] [{sent}] SKIP {company!r} → {_who(c)} already sent"
                      f"  (row {i}/{n})", flush=True)
                continue
            entry = {"idx": sent, "posting_id": pid, "company": company,
                     "contact_id": cid, "at": datetime.now(timezone.utc).isoformat()}
            try:
                # Their id for this pair if we hold one — always None on this
                # path's own terms (a pair we hold an id for is a pair we skip),
                # but the plumbing belongs here for every flow that re-posts one.
                result = await post_lead(practice, posting, lead, url=url, secret=secret,
                                         contact=c, td_lead_id=exports.get(cid))
                entry.update(ok=result["ok"], status=result["status"], message=result["message"],
                             local_entity_id=result["local_entity_id"],
                             td_lead_id=result.get("td_lead_id"), http_status=result["http_status"])
                if result["ok"]:
                    ok += 1
                    if mark and lead and cid:
                        contact_store.mark_contact_exported(
                            lead["id"], cid, td_lead_id=result.get("td_lead_id"))
                    print(f"[export] [{sent}] OK   {company!r} → {_who(c)} localEntityId="
                          f"{result['local_entity_id']} fields={result.get('fields')}  (row {i}/{n})", flush=True)
                else:
                    failed += 1
                    lead_all_ok = False
                    print(f"[export] [{sent}] FAIL {company!r} → {_who(c)} {result['status']}: "
                          f"{result['message']}  (row {i}/{n})", flush=True)
            except Exception as e:  # noqa: BLE001 — one bad row must not kill the batch
                failed += 1
                lead_all_ok = False
                entry.update(ok=False, error=f"{type(e).__name__}: {e}")
                print(f"[export] [{sent}] ERROR {company!r} → {_who(c)} — "
                      f"{type(e).__name__}: {e}  (row {i}/{n})", flush=True)
            results.append(entry)
            time.sleep(delay)

        # The lead-level marker closes only on a clean sweep — a lead with a
        # failed contact stays in the universe so the next run re-enters it, and
        # the contact markers above keep that re-entry from duplicating the
        # people who already landed.
        if lead_all_ok and mark and lead:
            _mark_exported(company_id, lead["id"])

    skip_line = (f"skipped: billing={states['skip_billing']} no_practice={states['no_practice']} "
                 f"no_eligible_contact={states['no_eligible_contact']} "
                 f"contacts_no_email={states['contacts_no_email']} "
                 f"contacts_no_phone={states['contacts_no_phone']} "
                 f"contact_already_exported={states['contact_already_exported']} "
                 f"already_exported={states['already_exported']} missing_posting={states['missing_posting']} "
                 f"resolve_error={states['resolve_error']}")
    if dry_run:
        print(f"\n[dry-run] leads previewed: {sent}   {skip_line}", flush=True)
        print("[dry-run] nothing sent. Re-run with --yes to POST live.", flush=True)
        return

    os.makedirs("docs/runbooks", exist_ok=True)
    log_path = f"docs/runbooks/talentdb-export-{stamp}.log.json"
    with open(log_path, "w") as fp:
        json.dump({"run_at": stamp, "destination": host, "company_id": company_id,
                   "leadset": leadset, "leads": sent, "posts_ok": ok, "posts_failed": failed,
                   "marked": mark, "resend": resend,
                   "billing_guard": not allow_billing,
                   "skipped": dict(states), "tracks": dict(tracks), "results": results}, fp, indent=2)
    # ok/failed count POSTs (= Talent-DB leads); `leads` counts OUR leads, which
    # fan out to one POST per reachable contact.
    print(f"\n[export] done: posts ok={ok} failed={failed} from leads={sent}   {skip_line}", flush=True)
    print(f"[export] track spread: {dict(tracks)}", flush=True)
    print(f"[export] log → {log_path}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Standalone Talent-DB exporter — inline replica of the prod webhook mapping + push.")
    ap.add_argument("--leadset", default="leadset-talentdb-push.json",
                    help="leadset JSON (rows with `id`=job_postings.id). Default: leadset-talentdb-push.json")
    ap.add_argument("--company-id", default=None, help="tenant (default: settings.lead_company_id)")
    ap.add_argument("--yes", action="store_true", help="send live (default is a dry run)")
    ap.add_argument("--allow-staging", action="store_true", help="permit a staging destination")
    ap.add_argument("--limit", type=int, default=None,
                    help="only send the first N eligible LEADS (not POSTs — one lead "
                         "fans out to one Talent-DB lead per reachable contact)")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="seconds between POSTs, contact-level included (default: 2)")
    ap.add_argument("--resend", action="store_true",
                    help="also send leads already marked exported; contacts already sent "
                         "are still skipped, so this is the late-arriving-contact tool")
    ap.add_argument("--no-mark", action="store_true",
                    help="do NOT set talentdb_exported_at or the per-contact markers on success")
    ap.add_argument("--allow-billing", action="store_true",
                    help="disable the billing guard (billing is skipped by default)")
    args = ap.parse_args()

    company_id = args.company_id or settings.lead_company_id
    if not company_id:
        sys.exit("No company_id — pass --company-id or set LEAD_COMPANY_ID.")

    asyncio.run(run(args.leadset, company_id, dry_run=not args.yes,
                    allow_staging=args.allow_staging, limit=args.limit, delay=args.delay,
                    resend=args.resend, mark=not args.no_mark,
                    allow_billing=args.allow_billing))


if __name__ == "__main__":
    main()
