#!/usr/bin/env python3
"""Run the lead pipeline directly — collect, qualify, or both.

The cron stages are the steady-state path, but they run inside a serverless
invocation with a hard wall-clock ceiling, and LinkedIn averages ~22s per query
against Indeed's ~1.5s. A 40-target sweep across both boards is ~15 minutes,
which no serverless function will survive. This script calls the same modules
with no HTTP layer, no timeout, and no scheduler — so a first run, a backfill,
or a debugging pass can be as large as it needs to be.

It is not a second implementation: `lead_targets`, `job_boards`, `lead_store`
and `lead_qualifier` are the same code the cron drives. Only the wrapper differs.

    python scripts/run_leads.py --stage both --budget-minutes 40
    python scripts/run_leads.py --stage collect --sources indeed --budget-minutes 10
    python scripts/run_leads.py --stage qualify --limit 200
    python scripts/run_leads.py --preflight        # one cheap model call

Reads .env for SUPABASE_*, OPENAI_API_KEY and LEAD_COMPANY_ID.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import lead_config, lead_qualifier, lead_store, lead_targets  # noqa: E402
from src.credits import InsufficientCreditsError  # noqa: E402
from src.settings import settings  # noqa: E402


def _setup_logging(verbose: bool) -> None:
    # Python block-buffers stdout when it isn't a terminal, so a run piped to a
    # file or a log collector shows nothing until it exits — which for a
    # 20-minute sweep is indistinguishable from a hang.
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


# ---------------------------------------------------------------------------


def preflight(company_id: str) -> bool:
    """One two-posting model call before spending a real batch.

    The qualifier model id came from an evaluation prototype that could not
    verify it. If it is wrong every batch 400s and the run produces postings
    with zero leads — which reads as a pipeline bug and isn't one. Ten seconds
    here also exercises the real parameter handling rather than a test stub.
    """
    _rule("PREFLIGHT — one model call")
    samples = [
        {
            "id": -1,
            "title": "Front Office Coordinator",
            "employer_name": "Bay Family Dentistry",
            "location_raw": "Tampa, FL, US",
            "board_remote_flag": False,
            "salary_min": 20.0, "salary_max": 24.0, "salary_interval": "hourly",
            "service_line_hint": "Virtual Dental Assistant",
            "description": "Answer phones, schedule patients, verify insurance "
                           "benefits for a two-dentist practice.",
        },
        {
            "id": -2,
            "title": "Registered Nurse - ICU",
            "employer_name": "AdventHealth Orlando",
            "location_raw": "Orlando, FL, US",
            "board_remote_flag": False,
            "salary_min": None, "salary_max": None, "salary_interval": None,
            "service_line_hint": "Virtual Medical Assistant",
            "description": "Provide direct bedside patient care in a 40-bed ICU.",
        },
    ]
    print(f"  model            : {settings.qualifier_model}")
    print(f"  reasoning_effort : {settings.qualifier_reasoning_effort}")
    try:
        verdicts, _ = lead_qualifier.qualify_batch(samples, company_id=company_id)
    except Exception as e:
        print(f"\n  FAILED — {type(e).__name__}: {str(e)[:400]}")
        print("\n  If this is a model-not-found error, correct QUALIFIER_MODEL in .env.")
        return False

    by_id = {v["posting_id"]: v for v in verdicts}
    ok = True
    for posting_id, expected, label in ((-1, "keep", "independent + admin role"),
                                        (-2, "discard", "hospital system + clinical role")):
        verdict = by_id.get(posting_id)
        if verdict is None:
            print(f"  [MISS] {label}: no verdict returned")
            ok = False
            continue
        hit = verdict["decision"] == expected
        ok = ok and hit
        print(f"  [{'OK  ' if hit else 'MISS'}] {label}: {verdict['decision']} "
              f"(conf {verdict['confidence']}, {verdict['employer_type']}) "
              f"— {(verdict['reason'] or '')[:80]}")

    # Nothing is persisted: these ids are negative and never reach the DB.
    print(f"\n  {'PASS — the model answers and reasons correctly' if ok else 'The model answered, but not as expected. Inspect before a full run.'}")
    return ok


_SOURCE_ORDER = ("indeed", "linkedin")


def collect(company_id: str, budget_minutes: float, sources: list[str]) -> dict:
    """The two-phase budget loop (plan §3): claim the stalest due location for
    one source at a time, cross it with every enabled term, search, upsert,
    then stamp — only once every term for that location has actually run.

    Indeed sweeps first and LinkedIn gets whatever budget is left, because
    Indeed answers in ~1.5s against LinkedIn's ~22s; sharing one deadline
    across both phases (rather than a budget each) is what makes that
    trade-off automatic instead of a config knob.
    """
    ordered_sources = [s for s in _SOURCE_ORDER if s in sources]
    _rule(f"COLLECT — budget={budget_minutes:g} min, sources={','.join(ordered_sources)}")

    seeded = lead_targets.ensure_targets(company_id)
    if seeded["terms"] or seeded["locations"]:
        print(f"  seeded {seeded['terms']} terms, {seeded['locations']} locations from config")

    # Fetched ONCE per run, not per claimed location — neither the enabled
    # term list nor the override pins change mid-sweep, and re-querying them
    # per location would be two extra round trips for every claim.
    terms = lead_targets.enabled_terms(company_id)
    config = lead_targets.list_config(company_id)
    overrides = {(o["term_id"], o["location_id"]): o["enabled"] for o in config["overrides"]}
    print(f"  terms={len(terms)}  locations={len(config['locations'])}  "
          f"overrides={len(overrides)}\n")

    if not terms:
        print("  no enabled terms — nothing to search")
        return {}

    # Imported here rather than at module scope: it pulls in jobspy and pandas,
    # which a qualify-only or preflight-only run has no reason to load.
    from src.job_boards import search_jobs

    totals = Counter()
    per_source = {s: Counter() for s in ordered_sources}
    started = time.time()
    deadline = time.monotonic() + budget_minutes * 60
    loc_index = 0

    for source in ordered_sources:
        cursor_col = f"last_{source}_at"
        print(f"  -- {source} --")
        while time.monotonic() < deadline:
            claimed = lead_targets.claim_locations(company_id, source, limit=1)
            if not claimed:
                print(f"  {source}: nothing due — sweep is caught up")
                break
            location = claimed[0]
            loc_index += 1

            window = lead_targets.adaptive_window_hours(
                location.get(cursor_col), settings.lead_window_buffer_hours
            )
            rows_for_loc = lead_targets.build_claim_rows(location, terms, overrides)

            t0 = time.time()
            loc_rows = 0
            loc_new = 0
            completed = True
            for row in rows_for_loc:
                if time.monotonic() >= deadline:
                    completed = False  # deadline hit mid-location — the
                    break              # unfinished terms are redone next run

                try:
                    found, stats = search_jobs(
                        row["term"], row["location"],
                        sources=[source], target=row, hours_old=window,
                    )
                except Exception as e:
                    print(f"      ERROR {row['term'][:28]:30s} {type(e).__name__}: "
                          f"{str(e)[:80]}")
                    totals["errors"] += 1
                    continue

                stat = stats.get(source, {})
                if stat.get("error"):
                    totals["errors"] += 1
                row_count = stat.get("rows", 0)
                if found:
                    existing = lead_store.existing_external_ids(
                        source, [r["external_id"] for r in found if r.get("external_id")]
                    )
                    new_count = sum(
                        1 for r in found if r.get("external_id") not in existing
                    )
                    totals["written"] += lead_store.upsert_postings(found)
                else:
                    new_count = 0
                lead_targets.record_target_result(
                    row["term_id"], row["location_id"], source, row_count, new_count,
                )
                loc_rows += row_count
                loc_new += new_count
                totals["rows"] += row_count
                totals["new"] += new_count

            if completed:
                # Only stamp + record the sweep once EVERY term ran. Stamping
                # an incomplete location would tell the next run it is fresh
                # when part of it was never actually searched — crash-safe
                # semantics require the whole location to finish first.
                lead_targets.stamp_location(company_id, location["id"], source)
                lead_targets.record_location_sweep(company_id, location["id"], source, loc_rows)
            else:
                totals["incomplete"] += 1

            per_source[source]["locations"] += 1
            per_source[source]["rows"] += loc_rows
            per_source[source]["new"] += loc_new

            status = "" if completed else "  INCOMPLETE (budget)"
            print(f"  [{loc_index:>3}] {source:8s} {location['location'][:26]:28s} "
                  f"{loc_rows:>3} kept ({loc_new} new)  window={window:>3}h  "
                  f"{time.time() - t0:5.1f}s{status}")

            if not completed:
                break  # budget exhausted mid-location; stop this source's phase
        print()

    elapsed = time.time() - started
    print(f"  budget used: {elapsed / 60:.1f} / {budget_minutes:g} min\n")

    for source in ordered_sources:
        s = per_source[source]
        novelty = (s["new"] / s["rows"] * 100) if s["rows"] else 0.0
        print(f"  {source:10s}: {s['locations']} locations swept, {s['rows']} rows "
              f"({s['new']} new, {novelty:.0f}% novelty)")

    status = lead_targets.sweep_status(company_id)
    for source, st in status.items():
        oldest = (
            f"{st['oldest_cursor_age_hours']}h"
            if st["oldest_cursor_age_hours"] is not None else "never"
        )
        print(f"  {source:10s} sweep: {st['coverage_pct']}% coverage, "
              f"{st['never_swept']} never swept, oldest cursor {oldest}")

    print(f"\n  {totals['rows']} rows kept, {totals['written']} written, "
          f"{totals['new']} new, {totals['incomplete']} incomplete locations, "
          f"{totals['errors']} errors")

    # The Indeed failure mode is silence, not an error (ADR-02): the library
    # reaches an undocumented mobile API whose key can rotate upstream, after
    # which every query returns zero rows and nothing raises. A source that
    # swept at least one location and kept nothing across all of them is the
    # tripwire.
    alert_sources = [
        s for s in ordered_sources
        if per_source[s]["locations"] and per_source[s]["rows"] == 0
    ]
    if alert_sources:
        print(f"\n  ALERT: {', '.join(alert_sources)} swept locations but returned "
              "zero rows. This is the Indeed API-key\n         rotation failure "
              "mode — check the python-jobspy pin (ADR-02).")
    return dict(totals)


def qualify(company_id: str, limit: int) -> dict:
    _rule(f"QUALIFY — up to {limit} postings")
    postings = lead_store.claim_unqualified(company_id, limit)
    print(f"  claimed {len(postings)} unqualified postings")
    if not postings:
        print("  nothing to do — every collected posting is already qualified")
        return {}

    batch_size = settings.qualifier_batch_size
    totals = Counter()
    started = time.time()
    for i, chunk in enumerate(lead_qualifier.batched(postings), 1):
        t0 = time.time()
        try:
            verdicts, stats = lead_qualifier.qualify_batch(chunk, company_id=company_id)
        except InsufficientCreditsError:
            print("\n  STOPPED — insufficient credits. Postings stay unqualified "
                  "and are re-claimed after a top-up.")
            break
        except Exception as e:
            print(f"  batch {i}: FAILED {type(e).__name__}: {str(e)[:160]}")
            totals["failed_batches"] += 1
            continue

        written = lead_store.write_verdicts(company_id, verdicts)
        totals["verdicts"] += written
        totals["keeps"] += stats["keeps"]
        totals["missing"] += stats["missing"]
        print(f"  batch {i:>2} ({len(chunk):>2} postings): {stats['verdicts']} verdicts, "
              f"{stats['keeps']} keeps, {stats['missing']} missing  "
              f"{time.time() - t0:5.1f}s")

    print(f"\n  {totals['verdicts']} leads written, {totals['keeps']} keeps "
          f"in {(time.time() - started) / 60:.1f} min")

    # Link the fresh keepers to their practice. This is the path the GitHub
    # Actions pipeline actually runs (leads.yml calls THIS script, not the HTTP
    # cron), so the matcher has to fire here too — same shared incremental
    # matcher `cron_qualify_leads` uses, scoped to the postings just claimed.
    if totals["keeps"]:
        from src import practice_matcher

        m = practice_matcher.link_postings(
            company_id, posting_ids=[p["id"] for p in postings],
        )
        totals["linked"] = m["linked"]
        print(f"  linked {m['linked']} to practices "
              f"({m['auto']} auto, {m['review']} review, {m['cleared']} cleared)")
    return dict(totals)


def summarise(company_id: str) -> None:
    _rule("RESULT")
    rows, total = lead_store.list_leads(company_id, limit=10)
    analytics = lead_store.lead_analytics(company_id)
    # Kept leads that resolved to a practice — the actionable, contactable
    # subset. A live count of current state, so it reflects every link the
    # matcher has made, not just this run's.
    _, linked = lead_store.list_leads(company_id, filters={"practice": "yes"}, limit=1)
    print(f"  kept leads     : {total}")
    print(f"  linked         : {linked}"
          + (f" ({linked / total * 100:.0f}% of kept)" if total else ""))
    print(f"  all qualified  : {analytics['total']}")
    print(f"  keep rate      : {analytics['keep_rate'] * 100:.0f}%")
    print(f"  bands          : {analytics['bands']}")
    print(f"  tracks         : {analytics['tracks']}")
    if rows:
        print("\n  Top leads:")
        for row in rows[:10]:
            print(f"    {(row.get('employer_name') or '(confidential)')[:30]:32s} "
                  f"{row['title'][:30]:32s} {(row.get('city') or '')[:14]:16s} "
                  f"{row.get('confidence_band') or '':7s} "
                  f"conf={row.get('confidence')}")
    print("\n  Open /signals to see them.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("collect", "qualify", "both"), default="both")
    parser.add_argument("--budget-minutes", type=float, default=settings.lead_budget_minutes,
                        help="wall-clock ceiling for the collect phase")
    parser.add_argument("--limit", type=int, default=None,
                        help="postings to qualify; default = everything unqualified")
    parser.add_argument("--sources", default=None,
                        help="comma-separated board list; default = every enabled source")
    parser.add_argument("--company-id", default=None)
    parser.add_argument("--preflight", action="store_true",
                        help="run only the model check and exit")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    try:
        company_id = args.company_id or lead_targets.resolve_company_id()
    except lead_targets.NoLeadCompany as e:
        print(f"No tenant: {e}")
        return 2

    config = lead_config.validate()
    sources = (
        [s.strip() for s in args.sources.split(",") if s.strip()]
        if args.sources else list(lead_config.enabled_sources())
    )

    _rule("SETUP")
    print(f"  company_id : {company_id}")
    print(f"  config     : {config['terms']} terms x {config['locations']} locations "
          f"= {config['targets']} targets")
    print(f"  sources    : {', '.join(sources)}")

    if args.preflight:
        return 0 if preflight(company_id) else 1

    if args.stage in ("qualify", "both") and not args.skip_preflight:
        if not preflight(company_id):
            print("\nAborting before spending a full batch. "
                  "Re-run with --skip-preflight to override.")
            return 1

    if args.stage in ("collect", "both"):
        collect(company_id, args.budget_minutes, sources)

    if args.stage in ("qualify", "both"):
        # Default to draining everything collected rather than one cron-sized
        # batch — the point of running by hand is not being bounded.
        qualify(company_id, args.limit or 100_000)

    summarise(company_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
