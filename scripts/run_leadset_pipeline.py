"""Batch runner: walk a lead set (leadset-100.json) through the per-posting
pipeline (`scripts.posting_to_talentdb`), serially, with a HARD SPEND CAP.

Why the cap exists — read `docs/incidents/2026-08-07-operator-mode-uncapped-places-spend.md`:
a previous bulk Places scan ran in operator mode (no per-tenant credit gate) and
spent ~$140 with no ceiling and no real-dollar readout. This runner is the
compensating control that incident's P0 #1 asked for — a hard ceiling on operator
Places spend that **aborts cleanly** when the running `usage_events` total would
cross it. The per-posting pipeline already makes exactly ONE Places call per lead
(no pagination), so the only remaining risk is aggregate; that is what this caps.

Per lead the pipeline spends: 1 Places Text Search (~4¢, operator mode), ~3 OpenAI
calls (analyze + script + email ≈ 2.5¢), and 1 Clay enrichment (prepaid data
credits, no new cash). Qualify + Talent-DB webhook are skipped (leads are already
`keep`; the current decision is to keep everything in-system).

Safety model mirrors `scripts/backfill_talentdb.py`: defaults to a DRY RUN; a live
run requires `--yes`. NOT idempotent across runs — with reprocess-always, running
the set twice re-spends every lead. The cap bounds a SINGLE run, not the sum of
runs.

The ceiling is enforced BEFORE each lead against REAL spend read back from
`usage_events` (Places rows are operator-mode / company_id NULL, so Places calls
are counted by kind, not by tenant; OpenAI cost is summed for the tenant). If the
next lead would breach `--max-usd` or `--max-places-calls`, the batch stops
cleanly and writes its log — it never crosses the ceiling.

Usage:
    # preview: no external calls, shows the plan + estimate + caps
    .venv/bin/python -m scripts.run_leadset_pipeline --dry-run

    # canary: one lead, live, tight cap
    .venv/bin/python -m scripts.run_leadset_pipeline --max-leads 1 --max-usd 1 --yes

    # full run, default caps ($10 / 120 Places calls)
    .venv/bin/python -m scripts.run_leadset_pipeline --yes

Flags: --leadset, --company-id, --max-usd, --max-places-calls, --max-leads,
--delay, --skip-enrich, --dry-run, --yes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

from src.settings import settings
from src.storage import _get_client
from scripts.posting_to_talentdb import run as run_one

# Per-lead estimate for the pre-flight display and the one-lead cap look-ahead.
# The HARD cap uses real ledger spend, not this — accuracy here only affects the
# printed estimate and how early the look-ahead trips.
EST_LEAD_CENTS = 6.5          # Places ~4¢ + OpenAI ~2.5¢ (analyze + script + email)
PLACES_CALL_CENTS = 4.0       # SKU 120C ceiling (measured ~3.5¢; use the higher for safety)
CLAY_DATA_CREDITS_PER_LEAD = 2.16   # measured: 237.5 data credits / 110 enrichments
_PAGE = 1000                  # PostgREST row cap — paginate with .range()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(posting: dict) -> dict:
    """Drop keys the DB doesn't own before the pipeline upserts the posting.

    `leadset-*.json` rows carry build-time annotations (`_qualifier_*`) and the
    existing PK / link (`id`, `practice_id`) — none are valid `job_postings`
    insert columns (or the pipeline sets them itself), and an unknown column makes
    the upsert throw. Keep only the real posting fields.
    """
    return {
        k: v for k, v in posting.items()
        if not k.startswith("_") and k not in ("id", "practice_id")
    }


def _sum_since(client, since_iso: str, *, field: str,
               kind_eq: str | None = None, kind_like: str | None = None,
               company_id: str | None = None) -> float:
    """Sum `field` from usage_events created at/after `since_iso`, paginated."""
    total, page = 0.0, 0
    while True:
        q = (client.table("usage_events").select(f"{field},kind,created_at,company_id")
             .gte("created_at", since_iso))
        if kind_eq:
            q = q.eq("kind", kind_eq)
        if kind_like:
            q = q.like("kind", kind_like)
        if company_id:
            q = q.eq("company_id", company_id)
        batch = q.range(page * _PAGE, (page + 1) * _PAGE - 1).execute().data or []
        total += sum((r.get(field) or 0) for r in batch)
        if len(batch) < _PAGE:
            break
        page += 1
    return total


def _spent_since(client, since_iso: str, company_id: str) -> tuple[int, float]:
    """Real spend this run → (places_calls, total_cents).

    Places is recorded operator-mode (company_id NULL), so Places calls are
    counted by kind across all rows; OpenAI cost is summed for this tenant.
    """
    places_calls = int(_sum_since(client, since_iso, field="calls", kind_eq="places_search"))
    openai_cents = _sum_since(client, since_iso, field="cost_cents",
                              kind_like="openai_%", company_id=company_id)
    total_cents = places_calls * PLACES_CALL_CENTS + openai_cents
    return places_calls, total_cents


async def run(leadset_path: str, company_id: str, *, dry_run: bool, yes: bool,
              max_usd: float, max_places_calls: int, max_leads: int | None,
              delay: float, skip_enrich: bool) -> None:
    client = _get_client()
    if not client:
        sys.exit("No Supabase client — check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.")

    try:
        leads = json.load(open(leadset_path))
    except Exception as e:
        sys.exit(f"could not read leadset {leadset_path!r}: {e}")
    if not isinstance(leads, list) or not leads:
        sys.exit(f"{leadset_path!r} must be a non-empty JSON list of postings.")
    if max_leads is not None:
        leads = leads[:max_leads]
    n = len(leads)

    max_usd_cents = max_usd * 100.0
    est_cents = n * EST_LEAD_CENTS
    est_clay = n * CLAY_DATA_CREDITS_PER_LEAD

    print(f"[batch] leadset: {leadset_path}  ({n} leads)")
    print(f"[batch] company_id: {company_id}")
    print(f"[batch] pipeline per lead: 1 Places call + analyze + script + email"
          f"{'' if skip_enrich else ' + Clay enrich'}  (qualify + webhook skipped)")
    print(f"[batch] CAP: hard stop at ${max_usd:.2f} real spend OR {max_places_calls} Places calls")
    print(f"[batch] estimate: ~${est_cents/100:.2f} vendor cost"
          f"{'' if skip_enrich else f' + ~{est_clay:.0f} prepaid Clay data credits'}"
          f"  (~{EST_LEAD_CENTS:.1f}¢/lead)")
    if est_cents > max_usd_cents:
        halt_at = int(max_usd_cents // EST_LEAD_CENTS)
        print(f"[batch] ⚠  estimate ${est_cents/100:.2f} EXCEEDS the ${max_usd:.2f} cap — "
              f"the run will stop cleanly at ~{halt_at} leads. Raise --max-usd to do all {n}.")
    print(f"[batch] mode: {'DRY RUN (no external calls, no writes)' if dry_run else 'LIVE'}")

    if dry_run:
        print("\n[dry-run] leads that would be processed (sanitized), in order:")
        for idx, raw in enumerate(leads, 1):
            p = _sanitize(raw)
            emp, city = p.get("employer_name"), p.get("city")
            missing = [k for k in ("source", "external_id", "employer_name") if not p.get(k)]
            flag = f"  ⚠ missing {missing}" if missing else ""
            print(f"  [{idx:>3}/{n}] {p.get('source')}:{p.get('external_id')}  "
                  f"{emp!r} · {city}  → Places {f'{emp} {city}'.strip()!r}{flag}")
        print(f"\n[dry-run] No calls made. Re-run with --yes to execute live "
              f"(cap ${max_usd:.2f} / {max_places_calls} Places calls).")
        return

    if not yes:
        print("\n[batch] REFUSING to run live without --yes. "
              "Re-run with --dry-run to preview, or add --yes to execute.")
        return

    print("\n[batch] ⚠  reprocess-always: every lead runs this pass, even if a prior "
          "run already processed it — re-running the set re-spends. The cap bounds "
          "THIS run only.\n")

    run_start = _now()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = f"docs/runbooks/leadset-run-{stamp}.log.json"
    results: list[dict] = []
    ok = failed = 0
    cap_hit: str | None = None

    for idx, raw in enumerate(leads, 1):
        posting = _sanitize(raw)

        # ---- CAP CHECK: real spend so far + one-lead look-ahead ----
        places_calls, spent_cents = _spent_since(client, run_start, company_id)
        if places_calls + 1 > max_places_calls:
            cap_hit = (f"Places-call ceiling: {places_calls} made, "
                       f"limit {max_places_calls} — next lead would exceed it")
            print(f"[batch] 🛑 CAP HIT — {cap_hit}. Stopping cleanly before lead {idx}.")
            break
        if spent_cents + EST_LEAD_CENTS > max_usd_cents:
            cap_hit = (f"USD ceiling: ${spent_cents/100:.2f} spent, "
                       f"limit ${max_usd:.2f} — next lead would exceed it")
            print(f"[batch] 🛑 CAP HIT — {cap_hit}. Stopping cleanly before lead {idx}.")
            break

        emp = posting.get("employer_name")
        label = f"{emp!r} · {posting.get('city')}"
        print(f"[batch] [{idx}/{n}] spent ${spent_cents/100:.2f} / {places_calls} calls "
              f"→ processing {label}")

        entry = {"idx": idx, "source": posting.get("source"),
                 "external_id": posting.get("external_id"),
                 "employer": emp, "at": _now()}
        try:
            await run_one(posting, company_id, dry_run=False,
                          skip_qualify=True, skip_enrich=skip_enrich, skip_webhook=True)
            entry["outcome"] = "ok"
            ok += 1
        except SystemExit as e:  # pipeline aborts one lead — never kill the batch
            entry["outcome"] = f"aborted: {e}"
            failed += 1
            print(f"[batch] [{idx}/{n}] lead ABORTED (non-fatal) — {e}")
        except Exception as e:  # noqa: BLE001
            entry["outcome"] = f"error: {type(e).__name__}: {e}"
            failed += 1
            print(f"[batch] [{idx}/{n}] lead ERROR (non-fatal) — {type(e).__name__}: {e}")
        results.append(entry)

        if idx < n:
            time.sleep(delay)

    final_calls, final_cents = _spent_since(client, run_start, company_id)
    summary = {
        "run_at": stamp, "company_id": company_id, "leadset": leadset_path,
        "requested": n, "processed": len(results), "ok": ok, "failed": failed,
        "cap_usd": max_usd, "cap_places_calls": max_places_calls,
        "cap_hit": cap_hit,
        "final_places_calls": final_calls,
        "final_spend_usd": round(final_cents / 100.0, 4),
        "clay_enrich": not skip_enrich,
        "results": results,
    }
    import os
    os.makedirs("docs/runbooks", exist_ok=True)
    with open(log_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n[batch] done: ok={ok} failed={failed} processed={len(results)}/{n}")
    print(f"[batch] real spend this run: ${final_cents/100:.2f}  ·  {final_calls} Places calls")
    if cap_hit:
        print(f"[batch] stopped early by cap — {cap_hit}")
    print(f"[batch] log → {log_path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run a lead set through the posting→Talent-DB pipeline with a hard spend cap.")
    ap.add_argument("--leadset", default="leadset-100.json",
                    help="path to the lead set JSON (default: leadset-100.json)")
    ap.add_argument("--company-id", default=None,
                    help="tenant to attribute to (default: settings.lead_company_id)")
    ap.add_argument("--max-usd", type=float, default=10.0,
                    help="hard ceiling on real vendor spend for this run (default: $10)")
    ap.add_argument("--max-places-calls", type=int, default=120,
                    help="hard ceiling on Places calls for this run (default: 120)")
    ap.add_argument("--max-leads", type=int, default=None,
                    help="process at most N leads (e.g. 1 for a canary)")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="seconds to pause between leads (default: 2)")
    ap.add_argument("--skip-enrich", action="store_true",
                    help="skip Clay enrichment (no data-credit burn)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan + estimate + caps, make NO external calls (default-safe)")
    ap.add_argument("--yes", action="store_true",
                    help="required to run live — confirms real Places/OpenAI/Clay spend")
    args = ap.parse_args()

    company_id = args.company_id or settings.lead_company_id
    if not company_id:
        sys.exit("No company_id — pass --company-id or set LEAD_COMPANY_ID.")
    if args.max_leads is not None and args.max_leads <= 0:
        sys.exit("--max-leads must be positive.")

    dry_run = args.dry_run or not args.yes
    asyncio.run(run(
        args.leadset, company_id,
        dry_run=dry_run, yes=args.yes,
        max_usd=args.max_usd, max_places_calls=args.max_places_calls,
        max_leads=args.max_leads, delay=args.delay, skip_enrich=args.skip_enrich,
    ))


if __name__ == "__main__":
    main()
