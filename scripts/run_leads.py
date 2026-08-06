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

    python scripts/run_leads.py --stage both --targets 40
    python scripts/run_leads.py --stage collect --sources indeed --targets 10
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


def collect(company_id: str, targets: int, sources: list[str]) -> dict:
    _rule(f"COLLECT — {targets} targets, sources={','.join(sources)}")
    seeded = lead_targets.ensure_targets(company_id)
    if seeded["inserted"]:
        print(f"  seeded {seeded['inserted']} targets from config/leads/")

    claimed = lead_targets.claim_targets(company_id, targets)
    print(f"  claimed {len(claimed)} targets\n")

    # Imported here rather than at module scope: it pulls in jobspy and pandas,
    # which a qualify-only or preflight-only run has no reason to load.
    from src.job_boards import search_jobs

    totals = Counter()
    started = time.time()
    for i, target in enumerate(claimed, 1):
        t0 = time.time()
        try:
            rows, stats = search_jobs(
                target["term"], target["location"], sources=sources, target=target,
            )
        except Exception as e:
            print(f"  [{i:>3}/{len(claimed)}] {target['term'][:28]:30s} "
                  f"{target['location'][:18]:20s} ERROR {type(e).__name__}")
            totals["errors"] += 1
            continue

        written = lead_store.upsert_postings(rows) if rows else 0
        lead_targets.record_target_result(target["id"], len(rows))
        totals["rows"] += len(rows)
        totals["written"] += written
        if not rows:
            totals["zero"] += 1

        per_source = " ".join(
            f"{s}={st['rows']}" + (f"!{st['error'][:20]}" if st.get("error") else "")
            for s, st in stats.items()
        )
        print(f"  [{i:>3}/{len(claimed)}] {target['term'][:28]:30s} "
              f"{target['location'][:18]:20s} {len(rows):>3} kept  "
              f"{per_source:28s} {time.time() - t0:5.1f}s")

    elapsed = time.time() - started
    print(f"\n  {totals['rows']} rows kept, {totals['written']} upserted, "
          f"{totals['zero']} zero-row targets, {totals['errors']} errors "
          f"in {elapsed / 60:.1f} min")

    if claimed and totals["zero"] == len(claimed):
        print("\n  ALERT: every target returned zero rows. This is the Indeed "
              "API-key\n         rotation failure mode — check the "
              "python-jobspy pin (ADR-02).")
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
    parser.add_argument("--targets", type=int, default=settings.lead_collect_batch,
                        help="search targets to claim (collect)")
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
        collect(company_id, args.targets, sources)

    if args.stage in ("qualify", "both"):
        # Default to draining everything collected rather than one cron-sized
        # batch — the point of running by hand is not being bounded.
        qualify(company_id, args.limit or 100_000)

    summarise(company_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
