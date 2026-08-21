"""Re-trigger Clay enrichment for the practices behind ready-to-push leads.

The multi-contact webhook (docs/specs/2026-08-21-practice-contacts.md) only
fills `practice_contacts` for practices enriched AFTER it shipped. Practices
already `enriched` under the old one-contact flow hold a single owner_* mirror
and zero contact rows, so pushing their leads now would send one person when
Clay can find several. This script re-runs Clay for exactly that pool:

    kept leads, not yet exported to Talent-DB, created since --since
    (default 2026-08-18, the pipeline restart), whose practice has NO
    practice_contacts rows yet.

Practices that already have contact rows are excluded by default — they went
through the new flow already; their leads import as-is (--include-contacted
overrides). Practices currently `pending` are always skipped (a trigger is
already in flight).

Each trigger flips the practice to `pending`, which also parks its leads out
of run_enrich_push's push phase until Clay's callbacks land (or the 6h
stale-pending fallback fires) — so running this BEFORE the next scheduled
push is what gets those leads sent with full contact data.

CLAY CREDITS: the observed 2026-08-21 batch yielded ~1.64 contacts per
contacted practice at ~10 credits per contact. The dry run prints the
estimate; batch with --limit and watch Clay's credit meter between batches.

MAKE SURE Clay's HTTP-API callback URL points at a server running the
multi-contact webhook (prod after the 2026-08-22 deploy) before --yes.

Usage:
    .venv/bin/python -m scripts.reenrich_ready_leads              # dry run
    .venv/bin/python -m scripts.reenrich_ready_leads --limit 5 --yes   # canary
    .venv/bin/python -m scripts.reenrich_ready_leads --yes        # full batch
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone

from src.clay import trigger_enrichment
from src.models import Practice
from src.storage import _get_client, update_practice_fields

# Columns Clay's outbound payload needs, plus id/status for selection.
_FIELDS = "id, place_id, name, website, city, state, phone, enrichment_status"

# Observed 2026-08-21: 200 contacts across 122 contacted practices.
_AVG_CONTACTS = 1.64
_CREDITS_PER_CONTACT = 10

_PAGE = 1000
_CHUNK = 500


def _paged(query_builder):
    """Drain a PostgREST query past the 1000-row cap."""
    rows: list[dict] = []
    page = 0
    while True:
        res = query_builder.range(page * _PAGE, page * _PAGE + _PAGE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
        page += 1


def fetch_targets(since: str, include_contacted: bool) -> list[dict]:
    """Practices behind kept, unexported, recent leads — contact-less first."""
    client = _get_client()

    leads = _paged(
        client.table("company_job_leads")
        .select("posting_id")
        .eq("decision", "keep")
        .is_("talentdb_exported_at", "null")
        .gte("created_at", since)
    )
    posting_ids = sorted({r["posting_id"] for r in leads if r.get("posting_id")})

    practice_ids: set[int] = set()
    for i in range(0, len(posting_ids), _CHUNK):
        res = (
            client.table("job_postings")
            .select("practice_id")
            .in_("id", posting_ids[i:i + _CHUNK])
            .not_.is_("practice_id", "null")
            .execute()
        )
        practice_ids.update(r["practice_id"] for r in res.data or []
                            if r.get("practice_id"))

    contacted: set[int] = set()
    id_list = sorted(practice_ids)
    if not include_contacted:
        for i in range(0, len(id_list), _CHUNK):
            res = (
                client.table("practice_contacts")
                .select("practice_id")
                .in_("practice_id", id_list[i:i + _CHUNK])
                .execute()
            )
            contacted.update(r["practice_id"] for r in res.data or [])

    targets: list[dict] = []
    for i in range(0, len(id_list), _CHUNK):
        res = (
            client.table("practices")
            .select(_FIELDS)
            .in_("id", id_list[i:i + _CHUNK])
            .execute()
        )
        for row in res.data or []:
            if row.get("enrichment_status") == "pending":
                continue  # trigger already in flight
            if row["id"] in contacted:
                continue
            targets.append(row)

    targets.sort(key=lambda r: r["id"])  # stable order: reproducible, resumable
    return targets


async def run(since: str, limit: int | None, delay: float, live: bool,
              include_contacted: bool) -> None:
    targets = fetch_targets(since, include_contacted)
    total = len(targets)
    if limit is not None:
        targets = targets[:limit]

    est = round(len(targets) * _AVG_CONTACTS * _CREDITS_PER_CONTACT)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = f"docs/runbooks/reenrich-ready-{stamp}.log.json"
    print(f"[reenrich] {total} contact-less ready-lead practice(s); "
          f"processing {len(targets)}{'' if live else ' (DRY RUN)'}")
    print(f"[reenrich] Clay credit estimate: ~{est} "
          f"({len(targets)} × {_AVG_CONTACTS} contacts × "
          f"{_CREDITS_PER_CONTACT} credits)")

    results = []
    triggered = failed = skipped = 0
    for idx, row in enumerate(targets, 1):
        place_id = row["place_id"]
        name = row.get("name")
        if not live:
            print(f"  [{idx}/{len(targets)}] DRY  {name}  ({place_id})")
            results.append({"place_id": place_id, "name": name, "action": "dry-run"})
            continue

        entry = {"place_id": place_id, "name": name,
                 "at": datetime.now(timezone.utc).isoformat()}
        try:
            practice = Practice(**{k: row.get(k) for k in
                                   ("place_id", "name", "website", "city",
                                    "state", "phone")})
            result = await trigger_enrichment(practice)
            if result.get("skipped"):
                entry["outcome"] = "skipped:" + str(result.get("reason"))
                skipped += 1
                print(f"  [{idx}/{len(targets)}] SKIP {name} — {result.get('reason')}")
            else:
                update_practice_fields(place_id, {"enrichment_status": "pending"})
                entry["outcome"] = "pending"
                triggered += 1
                print(f"  [{idx}/{len(targets)}] OK   {name} → pending")
        except Exception as e:  # noqa: BLE001 — record and continue the batch
            entry["outcome"] = f"failed:{e}"
            failed += 1
            print(f"  [{idx}/{len(targets)}] FAIL {name} — {e}")
        results.append(entry)
        if idx < len(targets):
            time.sleep(delay)

    if live:
        with open(log_path, "w") as f:
            json.dump({"run_at": stamp, "since": since, "targets_total": total,
                       "processed": len(targets), "triggered": triggered,
                       "failed": failed, "skipped": skipped,
                       "credit_estimate": est, "results": results}, f, indent=2)
        print(f"[reenrich] log written → {log_path}")
    print(f"[reenrich] done: triggered={triggered} failed={failed} skipped={skipped}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Re-trigger Clay for contact-less practices behind ready leads.")
    ap.add_argument("--since", default="2026-08-18",
                    help="lead created_at cutoff (default: 2026-08-18, the restart)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only trigger the first N practices (credit batching)")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="seconds between Clay POSTs (default: 2)")
    ap.add_argument("--include-contacted", action="store_true",
                    help="also re-trigger practices that already have contact rows")
    ap.add_argument("--yes", action="store_true",
                    help="send live (default is a dry run)")
    args = ap.parse_args()
    asyncio.run(run(args.since, args.limit, args.delay, args.yes,
                    args.include_contacted))


if __name__ == "__main__":
    main()
