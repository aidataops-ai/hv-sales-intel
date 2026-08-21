"""Generate the missing playbook/analysis for ready-to-push fan-out leads.

A lead is pushable the moment its practice is enriched and has eligible
contacts — but "pushable" and "dressed" are different bars: the sales team
also wants the AI analysis, the call-script playbook, and the email draft on
the practice page. This script finds the gap and fills exactly it:

    kept leads, not yet exported, created in [--since, --until), practice
    `enriched` with >=1 eligible contact (email + direct phone), but
    `call_script` or `email_draft` missing.

For each such practice it runs the SAME slow half as the pipeline runners
(`run_leadset_batched.py` stage B, Clay excluded): `analyze_practice` →
(`generate_script` ∥ `generate_email_draft`), with the same writes and the
same usage-ledger accounting. Practices are deduped (several leads on one
practice → one generation). No Clay, no Places, no Talent-DB — this spends
only OpenAI (~2¢/practice) and touches nothing but the practice's AI fields.

Usage:
    .venv/bin/python -m scripts.dress_ready_leads --until 2026-08-21T20:00:00Z        # dry run
    .venv/bin/python -m scripts.dress_ready_leads --until 2026-08-21T20:00:00Z --yes  # generate
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

from src.analyzer import analyze_practice
from src.email_gen import generate_email_draft
from src.scriptgen import generate_script
from src.settings import settings
from src.storage import (
    _get_client,
    update_practice_analysis,
    update_practice_fields,
)
from src import contacts as contact_store
from src import talentdb_push

_PAGE = 1000
_CHUNK = 500


def _paged(query_builder):
    rows: list[dict] = []
    page = 0
    while True:
        res = query_builder.range(page * _PAGE, page * _PAGE + _PAGE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
        page += 1


def _blank(value) -> bool:
    return not str(value or "").strip()


def fetch_targets(since: str, until: str | None) -> list[dict]:
    """Distinct practices behind ready fan-out leads that lack script/draft."""
    client = _get_client()
    q = (
        client.table("company_job_leads").select("posting_id")
        .eq("decision", "keep")
        .is_("talentdb_exported_at", "null")
        .gte("created_at", since)
    )
    if until:
        q = q.lt("created_at", until)
    posting_ids = sorted({r["posting_id"] for r in _paged(q) if r.get("posting_id")})

    practice_ids: set[int] = set()
    for i in range(0, len(posting_ids), _CHUNK):
        res = (
            client.table("job_postings").select("practice_id")
            .in_("id", posting_ids[i:i + _CHUNK])
            .not_.is_("practice_id", "null")
            .execute()
        )
        practice_ids.update(r["practice_id"] for r in res.data or []
                            if r.get("practice_id"))

    targets: list[dict] = []
    id_list = sorted(practice_ids)
    for i in range(0, len(id_list), _CHUNK):
        res = (
            client.table("practices").select(
                "id, place_id, name, website, category, city, state, rating, "
                "review_count, enrichment_status, call_script, email_draft")
            .in_("id", id_list[i:i + _CHUNK])
            .execute()
        )
        for row in res.data or []:
            if row.get("enrichment_status") != "enriched":
                continue
            if not (_blank(row.get("call_script")) or _blank(row.get("email_draft"))):
                continue
            # Only practices the push will actually fan out — same eligibility
            # as push_lead_fanout; the legacy owner_* single is retired.
            rows = contact_store.list_contacts_for_practice(row["id"])
            if not talentdb_push.eligible_contacts(rows):
                continue
            targets.append(row)
    targets.sort(key=lambda r: r["id"])
    return targets


async def _dress(row: dict, company_id: str) -> None:
    place_id = row["place_id"]
    analysis = await analyze_practice(
        place_id=place_id, name=row.get("name"), website=row.get("website"),
        category=row.get("category"), city=row.get("city"), state=row.get("state"),
        rating=row.get("rating"), review_count=row.get("review_count") or 0,
        company_id=company_id, user_id=None,
    )
    update_practice_analysis(place_id, analysis, touched_by=None,
                             company_id=company_id)
    script, draft = await asyncio.gather(
        generate_script(
            name=row.get("name"), category=row.get("category"),
            summary=analysis.get("summary"),
            pain_points=analysis.get("pain_points"),
            sales_angles=analysis.get("sales_angles"),
            city=row.get("city"), state=row.get("state"),
            rating=row.get("rating"), review_count=row.get("review_count"),
            website_doctor_name=analysis.get("website_doctor_name"),
            company_id=company_id, user_id=None,
        ),
        generate_email_draft(
            name=row.get("name"), category=row.get("category"),
            summary=analysis.get("summary"),
            pain_points=analysis.get("pain_points"),
            sales_angles=analysis.get("sales_angles"),
            company_id=company_id, user_id=None,
        ),
    )
    now = datetime.now(timezone.utc).isoformat()
    update_practice_fields(place_id, {"call_script": json.dumps(script)},
                           touched_by=None, company_id=company_id)
    update_practice_fields(
        place_id,
        {"email_draft": json.dumps(draft), "email_draft_updated_at": now},
        touched_by=None, company_id=company_id,
    )


async def run(since: str, until: str | None, company_id: str, *,
              live: bool, limit: int | None, concurrency: int) -> None:
    targets = fetch_targets(since, until)
    if limit is not None:
        targets = targets[:limit]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"[dress] {len(targets)} undressed ready practice(s)"
          f"{'' if live else '  (DRY RUN)'}")
    for row in targets:
        print(f"  practice={row['id']} {row.get('name')}"
              f"  script={'yes' if not _blank(row.get('call_script')) else 'MISSING'}"
              f"  draft={'yes' if not _blank(row.get('email_draft')) else 'MISSING'}")
    if not live or not targets:
        if not live:
            print("[dress] dry run — nothing generated. Re-run with --yes.")
        return

    sem = asyncio.Semaphore(concurrency)
    results = []
    ok = failed = 0

    async def one(row):
        nonlocal ok, failed
        async with sem:
            t0 = time.monotonic()
            entry = {"practice_id": row["id"], "name": row.get("name"),
                     "at": datetime.now(timezone.utc).isoformat()}
            try:
                await _dress(row, company_id)
                entry["outcome"] = "ok"
                ok += 1
                print(f"  OK   {row.get('name')}  ({time.monotonic() - t0:.0f}s)")
            except Exception as e:  # noqa: BLE001 — one failure must not kill the batch
                entry["outcome"] = f"failed:{type(e).__name__}: {e}"
                failed += 1
                print(f"  FAIL {row.get('name')} — {type(e).__name__}: {e}")
            results.append(entry)

    await asyncio.gather(*(one(r) for r in targets))

    log_path = f"docs/runbooks/dress-ready-{stamp}.log.json"
    with open(log_path, "w") as f:
        json.dump({"run_at": stamp, "since": since, "until": until,
                   "targets": len(targets), "ok": ok, "failed": failed,
                   "results": results}, f, indent=2)
    print(f"[dress] done: ok={ok} failed={failed}  log → {log_path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate missing analysis/playbook/email for ready fan-out leads.")
    ap.add_argument("--since", default="2026-08-18",
                    help="lead created_at cutoff (default: 2026-08-18)")
    ap.add_argument("--until", default=None,
                    help="upper lead created_at cutoff — freezes the pool")
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N practices")
    ap.add_argument("--concurrency", type=int, default=3,
                    help="practices generated at once (default: 3)")
    ap.add_argument("--yes", action="store_true",
                    help="generate live (default is a dry run)")
    args = ap.parse_args()

    company_id = settings.lead_company_id
    if not company_id:
        sys.exit("No company_id — set LEAD_COMPANY_ID.")
    asyncio.run(run(args.since, args.until, company_id, live=args.yes,
                    limit=args.limit, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
