"""Per-practice people — the `practice_contacts` table and its export markers.

Clay used to hand us exactly one contact per practice, which we flattened into
the `practices.owner_*` columns. It now POSTs one webhook call *per person*, so
a practice can carry an office manager, an owner and a hiring lead at once. This
module owns the row-per-person table those calls land in:

    practice_contacts(practice_id, first_name, last_name, title,
                      linkedin_url, work_email, personal_email, phone,
                      source, dedupe_key, created_at, updated_at)
    unique (practice_id, dedupe_key)

**Shared, not per-tenant.** There is no `company_id` here, deliberately: the
`practices` universe is shared across tenants and a contact is a fact about the
practice, not about who is selling to it. Per-company opinions (disposition,
notes, assignment) live in the per-company tables and stay there.

**The legacy flat columns stay.** `practices.owner_name/_title/_linkedin/_email`
are still what the UI and the CSV export read, and they are the Talent-DB push's
fallback for a practice with no contact rows at all, so the primary contact is
mirrored back onto them (`pick_primary` → `owner_mirror_fields`).
`should_mirror` is the guard that keeps a personal-email-only contact from
clobbering a real `owner_email`.

**One Talent-DB lead per person.** The push fans out over these rows
(`src/talentdb_push.py`), so "already exported" becomes a fact about a (lead,
person) pair — `list_exported_contact_ids` / `mark_contact_exported` below own
the `talentdb_contact_exports` table that records it.

**Everything that touches the database is fail-soft.** The table arrives with a
migration, and a webhook is not the place to discover the migration has not been
applied yet: every read/write below catches, logs a warning and returns an empty
result. Clay retries hard on a 500 and we would rather drop a contact than have
an unapplied migration turn into a retry storm against the enrichment endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.talentdb import _scrub_email

log = logging.getLogger("hvsi.contacts")

TABLE = "practice_contacts"

# The columns Clay sends us, in the order the table declares them. Anything
# else in the payload is dropped — Clay adds fields to its exports freely and
# an unknown key would fail the insert on a column that does not exist.
CONTACT_FIELDS = (
    "first_name", "last_name", "title",
    "linkedin_url", "work_email", "personal_email", "phone",
)

DEFAULT_SOURCE = "clay"

# PostgREST caps a response at 1000 rows and truncates silently. See
# storage._paginated_query — same ceiling, same fix.
_PAGE = 1000


def _client():
    from src.storage import _get_client
    return _get_client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    """Trimmed string, or None for anything blank. Non-strings pass through."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _email(value):
    """Trimmed, placeholder-scrubbed email, or None."""
    return _scrub_email(_text(value))


def clean_contact(raw: dict) -> dict:
    """Normalize one Clay person payload into the table's column set.

    Every string is trimmed and blanks become None, both email columns run
    through `talentdb._scrub_email` so Clay's "Not Found" placeholders never
    reach the database as if they were addresses, and unknown keys are dropped.
    The result always carries all of `CONTACT_FIELDS`, so callers can read any
    of them without a `.get` dance.
    """
    raw = raw or {}
    cleaned = {field: _text(raw.get(field)) for field in CONTACT_FIELDS}
    cleaned["work_email"] = _scrub_email(cleaned["work_email"])
    cleaned["personal_email"] = _scrub_email(cleaned["personal_email"])
    return cleaned


def _normalize_linkedin(url) -> str | None:
    """`https://www.linkedin.com/in/x/` → `linkedin.com/in/x`.

    Lowercased, scheme and `www.` stripped, trailing slashes removed — the four
    spellings Clay actually emits for the same profile.
    """
    value = _text(url)
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    for scheme in ("https://", "http://"):
        if value.startswith(scheme):
            value = value[len(scheme):]
            break
    if value.startswith("www."):
        value = value[4:]
    value = value.rstrip("/")
    return value or None


def _normalize_name(contact: dict) -> str | None:
    parts = f"{_text(contact.get('first_name')) or ''} " \
            f"{_text(contact.get('last_name')) or ''}".split()
    return " ".join(parts).lower() or None


def contact_dedupe_key(contact: dict) -> str:
    """The identity we upsert on, strongest available signal first.

        li:  normalized LinkedIn URL
        we:  work email, lowercased
        pe:  personal email, lowercased
        nm:  first + last, whitespace-collapsed and lowercased

    Accepted limitation: the precedence is over *fields*, not over people, so
    the same human arriving once with a LinkedIn URL and once with only an
    email lands as two rows. That is the trade for a key that is stable under
    the case that actually happens — Clay re-sending the same enriched row,
    which always carries the same fields and therefore always resolves to the
    same key. De-duplicating across field sets would need a fuzzy match we are
    not willing to run inside a webhook.

    A payload with no identifying field at all collapses to the constant `nm:`,
    so a practice accumulates at most one nameless placeholder row rather than
    one per Clay retry.
    """
    contact = contact or {}

    linkedin = _normalize_linkedin(contact.get("linkedin_url"))
    if linkedin:
        return f"li:{linkedin}"

    work = _email(contact.get("work_email"))
    if isinstance(work, str) and work.strip():
        return f"we:{work.strip().lower()}"

    personal = _email(contact.get("personal_email"))
    if isinstance(personal, str) and personal.strip():
        return f"pe:{personal.strip().lower()}"

    return f"nm:{_normalize_name(contact) or ''}"


def upsert_contact(practice_id: int, contact: dict) -> dict | None:
    """Insert-or-update one contact for a practice; return the stored row.

    Conflicts resolve on `(practice_id, dedupe_key)`, so a Clay re-send of the
    same person refreshes their fields instead of adding a row. Fail-soft: any
    database error (an unapplied migration above all) logs a warning and
    returns None — the Clay webhook must not 500 over this.
    """
    client = _client()
    if not client or not practice_id:
        return None

    row = clean_contact(contact)
    row["practice_id"] = practice_id
    row["source"] = _text((contact or {}).get("source")) or DEFAULT_SOURCE
    row["dedupe_key"] = contact_dedupe_key(row)
    row["updated_at"] = _now()

    try:
        result = (
            client.table(TABLE)
            .upsert(row, on_conflict="practice_id,dedupe_key")
            .execute()
        )
    except Exception as e:
        log.warning("[contacts.upsert.error] practice=%s %s: %s",
                    practice_id, type(e).__name__, str(e)[:200])
        return None

    data = result.data or []
    return data[0] if data else None


def list_contacts_for_practice(practice_id: int) -> list[dict]:
    """Every contact on a practice, oldest first, paginated past PostgREST's
    1000-row ceiling. Ordered by `(created_at, id)` so `pick_primary` and the
    UI agree on which contact is "first". Fail-soft: returns [] on any error."""
    client = _client()
    if not client or not practice_id:
        return []

    rows: list[dict] = []
    page = 0
    while True:
        try:
            result = (
                client.table(TABLE)
                .select("*")
                .eq("practice_id", practice_id)
                .order("created_at")
                .order("id")
                .range(page * _PAGE, page * _PAGE + _PAGE - 1)
                .execute()
            )
        except Exception as e:
            log.warning("[contacts.list.error] practice=%s %s: %s",
                        practice_id, type(e).__name__, str(e)[:200])
            return []
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        page += 1
    return rows


# ---------------------------------------------------------------------------
# Talent-DB per-contact export markers
#
# The push fans out to one Talent-DB lead per contact, so "already exported" is
# a fact about a (lead, person) pair, not about the lead alone — and so is
# Talent-DB's own record id for that pair (`td_lead_id`, echoed back on a
# re-post). These read and write `talentdb_contact_exports`; the lead-level
# `company_job_leads.talentdb_exported_at` stays the gate and is set only when
# every eligible contact on the lead succeeded. See src/talentdb_push.py.
# ---------------------------------------------------------------------------

EXPORTS_TABLE = "talentdb_contact_exports"


def list_contact_exports(lead_id: int) -> dict[int, str | None]:
    """`{contact_id: td_lead_id}` for every contact already POSTed for this lead.

    Two answers in one read, because the fan-out needs both: the KEYS are the
    retry's skip list, and the VALUES are Talent-DB's own record id for that
    (lead, contact) pair — echoed back when a pair is posted again so the
    receiver updates instead of duplicating. A None value means the pair is
    exported but we never captured an id (which is every row today; see
    `talentdb._td_lead_id_from_response`).

    Fail-soft like everything else here: an unapplied migration returns an empty
    dict, which degrades the fan-out to "send everyone" rather than blocking it.
    """
    client = _client()
    if not client or not lead_id:
        return {}

    try:
        result = (
            client.table(EXPORTS_TABLE)
            .select("contact_id, td_lead_id")
            .eq("lead_id", lead_id)
            .execute()
        )
    except Exception as e:
        log.warning("[contacts.exports.list.error] lead=%s %s: %s",
                    lead_id, type(e).__name__, str(e)[:200])
        return {}

    return {row["contact_id"]: row.get("td_lead_id")
            for row in (result.data or []) if row.get("contact_id")}


def list_exported_contact_ids(lead_id: int) -> set[int]:
    """Just the skip list — `list_contact_exports` without the ids."""
    return set(list_contact_exports(lead_id))


def mark_contact_exported(lead_id: int, contact_id: int,
                          td_lead_id: str | None = None) -> None:
    """Record that this contact's Talent-DB lead was accepted.

    Upserts on `(lead_id, contact_id)` so re-posting the same person refreshes
    the row instead of failing on the unique constraint. `td_lead_id` is written
    ONLY when truthy: a later marker write that captured no id must not blank
    the id we already hold, because that id is the only thing that lets a
    re-post update the receiver's record instead of duplicating it.

    Fail-soft: a failed mark logs and returns — the POST already happened, and
    losing the marker costs a duplicate on a later retry, not the send.
    """
    client = _client()
    if not client or not lead_id or not contact_id:
        return

    row = {"lead_id": lead_id, "contact_id": contact_id, "exported_at": _now()}
    if td_lead_id:
        row["td_lead_id"] = td_lead_id
    try:
        (
            client.table(EXPORTS_TABLE)
            .upsert(row, on_conflict="lead_id,contact_id")
            .execute()
        )
    except Exception as e:
        log.warning("[contacts.exports.mark.error] lead=%s contact=%s %s: %s",
                    lead_id, contact_id, type(e).__name__, str(e)[:200])


def contact_email(contact: dict) -> str | None:
    """The address to actually mail: work first, then personal, placeholders
    scrubbed. None means this contact has no real email."""
    contact = contact or {}
    return _email(contact.get("work_email")) or _email(contact.get("personal_email"))


def pick_primary(contacts: list[dict]) -> dict | None:
    """The contact the legacy `owner_*` columns should reflect.

    A real work email wins — that is the address the outreach actually uses —
    and among several the earliest one does, which is why the list must arrive
    in `(created_at, id)` order. With no work email anywhere, the first contact
    stands in so the practice at least shows a name.
    """
    contacts = contacts or []
    for contact in contacts:
        if _email((contact or {}).get("work_email")):
            return contact
    return contacts[0] if contacts else None


def owner_mirror_fields(primary: dict) -> dict:
    """The primary contact as legacy `practices.owner_*` columns.

    Only non-empty keys come back — the Clay webhook's convention is that an
    absent key leaves the existing column alone, so emitting None would blank
    data we are not trying to change. `owner_phone` mirrors the contact's
    direct `phone` when present; the practice's own office line stays in
    `practices.phone` either way.
    """
    primary = primary or {}
    fields: dict = {}

    name = " ".join(
        part for part in (_text(primary.get("first_name")),
                          _text(primary.get("last_name"))) if part
    )
    if name:
        fields["owner_name"] = name

    title = _text(primary.get("title"))
    if title:
        fields["owner_title"] = title

    linkedin = _text(primary.get("linkedin_url"))
    if linkedin:
        fields["owner_linkedin"] = linkedin

    phone = _text(primary.get("phone"))
    if phone:
        fields["owner_phone"] = phone

    email = contact_email(primary)
    if email:
        fields["owner_email"] = email

    return fields


def should_mirror(primary: dict, existing_practice: dict) -> bool:
    """Whether `owner_mirror_fields(primary)` may be written to the practice.

    A work email always wins — that is a better contact than whatever is there.
    Without one we only write into an empty (or placeholder) `owner_email`,
    because overwriting a verified work address with somebody's gmail is a
    downgrade the SDR cannot see and cannot undo.
    """
    if not primary:
        return False
    if _email(primary.get("work_email")):
        return True
    return not _email((existing_practice or {}).get("owner_email"))
