"""The search space for `(term x location)` job-board queries, per tenant.

`lead_config` owns the files; this module owns the *dimensions*. Seeding
copies the config's terms and locations into `search_terms` /
`search_locations` once per tenant, and from then on the collector reads
only those tables — so a tenant can disable a city or add a term by editing
rows, without forking a checked-in file (ADR-03).

Unlike the retired `company_search_targets` matrix, the product is never
stored: a DB table should hold facts you cannot recompute, and every cell of
the matrix is derivable from its two dimensions. `build_claim_rows` computes
the cross for one claimed location at claim time. Rotation lives on
`search_locations` as a per-source cursor (`last_indeed_at` /
`last_linkedin_at`) rather than a query ordering trick, because the collector
needs a different staleness threshold per source and a yield-decay streak
that only makes sense per location — see
`docs/refactor/instant-signals-targets.md`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src import lead_config

log = logging.getLogger("hvsi.leads.targets")

_GRANULARITIES = ("state", "city")
_SOURCES = ("indeed", "linkedin")


class NoLeadCompany(RuntimeError):
    """No tenant could be resolved for a background stage to collect for."""


class TargetValidationError(ValueError):
    """A row handed to `add_terms` / `add_locations` is missing or malformed."""


class CatalogProtectedError(ValueError):
    """A delete targeted a row that is still in the checked-in catalog.

    `ensure_targets` diff-seeds from `lead_config` unconditionally on every
    collect run (Phase 4) — a catalog term/location that got DELETEd would
    just be silently re-inserted on the very next run, which makes "delete"
    look like it worked right up until the next hourly sweep undoes it.
    `set_term_enabled(..., False)` / `set_location_enabled(..., False)` is
    the durable off switch for anything in the catalog: the collector honors
    `enabled` forever, catalog membership notwithstanding, and re-seeding
    never flips it back on. Only a hand-added row (not in the catalog at
    all) can actually be removed."""


def resolve_company_id() -> str:
    """The single tenant collection runs for.

    v1 is deliberately single-tenant at run time even though the schema is not:
    `company_id` stays on every row so multi-tenancy is a pin to remove later
    rather than a migration to write, but the stages resolve exactly one
    company. That also sidesteps the duplicated board traffic a per-tenant loop
    would cause — postings are shared across tenants (ADR-04), so two tenants
    on the same config would run identical searches to produce one row.

    `LEAD_COMPANY_ID` wins. Unset, the sole company is used — which is the
    common case and saves a fresh deploy from needing the variable at all.
    More than one company and no pin is an error, not a guess: picking
    whichever row sorted first would quietly bill the wrong tenant.
    """
    from src.settings import settings
    from src.storage import _get_client

    if settings.lead_company_id:
        return settings.lead_company_id

    client = _get_client()
    if not client:
        raise NoLeadCompany("Supabase is not configured")
    try:
        rows = (client.table("companies").select("id").limit(2).execute()).data or []
    except Exception as e:
        raise NoLeadCompany(f"could not read companies: {type(e).__name__}") from e

    if not rows:
        raise NoLeadCompany("no companies exist")
    if len(rows) > 1:
        raise NoLeadCompany(
            "more than one company exists — set LEAD_COMPANY_ID to the tenant "
            "that owns the practices"
        )
    return rows[0]["id"]


# ---------------------------------------------------------------------------
# Config page — read the live dimensions and edit them by hand.
#
# The collector already reads only these tables (ADR-03), so editing rows
# here is the supported way to tune what gets searched without forking a
# checked-in file and deploying. See
# docs/specs/2026-08-10-instant-signals-config-page-design.md and
# docs/refactor/instant-signals-targets.md.
# ---------------------------------------------------------------------------


def catalog() -> dict:
    """The checked-in config as a UI catalog: states+cities, tracks+keywords.

    Pure read of `lead_config` — no DB. This is what the "Add …" forms suggest,
    so the common edit is picking from the curated file rather than free text.
    `locations()` flattens states into rows; we regroup them back into
    states-with-cities for display.
    """
    from src import lead_config

    states: dict[str, dict] = {}
    for loc in lead_config.locations():
        state = states.setdefault(
            loc["state"], {"code": loc["state"], "cities": [], "statewide_query": None}
        )
        if loc["granularity"] == "state":
            state["statewide_query"] = loc["query"]
        else:
            state["cities"].append(loc["query"])

    tracks: dict[str, list[str]] = {}
    for entry in lead_config.role_terms():
        tracks.setdefault(entry["service_line"], []).append(entry["term"])

    return {
        "states": [
            {
                "code": code,
                "statewide_query": s["statewide_query"],
                "cities": s["cities"],
            }
            for code, s in states.items()
        ],
        "tracks": [
            {"service_line": sl, "terms": terms} for sl, terms in tracks.items()
        ],
        "search": lead_config.search_params(),
        "sources": list(lead_config.enabled_sources()),
    }


def list_config(company_id: str) -> dict:
    """Every dimension row for a tenant: terms, locations, and pinned overrides.

    The dimension tables themselves stay small — roughly 100-200 rows
    combined for `search_terms` + `search_locations` at production scale —
    nowhere near PostgREST's 1000-row cap, so `terms` and `locations` read in
    one page each. `overrides` is NOT bounded the same way: it is a per-CELL
    table (one row can exist per `term x location` pair), so its ceiling is
    the *product* of the two dimension counts, not their sum — a few hundred
    terms and locations is easily north of 1000 possible cells. Paginated
    accordingly; a truncated overrides read would silently drop a pinned
    `enabled=false` cell and re-enable it in `build_claim_rows`.
    """
    from src.storage import _get_client, _paginated_query

    client = _get_client()
    if not client or not company_id:
        return {"terms": [], "locations": [], "overrides": []}

    try:
        terms = (
            client.table("search_terms")
            .select("*")
            .eq("company_id", company_id)
            .order("service_line", desc=False)
            .order("term", desc=False)
            .execute()
        ).data or []
    except Exception as e:
        log.warning("[leads.list_config.terms_error] %s: %s", type(e).__name__, str(e)[:200])
        terms = []

    try:
        locations = (
            client.table("search_locations")
            .select("*")
            .eq("company_id", company_id)
            .order("state", desc=False)
            .order("granularity", desc=True)  # 'state' (statewide) before 'city'
            .order("location", desc=False)
            .execute()
        ).data or []
    except Exception as e:
        log.warning(
            "[leads.list_config.locations_error] %s: %s", type(e).__name__, str(e)[:200]
        )
        locations = []

    # target_overrides has no company_id column of its own — scope through
    # the term/location ids we already fetched for this tenant. Per-cell, so
    # paginated (see docstring): a truncated read here would silently drop a
    # pinned `enabled=false` cell and re-enable it in `build_claim_rows`.
    term_ids = [t["id"] for t in terms]
    location_ids = [l["id"] for l in locations]
    overrides = []
    if term_ids and location_ids:
        builder = (
            client.table("target_overrides")
            .select("*")
            .in_("term_id", term_ids)
            .in_("location_id", location_ids)
        )
        overrides = _paginated_query(builder, limit=50_000)

    return {"terms": terms, "locations": locations, "overrides": overrides}


def _clean_term_row(company_id: str, row: dict) -> dict:
    """Normalise one incoming term row to the table's shape, or raise.

    Same invariants `search_terms`'s NOT NULL constraints enforce, checked
    here first so a bad row returns a 400 with a readable message instead of
    a Postgres error.
    """
    term = (row.get("term") or "").strip()
    service_line = (row.get("service_line") or "").strip()

    if not term:
        raise TargetValidationError("a term row has no `term`")
    if not service_line:
        raise TargetValidationError(f"term {term!r} has no `service_line`")

    return {
        "company_id": company_id,
        "term": term,
        "service_line": service_line,
        "enabled": bool(row.get("enabled", True)),
    }


def _clean_location_row(company_id: str, row: dict) -> dict:
    """Normalise one incoming location row to the table's shape, or raise.

    Same invariants `search_locations`'s CHECK constraints enforce.
    """
    location = (row.get("location") or "").strip()
    state = (row.get("state") or "").strip().upper()
    granularity = (row.get("granularity") or "").strip().lower()

    if not location:
        raise TargetValidationError("a location row has no `location`")
    if len(state) != 2:
        raise TargetValidationError(
            f"location {location!r} needs a 2-letter `state` (got {state!r})"
        )
    if granularity not in _GRANULARITIES:
        raise TargetValidationError(
            f"granularity must be one of {_GRANULARITIES} (got {granularity!r})"
        )

    return {
        "company_id": company_id,
        "location": location,
        "state": state,
        "granularity": granularity,
        "enabled": bool(row.get("enabled", True)),
    }


def add_terms(company_id: str, rows: list[dict]) -> dict[str, int]:
    """Insert hand-added terms. Idempotent via the `(company_id, term)`
    unique constraint.

    The whole batch validates before any write, so one bad row rejects the
    request instead of a half-applied add. One upsert call with
    `ignore_duplicates` handles existing pairs — no chunking or dedup
    pre-read needed at ~21 rows (contrast the old matrix's 200-row chunking).
    """
    cleaned = [_clean_term_row(company_id, r) for r in rows]
    if not cleaned:
        return {"requested": 0, "inserted": 0}

    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id:
        return {"requested": len(cleaned), "inserted": 0}

    inserted = 0
    try:
        client.table("search_terms").upsert(
            cleaned, on_conflict="company_id,term", ignore_duplicates=True
        ).execute()
        inserted = len(cleaned)
    except Exception as e:
        log.warning("[leads.add_terms.error] %s: %s", type(e).__name__, str(e)[:200])

    log.info("[leads.add_terms] company=%s requested=%d", company_id, len(cleaned))
    return {"requested": len(cleaned), "inserted": inserted}


def add_locations(company_id: str, rows: list[dict]) -> dict[str, int]:
    """Insert hand-added locations. Idempotent via the `(company_id, location)`
    unique constraint. Same validate-all-then-upsert-once shape as `add_terms`.
    """
    cleaned = [_clean_location_row(company_id, r) for r in rows]
    if not cleaned:
        return {"requested": 0, "inserted": 0}

    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id:
        return {"requested": len(cleaned), "inserted": 0}

    inserted = 0
    try:
        client.table("search_locations").upsert(
            cleaned, on_conflict="company_id,location", ignore_duplicates=True
        ).execute()
        inserted = len(cleaned)
    except Exception as e:
        log.warning("[leads.add_locations.error] %s: %s", type(e).__name__, str(e)[:200])

    log.info("[leads.add_locations] company=%s requested=%d", company_id, len(cleaned))
    return {"requested": len(cleaned), "inserted": inserted}


def set_term_enabled(company_id: str, term_id: int, enabled: bool) -> dict | None:
    """Flip one term's `enabled`, scoped to the tenant. Returns the row.

    One UPDATE flips a whole track (e.g. every "RN" variant) instead of the
    old per-cell PATCH storm across the whole matrix.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or not term_id:
        return None
    try:
        result = (
            client.table("search_terms")
            .update({"enabled": bool(enabled)})
            .eq("company_id", company_id)
            .eq("id", term_id)
            .execute()
        )
    except Exception as e:
        log.warning("[leads.set_term_enabled.error] %s: %s", type(e).__name__, str(e)[:200])
        return None
    rows = result.data or []
    return rows[0] if rows else None


def set_terms_enabled(
    company_id: str, term_ids: list[int], enabled: bool,
) -> list[dict]:
    """Flip many terms at once, scoped to the tenant. Returns the updated rows.

    One UPDATE ... WHERE id IN (...) rather than a PATCH per row: enabling a
    track from the config page fans out across every term in it, and the page
    was paying a request — and its auth round trips — for each one.

    Ids that don't exist, or belong to another tenant, are simply absent from
    the result; the `company_id` filter is what makes those the same case, so
    a caller cannot flip another tenant's rows by guessing ids.
    """
    return _set_enabled_bulk("search_terms", company_id, term_ids, enabled)


def set_locations_enabled(
    company_id: str, location_ids: list[int], enabled: bool,
) -> list[dict]:
    """Flip many locations at once, scoped to the tenant. Returns the updated
    rows. Same shape and scoping as `set_terms_enabled` — this is the one that
    matters most in practice, since enabling a state is ~64 city rows.
    """
    return _set_enabled_bulk("search_locations", company_id, location_ids, enabled)


def _set_enabled_bulk(
    table: str, company_id: str, ids: list[int], enabled: bool,
) -> list[dict]:
    """The shared body of the two bulk toggles: one tenant-scoped UPDATE."""
    from src.storage import _get_client

    client = _get_client()
    clean = [int(i) for i in (ids or []) if i]
    if not client or not company_id or not clean:
        return []
    try:
        result = (
            client.table(table)
            .update({"enabled": bool(enabled)})
            .eq("company_id", company_id)
            .in_("id", clean)
            .execute()
        )
    except Exception as e:
        log.warning("[leads.set_enabled_bulk.error] table=%s %s: %s",
                    table, type(e).__name__, str(e)[:200])
        return []
    rows = result.data or []
    log.info("[leads.set_enabled_bulk] table=%s company=%s requested=%d updated=%d "
             "enabled=%s", table, company_id, len(clean), len(rows), bool(enabled))
    return rows


def delete_term(company_id: str, term_id: int) -> dict | None:
    """Hard-delete one hand-added term, scoped to the tenant. Returns the
    deleted row, or `None` if it didn't exist (a stray id / another tenant's).

    Raises `CatalogProtectedError` — see its docstring for why — if the term
    string is still in `role_terms()`; the caller (the admin route) maps that
    to a 409 telling the operator to disable instead. `target_runs` and
    `target_overrides` rows referencing this term cascade on the FK, so a
    hand-added term with sweep history or a pin cleans up in one DELETE.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or not term_id:
        return None
    try:
        rows = (
            client.table("search_terms")
            .select("*")
            .eq("company_id", company_id)
            .eq("id", term_id)
            .limit(1)
            .execute()
        ).data or []
    except Exception as e:
        log.warning("[leads.delete_term.read_error] %s: %s", type(e).__name__, str(e)[:200])
        return None
    if not rows:
        return None
    row = rows[0]

    catalog_terms = {t["term"] for t in lead_config.role_terms()}
    if row["term"] in catalog_terms:
        raise CatalogProtectedError(
            f"{row['term']!r} is in the checked-in catalog — disable it instead of "
            "deleting it; deleting it would be undone by the next run's re-seed."
        )

    try:
        client.table("search_terms").delete().eq("company_id", company_id).eq(
            "id", term_id
        ).execute()
    except Exception as e:
        log.warning("[leads.delete_term.write_error] %s: %s", type(e).__name__, str(e)[:200])
        return None
    log.info("[leads.delete_term] company=%s term_id=%s term=%s",
             company_id, term_id, row["term"])
    return row


def set_location_enabled(company_id: str, location_id: int, enabled: bool) -> dict | None:
    """Flip one location's `enabled`, scoped to the tenant. Returns the row.

    One UPDATE flips a whole city or statewide row.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or not location_id:
        return None
    try:
        result = (
            client.table("search_locations")
            .update({"enabled": bool(enabled)})
            .eq("company_id", company_id)
            .eq("id", location_id)
            .execute()
        )
    except Exception as e:
        log.warning(
            "[leads.set_location_enabled.error] %s: %s", type(e).__name__, str(e)[:200]
        )
        return None
    rows = result.data or []
    return rows[0] if rows else None


def delete_location(company_id: str, location_id: int) -> dict | None:
    """Hard-delete one hand-added location, scoped to the tenant. Returns the
    deleted row, or `None` if it didn't exist. Same catalog-protection rule
    as `delete_term` — see `CatalogProtectedError` for why — checked against
    `locations()`'s `query` strings.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or not location_id:
        return None
    try:
        rows = (
            client.table("search_locations")
            .select("*")
            .eq("company_id", company_id)
            .eq("id", location_id)
            .limit(1)
            .execute()
        ).data or []
    except Exception as e:
        log.warning("[leads.delete_location.read_error] %s: %s", type(e).__name__, str(e)[:200])
        return None
    if not rows:
        return None
    row = rows[0]

    catalog_locations = {l["query"] for l in lead_config.locations()}
    if row["location"] in catalog_locations:
        raise CatalogProtectedError(
            f"{row['location']!r} is in the checked-in catalog — disable it instead of "
            "deleting it; deleting it would be undone by the next run's re-seed."
        )

    try:
        client.table("search_locations").delete().eq("company_id", company_id).eq(
            "id", location_id
        ).execute()
    except Exception as e:
        log.warning(
            "[leads.delete_location.write_error] %s: %s", type(e).__name__, str(e)[:200]
        )
        return None
    log.info("[leads.delete_location] company=%s location_id=%s location=%s",
             company_id, location_id, row["location"])
    return row


def set_override(
    company_id: str, term_id: int, location_id: int, enabled: bool | None
) -> dict | None:
    """Pin or unpin one `(term, location)` cell.

    `enabled=True`/`False` upserts a pin into `target_overrides`;
    `enabled=None` deletes it, returning the cell to the default
    (`term.enabled AND location.enabled`). `target_overrides` carries no
    `company_id` of its own, so this verifies both dimensions belong to the
    tenant first (two cheap scoped selects) — otherwise a stray id from
    another tenant could write a pin that silently changes this tenant's
    claim loop.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or not term_id or not location_id:
        return None

    try:
        term = (
            client.table("search_terms")
            .select("id")
            .eq("company_id", company_id)
            .eq("id", term_id)
            .limit(1)
            .execute()
        ).data or []
        location = (
            client.table("search_locations")
            .select("id")
            .eq("company_id", company_id)
            .eq("id", location_id)
            .limit(1)
            .execute()
        ).data or []
    except Exception as e:
        log.warning("[leads.set_override.scope_error] %s: %s", type(e).__name__, str(e)[:200])
        return None

    if not term or not location:
        log.warning(
            "[leads.set_override.scope_miss] company=%s term_id=%s location_id=%s",
            company_id, term_id, location_id,
        )
        return None

    try:
        if enabled is None:
            client.table("target_overrides").delete().eq("term_id", term_id).eq(
                "location_id", location_id
            ).execute()
            return None
        result = (
            client.table("target_overrides")
            .upsert(
                {"term_id": term_id, "location_id": location_id, "enabled": bool(enabled)},
                on_conflict="term_id,location_id",
            )
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception as e:
        log.warning("[leads.set_override.write_error] %s: %s", type(e).__name__, str(e)[:200])
        return None


# ---------------------------------------------------------------------------
# Collector strategy — claim, stamp, record. See plan §3.
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime:
    """Parse a PostgREST timestamptz string, tolerant of a trailing `Z`."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def adaptive_window_hours(cursor_iso: str | None, buffer_hours: int) -> int:
    """How far back one location's next search should look.

    A location swept 6h ago only needs to cover the gap since then plus a
    buffer for late-indexed postings — asking for a full week every time is
    what makes the board's top-40 cap bind on dense cities and floods the
    upsert with rows already seen. `None` (never swept) gets the full 168h
    (7-day) first sweep. Clamped to [24, 168]: below a day risks missing
    something indexed late the same day; above a week is never useful
    (boards don't index that far back anyway).
    """
    if cursor_iso is None:
        return 168
    hours_since = (
        datetime.now(timezone.utc) - _parse_iso(cursor_iso)
    ).total_seconds() / 3600
    return int(max(24, min(168, hours_since + buffer_hours)))


def effective_threshold_hours(base_hours: int, zero_streak: int, cap: int) -> int:
    """Yield-decay staleness threshold for one `(location, source)` cell.

    A sweep returning zero rows across every term doubles how long the
    collector waits before trying that location on that source again,
    capped at `2**cap` so a dead suburb decays toward a fixed ceiling
    instead of being starved forever. Any non-zero sweep resets the streak
    (see `record_location_sweep`), which snaps the threshold straight back
    to `base_hours`.
    """
    return base_hours * (2 ** min(zero_streak, cap))


def phase_deadline(
    source: str, sources: list[str], start: float, budget_seconds: float,
    indeed_fraction: float,
) -> float:
    """The wall-clock deadline (a `time.monotonic()`-style timestamp) for one
    source's phase within a shared collect budget.

    When both sources are enabled, Indeed's phase is capped at
    `indeed_fraction` of the total budget and LinkedIn's phase runs to the
    full deadline — a reserve, not a split, so LinkedIn is guaranteed a
    window even if Indeed still has due locations left when its own
    deadline arrives (plan §3's livelock fix: a re-seed can flood Indeed
    with dozens of never-swept locations in one run, and without a reserve
    that flood would consume the entire budget before LinkedIn's phase ever
    starts). A single enabled source — or a source not in `sources` at all —
    gets the whole budget; there is nothing to reserve against.
    """
    if len(sources) <= 1 or source == "linkedin" or "linkedin" not in sources:
        return start + budget_seconds
    return start + budget_seconds * indeed_fraction


def location_fits_budget(
    now: float, phase_deadline_ts: float, avg_term_seconds: float, term_count: int,
    safety_factor: float = 0.8,
) -> bool:
    """Would starting a NEW location (crossing `term_count` terms) plausibly
    finish before `phase_deadline_ts`, at `avg_term_seconds` observed cost per
    term?

    This is the other half of the livelock fix `phase_deadline` starts:
    without it, the same stalest location gets claimed, partially swept, and
    abandoned unstamped every single run, forever — it is always due (never
    stamped because it never finishes), the budget always dies partway
    through its term list, and the next run picks the exact same location
    right back up at term 0. Refusing to START a location that cannot fit
    means it stays claimable and untouched instead — the next run (with a
    fresh budget) may finish it, or a later reserve boundary may not even
    get this far.

    `safety_factor` under 1.0 leaves headroom for the estimate running low:
    starting a location that then still blows the deadline just produces one
    more incomplete location (handled elsewhere, and self-healing — it stays
    unstamped), which is a far smaller cost than under-estimating and
    skipping locations that would actually have fit.
    """
    estimate = avg_term_seconds * term_count
    return now + estimate * safety_factor <= phase_deadline_ts


def claim_locations(company_id: str, source: str, limit: int) -> list[dict]:
    """The `limit` stalest enabled locations due for one source's sweep.

    Ordered by that source's cursor (`nulls first` — never-swept locations
    are the stalest by definition), then id for a stable tie-break. The
    yield-decay threshold (`effective_threshold_hours`) is a computed value
    PostgREST can't express in an `.order()`/`.filter()` call, so we fetch
    ALL enabled locations and apply the threshold filter in Python, taking
    the first `limit` that are actually due. Fetch-all is deliberate, not
    lazy: a bounded window (say `limit * 3`) sorted stalest-first can fill
    up entirely with decayed locations that are old but not yet due, hiding
    due locations beyond it — a false-empty claim. The table is dimension-
    sized (~31 rows per state), so even national scale stays well under one
    PostgREST page. Does NOT stamp —
    the collector stamps per-location as each completes (see
    `stamp_location`), so a crashed run leaves only the finished locations
    fresh.
    """
    if source not in _SOURCES:
        raise ValueError(f"unknown source {source!r}")

    from src.settings import settings
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or limit <= 0:
        return []

    cursor_col = f"last_{source}_at"
    streak_col = f"{source}_zero_streak"
    base_hours = (
        settings.lead_indeed_stale_hours
        if source == "indeed"
        else settings.lead_linkedin_stale_hours
    )

    query = (
        client.table("search_locations")
        .select("*")
        .eq("company_id", company_id)
        .eq("enabled", True)
    )
    # Escape hatch: statewide-only LinkedIn drops the city recall tier
    # entirely (the pre-tier 2026-08-13 shape — see settings for the
    # measured history).
    if source == "linkedin" and settings.lead_linkedin_statewide_only:
        query = query.eq("granularity", "state")
    if source == "linkedin":
        # LinkedIn's statewide rows are its instant tier and must claim
        # ahead of the city recall tier: city cursors run DAYS older by
        # design (their threshold is `lead_linkedin_city_stale_hours`), so
        # pure stalest-first would bury a due statewide row behind hours of
        # city sweeps and break the ≤6h signal-latency promise. 'state' >
        # 'city' lexically, so one desc order expresses the tier split;
        # within a tier the order below is still stalest-first. Harmless
        # under statewide-only (a single granularity remains).
        query = query.order("granularity", desc=True)
    try:
        result = (
            query
            .order(cursor_col, desc=False, nullsfirst=True)
            .order("id", desc=False)
            .execute()
        )
    except Exception as e:
        log.warning("[leads.claim.error] %s: %s", type(e).__name__, str(e)[:200])
        return []

    now = datetime.now(timezone.utc)
    due: list[dict] = []
    for loc in result.data or []:
        cursor = loc.get(cursor_col)
        # LinkedIn city rows are judged against the recall-tier threshold,
        # not the statewide instant one — the whole point of the tier split.
        loc_base = base_hours
        if source == "linkedin" and (loc.get("granularity") or "") != "state":
            loc_base = settings.lead_linkedin_city_stale_hours
        if cursor is None:
            due.append(loc)
        else:
            streak = int(loc.get(streak_col) or 0)
            threshold = effective_threshold_hours(loc_base, streak, settings.lead_zero_streak_cap)
            age_hours = (now - _parse_iso(cursor)).total_seconds() / 3600
            if age_hours >= threshold:
                due.append(loc)
        if len(due) >= limit:
            break

    return due


def stamp_location(company_id: str, location_id: int, source: str) -> None:
    """Set one location's source cursor to now().

    Separate from `claim_locations` because the collector stamps
    per-location as each finishes, not the whole claimed batch up front —
    crash-safe (plan §3): a killed run leaves the not-yet-finished locations
    stale, so the next run picks them up first instead of skipping them for
    a full threshold period.
    """
    if source not in _SOURCES:
        raise ValueError(f"unknown source {source!r}")

    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or not location_id:
        return
    try:
        client.table("search_locations").update(
            {f"last_{source}_at": datetime.now(timezone.utc).isoformat()}
        ).eq("company_id", company_id).eq("id", location_id).execute()
    except Exception as e:
        log.warning("[leads.stamp.error] %s: %s", type(e).__name__, str(e)[:200])


def record_target_result(
    term_id: int, location_id: int, source: str, row_count: int, new_count: int
) -> None:
    """Upsert one `(term, location, source)` cell's last-run stats.

    Per-cell instrumentation: per-source yield and novelty, read by
    `sweep_status` and future threshold tuning. `target_runs` is sparse — a
    row only exists once its cell has actually run — and is never read by
    claim ordering, so a write failure here does not affect rotation.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not term_id or not location_id:
        return
    try:
        client.table("target_runs").upsert(
            {
                "term_id": term_id,
                "location_id": location_id,
                "source": source,
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "last_row_count": int(row_count),
                "last_new_count": int(new_count),
            },
            on_conflict="term_id,location_id,source",
        ).execute()
    except Exception as e:
        log.warning("[leads.record_target_result.error] %s: %s", type(e).__name__, str(e)[:200])


def record_location_sweep(company_id: str, location_id: int, source: str, total_rows: int) -> None:
    """Update one location's zero-streak after a FULL sweep (every enabled
    term) on one source.

    Split from `record_target_result` because the streak is a property of
    the whole location's sweep on a source, not a single `(term, location)`
    cell — a location with 10 terms where 9 return zero and 1 returns rows
    is alive, not dead. A zero-row sweep increments the streak (yield decay,
    `effective_threshold_hours`); any non-zero sweep resets it to 0 so a
    location that comes back to life snaps immediately back to the base
    threshold instead of decaying back down gradually.
    """
    if source not in _SOURCES:
        raise ValueError(f"unknown source {source!r}")

    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or not location_id:
        return
    streak_col = f"{source}_zero_streak"
    try:
        if total_rows == 0:
            # No PostgREST RPC for an atomic increment — read-modify-write is
            # fine here: one location per source per sweep (not a hot path),
            # and a lost increment just costs one extra scan before the
            # streak catches back up.
            current = (
                client.table("search_locations")
                .select(streak_col)
                .eq("company_id", company_id)
                .eq("id", location_id)
                .limit(1)
                .execute()
            ).data or []
            streak = int((current[0].get(streak_col) if current else 0) or 0) + 1
        else:
            streak = 0
        client.table("search_locations").update({streak_col: streak}).eq(
            "company_id", company_id
        ).eq("id", location_id).execute()
    except Exception as e:
        log.warning("[leads.record_location_sweep.error] %s: %s", type(e).__name__, str(e)[:200])


def enabled_terms(company_id: str) -> list[dict]:
    """Enabled term rows for a tenant — the collect loop's inner cross for
    each claimed location."""
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id:
        return []
    try:
        result = (
            client.table("search_terms")
            .select("*")
            .eq("company_id", company_id)
            .eq("enabled", True)
            .order("service_line", desc=False)
            .order("term", desc=False)
            .execute()
        )
    except Exception as e:
        log.warning("[leads.enabled_terms.error] %s: %s", type(e).__name__, str(e)[:200])
        return []
    return result.data or []


def build_claim_rows(location: dict, terms: list[dict], overrides: dict[tuple[int, int], bool]) -> list[dict]:
    """Cross one claimed location with the enabled terms, skipping
    override-disabled cells. Pure — no DB access — so the claim loop can call
    it per claimed location without an extra query per cell.

    `overrides` maps `(term_id, location_id) -> enabled`, e.g. reshaped by
    the caller from `list_config()["overrides"]`. A cell with no override
    entry is enabled by default, since `terms` and `location` are already
    filtered to `enabled=True`.

    Returns the claim contract callers (`search_jobs`, `lead_store`) depend
    on — these exact keys are load-bearing, do not change them.
    """
    rows: list[dict] = []
    for term in terms:
        if overrides.get((term["id"], location["id"])) is False:
            continue
        rows.append({
            "term": term["term"],
            "service_line": term["service_line"],
            "location": location["location"],
            "state": location["state"],
            "granularity": location["granularity"],
            "term_id": term["id"],
            "location_id": location["id"],
        })
    return rows


def sweep_status(company_id: str, locations: list[dict] | None = None) -> dict:
    """Per-source sweep period, coverage, and cursor age — raw numbers for
    the config page's sweep status strip (plan §5) and for tuning
    `lead_*_stale_hours` via env once real data exists.

    One select of the tenant's enabled locations (~100-200 dimension rows at
    production scale), computed in Python — cheap enough not to need a DB
    aggregate.

    `locations` skips that select for a caller that just read the same rows:
    the config route fetches every dimension through `list_config` and then
    called here, paying for `search_locations` twice per page load. Pass
    `list_config`'s `locations` — this filters `enabled` itself, because
    `list_config` deliberately returns disabled rows too (the page renders
    them as switched-off), while coverage is only ever measured against the
    rows the collector actually sweeps.

    Everything read below (`granularity`, `last_*_at`, `*_zero_streak`) is a
    real column and `list_config` selects `*`, so the pre-fetched rows carry
    the same fields this query would have.
    """
    from src.settings import settings
    from src.storage import _get_client

    if not company_id:
        return {}
    if locations is not None:
        locations = [l for l in locations if l.get("enabled")]
    else:
        client = _get_client()
        if not client:
            return {}
        try:
            locations = (
                client.table("search_locations")
                .select("*")
                .eq("company_id", company_id)
                .eq("enabled", True)
                .execute()
            ).data or []
        except Exception as e:
            log.warning("[leads.sweep_status.error] %s: %s",
                        type(e).__name__, str(e)[:200])
            return {}

    now = datetime.now(timezone.utc)
    status: dict[str, dict] = {}
    for source in _SOURCES:
        cursor_col = f"last_{source}_at"
        streak_col = f"{source}_zero_streak"
        base_hours = (
            settings.lead_indeed_stale_hours
            if source == "indeed"
            else settings.lead_linkedin_stale_hours
        )

        # Coverage is measured against what this source is actually asked to
        # sweep — LinkedIn scoped to statewide rows would otherwise report
        # against the 150 city rows it deliberately never touches.
        scoped = (
            [l for l in locations if l.get("granularity") == "state"]
            if source == "linkedin" and settings.lead_linkedin_statewide_only
            else locations
        )

        fresh = 0
        never_swept = 0
        ages: list[float] = []
        for loc in scoped:
            cursor = loc.get(cursor_col)
            if cursor is None:
                never_swept += 1
                continue
            age_hours = (now - _parse_iso(cursor)).total_seconds() / 3600
            ages.append(age_hours)
            streak = int(loc.get(streak_col) or 0)
            # Same per-tier threshold the claim uses: a LinkedIn city row
            # 30h old is FRESH against its recall threshold, not overdue
            # against the statewide instant one.
            loc_base = base_hours
            if source == "linkedin" and (loc.get("granularity") or "") != "state":
                loc_base = settings.lead_linkedin_city_stale_hours
            threshold = effective_threshold_hours(loc_base, streak, settings.lead_zero_streak_cap)
            if age_hours < threshold:
                fresh += 1

        enabled_count = len(scoped)
        status[source] = {
            "enabled_locations": enabled_count,
            "fresh_within_threshold": fresh,
            "coverage_pct": round(100 * fresh / enabled_count, 1) if enabled_count else 0.0,
            "never_swept": never_swept,
            "oldest_cursor_age_hours": round(max(ages), 1) if ages else None,
        }
    return status


# ---------------------------------------------------------------------------
# Seeding — dimension inserts from the checked-in lead config. Idempotent.
# ---------------------------------------------------------------------------


def seed_search_targets(company_id: str) -> dict[str, int]:
    """Insert this tenant's missing terms and locations from config.

    Existing rows are left alone on purpose: `enabled`, rotation cursors,
    and any hand-tuning belong to the tenant, and a re-seed after a config
    change must add new terms/locations without resetting rotation or
    re-enabling something an operator switched off. Idempotent via the
    unique constraints on `search_terms`/`search_locations` — no chunking or
    dedup pre-read needed (~100-200 dimension rows total vs. the old
    matrix's 651+ per state).

    `ensure_targets` now calls this unconditionally on every collect run
    (see its docstring), so the counts this returns had better be honest
    rather than "attempted" — a hot log line lying every hour is worse than
    a quiet one. They are, for free: `ignore_duplicates=True` sends
    `Prefer: resolution=ignore-duplicates`, which is Postgres's
    `INSERT ... ON CONFLICT DO NOTHING RETURNING *` — a row that conflicts
    (already exists) never reaches RETURNING, so `len(result.data)` is the
    count of rows Postgres actually inserted, not the batch size we sent.
    No pre/post count query needed.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id:
        return {"terms": 0, "locations": 0}

    term_rows = [
        {"company_id": company_id, "term": t["term"], "service_line": t["service_line"]}
        for t in lead_config.role_terms()
    ]
    location_rows = [
        {
            "company_id": company_id,
            "location": loc["query"],
            "state": loc["state"],
            "granularity": loc["granularity"],
        }
        for loc in lead_config.locations()
    ]

    terms_inserted = 0
    try:
        result = client.table("search_terms").upsert(
            term_rows, on_conflict="company_id,term", ignore_duplicates=True
        ).execute()
        terms_inserted = len(result.data or [])
    except Exception as e:
        log.warning("[leads.seed.terms_error] %s: %s", type(e).__name__, str(e)[:200])

    locations_inserted = 0
    try:
        result = client.table("search_locations").upsert(
            location_rows, on_conflict="company_id,location", ignore_duplicates=True
        ).execute()
        locations_inserted = len(result.data or [])
    except Exception as e:
        log.warning("[leads.seed.locations_error] %s: %s", type(e).__name__, str(e)[:200])

    # This runs every collect run now, so log level tracks whether anything
    # actually happened: a no-op every hour would drown the signal in the
    # rare run that actually delivers a config change.
    if terms_inserted or locations_inserted:
        log.info(
            "[leads.seed] company=%s terms_inserted=%d locations_inserted=%d "
            "(checked %d terms, %d locations from config)",
            company_id, terms_inserted, locations_inserted,
            len(term_rows), len(location_rows),
        )
    else:
        log.debug(
            "[leads.seed] company=%s up to date — checked %d terms, %d locations, nothing new",
            company_id, len(term_rows), len(location_rows),
        )
    return {"terms": terms_inserted, "locations": locations_inserted}


def ensure_targets(company_id: str) -> dict[str, int]:
    """Diff-seed this tenant's dimensions from config. Called at the top of
    every collect run.

    Used to gate on "only seed when the tenant has zero locations". That
    gate existed because seeding was a write of the full `(term x location)`
    matrix — 651+ rows per state — with a dedup pre-read, expensive enough
    that re-running it on every firing would have mattered. `seed_search_targets`
    is now a single idempotent upsert per dimension (`ignore_duplicates`,
    ~100-200 rows total) that can never re-enable a row an operator disabled
    or touch a rotation cursor — the reasons for the gate died with the
    matrix, and the gate itself became a liability: it made every config
    expansion (e.g. new states added to geography.json) silently invisible
    to any tenant that had already been seeded once, since the zero-rows
    check always short-circuited. Calling `seed_search_targets` unconditionally
    is what makes a config change actually reach existing tenants.
    """
    return seed_search_targets(company_id)
