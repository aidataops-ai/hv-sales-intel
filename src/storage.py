import re
from datetime import datetime, timezone

from supabase import create_client

from src.models import Practice
from src.settings import settings

PROFILE_JOIN_SELECT = "*, last_touched_by_profile:profiles!last_touched_by(name)"


def _get_client():
    """Return Supabase client or None if unconfigured.

    Uses the service-role key when available so backend writes bypass RLS.
    The backend is the only client talking to the DB and performs its own
    auth checks, so service-role is the correct scope here.
    """
    if not settings.supabase_url:
        return None
    key = settings.supabase_service_role_key or settings.supabase_key
    if not key:
        return None
    return create_client(settings.supabase_url, key)


def _with_attribution(fields: dict, touched_by: str | None) -> dict:
    if not touched_by:
        return fields
    return {
        **fields,
        "last_touched_by": touched_by,
        "last_touched_at": datetime.now(timezone.utc).isoformat(),
    }


def _flatten_attribution(row: dict) -> dict:
    """Flatten the joined profile into last_touched_by_name."""
    if not row:
        return row
    joined = row.pop("last_touched_by_profile", None)
    row["last_touched_by_name"] = joined.get("name") if joined else None
    return row


# ----------------------------- dedup helpers --------------------------------


def _norm_phone(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")


def _dedup_key(
    name: str | None,
    address: str | None,
    phone: str | None,
) -> tuple[str, str, str]:
    """Normalized key used to detect Google's duplicate listings for the
    same physical business: lowercase trimmed name + address + digits-only
    phone. All three must match for two rows to be considered duplicates."""
    return (
        (name or "").strip().lower(),
        (address or "").strip().lower(),
        _norm_phone(phone),
    )


def find_duplicate_place_ids(practices: list[Practice]) -> dict[str, str]:
    """Map each incoming place_id → existing canonical place_id when an
    existing DB row matches by (normalized name + address + phone) under a
    different place_id.

    Prevents Google's parallel listings (same business with two place_ids)
    from creating duplicate rows. One DB round-trip per search.
    """
    client = _get_client()
    if not client or not practices:
        return {}

    names = list({p.name for p in practices if p.name})
    if not names:
        return {}
    try:
        result = (
            client.table("practices")
            .select("place_id,name,address,phone")
            .in_("name", names)
            .execute()
        )
    except Exception:
        return {}

    existing_by_key: dict[tuple, str] = {}
    for row in (result.data or []):
        key = _dedup_key(row.get("name"), row.get("address"), row.get("phone"))
        # Stable choice if pre-existing dupes share a key: lowest place_id wins.
        prev = existing_by_key.get(key)
        if prev is None or row["place_id"] < prev:
            existing_by_key[key] = row["place_id"]

    mapping: dict[str, str] = {}
    for p in practices:
        key = _dedup_key(p.name, p.address, p.phone)
        existing = existing_by_key.get(key)
        if existing and existing != p.place_id:
            mapping[p.place_id] = existing
    return mapping


# Optional columns that may not exist on older deployments. We retry the
# UPSERT/UPDATE without these fields if PostgREST rejects them as missing
# columns. These are columns added by later migrations that operators may
# not have run yet — failing the whole write because a migration hasn't
# been applied would break search / analyze with a 500.
_OPTIONAL_COLUMNS = {
    "salesforce_lead_url",
    "salesforce_lead_id",
    "salesforce_owner_id",
    "salesforce_owner_name",
    "salesforce_synced_at",
    "icp_vertical",
    "icp_tier",
    "icp_breakdown",
    "analysis_input_hash",
    "website_contacts",
    "website_doctor_name",
    "website_doctor_phone",
    "call_count",
    "call_notes",
    "call_script",
    "email",
    "email_draft",
    "email_draft_updated_at",
    "owner_name",
    "owner_email",
    "owner_phone",
    "owner_title",
    "owner_linkedin",
    "enrichment_status",
    "enriched_at",
    "assigned_to",
    "assigned_at",
    "assigned_by",
    "tags",
}


def upsert_practices(
    practices: list[Practice],
    touched_by: str | None = None,
) -> int:
    """Upsert practices. Returns count. Stamps attribution when touched_by set.

    Only core Google Places fields + attribution are written. Every other
    column is owned by a downstream write path (analyze, call log, enrich,
    email, etc.) and would be clobbered by sending None here when a search
    hits an already-worked row.

    Defense in depth: if PostgREST rejects a column that doesn't exist on
    the deployed schema (half-applied migrations), retry without that
    column instead of 500-ing the entire search.
    """
    client = _get_client()
    if not client or not practices:
        return 0
    # Every non-Google-Places field. (last_touched_by_name is derived from a
    # read-time join, not a stored column.)
    preserved = {
        # Phase 2 analysis
        "summary",
        "pain_points",
        "sales_angles",
        "recommended_service",
        "lead_score",
        "urgency_score",
        "hiring_signal_score",
        "icp_breakdown",
        "icp_vertical",
        "icp_tier",
        "analysis_input_hash",
        "website_contacts",
        "website_doctor_name",
        "website_doctor_phone",
        # Phase 3 CRM + script
        "status",
        "notes",
        "call_script",
        # Email outreach
        "email",
        "email_draft",
        "email_draft_updated_at",
        # Read-time join
        "last_touched_by_name",
        # Clay enrichment
        "owner_name",
        "owner_email",
        "owner_phone",
        "owner_title",
        "owner_linkedin",
        "enrichment_status",
        "enriched_at",
        # Salesforce integration
        "salesforce_lead_id",
        "salesforce_lead_url",
        "salesforce_owner_id",
        "salesforce_owner_name",
        "salesforce_synced_at",
        "call_count",
        "call_notes",
        # Assignment (admin-only)
        "assigned_to",
        "assigned_at",
        "assigned_by",
        "assigned_to_name",
    }
    rows = []
    for p in practices:
        row = p.model_dump(exclude=preserved)
        rows.append(_with_attribution(row, touched_by))

    try:
        result = client.table("practices").upsert(rows, on_conflict="place_id").execute()
    except Exception as e:
        msg = str(e)
        # Drop any column the schema doesn't have yet and retry once.
        # This survives half-applied migrations (e.g. salesforce_lead_url
        # missing on older deployments).
        missing = [c for c in _OPTIONAL_COLUMNS if c in msg]
        if not missing:
            raise
        filtered_rows = [
            {k: v for k, v in r.items() if k not in missing} for r in rows
        ]
        result = (
            client.table("practices")
            .upsert(filtered_rows, on_conflict="place_id")
            .execute()
        )
    return len(result.data) if result.data else 0


def query_practices(
    city: str | None = None,
    category: str | None = None,
    min_rating: float | None = None,
    limit: int = 50,
) -> list[dict]:
    """List practices with profile join. Returns [] if unconfigured."""
    client = _get_client()
    if not client:
        return []
    q = client.table("practices").select(PROFILE_JOIN_SELECT)
    if city:
        q = q.ilike("city", f"%{city}%")
    if category:
        q = q.eq("category", category)
    if min_rating:
        q = q.gte("rating", min_rating)
    q = q.order("rating", desc=True).limit(limit)
    result = q.execute()
    return [_flatten_attribution(r) for r in (result.data or [])]


def get_practice(place_id: str) -> dict | None:
    """Get single practice with profile join. Returns None if not found."""
    client = _get_client()
    if not client:
        return None
    try:
        result = (
            client.table("practices").select(PROFILE_JOIN_SELECT)
            .eq("place_id", place_id).maybe_single().execute()
        )
    except Exception:
        return None
    return _flatten_attribution(result.data) if result and result.data else None


def _update_with_optional_retry(
    place_id: str,
    payload: dict,
) -> dict | None:
    """UPDATE practices, dropping any _OPTIONAL_COLUMNS the DB rejects."""
    client = _get_client()
    if not client:
        return None
    try:
        result = (
            client.table("practices").update(payload)
            .eq("place_id", place_id).execute()
        )
    except Exception as e:
        msg = str(e)
        retry_payload = {k: v for k, v in payload.items() if k not in _OPTIONAL_COLUMNS}
        if any(c in msg for c in _OPTIONAL_COLUMNS) and len(retry_payload) < len(payload):
            result = (
                client.table("practices").update(retry_payload)
                .eq("place_id", place_id).execute()
            )
        else:
            raise
    return result.data[0] if result.data else None


def update_practice_analysis(
    place_id: str,
    analysis: dict,
    touched_by: str | None = None,
) -> dict | None:
    """Update Phase 2 analysis fields. Stamps attribution when touched_by set.

    Uses the optional-column retry so a missing post-deploy migration
    (e.g. `analysis_input_hash` / `website_contacts`) degrades gracefully
    instead of 500-ing the analyze endpoint.
    """
    return _update_with_optional_retry(
        place_id,
        _with_attribution(analysis, touched_by),
    )


def update_practice_fields(
    place_id: str,
    fields: dict,
    touched_by: str | None = None,
) -> dict | None:
    """Update arbitrary fields. Stamps attribution when touched_by set.

    If an optional column doesn't exist in the DB yet, retries the update
    without that column instead of failing the whole write.
    """
    return _update_with_optional_retry(
        place_id,
        _with_attribution(fields, touched_by),
    )


def insert_email_message(
    practice_id: int,
    user_id: str | None,
    direction: str,
    subject: str | None,
    body: str | None,
    message_id: str | None,
    in_reply_to: str | None,
    error: str | None,
) -> dict | None:
    """Insert a row into email_messages. Returns the inserted row."""
    client = _get_client()
    if not client:
        return None
    row = {
        "practice_id": practice_id,
        "user_id": user_id,
        "direction": direction,
        "subject": subject,
        "body": body,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "error": error,
    }
    result = client.table("email_messages").insert(row).execute()
    return result.data[0] if result.data else None


def list_email_messages(practice_id: int) -> list[dict]:
    """List email messages for a practice, oldest first."""
    client = _get_client()
    if not client:
        return []
    result = (
        client.table("email_messages").select("*")
        .eq("practice_id", practice_id)
        .order("sent_at")
        .execute()
    )
    return result.data or []


def get_cached_search(query: str, max_age_hours: int = 24) -> list[dict] | None:
    """Return cached practices for this query if a recent entry exists.

    Returns None if no cache or cache is stale. The cache key is a
    lowercased + whitespace-collapsed version of the query so trivial
    formatting differences still hit the same row.
    """
    norm = " ".join((query or "").lower().split())
    if not norm:
        return None
    client = _get_client()
    if not client:
        return None
    try:
        result = (
            client.table("searches").select("place_ids,searched_at")
            .eq("query_norm", norm).maybe_single().execute()
        )
    except Exception:
        return None
    if not result or not result.data:
        return None

    from datetime import datetime, timedelta, timezone
    searched_at = result.data.get("searched_at")
    if searched_at:
        try:
            ts = datetime.fromisoformat(str(searched_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if datetime.now(timezone.utc) - ts > timedelta(hours=max_age_hours):
            return None

    place_ids: list[str] = result.data.get("place_ids") or []
    if not place_ids:
        return []
    rows = (
        client.table("practices").select(PROFILE_JOIN_SELECT)
        .in_("place_id", place_ids).execute()
    )
    return [_flatten_attribution(r) for r in (rows.data or [])]


def save_search_cache(query: str, place_ids: list[str]) -> None:
    """Upsert a cache row for this query → place_ids."""
    norm = " ".join((query or "").lower().split())
    if not norm or not place_ids:
        return
    client = _get_client()
    if not client:
        return
    from datetime import datetime, timezone
    row = {
        "query_norm": norm,
        "query_raw": query,
        "place_ids": place_ids,
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.table("searches").upsert(row, on_conflict="query_norm").execute()
    except Exception:
        return


def add_tags(place_id: str, new_tags: list[str]) -> None:
    """Append tags to a practice's tags array, deduped. No-op if list empty.

    Reads current tags, computes union, writes back. Two roundtrips is fine
    for our write rate; postgres array_cat is not exposed via the PostgREST
    client, so this read-modify-write pattern is the simplest reliable shape.
    """
    if not new_tags:
        return
    client = _get_client()
    if not client:
        return
    try:
        result = (
            client.table("practices").select("tags")
            .eq("place_id", place_id).maybe_single().execute()
        )
    except Exception:
        return
    if result is None:
        return
    existing = (result.data or {}).get("tags") or []
    merged = sorted(set(existing) | set(new_tags))
    if sorted(existing) == merged:
        return  # nothing new
    client.table("practices").update({"tags": merged}).eq("place_id", place_id).execute()


def list_outbound_message_ids(practice_id: int) -> list[str]:
    """Return all outbound message_ids for a practice (used by poll threading)."""
    client = _get_client()
    if not client:
        return []
    result = (
        client.table("email_messages").select("message_id")
        .eq("practice_id", practice_id)
        .eq("direction", "out")
        .execute()
    )
    return [r["message_id"] for r in (result.data or []) if r.get("message_id")]
