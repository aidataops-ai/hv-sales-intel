#!/usr/bin/env python3
"""Seed the `practices` universe from Google Places — operator mode.

This is a ONE-TIME (or occasional) backfill that populates `practices` for the
whole search geography before any relation to `job_postings` exists. It is the
supply-side mirror of `scripts/run_leads.py`: same modules the product uses
(`places.search_places`, `storage.upsert_practices`), no HTTP layer, no cron
wall-clock ceiling.

Two deliberate differences from the `/api/practices/search` endpoint:

  1. OPERATOR MODE — unbilled. `search_places` is called with NO `company_id`,
     so `credits.consume_for_record` short-circuits and no `consume_credits`
     RPC runs. Building the SHARED practices universe is infrastructure, not one
     tenant's cost, and skipping the RPC also removes the single-row
     `companies.credit_balance` lock that would otherwise serialise a concurrent
     run. (`places_details` is already treated as unbilled operator-side.)

  2. CONCURRENT — a bounded fan-out. The endpoint scans one query per request;
     here an `asyncio.Semaphore` runs a handful at once. The Google page fetch
     is the slow part (~2-3s/query) and overlaps cleanly; the synchronous
     Supabase writes run off the event loop via `to_thread`. Concurrency is
     capped low on purpose — past ~6 the bottleneck stops being Google and
     becomes the Supabase connection pool, so more workers buy contention, not
     speed. A full Florida sweep is ~2-3 min at the default width.

Geography is read from `config/leads/geography.json` via `lead_config.locations`
(the same Florida cities + statewide the lead collector uses). Service lines map
to the place-type NOUNS below — searching what practices ARE on the map, not the
service we sell ("dental office", never "Virtual Dental Assistant"; same lesson
as roles.json / ADR-03).

    python scripts/seed_practices.py                      # full FL, all tracks
    python scripts/seed_practices.py --tracks "Virtual Dental Assistant"
    python scripts/seed_practices.py --cities "Tampa,Miami" --concurrency 4
    python scripts/seed_practices.py --dry-run            # fetch + report, no writes
    python scripts/seed_practices.py --limit 5 --dry-run  # smoke test, 5 queries

Reads .env for SUPABASE_* and GOOGLE_MAPS_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import lead_config  # noqa: E402
from src.places import search_places  # noqa: E402
from src.settings import settings  # noqa: E402
from src.storage import (  # noqa: E402
    _get_client,
    find_duplicate_place_ids,
    upsert_practices,
)


# Place-type search nouns per service line, keyed by the exact `service_line`
# strings `lead_config.service_lines()` emits. A single service line fans out to
# several nouns where one under-covers the market (medicine spans many specialty
# names); dental and home-health each resolve cleanly to one or two. Extend a
# list to widen coverage — every noun added multiplies queries by the city count.
PLACE_NOUNS: dict[str, list[str]] = {
    "Virtual Dental Assistant": ["dental office"],
    "Virtual Medical Assistant": [
        "medical clinic",
        "family medicine practice",
        "internal medicine practice",
    ],
    "Virtual Home Health Operations Coordinator": [
        "home health agency",
        "home care agency",
    ],
    "Virtual Medical Scheduler": [
        "medical clinic",
        "specialty clinic",
    ],
    "Virtual Chiropractic Assistant": ["chiropractic clinic"],
}


def _setup_logging(verbose: bool) -> None:
    # Line-buffer so a piped multi-minute run shows progress instead of looking
    # hung (same reasoning as run_leads.py).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("  %(name)s %(message)s"))
    logger = logging.getLogger("hvsi")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    logger.propagate = False


def _rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


async def _write_with_retry(fn, *args, attempts: int = 5):
    """Run a synchronous DB write off the loop, retrying transient failures.

    Concurrent writes to overlapping rows (statewide vs city) deadlock in
    Postgres (40P01), and Supabase's HTTP/2 layer occasionally resets a stream
    under load. Both are transient: the loser of a deadlock just needs to retry,
    and the writes here are idempotent (upsert on place_id, single-column
    stamp), so retrying is always safe. This replaces a global write-lock —
    which was correct but serialised every write and made a 750-query sweep
    crawl — with optimistic concurrency plus backoff.
    """
    for i in range(attempts):
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as e:
            transient = (
                "40P01" in str(e)
                or "deadlock" in str(e).lower()
                or "RemoteProtocol" in type(e).__name__
                or "StreamReset" in str(e)
            )
            if i == attempts - 1 or not transient:
                raise
            await asyncio.sleep(0.25 * (i + 1))


def tag_service_line(place_ids: list[str], service_line: str) -> None:
    """Stamp the discovery service line onto just-upserted rows.

    A separate targeted UPDATE, not a field on the upsert: the shared
    `upsert_practices` path also serves the product's generic search, and
    routing `service_line` through it would let a later generic search of the
    same practice overwrite the tag with null. Here only these place_ids, only
    this one column, are touched — nothing else can be clobbered.

    A practice surfaced under two nouns of the SAME service line is a no-op
    rewrite; the rare cross-service-line overlap is last-write-wins, which is
    acceptable for a discovery tag (the posting-match step is the authority on
    which line actually fits).
    """
    client = _get_client()
    if not client or not place_ids:
        return
    client.table("practices").update(
        {"service_line": service_line}
    ).in_("place_id", place_ids).execute()


def _loc_key(query: str) -> str:
    """Fold a location query to its city for dedup — 'Tampa, FL' -> 'tampa'."""
    return re.sub(r"[^a-z0-9]", "", query.split(",")[0].lower())


def demand_locations() -> list[dict]:
    """City locations pulled from the leads themselves — every FL city with at
    least one KEPT lead. This is the demand signal: scan where the leads we
    actually pursue live, so the bank grows to match demand instead of a fixed
    checked-in city list, and self-updates as new leads qualify. Two steps
    (kept posting ids, then their cities) rather than a PostgREST embed so it
    works regardless of how the FK is exposed.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client:
        return []

    ids: list[int] = []
    page, size = 0, 1000
    while True:
        chunk = (
            client.table("company_job_leads").select("posting_id")
            .eq("decision", "keep")
            .range(page * size, (page + 1) * size - 1).execute().data
        )
        if not chunk:
            break
        ids.extend(r["posting_id"] for r in chunk)
        if len(chunk) < size:
            break
        page += 1
    ids = list(set(ids))
    if not ids:
        return []

    cities: set[str] = set()
    for i in range(0, len(ids), 400):
        rows = (
            client.table("job_postings").select("city,state")
            .in_("id", ids[i:i + 400]).eq("state", "FL")
            .not_.is_("city", "null").execute().data
        )
        for r in rows:
            city = (r.get("city") or "").strip()
            if city:
                cities.add(city)
    return [
        {"query": f"{c}, FL", "state": "FL", "granularity": "city"}
        for c in sorted(cities)
    ]


def build_queries(
    cities_filter: list[str] | None,
    tracks_filter: list[str] | None,
    extra_locations: list[dict] | None = None,
) -> list[dict]:
    """The `noun x location` matrix as query rows, location-major.

    Each row is `{query, noun, service_line, location, state}`. `query` is the
    free-text Google Text Search string ("dental office in Tampa, FL") — the
    endpoint sends nothing but `textQuery`, so geography lives in the string.

    `extra_locations` (from --from-postings) are merged onto the config
    locations, deduped by city. `cities_filter` / `tracks_filter` are
    case-insensitive substring matches so an operator can scope a run without
    editing config ("Tampa,Miami").
    """
    tracks = list(lead_config.service_lines())
    if tracks_filter:
        wanted = [t.lower() for t in tracks_filter]
        tracks = [t for t in tracks if any(w in t.lower() for w in wanted)]

    missing = [t for t in tracks if t not in PLACE_NOUNS]
    if missing:
        raise SystemExit(
            "No place-type nouns for service line(s): "
            f"{', '.join(missing)}.\nAdd them to PLACE_NOUNS in "
            f"{os.path.relpath(__file__)} before scanning."
        )

    locations = list(lead_config.locations())
    if extra_locations:
        seen = {_loc_key(loc["query"]) for loc in locations}
        for loc in extra_locations:
            if _loc_key(loc["query"]) not in seen:
                seen.add(_loc_key(loc["query"]))
                locations.append(loc)
    if cities_filter:
        wanted = [c.lower() for c in cities_filter]
        locations = [
            loc for loc in locations
            if any(w in loc["query"].lower() for w in wanted)
        ]

    rows: list[dict] = []
    for loc in locations:
        for track in tracks:
            for noun in PLACE_NOUNS[track]:
                rows.append({
                    "query": f"{noun} in {loc['query']}",
                    "noun": noun,
                    "service_line": track,
                    "location": loc["query"],
                    "state": loc["state"],
                })
    return rows


async def scan_one(
    row: dict,
    index: int,
    total: int,
    counter: Counter,
    dry_run: bool,
    sem: asyncio.Semaphore,
) -> None:
    """Fetch one query, drop out-of-scope results, dedup, upsert.

    The semaphore bounds concurrent Google fetches (the slow part). Writes run
    concurrently too, guarded by `_write_with_retry` — overlapping-row deadlocks
    and stream resets are retried rather than prevented by serialising, which
    keeps the fan-out fast. A write that still fails after retries is counted as
    an error for this one query and skipped, so it can't abort the whole sweep.
    """
    async with sem:
        t0 = time.time()
        try:
            practices = await search_places(row["query"])  # operator mode: no billing
        except Exception as e:
            counter["errors"] += 1
            print(f"  [{index:>3}/{total}] {row['query'][:44]:46s} "
                  f"ERROR {type(e).__name__}: {str(e)[:60]}")
            return

        relevant = [p for p in practices if "IRRELEVANT" not in p.tags]
        irrelevant = len(practices) - len(relevant)

        upserted = 0
        if relevant and not dry_run:
            # Google occasionally returns two place_ids for one business; rewrite
            # to the canonical existing row so we UPDATE rather than duplicate.
            dupe_map = await asyncio.to_thread(find_duplicate_place_ids, relevant)
            if dupe_map:
                for p in relevant:
                    canonical = dupe_map.get(p.place_id)
                    if canonical:
                        p.place_id = canonical
            # Collapse to one row per place_id BEFORE the upsert. Pagination
            # overlap and the canonical rewrite above can each put the same
            # place_id in the batch twice, and a single upsert with
            # on_conflict=place_id cannot affect one row twice (Postgres 21000).
            seen_ids: set[str] = set()
            unique = [
                p for p in relevant
                if p.place_id not in seen_ids and not seen_ids.add(p.place_id)
            ]
            try:
                # `upsert_practices` returns the rows it wrote; this script
                # only ever wanted how many.
                upserted = len(await _write_with_retry(upsert_practices, unique) or [])
                # Stamp which service line surfaced these rows — the one signal
                # `category` can't carry (home-health classifies as `specialty`).
                await _write_with_retry(
                    tag_service_line,
                    [p.place_id for p in unique],
                    row["service_line"],
                )
            except Exception as e:
                counter["errors"] += 1
                print(f"  [{index:>3}/{total}] {row['query'][:44]:46s} "
                      f"WRITE FAILED {type(e).__name__}: {str(e)[:50]}")
                return

        counter["queries"] += 1
        counter["returned"] += len(practices)
        counter["relevant"] += len(relevant)
        counter["irrelevant"] += irrelevant
        counter["upserted"] += upserted
        for p in relevant:
            counter[f"cat::{p.category or 'uncategorised'}"] += 1

        print(f"  [{index:>3}/{total}] {row['query'][:44]:46s} "
              f"{len(relevant):>3} kept  {irrelevant:>2} skip  "
              f"{'(dry)' if dry_run else f'{upserted:>3} up'}  "
              f"{time.time() - t0:5.1f}s")


async def run(rows: list[dict], concurrency: int, dry_run: bool) -> Counter:
    counter: Counter = Counter()
    sem = asyncio.Semaphore(concurrency)
    total = len(rows)
    started = time.time()
    # A shared completion index so the live log reads 1..N even as tasks finish
    # out of order.
    seq = {"n": 0}

    async def _wrapped(row: dict) -> None:
        seq["n"] += 1
        await scan_one(row, seq["n"], total, counter, dry_run, sem)

    await asyncio.gather(*(_wrapped(row) for row in rows))
    counter["elapsed"] = time.time() - started
    return counter


def summarise(counter: Counter, dry_run: bool) -> None:
    _rule("RESULT")
    print(f"  queries run    : {counter['queries']}  ({counter['errors']} errors)")
    print(f"  places returned: {counter['returned']}")
    print(f"  in scope       : {counter['relevant']}  "
          f"({counter['irrelevant']} out-of-scope dropped)")
    if dry_run:
        print("  upserted       : 0  (--dry-run: nothing written)")
    else:
        print(f"  upserted       : {counter['upserted']}  "
              "(dedup means the unique practice count is lower)")

    cats = sorted(
        ((k[5:], v) for k, v in counter.items() if k.startswith("cat::")),
        key=lambda kv: -kv[1],
    )
    if cats:
        print("\n  Category breakdown (in-scope rows, pre-dedup):")
        for name, n in cats:
            print(f"    {name[:28]:30s} {n:>5}")

    print(f"\n  {counter['elapsed'] / 60:.1f} min. Operator mode — no credits billed.")
    if not dry_run:
        print("  Open the practices page to see them, or query `practices` directly.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities", default=None,
                        help="comma-separated substrings to keep (e.g. 'Tampa,Miami'); "
                             "default = every location in geography.json")
    parser.add_argument("--tracks", default=None,
                        help="comma-separated service-line substrings; default = all")
    parser.add_argument("--from-postings", action="store_true",
                        help="scan the demand geography: config cities PLUS every "
                             "FL city that has a kept lead (self-updating)")
    parser.add_argument("--concurrency", type=int, default=6,
                        help="queries in flight at once (default 6; past ~6 the "
                             "Supabase pool, not Google, becomes the limit)")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the number of queries — for a smoke test")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report, but write nothing")
    parser.add_argument("--allow-mock", action="store_true",
                        help="proceed even without GOOGLE_MAPS_API_KEY (returns mock data)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if not settings.google_maps_api_key and not args.allow_mock:
        print("GOOGLE_MAPS_API_KEY is not set — search_places would return MOCK "
              "data, not a real scan.\nSet the key, or pass --allow-mock to test "
              "the pipeline against fixtures.")
        return 2

    cities = [c.strip() for c in args.cities.split(",")] if args.cities else None
    tracks = [t.strip() for t in args.tracks.split(",")] if args.tracks else None

    extra = None
    if args.from_postings:
        extra = demand_locations()
        print(f"  demand    : {len(extra)} FL cities with kept leads "
              "(merged onto config geography)")

    rows = build_queries(cities, tracks, extra_locations=extra)
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("No queries matched the given --cities / --tracks filters.")
        return 1

    n_locations = len({r["location"] for r in rows})
    n_tracks = len({r["service_line"] for r in rows})
    n_nouns = len({r["noun"] for r in rows})

    _rule("SETUP")
    print(f"  geography   : {n_locations} locations (from config/leads/geography.json)")
    print(f"  tracks      : {n_tracks} service line(s), {n_nouns} place-type noun(s)")
    print(f"  queries     : {len(rows)}")
    print(f"  concurrency : {args.concurrency}")
    print(f"  mode        : OPERATOR (unbilled){'  DRY-RUN' if args.dry_run else ''}")
    print(f"  data source : {'Google Places' if settings.google_maps_api_key else 'MOCK'}")

    _rule("SCAN")
    counter = asyncio.run(run(rows, max(1, args.concurrency), args.dry_run))
    summarise(counter, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
