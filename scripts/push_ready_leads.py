"""Push the ready-to-push leads straight to Talent-DB with the new payload.

Leadset-free companion to `scripts/talentdb_export.py`: instead of a staged
JSON leadset, this selects the ready pool live —

    kept leads, not yet exported, created since --since (default 2026-08-18,
    the pipeline restart), practice attached and `enriched`

— and pushes each through `src.talentdb_push.push_lead_fanout`, i.e. the full
new payload: one Talent-DB lead per eligible contact (email AND direct phone,
per the 2026-08-22 phone gate), per-person FirstName/LastName/Title,
Email=personal, work_email, linkedin_url, Phone=contact direct,
alternate_phone=practice office line, `td_lead_id` echoed for pairs we hold an
id for, and the legacy single owner_* lead when a practice has no eligible
contacts. Markers (lead-level + per-contact + td_lead_id) are written by the
orchestrator on success.

Practices currently `pending` are skipped — their Clay re-enrichment is in
flight and their leads should go out with contacts, not before them (the
scheduled run_enrich_push applies the same hold). Billing-sweep postings are
skipped unless --allow-billing, mirroring talentdb_export.

Usage:
    .venv/bin/python -m scripts.push_ready_leads                # dry run
    .venv/bin/python -m scripts.push_ready_leads --limit 1 --yes    # canary
    .venv/bin/python -m scripts.push_ready_leads --yes          # full pool
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from src import talentdb_push
from src.settings import settings
from src.storage import _get_client
from src.talentdb import _postable_email
from src import contacts as contact_store

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


def _by_id(client, table: str, ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    ids = sorted(set(ids))
    for i in range(0, len(ids), _CHUNK):
        res = (client.table(table).select("*")
               .in_("id", ids[i:i + _CHUNK]).execute())
        for row in res.data or []:
            out[row["id"]] = row
    return out


def _is_billing(posting: dict | None) -> bool:
    return "billing" in ((posting or {}).get("search_term") or "").lower()


def fetch_pool(client, since: str) -> list[tuple[dict, dict, dict]]:
    """(lead, posting, practice) triples for the ready pool, lead-id order."""
    leads = _paged(
        client.table("company_job_leads").select("*")
        .eq("decision", "keep")
        .is_("talentdb_exported_at", "null")
        .gte("created_at", since)
    )
    postings = _by_id(client, "job_postings",
                      [l["posting_id"] for l in leads if l.get("posting_id")])
    practices = _by_id(client, "practices",
                       [p["practice_id"] for p in postings.values()
                        if p.get("practice_id")])

    pool = []
    for lead in leads:
        posting = postings.get(lead.get("posting_id"))
        practice = practices.get((posting or {}).get("practice_id"))
        if posting and practice:
            pool.append((lead, posting, practice))
    pool.sort(key=lambda t: t[0]["id"])  # stable: reproducible, resumable
    return pool


async def run(since: str, company_id: str, *, live: bool, limit: int | None,
              delay: float, mark: bool, allow_billing: bool) -> None:
    host = urlparse(settings.talentdb_webhook_url or "").netloc
    if live and not host:
        sys.exit("ABORT: TALENTDB_WEBHOOK_URL not configured.")
    client = _get_client()
    pool = fetch_pool(client, since)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"[push] {len(pool)} ready lead(s) since {since} → {host or '(dry)'}"
          f"{'' if live else '  (DRY RUN)'}")

    states: dict[str, int] = {"pending_practice": 0, "not_enriched": 0,
                              "billing": 0, "no_contact_or_email": 0}
    results = []
    pushed = posts_ok = posts_failed = 0
    for lead, posting, practice in pool:
        if limit is not None and pushed >= limit:
            break
        name = practice.get("name")
        status = practice.get("enrichment_status")
        if status == "pending":
            states["pending_practice"] += 1
            continue
        if status != "enriched":
            states["not_enriched"] += 1
            continue
        if _is_billing(posting) and not allow_billing:
            states["billing"] += 1
            continue

        rows = contact_store.list_contacts_for_practice(practice.get("id"))
        eligible = talentdb_push.eligible_contacts(rows)
        if not eligible and not _postable_email(practice):
            states["no_contact_or_email"] += 1
            continue

        n = len(eligible) or 1
        label = (f"{n} contact lead(s)" if eligible
                 else "1 legacy owner_* lead")
        if not live:
            pushed += 1
            print(f"  [{pushed}] DRY  lead={lead['id']} {name} → {label}")
            results.append({"lead_id": lead["id"], "practice": name,
                            "action": "dry-run", "would_send": n})
            continue

        result = await talentdb_push.push_lead_fanout(
            practice, posting, lead, company_id, mark=mark)
        pushed += 1
        posts_ok += result.get("sent", 0)
        n_failed = sum(1 for r in result.get("results", [])
                       if not r.get("ok") and not str(r.get("status", ""))
                       .startswith("skipped"))
        posts_failed += n_failed
        print(f"  [{pushed}] {'OK  ' if result.get('ok') else 'FAIL'} "
              f"lead={lead['id']} {name} → sent={result.get('sent')} "
              f"status={result.get('status')}")
        results.append({"lead_id": lead["id"], "practice": name,
                        "ok": result.get("ok"), "sent": result.get("sent"),
                        "status": result.get("status"),
                        "td_lead_ids": [r.get("td_lead_id")
                                        for r in result.get("results", [])]})
        time.sleep(delay)

    summary = {"run_at": stamp, "since": since, "destination": host,
               "company_id": company_id, "pool": len(pool),
               "leads_pushed": pushed, "posts_ok": posts_ok,
               "posts_failed": posts_failed, "marked": mark and live,
               "skipped": states, "results": results}
    if live:
        log_path = f"docs/runbooks/push-ready-{stamp}.log.json"
        with open(log_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"[push] log written → {log_path}")
    print(f"[push] done: leads={pushed} posts_ok={posts_ok} "
          f"posts_failed={posts_failed} skipped={states}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Push ready leads to Talent-DB via the per-contact fan-out.")
    ap.add_argument("--since", default="2026-08-18",
                    help="lead created_at cutoff (default: 2026-08-18)")
    ap.add_argument("--company-id", default=None,
                    help="tenant (default: settings.lead_company_id)")
    ap.add_argument("--limit", type=int, default=None,
                    help="push at most N LEADS (a lead may be several POSTs)")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="seconds between leads (default: 2)")
    ap.add_argument("--no-mark", action="store_true",
                    help="do not write export markers on success")
    ap.add_argument("--allow-billing", action="store_true",
                    help="include billing-sweep postings (skipped by default)")
    ap.add_argument("--yes", action="store_true",
                    help="send live (default is a dry run)")
    args = ap.parse_args()

    company_id = args.company_id or settings.lead_company_id
    if not company_id:
        sys.exit("No company_id — pass --company-id or set LEAD_COMPANY_ID.")
    asyncio.run(run(args.since, company_id, live=args.yes, limit=args.limit,
                    delay=args.delay, mark=not args.no_mark,
                    allow_billing=args.allow_billing))


if __name__ == "__main__":
    main()
