"""Twice-daily enrich → push automation — closes the lead pipeline loop.

The scheduled sweeps end at qualify → practice-link. This orchestrator runs the
second half unattended, in two phases per run (push FIRST, enrich SECOND):

  Phase A — PUSH the ready ones. Keeps that are fully enriched (call_script +
    email_draft) AND whose Clay enrichment has resolved AND that have not been
    exported are sent to Talent-DB via `scripts.talentdb_export`.

  Phase B — ENRICH the new keeps. Keeps still missing call_script/email_draft
    (or with no practice yet) are run through `scripts.run_leadset_batched`
    (Places → analyze → call script + email → trigger Clay). They become
    Phase-A-eligible at the NEXT run, once Clay has called back.

Clay is asynchronous (it triggers here, the contact lands later via the inbound
webhook), so a lead enriched in run N is pushed in run N+1 — which is why this
runs twice a day, ~12h apart. Push-first means a slow/capped Phase B never
delays a delivery that was already ready.

Both underlying runners are invoked as subprocesses so their spend cap, dry-run
guard, and fail-soft behaviour are untouched. DRY-RUN by default; pass --yes to
run live.

Design: docs/specs/2026-08-18-enrich-push-automation.md

Usage (from repo root):
    .venv/bin/python scripts/run_enrich_push.py                 # dry run (selects + prints, sends nothing)
    .venv/bin/python scripts/run_enrich_push.py --yes           # live: push ready, enrich new
    .venv/bin/python scripts/run_enrich_push.py --yes --max-places-calls 250 --max-usd 25
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_leadset import _POSTING_FIELDS
from src.settings import settings
from src.storage import _get_client

_PAGE = 1000  # PostgREST hard-caps a response at 1000 rows; paginate with .range().

# Practice columns that decide "enriched enough to push" + Clay state.
_PRACTICE_COLS = "id, call_script, email_draft, enrichment_status, email_draft_updated_at"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    """Parse an ISO timestamp (with or without 'Z') to aware UTC, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fetch_universe(client, company_id: str) -> list[dict]:
    """Every unexported KEEP with an employer and a real track, paginated.

    `job_postings` is the parent so `practice_id` / `service_line_hint` are real
    top-level columns; the lead is embedded `!inner` only to filter on
    decision / tenant / export marker. No hardcoded track list — any non-null
    `service_line_hint` qualifies, so a newly-added track is included on its own.
    """
    rows: list[dict] = []
    start = 0
    while True:
        batch = (
            client.table("job_postings")
            .select(
                f"{_POSTING_FIELDS}, "
                "lead:company_job_leads!inner(company_id, decision, talentdb_exported_at)"
            )
            .not_.is_("employer_name", "null")
            .not_.is_("service_line_hint", "null")
            .eq("lead.company_id", company_id)
            .eq("lead.decision", "keep")
            .is_("lead.talentdb_exported_at", "null")
            .order("posted_at", desc=True, nullsfirst=False)
            .order("id", desc=True)
            .range(start, start + _PAGE - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        start += _PAGE
    return rows


def _fetch_practices(client, practice_ids: list[int]) -> dict[int, dict]:
    """id -> practice row (call_script / email_draft / clay state) for linked postings."""
    out: dict[int, dict] = {}
    for i in range(0, len(practice_ids), 500):
        chunk = practice_ids[i:i + 500]
        rows = (
            client.table("practices").select(_PRACTICE_COLS).in_("id", chunk).execute()
        ).data or []
        for r in rows:
            out[r["id"]] = r
    return out


def _partition(universe: list[dict], practices: dict[int, dict], *, stuck_after: timedelta):
    """Split the keep universe into (push_now, enrich_now, waiting_on_clay).

    push_now      : list of {"id": posting_id} — fully enriched + Clay resolved.
    enrich_now    : list of posting dicts       — no practice, or missing script/email.
    waiting_on_clay: count — enriched by us but Clay still pending (pushed next run).
    """
    push_now: list[dict] = []
    enrich_now: list[dict] = []
    waiting = 0
    now = _now()
    seen: set[int] = set()

    for posting in universe:
        pid = posting["id"]
        if pid in seen:
            continue
        seen.add(pid)
        practice = practices.get(posting.get("practice_id"))
        has_content = bool(practice and practice.get("call_script") and practice.get("email_draft"))

        if not has_content:
            posting.pop("lead", None)  # drop the embed; the runner re-derives its own
            enrich_now.append(posting)
            continue

        status = (practice.get("enrichment_status") or "").strip().lower()
        if status == "pending":
            # Clay hasn't called back yet. Push anyway only if it's been stuck
            # long past a normal round-trip (a lost callback must not strand it).
            edu = _parse_ts(practice.get("email_draft_updated_at"))
            if edu and now - edu > stuck_after:
                push_now.append({"id": pid})
            else:
                waiting += 1
        else:
            # enriched / failed / skipped / null → Clay resolved (or N/A). Push.
            push_now.append({"id": pid})

    return push_now, enrich_now, waiting


def _run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}\n", flush=True)
    return subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1])).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--company-id", default=None, help="tenant (default: settings.lead_company_id)")
    ap.add_argument("--yes", action="store_true",
                    help="run live: push ready leads and enrich new ones (default: dry run)")
    ap.add_argument("--max-usd", type=float, default=25.0,
                    help="Phase B USD spend ceiling passed to run_leadset_batched (default 25)")
    ap.add_argument("--max-places-calls", type=int, default=250,
                    help="Phase B Places-call ceiling (default 250)")
    ap.add_argument("--concurrency", type=int, default=5, help="Phase B enrichment workers")
    ap.add_argument("--push-limit", type=int, default=None, help="cap Phase A to first N leads")
    ap.add_argument("--enrich-limit", type=int, default=None, help="cap Phase B to first N leads")
    ap.add_argument("--stuck-pending-hours", type=float, default=6.0,
                    help="push a Clay-pending lead anyway once it is older than this (default 6h)")
    ap.add_argument("--skip-push", action="store_true", help="run only Phase B (enrich)")
    ap.add_argument("--skip-enrich", action="store_true", help="run only Phase A (push)")
    args = ap.parse_args()

    company_id = args.company_id or settings.lead_company_id
    if not company_id:
        print("No company_id — pass --company-id or set LEAD_COMPANY_ID.", file=sys.stderr)
        return 2
    client = _get_client()
    if not client:
        print("No Supabase client — check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.", file=sys.stderr)
        return 2

    live = args.yes
    root = Path(__file__).resolve().parents[1]
    push_file = root / "leadset-push-auto.json"
    enrich_file = root / "leadset-enrich-auto.json"

    print("=" * 72)
    print(f"[enrich-push] company={company_id}  mode={'LIVE' if live else 'DRY-RUN'}")
    print("=" * 72)

    universe = _fetch_universe(client, company_id)
    linked = sorted({p["practice_id"] for p in universe if p.get("practice_id")})
    practices = _fetch_practices(client, linked)
    push_now, enrich_now, waiting = _partition(
        universe, practices, stuck_after=timedelta(hours=args.stuck_pending_hours))

    print(f"  keep + unexported universe : {len(universe)}")
    print(f"  → push now (enriched+Clay) : {len(push_now)}")
    print(f"  → waiting on Clay          : {waiting}  (pushed a later run)")
    print(f"  → enrich now (new keeps)   : {len(enrich_now)}")

    push_file.write_text(json.dumps(push_now, indent=2))
    enrich_file.write_text(json.dumps(enrich_now, indent=2, default=str))

    rc = 0

    # ---- Phase A: PUSH the ready ones (first, so a slow Phase B can't delay it) ----
    if args.skip_push:
        print("\n[Phase A] skipped (--skip-push)")
    elif not push_now:
        print("\n[Phase A] nothing ready to push.")
    else:
        print(f"\n[Phase A] pushing {len(push_now)} enriched leads → Talent-DB")
        cmd = [sys.executable, "-m", "scripts.talentdb_export",
               "--leadset", str(push_file), "--company-id", company_id]
        if live:
            cmd.append("--yes")
        if args.push_limit:
            cmd += ["--limit", str(args.push_limit)]
        rc |= _run(cmd)

    # ---- Phase B: ENRICH the new keeps (they become pushable next run) ----
    if args.skip_enrich:
        print("\n[Phase B] skipped (--skip-enrich)")
    elif not enrich_now:
        print("\n[Phase B] nothing new to enrich.")
    else:
        print(f"\n[Phase B] enriching {len(enrich_now)} new keeps "
              f"(cap {args.max_places_calls} Places / ${args.max_usd:g})")
        cmd = [sys.executable, "-m", "scripts.run_leadset_batched",
               "--leadset", str(enrich_file), "--company-id", company_id,
               "--concurrency", str(args.concurrency),
               "--max-usd", str(args.max_usd),
               "--max-places-calls", str(args.max_places_calls)]
        cmd.append("--yes" if live else "--dry-run")
        if args.enrich_limit:
            cmd += ["--max-leads", str(args.enrich_limit)]
        rc |= _run(cmd)

    print("\n" + "=" * 72)
    print(f"[enrich-push] done  mode={'LIVE' if live else 'DRY-RUN'}  "
          f"pushed_set={len(push_now)}  enriched_set={len(enrich_now)}  exit={rc}")
    print("=" * 72)
    return rc


if __name__ == "__main__":
    sys.exit(main())
