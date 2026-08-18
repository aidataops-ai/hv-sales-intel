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
    "category, service_line, rating, review_count, "
    "owner_name, owner_email, owner_phone, owner_title"
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
    "export_count, last_exported_at, last_exported_by, created_at, "
    "talentdb_exported_at"
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

# The CSV export is a pure Talent-DB mapping: every column below is read by
# `talentdb.build_fields` (whose keys ARE `talentdb.CSV_COLUMNS`) or by the
# route's `_posting_from_lead`. Two consequences worth spelling out:
#
# - `description` STAYS. It is the CSV's `posting_description` column — the one
#   heavy column the export genuinely needs. The lead `draft` (up to 8 KB) does
#   not, and neither do reason / notes / model / the workflow timestamps.
# - The practice embed shrinks to `place_id`. The export route immediately
#   re-fetches the full practice rows by place_id (`get_practices_by_place_ids`)
#   because build_fields needs the analysis/CRM columns the embed never carried
#   — so every other embedded column was fetched and then thrown away.
#
# Filter columns (`export_count`, the posting's `salary_min` / `practice_id`)
# are deliberately absent: PostgREST filters against the table, not the
# projection.
_LEAD_EXPORT_COLS = "id, posting_id, service_line, provider_count"
_POSTING_EXPORT_COLS = (
    "source, url, title, employer_name, city, state, posted_at, "
    "board_remote_flag, description, search_term, search_location, "
    "service_line_hint, first_seen_at, last_seen_at, "
    "match_confidence, match_status"
)
LEAD_EXPORT_SELECT = (
    f"{_LEAD_EXPORT_COLS}, posting:job_postings!inner("
    f"{_POSTING_EXPORT_COLS}, practice:practices(place_id))"
)

# The same posting columns, fetched directly rather than through a lead — so
# this one needs the `id` the export reads off the lead's `posting_id`.
# `get_posting` is the only reader, and it exists for exactly one job: the
# practice-detail Import Lead button, which hands the row to
# `talentdb.build_fields`. So this list is "what build_fields reads", and the
# test in tests/test_lead_store.py holds it to that. What it drops is what
# build_fields never touches: `external_id`, `employer_name_norm`,
# `location_raw`, the salary triple, `practice_id`, `match_method`,
# `matched_at`.
_POSTING_TALENTDB_COLS = f"id, {_POSTING_EXPORT_COLS}"

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


# Every column the qualify path actually reads off a claimed posting, and
# nothing else. `lead_qualifier._posting_row` renders title / employer_name /
# location_raw / board_remote_flag / the three salary columns /
# service_line_hint / description; `parse_verdict` reads `id` and
# `service_line_hint`; both callers pass `[p["id"] for p in postings]` to
# `practice_matcher.link_postings`, which re-selects its own columns. Nothing
# reads url, city, state, posted_at, source, external_id, first/last_seen_at
# or the match_* link columns, so `select("*")` was paying for all of them.
#
# `description` is the expensive one — the whole reason `job_postings` is a
# 45 MB table — and it IS needed here, so it is fetched, but only for the
# `limit` rows that survive the anti-join, never for the scan.
_QUALIFY_POSTING_COLS = (
    "id, title, employer_name, location_raw, board_remote_flag, "
    "salary_min, salary_max, salary_interval, service_line_hint, description"
)


def claim_unqualified(company_id: str, limit: int) -> list[dict]:
    """Postings this tenant has no lead row for yet, newest first.

    Implemented as a set difference rather than a `not exists` join because
    PostgREST cannot express anti-joins. The tenant's existing `posting_id` set
    is the bounded side — it only ever contains postings this tenant has
    already paid to qualify, and `unique (company_id, posting_id)` guarantees
    one entry each. If that set outgrows a few tens of thousands, this wants to
    become a database view.

    Two phases, and the split is the point. The anti-join scan pages
    `select("id")` — in steady state nearly every scanned row is already
    qualified, so pulling whole rows meant transferring most of a 45 MB table
    (full `description` bodies included) on every run just to test integers
    against a Python set. Only the `limit` survivors are then re-fetched with
    the columns the qualifier and the matcher actually read
    (`_QUALIFY_POSTING_COLS`). Same rows, same order, ~1/1000th the egress.
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

    # Phase 1 — the anti-join. Scan newest postings first and stop as soon as
    # the batch is full. The window is generous because a tenant that has
    # qualified everything recent would otherwise see only its own already-done
    # rows — which is exactly why this scan must stay id-only: the generous
    # window is the whole table in steady state.
    claimed: list[int] = []
    scanned = 0
    page = 0
    while len(claimed) < limit and scanned < limit * 20 + _PAGE:
        try:
            result = (
                client.table("job_postings")
                .select("id")
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
                claimed.append(posting["id"])
                if len(claimed) >= limit:
                    break
        if len(batch) < _PAGE:
            break
        page += 1

    if not claimed:
        return []

    # Phase 2 — hydrate the survivors, and only the survivors. Chunked so a
    # large `limit` can never blow the URL out; PostgREST does not promise an
    # order on an `in_` filter, so the sort is restated rather than assumed.
    CHUNK = 500
    by_id: dict[int, dict] = {}
    for i in range(0, len(claimed), CHUNK):
        try:
            rows = (
                client.table("job_postings")
                .select(_QUALIFY_POSTING_COLS)
                .in_("id", claimed[i:i + CHUNK])
                .order("id", desc=True)
                .execute()
            ).data or []
        except Exception as e:
            log.warning("[leads.claim.hydrate_error] %s: %s",
                        type(e).__name__, str(e)[:200])
            break
        for row in rows:
            by_id[row["id"]] = row

    # Re-key onto the scan order so callers still see newest-first, whatever
    # the server returned. A row that vanished between the two phases (the
    # retention job nulls bodies but never deletes; a manual delete could)
    # simply isn't claimed this run.
    return [by_id[pid] for pid in claimed if pid in by_id]


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
    if states := filters.get("states"):
        query = query.in_("posting.state", states)
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
    """One page of the tenant's feed plus a total. `([], 0)` if unset.

    Default order is ADR-07's: band first, then posting recency. Anything the
    operator picks instead still breaks ties on recency, because two leads in
    the same band with the same status are otherwise ordered arbitrarily and
    the page would reshuffle between loads.

    `total` is exact on the last page and a planner estimate before it —
    `count="exact"` re-counted the whole 29k-row join on every page. Callers
    turn it into `has_more` (`offset + len(rows) < total`), and THAT stays
    exact: we fetch one row past the page, so "is there another page?" is
    answered by data and the estimate can only widen a total already known to
    be short. Paging therefore never stalls on a stale estimate.
    """
    client = _client()
    if not client or not company_id:
        return [], 0

    filters = filters or {}
    column, on_posting = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["band"])
    desc = (direction or "asc").lower() == "desc"
    start = max(0, offset)
    page_size = max(1, limit)
    probe_end = start + page_size  # .range() is inclusive → page_size + 1 rows

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
        ).range(start, probe_end).execute().data or []
    except Exception as e:
        log.warning("[leads.list.error] %s: %s", type(e).__name__, str(e)[:250])
        return [], 0

    # Short read → the page ran off the end of the feed, so this total is
    # exact and the count query is skipped entirely.
    if len(rows) <= page_size:
        return [_flatten(r) for r in rows], start + len(rows)
    rows = rows[:page_size]

    total = start + page_size + 1
    try:
        counted = (
            _apply_filters(
                client.table("company_job_leads")
                .select("id, posting:job_postings!inner(id)", count="planned"),
                filters=filters,
            )
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
        if counted.count is not None:
            total = max(total, counted.count)
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

    Returns the row the UPDATE itself returned — PostgREST hands back the
    updated representation, so there is no reason to read it again. `None`
    means the write matched no row: either the lead doesn't exist or it
    belongs to another tenant (the `company_id` filter is what makes those
    the same case), which is why the caller answers it with a 404. A write
    that raised is logged above and also reports `None`.

    Note the returned row is the RAW lead — no posting/practice embed. A
    caller that has to render the lead still needs one `get_lead`; a caller
    that only needs to know the write landed does not.
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
        result = (
            client.table("company_job_leads").update(payload)
            .eq("company_id", company_id).eq("id", lead_id).execute()
        )
    except Exception as e:
        log.warning("[leads.update.error] %s: %s", type(e).__name__, str(e)[:250])
        return None
    rows = result.data or []
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Talent-DB export (Import Lead) — dedup marker + posting lookups
# ---------------------------------------------------------------------------


def get_posting(posting_id: int) -> dict | None:
    """Fetch one raw posting row (shared across tenants) for the Talent-DB push.

    Selects `_POSTING_TALENTDB_COLS`, not `*`: the only caller hands this row
    straight to `talentdb.build_fields`, and that column list IS what
    build_fields reads — `description` included, since the webhook's
    `posting_description` is the one heavy column the export genuinely needs.

    Read only when a practice-initiated export is actually going to send.
    `newest_lead_for_practice` answers the dedup question first, and it carries
    the id this takes — so a practice-detail page load, or a click on an
    already-exported practice, never reaches this at all.
    """
    client = _client()
    if not client or not posting_id:
        return None
    try:
        result = (
            client.table("job_postings").select(_POSTING_TALENTDB_COLS)
            .eq("id", posting_id).maybe_single().execute()
        )
    except Exception:
        return None
    return result.data if result and result.data else None


# What a practice-initiated Talent-DB export reads off the lead: the dedup
# marker plus the two qualifier fields `talentdb.build_fields` maps
# (`No_of_Providers__c` and the track).
_LEAD_EXPORT_MARKER_COLS = "id, talentdb_exported_at, provider_count, service_line"


def newest_lead_for_practice(company_id: str, practice_id: int) -> dict | None:
    """The practice's newest linked posting and this tenant's lead for it — in
    ONE query. `None` when the practice has no linked posting at all.

    Returns `{"posting_id": int, "lead": dict | None}`. A `lead` of `None` is a
    real answer, not a miss: postings are shared across tenants (ADR-04) while
    leads are per-company, so a posting this tenant never qualified has no lead
    row and nothing to dedup against.

    Replaces the `newest_posting_for_practice` → `find_lead_by_posting` pair,
    which cost two sequential round trips on every practice-detail load. The
    shape matters:

    - The top-level table is `job_postings`, so `practice_id`, the ordering and
      the `limit(1)` all apply to the postings — this picks the SAME posting
      the old two-step did, rather than the newest posting that happens to have
      a lead row. Keeping that identical is what stops the detail page's
      "Exported" badge drifting from what Import Lead would actually send.
    - The lead is a to-many embed WITHOUT `!inner`, so `lead.company_id`
      filters the embedded rows rather than the top-level ones (an `!inner`
      embed here would drop practices whose newest posting this tenant never
      qualified). `unique (company_id, posting_id)` makes the list 0 or 1 long.
    - Both halves of that syntax are already load-bearing in production here:
      `_apply_filters` filters on `posting.city` / `posting.practice_id`
      through an embed on every signals page load. Ordering stays on a
      top-level column, so this needs no embedded-order support at all.

    Selects the posting `id` only. The export path re-reads the full row via
    `get_posting` when it actually sends; the detail page never needs the
    description it used to drag back on every load.
    """
    client = _client()
    if not client or not company_id or not practice_id:
        return None
    try:
        result = (
            client.table("job_postings")
            .select(f"id, lead:company_job_leads({_LEAD_EXPORT_MARKER_COLS})")
            .eq("practice_id", practice_id)
            .eq("lead.company_id", company_id)
            .order("posted_at", desc=True, nullsfirst=False)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        log.warning("[leads.newest_for_practice.error] %s: %s",
                    type(e).__name__, str(e)[:200])
        return None
    rows = result.data or []
    if not rows:
        return None
    row = rows[0]
    # A to-many embed answers with a list; tolerate a dict in case PostgREST
    # resolves the relationship as to-one on some deployment.
    embedded = row.get("lead")
    if isinstance(embedded, list):
        embedded = embedded[0] if embedded else None
    return {"posting_id": row.get("id"), "lead": embedded or None}


def mark_lead_exported(company_id: str, lead_id: int) -> None:
    """Stamp `talentdb_exported_at` so this posting isn't re-sent. Fail-soft."""
    client = _client()
    if not client or not company_id or not lead_id:
        return
    try:
        (
            client.table("company_job_leads")
            .update({"talentdb_exported_at": _now()})
            .eq("company_id", company_id).eq("id", lead_id).execute()
        )
    except Exception as e:
        log.warning("[leads.mark_exported.error] %s: %s",
                    type(e).__name__, str(e)[:200])


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

    Reads `LEAD_EXPORT_SELECT`, not the detail view's `LEAD_SELECT`: an export
    is tens of thousands of rows in one serverless invocation, so the columns
    the CSV never prints are the ones that decide whether it finishes.
    """
    client = _client()
    if not client or not company_id:
        return []

    rows: list[dict] = []
    page = 0
    while len(rows) < 50_000:
        query = _apply_filters(
            client.table("company_job_leads").select(LEAD_EXPORT_SELECT),
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

    Read-then-write, because supabase-py exposes no `+= 1` SQL fragment — but
    grouped, not per row. Every lead sharing a current `export_count` gets the
    same new value, so the writes collapse to one `update ... in (ids)` per
    distinct count. Real exports are dominated by rows at count 0, which makes
    this ~1-2 round trips per 500-row chunk instead of 500.
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
        ids_by_count: dict[int, list[int]] = {}
        for row in existing:
            ids_by_count.setdefault(
                (row.get("export_count") or 0) + 1, []
            ).append(row["id"])
        for next_count, ids in ids_by_count.items():
            payload: dict[str, Any] = {
                "export_count": next_count,
                "last_exported_at": now,
            }
            if user_id:
                payload["last_exported_by"] = user_id
            try:
                client.table("company_job_leads").update(payload).in_(
                    "id", ids
                ).execute()
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def lead_analytics(company_id: str, days: int = 30) -> dict:
    """Aggregates for `/signals/analytics`, over the trailing `days` window.

    Computed in Python over the window's rows rather than in SQL: the numbers
    are small (a tenant's leads, not the shared posting universe), and keeping
    it here means the analytics view needs no database function to deploy
    alongside it.

    Two things make "in Python" honest at 29k+ lead rows:

    * The window is applied SERVER-SIDE (`gte` on `created_at`) instead of by
      slicing `per_day` after the fact. So `total`, `keep_rate`, the bands and
      the dispositions all describe the same window the chart draws — they
      used to be whole-table figures next to a 30-day chart — and the scan is
      bounded by the window rather than by the table.
    * The read is PAGED. A bare `.limit(20_000)` does NOT lift PostgREST's
      1,000-row ceiling: every figure on this page was previously computed
      over the newest 1,000 leads, with `total` pinned at exactly 1000 and
      `keep_rate` a ratio of whatever that window happened to hold.

    Full coverage is affordable because the select carries only the six
    columns the buckets read plus the posting's `source` — no `draft`, no
    posting `description`, and no `posted_at` (nothing here reads it).
    """
    client = _client()
    if not client or not company_id:
        return _empty_analytics()

    from datetime import timedelta

    from src.storage import _paginated_query

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        builder = (
            client.table("company_job_leads")
            .select(
                "decision, confidence_band, disposition, reject_reason, service_line, "
                "created_at, posting:job_postings!inner(source)"
            )
            .eq("company_id", company_id)
            .gte("created_at", since)
            # Ordering by the primary key keeps the pages disjoint: an
            # unordered paged read can repeat or skip rows between requests.
            .order("id", desc=True)
        )
        # A ceiling, not an expectation — a 365-day window on the busiest
        # tenant is still well under this, and it stops a runaway loop if the
        # filter above ever fails to bind.
        rows = _paginated_query(builder, limit=100_000)
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
        # The window is already enforced server-side; the slice is a belt to
        # that brace (a day boundary can land one extra bucket in the set).
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
    # A `.limit(N)` alone does not paginate — PostgREST truncates any single
    # request at 1000 rows regardless of what N asks for. Safe at today's ~160
    # locations, but at national scale a bare limit would silently drop every
    # location past the first page: `locations`/`swept` would under-report and
    # the zero-row tripwire would stop seeing the locations it exists to watch.
    from src.storage import _paginated_query

    try:
        builder = (
            client.table("search_locations")
            .select("id,last_indeed_at,last_linkedin_at,"
                    "indeed_zero_streak,linkedin_zero_streak")
            .eq("company_id", company_id).eq("enabled", True)
            # Paged reads need a stable order or the pages can overlap.
            .order("id")
        )
        locations = _paginated_query(builder, limit=20_000)
    except Exception:
        locations = []

    swept = [l for l in locations if l.get("last_indeed_at") or l.get("last_linkedin_at")]
    swept_ids = {l.get("id") for l in swept}

    location_ids = [l.get("id") for l in locations if l.get("id") is not None]
    started_ids: set = set()
    if location_ids:
        # Same reason as above, and it bites sooner here: term_count x
        # location_count cells can exceed 1000 well before a tenant's
        # dimension tables do. `target_runs` has no surrogate key (its PK is
        # the (term, location, source) triple), so `location_id` is what the
        # pages sort on.
        builder = (
            client.table("target_runs")
            .select("location_id")
            .in_("location_id", location_ids)
            .order("location_id")
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
