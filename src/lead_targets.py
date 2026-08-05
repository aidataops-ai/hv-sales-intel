"""The `(role term x location)` search matrix, per tenant.

`lead_config` owns the files; this module owns the *table*. Seeding copies the
config matrix into `company_search_targets` once per tenant, and from then on
collection reads only the table — so a tenant can disable a city or add a term
by editing rows, without forking a checked-in file (ADR-03).

The matrix is large (14 terms x 31 Florida locations = 434 queries), far more
than one serverless invocation can sweep. Each collect run therefore claims a
bounded slice of the least-recently-run targets and stamps `last_run_at`, which
makes the rotation a plain database ordering rather than a stored cursor: two
concurrent runs pick different rows, and a crashed run just leaves its slice
stale enough to be picked up first next time.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from src import lead_config

log = logging.getLogger("hvsi.leads.targets")


def build_targets() -> list[dict]:
    """The full config matrix as rows ready for `company_search_targets`.

    Ordered location-major so a contiguous slice spans several terms in one
    city rather than hammering a single term across every city — a run's
    results then cover a readable geography instead of a thin stripe.
    """
    terms = lead_config.role_terms()
    rows: list[dict] = []
    for loc in lead_config.locations():
        for entry in terms:
            rows.append({
                "term": entry["term"],
                "service_line": entry["service_line"],
                "location": loc["query"],
                "state": loc["state"],
                "granularity": loc["granularity"],
            })
    return rows


def seed_search_targets(company_id: str) -> dict[str, int]:
    """Insert this tenant's missing targets from config. Idempotent.

    Existing rows are left alone on purpose: `enabled`, `last_run_at` and any
    hand-tuning belong to the tenant, and a re-seed after a config change must
    add new terms without resetting the rotation or re-enabling a city an
    operator switched off.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id:
        return {"config": 0, "existing": 0, "inserted": 0}

    rows = build_targets()

    try:
        current = (
            client.table("company_search_targets")
            .select("term,location")
            .eq("company_id", company_id)
            .execute()
        )
        existing = {(r["term"], r["location"]) for r in (current.data or [])}
    except Exception as e:
        log.warning("[leads.seed.read_error] %s: %s", type(e).__name__, str(e)[:200])
        existing = set()

    missing = [
        {**row, "company_id": company_id}
        for row in rows
        if (row["term"], row["location"]) not in existing
    ]
    inserted = 0
    # Chunked: 434 rows is one payload today, but the matrix grows with every
    # state added and PostgREST has a request-size ceiling.
    CHUNK = 200
    for i in range(0, len(missing), CHUNK):
        batch = missing[i:i + CHUNK]
        try:
            client.table("company_search_targets").upsert(
                batch,
                on_conflict="company_id,term,location",
                ignore_duplicates=True,
            ).execute()
            inserted += len(batch)
        except Exception as e:
            log.warning("[leads.seed.write_error] %s: %s", type(e).__name__, str(e)[:200])

    log.info(
        "[leads.seed] company=%s config=%d existing=%d inserted=%d",
        company_id, len(rows), len(existing), inserted,
    )
    return {"config": len(rows), "existing": len(existing), "inserted": inserted}


def sources_for_run(run_index: int) -> list[str]:
    """Which boards this run should hit.

    Indeed answers in ~1.5s and LinkedIn in ~22s, so running both on every
    firing would spend most of a bounded invocation waiting on the slower one.
    Config weights set the cycle: at weights 3 and 1, Indeed runs every firing
    and LinkedIn every third. Dropping a source from config removes it here
    without touching any caller (ADR-02's degrade-don't-halt property).
    """
    enabled = lead_config.enabled_sources()
    if not enabled:
        return []
    weights = {s: max(1, lead_config.source_weight(s)) for s in enabled}
    top = max(weights.values())
    picked = []
    for source in enabled:
        every = max(1, math.ceil(top / weights[source]))
        if run_index % every == 0:
            picked.append(source)
    # Never return nothing: a weird weight combination shouldn't produce a
    # no-op run that looks identical to a board outage.
    return picked or [enabled[0]]


def claim_targets(company_id: str, limit: int) -> list[dict]:
    """Take the `limit` least-recently-run enabled targets and stamp them.

    Stamping happens BEFORE the searches run, not after. A collect run that
    dies mid-sweep should not hand the same slice to the next firing — the
    postings it did fetch are already upserted, and re-running the slice would
    only burn wall clock. Stale targets come back round on the next full sweep.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id or limit <= 0:
        return []

    try:
        result = (
            client.table("company_search_targets")
            .select("*")
            .eq("company_id", company_id)
            .eq("enabled", True)
            .order("last_run_at", desc=False, nullsfirst=True)
            .order("id", desc=False)
            .limit(limit)
            .execute()
        )
    except Exception as e:
        log.warning("[leads.claim.error] %s: %s", type(e).__name__, str(e)[:200])
        return []

    targets = result.data or []
    if not targets:
        return []

    now = datetime.now(timezone.utc).isoformat()
    try:
        client.table("company_search_targets").update({"last_run_at": now}).in_(
            "id", [t["id"] for t in targets]
        ).execute()
    except Exception as e:
        log.warning("[leads.claim.stamp_error] %s: %s", type(e).__name__, str(e)[:200])

    return targets


def record_target_result(target_id: int, row_count: int) -> None:
    """Store how many rows a target kept, so zero-row runs are queryable.

    This is the Indeed-key-rotation tripwire (ADR-02): the library reaches
    Indeed through an undocumented mobile API whose embedded key can be
    rotated without notice, and the failure mode is silence, not an error.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not target_id:
        return
    try:
        client.table("company_search_targets").update(
            {"last_row_count": int(row_count)}
        ).eq("id", target_id).execute()
    except Exception:
        pass


def companies_with_targets() -> list[str]:
    """Tenants that have at least one enabled target.

    The cron stages have no logged-in user to resolve a company from, so they
    sweep every tenant that has opted in by seeding targets. A tenant that has
    never called seed-targets simply does not appear.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client:
        return []
    try:
        rows = (
            client.table("company_search_targets")
            .select("company_id")
            .eq("enabled", True)
            .limit(20_000)
            .execute()
        ).data or []
    except Exception as e:
        log.warning("[leads.companies.error] %s: %s", type(e).__name__, str(e)[:200])
        return []
    seen: list[str] = []
    for row in rows:
        if row["company_id"] not in seen:
            seen.append(row["company_id"])
    return seen


def sweep_size(company_id: str) -> int:
    """Count of enabled targets — how many claims make one full sweep."""
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id:
        return 0
    try:
        result = (
            client.table("company_search_targets")
            .select("id", count="exact")
            .eq("company_id", company_id)
            .eq("enabled", True)
            .limit(1)
            .execute()
        )
        return int(result.count or 0)
    except Exception:
        return 0
