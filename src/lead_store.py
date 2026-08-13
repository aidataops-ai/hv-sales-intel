"""Persistence for job-posting leads — the three tables and nothing else.

The module is split down the middle by ADR-04's column groups, and that split
is the whole point:

    VERDICT_COLUMNS   written by the qualifier, safe to overwrite on re-qualify
    WORKFLOW_COLUMNS  written by operators, never touched by the qualifier

`write_verdicts` filters its payload down to `VERDICT_COLUMNS` before it
writes. A careless `update ... set disposition = ...` from a re-qualification
pass would silently reset every SDR's pipeline, and because the leads still
*look* fine afterwards nobody would notice until a rep asked where their
approvals went. `tests/test_lead_store.py` covers it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("hvsi.leads.store")

# ADR-07 bands. Ranked because 'ready' < 'check' < 'decide' is not the
# alphabetical order of those words, and the feed sorts on it.
BAND_RANK = {"ready": 1, "check": 2, "decide": 3}
READY_THRESHOLD = 0.85
CHECK_THRESHOLD = 0.70

# Written by the qualifier. Every re-qualification overwrites exactly these.
VERDICT_COLUMNS = frozenset({
    "decision", "confidence", "confidence_band", "band_rank", "reason",
    "employer_type", "role_suitable", "work_mode", "service_line",
    "provider_count", "draft", "model", "qualified_at",
})

# Written by operators. The qualifier must never touch these.
WORKFLOW_COLUMNS = frozenset({
    "disposition", "reject_reason", "notes",
    "last_touched_by", "last_touched_at", "contacted_at",
})

LEAD_DISPOSITIONS = ("undecided", "approved", "rejected")

# What the feed shows when nothing is asked for. See `_apply_filters`.
DEFAULT_DECISION = "keep"
DECISION_FILTERS = ("keep", "discard", "all")

# Columns of the linked practice (job_postings.practice_id -> practices.id).
# The provider data an operator needs to act on a signal — who to call, where,
# the website — plus the match fields so a 'review'-grade link reads as less
# certain than an auto one. Embedded to-one, so it is null on unlinked postings.
_PRACTICE_COLS = (
    "id, place_id, name, address, city, state, phone, website, "
    "category, service_line, rating, review_count"
)

# One row per lead, with its posting inlined. `!inner` matters: it makes the
# embed a join rather than a nested fetch, so filters on posting columns
# (city, source, work mode) restrict the *lead* rows instead of just blanking
# the embedded object. The practice is a nested to-one embed under the posting.
LEAD_SELECT = (
    f"*, posting:job_postings!inner(*, practice:practices({_PRACTICE_COLS}))"
)

# The list feed shows a table row per lead and never renders the two heavy text
# columns — the lead `draft` (up to 8 KB) and the posting `description` (the full
# raw posting). Fetching `*` dragged both along on every page, so a 25-row page
# carried hundreds of KB the table throws away. This select names every column
# EXCEPT those two, keeping the payload to what the feed actually paints. The
# detail view still uses `LEAD_SELECT` — it needs both, and it is one row.
_LEAD_LIST_COLS = (
    "id, company_id, posting_id, decision, confidence, confidence_band, "
    "band_rank, reason, employer_type, role_suitable, work_mode, service_line, "
    "provider_count, model, qualified_at, disposition, reject_reason, notes, "
    "last_touched_by, last_touched_at, contacted_at, "
    "export_count, last_exported_at, last_exported_by, created_at"
)
_POSTING_LIST_COLS = (
    "id, source, external_id, url, title, employer_name, employer_name_norm, "
    "location_raw, city, state, posted_at, salary_min, salary_max, "
    "salary_interval, board_remote_flag, search_term, search_location, "
    "service_line_hint, first_seen_at, last_seen_at, "
    "practice_id, match_confidence, match_status"
)
LEAD_LIST_SELECT = (
    f"{_LEAD_LIST_COLS}, posting:job_postings!inner("
    f"{_POSTING_LIST_COLS}, practice:practices({_PRACTICE_COLS}))"
)

_PAGE = 1000


def band_for(confidence: float | None) -> tuple[str, int]:
    """Map a confidence score onto its triage band (ADR-07).

    Measured across repeated runs: zero of 32 high-confidence postings ever
    changed decision, while every observed flip sat in the mid and low bands.
    The 0.85 boundary is an initial estimate to be tuned against
    booked-versus-rejected outcomes once that data exists.
    """
    if confidence is None:
        return "decide", BAND_RANK["decide"]
    if confidence >= READY_THRESHOLD:
        return "ready", BAND_RANK["ready"]
    if confidence >= CHECK_THRESHOLD:
        return "check", BAND_RANK["check"]
    return "decide", BAND_RANK["decide"]


def _client():
    from src.storage import _get_client
    return _get_client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _flatten(row: dict) -> dict:
    """Lift the embedded posting onto the lead so the API returns one object.

    Posting fields are prefixed only where they would collide with a lead
    column (`source` and `url` are unambiguous; `created_at` is not).
    """
    if not row:
        return row
    posting = row.pop("posting", None) or {}
    # Lift the nested practice up to a top-level key, not folded into the
    # posting fields — it is its own object (or null), and callers render it
    # as a distinct panel rather than more posting columns.
    practice = posting.pop("practice", None)
    merged = dict(row)
    merged["practice"] = practice
    merged["posting_created_at"] = posting.pop("first_seen_at", None)
    posting.pop("id", None)
    for key, value in posting.items():
        merged.setdefault(key, value)
    return merged


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def upsert_postings(rows: list[dict]) -> int:
    """Upsert raw postings on `(source, external_id)`. Returns rows written.

    Postings are shared across tenants (ADR-04), so this is a plain upsert with
    no company scoping. `last_seen_at` is refreshed on every sighting while
    `first_seen_at` keeps its insert default — the gap between the two is how
    long a posting has stayed open, which is the closest thing v1 has to a
    staleness signal.
    """
    client = _client()
    if not client or not rows:
        return 0

    stamped = [{**row, "last_seen_at": _now()} for row in rows]
    written = 0
    CHUNK = 200
    for i in range(0, len(stamped), CHUNK):
        batch = stamped[i:i + CHUNK]
        try:
            result = (
                client.table("job_postings")
                .upsert(batch, on_conflict="source,external_id")
                .execute()
            )
            written += len(result.data or [])
        except Exception as e:
            log.warning("[leads.postings.upsert_error] %s: %s",
                        type(e).__name__, str(e)[:250])
    return written


def existing_external_ids(source: str, external_ids: list[str]) -> set[str]:
    """Which of these `(source, external_id)` pairs are already in `job_postings`.

    The novelty metric instant-signals is built around (plan §3's whole point
    is killing redundant re-fetches) needs a NEW-vs-seen split that
    `upsert_postings` cannot provide on its own: PostgREST's upsert returns
    every affected row, inserted or updated alike, so its return count is
    "rows written", not "rows that were new". The collector calls this once
    per search — before the upsert — and treats anything not in the returned
    set as new.
    """
    client = _client()
    if not client or not external_ids:
        return set()
    try:
        result = (
            client.table("job_postings")
            .select("external_id")
            .eq("source", source)
            .in_("external_id", external_ids)
            .execute()
        )
    except Exception as e:
        log.warning("[leads.postings.novelty_error] %s: %s",
                    type(e).__name__, str(e)[:200])
        return set()
    return {r["external_id"] for r in (result.data or []) if r.get("external_id")}


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------


def claim_unqualified(company_id: str, limit: int) -> list[dict]:
    """Postings this tenant has no lead row for yet, newest first.

    Implemented as a set difference rather than a `not exists` join because
    PostgREST cannot express anti-joins. The tenant's existing `posting_id` set
    is the bounded side — it only ever contains postings this tenant has
    already paid to qualify, and `unique (company_id, posting_id)` guarantees
    one entry each. If that set outgrows a few tens of thousands, this wants to
    become a database view.
    """
    client = _client()
    if not client or not company_id or limit <= 0:
        return []

    qualified: set[int] = set()
    page = 0
    while True:
        try:
            result = (
                client.table("company_job_leads")
                .select("posting_id")
                .eq("company_id", company_id)
                .range(page * _PAGE, page * _PAGE + _PAGE - 1)
                .execute()
            )
        except Exception as e:
            log.warning("[leads.claim.read_error] %s: %s",
                        type(e).__name__, str(e)[:200])
            return []
        batch = result.data or []
        qualified.update(r["posting_id"] for r in batch)
        if len(batch) < _PAGE:
            break
        page += 1

    # Scan newest postings first and stop as soon as the batch is full. The
    # window is generous because a tenant that has qualified everything recent
    # would otherwise see only its own already-done rows.
    candidates: list[dict] = []
    scanned = 0
    page = 0
    while len(candidates) < limit and scanned < limit * 20 + _PAGE:
        try:
            result = (
                client.table("job_postings")
                .select("*")
                .order("id", desc=True)
                .range(page * _PAGE, page * _PAGE + _PAGE - 1)
                .execute()
            )
        except Exception as e:
            log.warning("[leads.claim.postings_error] %s: %s",
                        type(e).__name__, str(e)[:200])
            break
        batch = result.data or []
        scanned += len(batch)
        for posting in batch:
            if posting["id"] not in qualified:
                candidates.append(posting)
                if len(candidates) >= limit:
                    break
        if len(batch) < _PAGE:
            break
        page += 1

    return candidates


def write_verdicts(company_id: str, verdicts: list[dict]) -> int:
    """Insert or refresh the verdict half of a lead row. Returns rows written.

    **Workflow columns are stripped, not merely omitted.** A caller that
    accidentally passes `disposition` gets it dropped here rather than
    clobbering an SDR's pipeline — the qualifier runs unattended on a cron, so a
    bug in it would go unnoticed until someone asked where their approvals went.

    New rows land at `disposition='undecided'` via the column default.
    Re-qualified rows keep whatever disposition they had.
    """
    client = _client()
    if not client or not company_id or not verdicts:
        return 0

    # Every row carries the full verdict column set, explicit nulls included.
    # PostgREST rejects a bulk upsert whose rows have differing keys, so one
    # verdict that happened to omit `draft` would 400 the whole batch — and
    # the batch is 20 postings the tenant has already been billed for.
    stamped = _now()
    payload = []
    for verdict in verdicts:
        posting_id = verdict.get("posting_id")
        if not posting_id:
            continue
        row = {column: verdict.get(column) for column in VERDICT_COLUMNS}
        row["qualified_at"] = verdict.get("qualified_at") or stamped
        row["company_id"] = company_id
        row["posting_id"] = posting_id
        payload.append(row)

    if not payload:
        return 0

    written = 0
    CHUNK = 200
    for i in range(0, len(payload), CHUNK):
        batch = payload[i:i + CHUNK]
        try:
            result = (
                client.table("company_job_leads")
                .upsert(batch, on_conflict="company_id,posting_id")
                .execute()
            )
            written += len(result.data or [])
        except Exception as e:
            log.warning("[leads.verdicts.write_error] %s: %s",
                        type(e).__name__, str(e)[:250])
    return written


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------

# User-facing sort keys -> (column, is_on_the_embedded_posting). Anything not
# in here falls back to the band so a bad ?sort= can't reach the query builder.
_SORT_COLUMNS: dict[str, tuple[str, bool]] = {
    "band":       ("band_rank", False),
    "posted":     ("posted_at", True),
    "employer":   ("employer_name", True),
    "role":       ("title", True),
    "city":       ("city", True),
    "track":      ("service_line", False),
    "confidence": ("confidence", False),
    "disposition": ("disposition", False),
    "created":    ("created_at", False),
}


def _apply_filters(query, *, filters: dict):
    """Every feed filter, shared by the list and the CSV export.

    Sharing this is what stops the obvious export trap: an operator looking at
    "Miami + Tampa, Dental track" who exports the whole table instead.
    """
    if cities := filters.get("cities"):
        query = query.in_("posting.city", cities)
    if tracks := filters.get("tracks"):
        query = query.in_("service_line", tracks)
    if disposition := filters.get("disposition"):
        query = query.eq("disposition", disposition)
    if band := filters.get("band"):
        query = query.eq("confidence_band", band)
    # The qualifier writes a row for every posting it judges, and most are
    # discards — systems, DSOs, agencies, clinical roles. Storing them is
    # deliberate (they feed the reject-reason analytics and stop a posting
    # being re-qualified and re-billed), but showing them by default would
    # bury a handful of real leads in the noise. "all" is the explicit
    # opt-out, used when spot-checking the qualifier.
    decision = filters.get("decision") or DEFAULT_DECISION
    if decision != "all":
        query = query.eq("decision", decision)
    if work_mode := filters.get("work_mode"):
        query = query.eq("work_mode", work_mode)
    if source := filters.get("source"):
        query = query.eq("posting.source", source)
    if state := filters.get("state"):
        query = query.eq("posting.state", state)
    if filters.get("salary") == "yes":
        query = query.not_.is_("posting.salary_min", "null")
    elif filters.get("salary") == "no":
        query = query.is_("posting.salary_min", "null")
    # Whether the posting resolved to a practice in the bank. "yes" is the set
    # an operator can act on with provider data in hand; "no" is the backlog
    # the next scan/matcher pass should try to cover.
    if filters.get("practice") == "yes":
        query = query.not_.is_("posting.practice_id", "null")
    elif filters.get("practice") == "no":
        query = query.is_("posting.practice_id", "null")
    if search := (filters.get("search") or "").strip():
        # Commas separate OR members and parens delimit groups; `*` is the
        # wildcard. Strip all of them so a search term can't corrupt the filter.
        for ch in (",", "(", ")", "*"):
            search = search.replace(ch, " ")
        search = search.strip()
        if search:
            like = f"*{search}*"
            query = query.or_(
                f"employer_name.ilike.{like},title.ilike.{like}",
                reference_table="posting",
            )
    return query


def list_leads(
    company_id: str,
    *,
    filters: dict | None = None,
    sort: str = "band",
    direction: str = "asc",
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    """One page of the tenant's feed plus the exact total. `([], 0)` if unset.

    Default order is ADR-07's: band first, then posting recency. Anything the
    operator picks instead still breaks ties on recency, because two leads in
    the same band with the same status are otherwise ordered arbitrarily and
    the page would reshuffle between loads.
    """
    client = _client()
    if not client or not company_id:
        return [], 0

    filters = filters or {}
    column, on_posting = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["band"])
    desc = (direction or "asc").lower() == "desc"
    start = max(0, offset)
    end = start + max(1, limit) - 1

    def _ordered(query):
        if on_posting:
            query = query.order(column, desc=desc, foreign_table="posting",
                                nullsfirst=False)
        else:
            query = query.order(column, desc=desc, nullsfirst=False)
        # Secondary: posting recency, then salary present, then id for
        # a stable page boundary.
        if not (on_posting and column == "posted_at"):
            query = query.order("posted_at", desc=True, foreign_table="posting",
                                nullsfirst=False)
        return query.order("id", desc=True)

    try:
        rows = _ordered(
            _apply_filters(client.table("company_job_leads").select(LEAD_LIST_SELECT),
                           filters=filters)
            .eq("company_id", company_id)
        ).range(start, end).execute().data or []
    except Exception as e:
        log.warning("[leads.list.error] %s: %s", type(e).__name__, str(e)[:250])
        return [], 0

    total = start + len(rows)
    try:
        counted = (
            _apply_filters(
                client.table("company_job_leads")
                .select("id, posting:job_postings!inner(id)", count="exact"),
                filters=filters,
            )
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
        if counted.count is not None:
            total = counted.count
    except Exception:
        # A failed count still leaves a usable page — the pager just can't
        # show a final page number.
        pass

    return [_flatten(r) for r in rows], total


def get_lead(company_id: str, lead_id: int) -> dict | None:
    client = _client()
    if not client or not company_id:
        return None
    try:
        result = (
            client.table("company_job_leads").select(LEAD_SELECT)
            .eq("company_id", company_id).eq("id", lead_id)
            .maybe_single().execute()
        )
    except Exception:
        return None
    return _flatten(result.data) if result and result.data else None


def update_lead_workflow(
    company_id: str,
    lead_id: int,
    fields: dict,
    user_id: str | None = None,
) -> dict | None:
    """Write the workflow half of a lead. Verdict columns are stripped.

    The mirror image of `write_verdicts`: an operator action must never
    overwrite a qualifier field, or a disposition change would rewrite the
    reason the lead was surfaced in the first place.

    `last_touched_by`/`last_touched_at` are stamped from the write rather than
    trusted from the client, so the history reflects who actually touched it.
    """
    client = _client()
    if not client or not company_id:
        return None

    payload = {k: v for k, v in fields.items() if k in WORKFLOW_COLUMNS}
    if not payload:
        return get_lead(company_id, lead_id)

    if user_id:
        payload["last_touched_by"] = user_id
        payload["last_touched_at"] = _now()

    try:
        (
            client.table("company_job_leads").update(payload)
            .eq("company_id", company_id).eq("id", lead_id).execute()
        )
    except Exception as e:
        log.warning("[leads.update.error] %s: %s", type(e).__name__, str(e)[:250])
        return None
    return get_lead(company_id, lead_id)


# ---------------------------------------------------------------------------
# Filter options + export
# ---------------------------------------------------------------------------


def filter_options(company_id: str) -> dict[str, list[str]]:
    """Distinct cities and tracks present in this tenant's KEPT leads.

    Derived from the data rather than a fixed list: a tenant whose targets
    cover three cities should not scroll past thirty empty ones.

    Two things this must get right, both learned the hard way:

    - Kept leads only (`service_line not null`). Discards outnumber keeps ~13:1,
      carry no track, and are never shown in the signals list — so they must not
      populate its filters, and letting them in silently starved the real facets.
    - Paginate. PostgREST caps any response at `_PAGE` rows regardless of the
      requested `limit`, so a single `.limit(20_000)` returned an arbitrary
      1000-row window. A track with only a handful of leads (a freshly added
      one) fell outside that window and never appeared in the dropdown.
    """
    client = _client()
    if not client or not company_id:
        return {"cities": [], "tracks": [], "states": []}

    cities, tracks, states = set(), set(), set()
    page = 0
    while True:
        try:
            batch = (
                client.table("company_job_leads")
                .select("service_line, posting:job_postings!inner(city, state)")
                .eq("company_id", company_id)
                .not_.is_("service_line", "null")
                .range(page * _PAGE, page * _PAGE + _PAGE - 1)
                .execute()
            ).data or []
        except Exception:
            return {"cities": [], "tracks": [], "states": []}

        for row in batch:
            posting = row.get("posting") or {}
            if posting.get("city"):
                cities.add(posting["city"])
            if posting.get("state"):
                states.add(posting["state"])
            if row.get("service_line"):
                tracks.add(row["service_line"])

        if len(batch) < _PAGE:
            break
        page += 1

    return {
        "cities": sorted(cities),
        "tracks": sorted(tracks),
        "states": sorted(states),
    }


def leads_for_export(
    company_id: str,
    *,
    filters: dict | None = None,
    max_exports: int | None = None,
) -> list[dict]:
    """Every lead matching the active filters, for the CSV export.

    `max_exports` mirrors the practices export exactly:
      - None → no filter; export every matching row
      - 0    → only never-exported rows (export_count = 0)
      - N    → only rows with export_count <= N
    """
    client = _client()
    if not client or not company_id:
        return []

    rows: list[dict] = []
    page = 0
    while len(rows) < 50_000:
        query = _apply_filters(
            client.table("company_job_leads").select(LEAD_SELECT),
            filters=filters or {},
        ).eq("company_id", company_id)
        if max_exports is not None:
            query = query.lte("export_count", max_exports)
        try:
            batch = (
                query.order("band_rank", desc=False, nullsfirst=False)
                .order("id", desc=True)
                .range(page * _PAGE, page * _PAGE + _PAGE - 1)
                .execute()
            ).data or []
        except Exception as e:
            log.warning("[leads.export.error] %s: %s", type(e).__name__, str(e)[:250])
            break
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        page += 1
    return [_flatten(r) for r in rows]


def increment_export_counts(
    lead_ids: list[int],
    user_id: str | None = None,
) -> None:
    """Bump `export_count` and stamp who/when, so a follow-up export with
    `max_exports=0` skips the rows already pulled.

    Read-then-write per row: supabase-py exposes no `+= 1` SQL fragment, and
    export batches are infrequent enough that it doesn't matter.
    """
    client = _client()
    if not client or not lead_ids:
        return
    now = _now()
    CHUNK = 500
    for i in range(0, len(lead_ids), CHUNK):
        chunk = lead_ids[i:i + CHUNK]
        try:
            existing = (
                client.table("company_job_leads")
                .select("id,export_count").in_("id", chunk).execute()
            ).data or []
        except Exception:
            continue
        for row in existing:
            payload: dict[str, Any] = {
                "export_count": (row.get("export_count") or 0) + 1,
                "last_exported_at": now,
            }
            if user_id:
                payload["last_exported_by"] = user_id
            try:
                client.table("company_job_leads").update(payload).eq(
                    "id", row["id"]
                ).execute()
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def lead_analytics(company_id: str, days: int = 30) -> dict:
    """Aggregates for `/signals/analytics`.

    Computed in Python over a bounded row set rather than in SQL: the numbers
    are small (a tenant's leads, not the shared posting universe), and keeping
    it here means the analytics view needs no database function to deploy
    alongside it.
    """
    client = _client()
    if not client or not company_id:
        return _empty_analytics()

    try:
        rows = (
            client.table("company_job_leads")
            .select(
                "decision, confidence_band, disposition, reject_reason, service_line, "
                "created_at, posting:job_postings!inner(source, posted_at)"
            )
            .eq("company_id", company_id)
            .order("id", desc=True)
            .limit(20_000)
            .execute()
        ).data or []
    except Exception as e:
        log.warning("[leads.analytics.error] %s: %s", type(e).__name__, str(e)[:250])
        return _empty_analytics()

    by_day: dict[str, dict[str, int]] = {}
    bands: dict[str, int] = {}
    dispositions: dict[str, int] = {}
    reject_reasons: dict[str, int] = {}
    tracks: dict[str, int] = {}
    keeps = 0

    for row in rows:
        posting = row.get("posting") or {}
        day = (row.get("created_at") or "")[:10]
        if day:
            bucket = by_day.setdefault(day, {"total": 0})
            bucket["total"] += 1
            source = posting.get("source") or "unknown"
            bucket[source] = bucket.get(source, 0) + 1
        is_keep = row.get("decision") == "keep"
        if is_keep:
            keeps += 1
        # Bands are counted over KEEPS ONLY. The band measures confidence in
        # the verdict, not lead quality, so a confidently-rejected hospital
        # system also scores "ready" — and discards outnumber keeps roughly
        # 9:1. Counting everything made the chart read as "766 strong leads"
        # when 743 of those were confident rejections.
        if is_keep and (band := row.get("confidence_band")):
            bands[band] = bands.get(band, 0) + 1
        disposition = row.get("disposition") or "undecided"
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
        if disposition == "rejected":
            reason = (row.get("reject_reason") or "(no reason given)").strip()
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        if track := row.get("service_line"):
            tracks[track] = tracks.get(track, 0) + 1

    return {
        "total": len(rows),
        "keep_rate": round(keeps / len(rows), 3) if rows else 0.0,
        "per_day": [
            {"day": day, **counts}
            for day, counts in sorted(by_day.items())[-days:]
        ],
        "bands": bands,
        "dispositions": dispositions,
        "tracks": tracks,
        "reject_reasons": sorted(
            ({"reason": r, "count": c} for r, c in reject_reasons.items()),
            key=lambda x: -x["count"],
        )[:20],
        "collector": collector_health(company_id),
    }


def _empty_analytics() -> dict:
    return {
        "total": 0, "keep_rate": 0.0, "per_day": [], "bands": {},
        "dispositions": {}, "tracks": {}, "reject_reasons": [],
        "collector": {"locations": 0, "swept": 0, "unfinished": 0,
                      "zero_row_locations": 0, "last_run_at": None,
                      "last_posting_at": None, "alert": None},
    }


def collector_health(company_id: str) -> dict:
    """Is collection actually returning rows?

    The Indeed failure mode is silence, not an error: the library reaches an
    undocumented mobile API whose embedded key can be rotated upstream without
    notice, after which every query returns zero rows and nothing raises. A
    sweep that swept locations and kept nothing is the tripwire (ADR-02).

    Rewritten for the dimension model (instant-signals refactor, Phase 2):
    `company_search_targets.last_run_at` (stamped at claim) no longer exists.
    `search_locations` has a per-source cursor stamped only once a location's
    FULL sweep finishes (`stamp_location`), so a cursor is proof of a
    completed sweep, not a claim — "unfinished" now has to come from
    `target_runs` instead: a location with at least one recorded cell but no
    cursor on either source is a sweep that was interrupted mid-flight.
    """
    client = _client()
    if not client or not company_id:
        return {"locations": 0, "swept": 0, "unfinished": 0,
                "zero_row_locations": 0, "last_run_at": None,
                "last_posting_at": None, "alert": None}
    try:
        locations = (
            client.table("search_locations")
            .select("id,last_indeed_at,last_linkedin_at,"
                    "indeed_zero_streak,linkedin_zero_streak")
            .eq("company_id", company_id).eq("enabled", True)
            .limit(5000).execute()
        ).data or []
    except Exception:
        locations = []

    swept = [l for l in locations if l.get("last_indeed_at") or l.get("last_linkedin_at")]
    swept_ids = {l.get("id") for l in swept}

    location_ids = [l.get("id") for l in locations if l.get("id") is not None]
    started_ids: set = set()
    if location_ids:
        # A `.limit(N)` alone does not paginate — PostgREST truncates any
        # single request at 1000 rows regardless of what N asks for.
        # `_paginated_query` issues successive `.range()` calls to get past
        # that; term_count x location_count can exceed 1000 well before a
        # tenant's dimension tables do.
        from src.storage import _paginated_query

        builder = (
            client.table("target_runs")
            .select("location_id")
            .in_("location_id", location_ids)
        )
        runs = _paginated_query(builder, limit=20_000)
        started_ids = {r["location_id"] for r in runs if r.get("location_id")}
    # Claimed (has a recorded cell) but never finished (no cursor on either
    # source) — a run that was interrupted. Not an error on its own; a
    # persistently high number means runs are being killed mid-sweep,
    # probably by a function timeout.
    unfinished = len(started_ids - swept_ids)

    # Zero-row tripwire: the zero-streak columns already ARE "did the last
    # full sweep on this source return nothing" (record_location_sweep), so
    # no per-cell scan of target_runs is needed here — a location counts as
    # zero-row if every source it has swept currently carries a nonzero
    # streak.
    def _is_zero_row(loc: dict) -> bool:
        swept_sources = [s for s in ("indeed", "linkedin") if loc.get(f"last_{s}_at")]
        return bool(swept_sources) and all(
            (loc.get(f"{s}_zero_streak") or 0) > 0 for s in swept_sources
        )

    zero_rows = [l for l in swept if _is_zero_row(l)]
    last_run = max(
        (t for l in locations
         for t in (l.get("last_indeed_at"), l.get("last_linkedin_at")) if t),
        default=None,
    )

    try:
        newest = (
            client.table("job_postings").select("last_seen_at")
            .order("last_seen_at", desc=True).limit(1).execute()
        ).data or []
        last_posting = newest[0]["last_seen_at"] if newest else None
    except Exception:
        last_posting = None

    alert = None
    if swept and len(zero_rows) == len(swept):
        alert = (
            "Every swept location returned zero rows. This is the Indeed "
            "API-key rotation failure mode — check the python-jobspy pin."
        )
    elif swept and len(zero_rows) > len(swept) * 0.8:
        alert = f"{len(zero_rows)} of {len(swept)} swept locations returned zero rows."

    return {
        "locations": len(locations),
        "swept": len(swept),
        "unfinished": unfinished,
        "zero_row_locations": len(zero_rows),
        "last_run_at": last_run,
        "last_posting_at": last_posting,
        "alert": alert,
    }
