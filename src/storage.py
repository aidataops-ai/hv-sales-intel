import json
import re
from datetime import datetime, timezone

from supabase import create_client

from src.models import Practice
from src.settings import settings


# ---------------------------------------------------------------------------
# Phase 3 — dual-write routing.
#
# Fields the new per-company tables own. update_practice_fields routes each
# incoming key by category: state fields → company_practice_state, analysis
# fields → company_practice_analyses, everything else stays on `practices`
# (the shared dedup table) only.
# ---------------------------------------------------------------------------

_STATE_FIELDS = frozenset({
    "status", "notes", "tags",
    "call_count", "call_notes", "call_script",
    "email", "email_draft", "email_draft_updated_at",
    "salesforce_lead_id", "salesforce_lead_url",
    "salesforce_owner_id", "salesforce_owner_name", "salesforce_synced_at",
    "assigned_to", "assigned_at", "assigned_by",
    "last_touched_by", "last_touched_at",
    "export_count", "last_exported_at", "last_exported_by",
    "enrichment_status", "enriched_at",
    "owner_name", "owner_email", "owner_phone", "owner_title", "owner_linkedin",
})

_ANALYSIS_FIELDS = frozenset({
    "summary", "pain_points", "sales_angles",
    "lead_score", "urgency_score", "hiring_signal_score",
    "icp_breakdown", "icp_vertical", "icp_tier",
    "analysis_input_hash", "website_contacts",
    "classification",
})

# Fields that arrive as JSON-string in legacy code but are jsonb in the
# per-company analyses table — coerce on write.
_JSONB_ANALYSIS_FIELDS = frozenset({
    "pain_points", "sales_angles", "website_contacts", "icp_breakdown",
})

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
    "export_count",
    "last_exported_at",
    "last_exported_by",
}


def upsert_practices(
    practices: list[Practice],
    touched_by: str | None = None,
    company_id: str | None = None,
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
        # Export tracking
        "export_count",
        "last_exported_at",
        "last_exported_by",
        "last_exported_by_name",
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

    # Phase 3 dual-write: seed per-company state for every upserted place
    # so the active tenant sees new search results in their sidebar.
    _ensure_state_rows_for_practices(
        company_id,
        [p.place_id for p in practices if p.place_id],
    )

    return len(result.data) if result.data else 0


# PostgREST has a default `db-max-rows` cap (1000 on hosted Supabase) that
# silently truncates large responses. We paginate via Range headers so a
# `.limit(20000)` actually returns 20000 instead of clipping at 1000.
_POSTGREST_PAGE_SIZE = 1000


def _paginated_query(builder, limit: int) -> list[dict]:
    """Fetch up to `limit` rows from a Supabase query builder, working around
    PostgREST's per-request max-rows ceiling by issuing successive .range()
    calls. The caller is responsible for any .order() / filters — those are
    applied to the builder before we get it.
    """
    rows: list[dict] = []
    page = 0
    while len(rows) < limit:
        want = min(_POSTGREST_PAGE_SIZE, limit - len(rows))
        start = page * _POSTGREST_PAGE_SIZE
        end = start + want - 1  # .range() is inclusive
        try:
            result = builder.range(start, end).execute()
        except Exception:
            break
        batch = result.data or []
        rows.extend(batch)
        # Short read → we're at the end of the result set.
        if len(batch) < want:
            break
        page += 1
    return rows


# ---------------------------------------------------------------------------
# Phase 3 — per-company write helpers.
#
# Every helper is fail-soft: if the per-company write blows up (e.g. the
# practice id can't be resolved, or the new tables don't exist on this
# deployment), we silently drop it. The legacy `practices` write is the
# source of truth until Phase 4 swaps the reads.
# ---------------------------------------------------------------------------


def _practice_id_by_place(place_id: str) -> int | None:
    """Look up `practices.id` (bigint) given a `place_id` (text)."""
    if not place_id:
        return None
    client = _get_client()
    if not client:
        return None
    try:
        result = (
            client.table("practices").select("id")
            .eq("place_id", place_id).maybe_single().execute()
        )
    except Exception:
        return None
    if not result or not result.data:
        return None
    return result.data["id"]


def _coerce_jsonb(value):
    """Pass dicts/lists/None through; parse JSON strings into structures."""
    if value is None or isinstance(value, (list, dict, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _split_fields_for_dual_write(
    fields: dict,
) -> tuple[dict, dict, list[str] | None]:
    """Split a `fields` dict into (state_fields, analysis_fields, new_tags).

    `tags` is pulled out separately because `add_tags` (read-modify-write
    union) is the right shape; a blind UPDATE would clobber existing tags.
    """
    state: dict = {}
    analysis: dict = {}
    new_tags: list[str] | None = None
    for k, v in fields.items():
        if k == "tags":
            if isinstance(v, list):
                new_tags = list(v)
            continue
        if k in _STATE_FIELDS:
            state[k] = v
        elif k in _ANALYSIS_FIELDS:
            analysis[k] = v
    return state, analysis, new_tags


def _per_company_upsert(
    table: str,
    company_id: str,
    practice_id: int,
    fields: dict,
) -> None:
    """Upsert per-(company, practice) state OR analyses. Silent on failure."""
    if not company_id or not practice_id or not fields:
        return
    client = _get_client()
    if not client:
        return
    payload = {"company_id": company_id, "practice_id": practice_id, **fields}
    try:
        client.table(table).upsert(payload, on_conflict="company_id,practice_id").execute()
    except Exception:
        # Don't fail the legacy write because the per-company mirror failed.
        pass


def _write_per_company_state(
    place_id: str | None,
    company_id: str | None,
    state_fields: dict,
    touched_by: str | None,
) -> None:
    if not place_id or not company_id or not state_fields:
        return
    pid = _practice_id_by_place(place_id)
    if not pid:
        return
    payload = {**state_fields}
    if touched_by:
        payload.setdefault("last_touched_by", touched_by)
        payload.setdefault("last_touched_at", datetime.now(timezone.utc).isoformat())
    _per_company_upsert("company_practice_state", company_id, pid, payload)


def _write_per_company_analyses(
    place_id: str | None,
    company_id: str | None,
    analysis_fields: dict,
) -> None:
    if not place_id or not company_id or not analysis_fields:
        return
    pid = _practice_id_by_place(place_id)
    if not pid:
        return
    # Coerce JSON-string fields into structured jsonb.
    coerced = {
        k: (_coerce_jsonb(v) if k in _JSONB_ANALYSIS_FIELDS else v)
        for k, v in analysis_fields.items()
    }
    # Always stamp analyzed_at on writes; the column default only fires on insert.
    coerced.setdefault("analyzed_at", datetime.now(timezone.utc).isoformat())
    _per_company_upsert("company_practice_analyses", company_id, pid, coerced)


def _add_tags_per_company(
    place_id: str | None,
    company_id: str | None,
    new_tags: list[str],
) -> None:
    """Dedup-merge tags onto company_practice_state for (company, practice)."""
    if not place_id or not company_id or not new_tags:
        return
    pid = _practice_id_by_place(place_id)
    if not pid:
        return
    client = _get_client()
    if not client:
        return
    try:
        existing = (
            client.table("company_practice_state").select("tags")
            .eq("company_id", company_id).eq("practice_id", pid)
            .maybe_single().execute()
        )
    except Exception:
        existing = None
    current = []
    if existing and existing.data:
        current = (existing.data.get("tags") or [])
    merged = sorted(set(current) | set(new_tags))
    if sorted(current) == merged:
        return
    _per_company_upsert(
        "company_practice_state",
        company_id,
        pid,
        {"tags": merged},
    )


def _ensure_state_rows_for_practices(
    company_id: str | None,
    place_ids: list[str],
) -> None:
    """Seed blank company_practice_state rows so newly-upserted practices
    show up in the active tenant's sidebar even before any per-practice
    action is taken. Idempotent — only inserts missing rows."""
    if not company_id or not place_ids:
        return
    client = _get_client()
    if not client:
        return
    # Resolve place_id → practice_id in one round-trip.
    try:
        rows = (
            client.table("practices").select("id,place_id")
            .in_("place_id", place_ids).execute()
        )
    except Exception:
        return
    payload = [
        {"company_id": company_id, "practice_id": r["id"]}
        for r in (rows.data or [])
        if r.get("id")
    ]
    if not payload:
        return
    try:
        client.table("company_practice_state").upsert(
            payload,
            on_conflict="company_id,practice_id",
            ignore_duplicates=True,
        ).execute()
    except Exception:
        pass


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
    q = q.order("rating", desc=True)
    rows = _paginated_query(q, limit)
    return [_flatten_attribution(r) for r in rows]


# Whitelist of user-facing sort keys -> real DB columns. Anything not in here
# falls back to lead_score so a bad ?sort= value can't reach the query builder.
_SORT_COLUMNS: dict[str, str] = {
    "lead_score": "lead_score",
    "rating": "rating",
    "review_count": "review_count",
    "last_touched": "last_touched_at",
    "name": "name",
    "country": "state",        # groups UK vs US states
    "vertical": "icp_vertical",
}


def _escape_or_value(text: str) -> str:
    """Strip characters that would break a PostgREST or=() filter string.

    Commas separate OR members and parens delimit groups; `*` is the ilike
    wildcard. We drop all of them so a free-text search term can be embedded
    safely as `*term*` without corrupting the filter.
    """
    for ch in (",", "(", ")", "*"):
        text = text.replace(ch, " ")
    return text.strip()


def query_practices_page(
    *,
    search: str | None = None,
    category: str | None = None,
    vertical: str | None = None,
    geo: str | None = None,          # "US" | "UK" | "<2-letter state code>"
    tier: str | None = None,
    status: str | None = None,
    min_rating: float | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    enriched: str | None = None,     # "yes" | "no"
    owner: str | None = None,        # profiles.id (uuid)
    tags: list[str] | None = None,
    sort: str = "lead_score",
    direction: str = "desc",
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[dict], int]:
    """Server-side filtered + sorted + paginated practice list.

    Returns ``(rows, total)`` where ``total`` is the exact count of rows
    matching the filters (ignoring pagination), so the caller can drive
    "load more" / infinite scroll. Returns ``([], 0)`` if unconfigured.

    Replaces the all-rows ``query_practices`` for the main list view: a single
    ``.range()`` request per page instead of the serial 1000-row loop.
    """
    client = _get_client()
    if not client:
        return [], 0

    col = _SORT_COLUMNS.get(sort, "lead_score")
    desc = (direction or "desc").lower() != "asc"
    start = max(0, offset)
    end = start + max(1, limit) - 1  # .range() is inclusive

    def _build(include_icp: bool):
        """Build the query. ``include_icp`` lets us retry without the icp_*
        filter/sort on deployments that haven't run the ICP migration."""
        q = client.table("practices").select(PROFILE_JOIN_SELECT, count="exact")

        # Filters: each .or_ is its own OR-group; multiple groups AND-combine.
        if search and search.strip():
            safe = _escape_or_value(search)
            if safe:
                like = f"*{safe}*"
                q = q.or_(
                    f"name.ilike.{like},address.ilike.{like},city.ilike.{like},"
                    f"owner_name.ilike.{like},website_doctor_name.ilike.{like}"
                )
        if category:
            q = q.eq("category", category)
        if vertical and include_icp:
            q = q.eq("icp_vertical", vertical)
        if geo:
            if geo == "UK":
                q = q.eq("state", "UK")
            elif geo == "US":
                # "not UK" must also keep rows with an unresolved state: in SQL
                # `state <> 'UK'` is UNKNOWN for NULL, so .neq alone drops them.
                q = q.or_("state.is.null,state.neq.UK")
            else:
                q = q.eq("state", geo)
        if tier and include_icp:
            q = q.eq("icp_tier", tier)
        if status:
            q = q.eq("status", status)
        if min_rating:
            q = q.gte("rating", min_rating)
        if min_score is not None:
            q = q.gte("lead_score", min_score)
        if max_score is not None:
            q = q.lte("lead_score", max_score)
        if enriched == "yes":
            q = q.eq("enrichment_status", "enriched")
        elif enriched == "no":
            # "not enriched" includes rows that were never analyzed (null status).
            q = q.or_("enrichment_status.is.null,enrichment_status.neq.enriched")
        if owner:
            q = q.or_(f"assigned_to.eq.{owner},last_touched_by.eq.{owner}")
        if tags:
            q = q.overlaps("tags", tags)

        # Single combined ORDER string — passed as the column with desc=False so
        # postgrest-py emits it verbatim. This sidesteps two version pitfalls:
        #  - the nullsfirst kwarg only ever emits ".nullsfirst" (never
        #    ".nullslast"), and Postgres defaults DESC -> NULLS FIRST, which
        #    would float unscored (NULL) leads to the TOP. We spell ".nullslast"
        #    so unscored leads always sort last, in any direction.
        #  - chaining two .order() calls only comma-combines on postgrest
        #    >=0.16.11; older versions send two params and silently drop the
        #    tiebreak, which would duplicate/skip rows across pages.
        sort_col = col if (include_icp or col != "icp_vertical") else "lead_score"
        order_str = f"{sort_col}.{'desc' if desc else 'asc'}.nullslast"
        if sort_col != "place_id":
            order_str += ",place_id.asc"
        return q.order(order_str, desc=False, nullsfirst=False)

    try:
        result = _build(include_icp=True).range(start, end).execute()
    except Exception as exc:
        # Degrade gracefully if this deployment lacks the icp_* columns:
        # retry once without the icp filter/sort. Other errors keep the old
        # fail-soft ([], 0) so a transient hiccup never blanks the UI hard.
        if any(c in str(exc) for c in _OPTIONAL_COLUMNS):
            try:
                result = _build(include_icp=False).range(start, end).execute()
            except Exception:
                return [], 0
        else:
            return [], 0
    rows = result.data or []
    total = result.count if result.count is not None else len(rows)
    return [_flatten_attribution(r) for r in rows], total


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


def query_for_export(max_exports: int | None) -> list[dict]:
    """Fetch every practice eligible for a CSV export.

    `max_exports` semantics:
      - None       → no filter; export every row
      - 0          → only never-exported rows (export_count = 0)
      - N          → only rows with export_count <= N

    Returns rows with the profile join (so last_touched_by_name resolves).
    Paginates through PostgREST's max-rows cap so a 5k-lead DB exports
    every row instead of clipping at 1000.
    """
    client = _get_client()
    if not client:
        return []
    q = client.table("practices").select(PROFILE_JOIN_SELECT)
    if max_exports is not None:
        q = q.lte("export_count", max_exports)
    q = q.order("lead_score", desc=True)
    rows = _paginated_query(q, 50_000)
    return [_flatten_attribution(r) for r in rows]


def increment_export_counts(
    place_ids: list[str],
    user_id: str | None = None,
    company_id: str | None = None,
) -> None:
    """Increment export_count by 1 and stamp who/when on each `place_id`.

    Single SELECT then per-row UPDATE — Supabase-py doesn't expose `+= 1`
    SQL fragments. Fine at export-batch scale (a few thousand rows,
    infrequent). Each UPDATE writes `export_count`, `last_exported_at`,
    and `last_exported_by`; missing columns are dropped via the optional-
    column retry pattern.
    """
    if not place_ids:
        return
    client = _get_client()
    if not client:
        return
    try:
        existing = (
            client.table("practices")
            .select("place_id,export_count")
            .in_("place_id", place_ids)
            .execute()
        )
    except Exception:
        return
    now = datetime.now(timezone.utc).isoformat()
    for row in existing.data or []:
        next_count = (row.get("export_count") or 0) + 1
        payload: dict = {
            "export_count": next_count,
            "last_exported_at": now,
        }
        if user_id:
            payload["last_exported_by"] = user_id
        try:
            client.table("practices").update(payload).eq(
                "place_id", row["place_id"]
            ).execute()
            # Phase 3 dual-write: mirror to per-company state so Phase 4
            # can read export_count from there without a backfill scramble.
            if company_id:
                _write_per_company_state(
                    row["place_id"], company_id, payload, touched_by=None,
                )
        except Exception as e:
            msg = str(e)
            # Drop columns the deployed schema is missing and retry once.
            missing = [c for c in _OPTIONAL_COLUMNS if c in msg]
            if not missing:
                continue
            retry = {k: v for k, v in payload.items() if k not in missing}
            if not retry:
                continue
            try:
                client.table("practices").update(retry).eq(
                    "place_id", row["place_id"]
                ).execute()
            except Exception:
                continue


def resolve_user_names(user_ids: list[str]) -> dict[str, str]:
    """Look up display names for a batch of profile UUIDs. Missing ids
    return an empty string. Used by the CSV export to render the
    `last_exported_by_name` column without doing a row-by-row join."""
    if not user_ids:
        return {}
    client = _get_client()
    if not client:
        return {}
    try:
        result = (
            client.table("profiles").select("id,name")
            .in_("id", list({u for u in user_ids if u}))
            .execute()
        )
    except Exception:
        return {}
    return {row["id"]: (row.get("name") or "") for row in (result.data or [])}


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
    company_id: str | None = None,
) -> dict | None:
    """Update Phase 2 analysis fields. Stamps attribution when touched_by set.

    Uses the optional-column retry so a missing post-deploy migration
    (e.g. `analysis_input_hash` / `website_contacts`) degrades gracefully
    instead of 500-ing the analyze endpoint.

    Phase 3 dual-write: when company_id is set, also upserts the analysis
    into company_practice_analyses and the auto-status into
    company_practice_state.
    """
    result = _update_with_optional_retry(
        place_id,
        _with_attribution(analysis, touched_by),
    )

    if company_id:
        # Split: most analysis dict keys are analysis; status (auto-advance) is state.
        analysis_part = {k: v for k, v in analysis.items() if k in _ANALYSIS_FIELDS}
        state_part = {k: v for k, v in analysis.items() if k in _STATE_FIELDS}
        _write_per_company_analyses(place_id, company_id, analysis_part)
        if state_part:
            _write_per_company_state(place_id, company_id, state_part, touched_by)

    return result


def update_practice_fields(
    place_id: str,
    fields: dict,
    touched_by: str | None = None,
    company_id: str | None = None,
) -> dict | None:
    """Update arbitrary fields. Stamps attribution when touched_by set.

    If an optional column doesn't exist in the DB yet, retries the update
    without that column instead of failing the whole write.

    Phase 3 dual-write: when company_id is set, splits the dict by
    category (state vs analysis vs other) and upserts into the matching
    per-company table(s). `tags` is routed through the tag-union helper.
    """
    result = _update_with_optional_retry(
        place_id,
        _with_attribution(fields, touched_by),
    )

    if company_id:
        state, analysis, new_tags = _split_fields_for_dual_write(fields)
        if state:
            _write_per_company_state(place_id, company_id, state, touched_by)
        if analysis:
            _write_per_company_analyses(place_id, company_id, analysis)
        if new_tags:
            _add_tags_per_company(place_id, company_id, new_tags)

    return result


def insert_email_message(
    practice_id: int,
    user_id: str | None,
    direction: str,
    subject: str | None,
    body: str | None,
    message_id: str | None,
    in_reply_to: str | None,
    error: str | None,
    company_id: str | None = None,
) -> dict | None:
    """Insert a row into email_messages. Returns the inserted row.

    Phase 3 dual-write: when company_id is set, also mirrors into
    company_email_messages so Phase 4 can swap reads cleanly.
    """
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
    inserted = result.data[0] if result.data else None

    if company_id and practice_id:
        try:
            client.table("company_email_messages").insert(
                {**row, "company_id": company_id}
            ).execute()
        except Exception:
            pass

    return inserted


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


def add_tags(
    place_id: str,
    new_tags: list[str],
    company_id: str | None = None,
) -> None:
    """Append tags to a practice's tags array, deduped. No-op if list empty.

    Reads current tags, computes union, writes back. Two roundtrips is fine
    for our write rate; postgres array_cat is not exposed via the PostgREST
    client, so this read-modify-write pattern is the simplest reliable shape.

    Phase 3 dual-write: when company_id is set, also unions the same tags
    into company_practice_state for (company_id, practice_id).
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
    if sorted(existing) != merged:
        client.table("practices").update({"tags": merged}).eq("place_id", place_id).execute()

    # Mirror to per-company state (handles its own dedup separately).
    if company_id:
        _add_tags_per_company(place_id, company_id, new_tags)


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
