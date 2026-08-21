"""End-to-end: one job posting → Places → qualify → analyze → playbook + email
→ enrich → Talent-DB, run serially for a single posting.

This stitches together the exact steps the app already performs, in the order it
performs them, so a single raw posting can be walked all the way to a Talent-DB
Lead without the collector / cron machinery. Nothing here is new behaviour — each
step calls the same module the API endpoint calls:

    1. Upsert the posting                → lead_store.upsert_postings        (job_postings)
    2. Qualify it (the one AI pass)       → lead_qualifier.qualify_batch      (OpenAI qualifier_model)
                                            → lead_store.write_verdicts        (company_job_leads verdict half)
    3. ONE Places Text Search + link      → Google Places (New) + storage.upsert_practices
                                            (mirrors scripts/poc_manual_fetch.py — a single billable call)
    4. Analyze the practice with AI       → analyzer.analyze_practice         (OpenAI openai_model)
                                            → storage.update_practice_analysis
    5. Playbook (call script)             → scriptgen.generate_script         (OpenAI openai_model)
       Email draft                        → email_gen.generate_email_draft    (OpenAI openai_model)
                                            → storage.update_practice_fields
    6. Trigger owner enrichment           → clay.trigger_enrichment           (Clay webhook, async write-back)
    7. Push the signed Lead(s)            → talentdb_push.push_lead_fanout    (one HMAC POST per contact)
                                            → talentdb.import_lead per person
                                            → lead_store.mark_lead_exported

Ordering note: the app generates the playbook + email AFTER the analysis because
both consume the analysis outputs (summary / pain_points / sales_angles), so this
script keeps analyze → playbook/email even though the request phrased them the
other way round. Clay writes owner_* back asynchronously via /api/webhooks/clay,
so on a first run the Lead is pushed with the contact fields NOT yet populated —
exactly as the live "Import Lead" button behaves on an un-enriched practice.

COST — every live run spends real money, which is why it defaults to a dry run:
    • Places   : 1 Text Search (New) call  (~4.0¢, SKU 120C-BEC3-B48F)
    • OpenAI   : 3 chat completions         (qualify + analyze + script + email = 4;
                 analyze also crawls the site and fetches reviews)
    • Clay     : 1 enrichment webhook POST
    • Talent-DB: 1 signed webhook POST
Nothing external is called under --dry-run. Live sends require --yes.

Usage:
    .venv/bin/python -m scripts.posting_to_talentdb --posting-file posting.json --dry-run
    .venv/bin/python -m scripts.posting_to_talentdb --posting-file posting.json --yes
    .venv/bin/python -m scripts.posting_to_talentdb --posting-file posting.json --yes --skip-enrich
    .venv/bin/python -m scripts.posting_to_talentdb --posting-file posting.json --yes --company-id <uuid>

The posting JSON is a single object shaped like a `job_postings` row. Minimum:

    {
      "source": "indeed",                 // indeed | linkedin  (half of the upsert key)
      "external_id": "abc123",            // the board's id      (other half)
      "title": "Front Office Coordinator",
      "employer_name": "Bright Smile Dental",
      "city": "Tampa",
      "state": "FL",
      "location_raw": "Tampa, FL",
      "description": "Full posting text ...",
      "url": "https://www.indeed.com/viewjob?jk=abc123",
      "posted_at": "2026-08-10",
      "board_remote_flag": false,
      "salary_min": 42000, "salary_max": 50000, "salary_interval": "yearly",
      "service_line_hint": "Medical Virtual Assistant",
      "search_term": "front office", "search_location": "Tampa, FL"
    }

Idempotent where the app is: the posting upserts on (source, external_id), the
practice on place_id, the verdict on (company_id, posting_id), and the Talent-DB
marker (`talentdb_exported_at`) stops a second run from re-sending. A posting the
receiver has already accepted for this company is skipped with
`already_exported`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from src import lead_qualifier, lead_store, talentdb, talentdb_push
from src.email_gen import generate_email_draft
from src.analyzer import analyze_practice
from src.job_boards import normalise_employer
from src.places import FIELD_MASK, _map_google_place
from src.practice_matcher import AUTO_SCORE, score
from src.scriptgen import generate_script
from src.settings import settings
from src.storage import (
    _get_client,
    _practice_id_by_place,
    get_practice,
    update_practice_analysis,
    update_practice_fields,
    upsert_practices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_posting(path: str, inline: str | None) -> dict:
    """Read the input posting from a JSON file or an inline --posting-json string."""
    if inline:
        posting = json.loads(inline)
    else:
        with open(path) as f:
            posting = json.load(f)
    if not isinstance(posting, dict):
        sys.exit("posting must be a single JSON object (a job_postings-shaped row).")
    for key in ("source", "external_id"):
        if not posting.get(key):
            sys.exit(f"posting is missing required key {key!r} "
                     f"(source + external_id are the upsert key).")
    if not posting.get("employer_name"):
        sys.exit("posting has no employer_name — nothing to search Places for.")
    return posting


def _resolve_posting_row(source: str, external_id: str) -> dict | None:
    """Re-fetch the persisted posting (with its DB id) after the upsert."""
    client = _get_client()
    if not client:
        return None
    row = (
        client.table("job_postings").select("*")
        .eq("source", source).eq("external_id", str(external_id))
        .maybe_single().execute()
    ).data
    return row or None


def _one_places_call(query: str) -> list[dict]:
    """Single Text Search (New) page — one billable call, no pagination.

    Deliberately mirrors scripts/poc_manual_fetch.py: no nextPageToken in the
    field mask guarantees exactly one page so the Google meter ticks +1 and the
    cost is unambiguous. This is the cost-controlled variant of
    places.search_places (which paginates up to 3 pages = 3 calls).
    """
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": FIELD_MASK,
        "Content-Type": "application/json",
    }
    body = {"textQuery": query, "maxResultCount": 5}  # still ONE billable call
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
    return resp.json().get("places", [])


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run(
    posting_input: dict,
    company_id: str,
    *,
    dry_run: bool,
    skip_qualify: bool,
    skip_enrich: bool,
    skip_webhook: bool,
) -> None:
    client = _get_client()
    if not client:
        sys.exit("No Supabase client — check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.")

    employer = posting_input.get("employer_name")
    city = posting_input.get("city")
    query = f"{employer} {city}".strip() if city else employer
    dest_host = urlparse(settings.talentdb_webhook_url or "").netloc or "(unset)"

    print(f"[flow] posting: source={posting_input['source']!r} "
          f"external_id={posting_input['external_id']!r} employer={employer!r} city={city!r}")
    print(f"[flow] company_id={company_id}")
    print(f"[flow] Talent-DB destination: {dest_host}")
    print(f"[flow] mode: {'DRY RUN (no external calls, no writes)' if dry_run else 'LIVE'}")

    if dry_run:
        print("\n[dry-run] Would perform, in order:")
        print(f"  1. upsert posting on (source, external_id) → job_postings")
        print(f"  2. {'SKIP qualify' if skip_qualify else 'qualify_batch([posting]) (OpenAI ' + settings.qualifier_model + ') → write_verdicts'}")
        print(f"  3. ONE Places Text Search: {query!r} (~4.0¢) → upsert_practices + link posting")
        print(f"  4. analyze_practice (OpenAI {settings.openai_model}) → update_practice_analysis")
        print(f"  5. generate_script + generate_email_draft (OpenAI {settings.openai_model}) → update_practice_fields")
        print(f"  6. {'SKIP enrich' if skip_enrich else 'trigger_enrichment → Clay webhook (async write-back)'}")
        if skip_webhook:
            print(f"  7. SKIP webhook (--skip-webhook) — lead kept in-system, NOT sent to Talent-DB")
        else:
            print(f"  7. talentdb_push.push_lead_fanout → {dest_host} "
                  f"(one signed POST per contact) → mark_lead_exported")
        print("\n[dry-run] No calls made. Re-run with --yes to execute live.")
        return

    # ---- Step 1: persist the posting, resolve its DB id ------------------
    written = lead_store.upsert_postings([posting_input])
    posting = _resolve_posting_row(posting_input["source"], posting_input["external_id"])
    if not posting:
        sys.exit("posting upsert did not resolve to a row — aborting.")
    posting_id = posting["id"]
    print(f"[1/7] posting upserted (rows={written}) → job_postings.id={posting_id}")

    # ---- Step 2: qualify (the one AI pass) → verdict half ----------------
    lead_row = None
    if skip_qualify:
        print("[2/7] qualify SKIPPED (--skip-qualify)")
    else:
        verdicts, stats = lead_qualifier.qualify_batch(
            [posting], company_id=company_id, user_id=None
        )
        lead_store.write_verdicts(company_id, verdicts)
        decision = verdicts[0]["decision"] if verdicts else "(none)"
        print(f"[2/7] qualified: decision={decision} "
              f"verdicts={stats['verdicts']} missing={stats['missing']}")
    # The (company, posting) lead row — carries provider_count / service_line
    # and the export dedup marker for the Talent-DB payload.
    lead_row = lead_store.find_lead_by_posting(company_id, posting_id)

    # Dedup exactly like the endpoints: a posting already accepted for this
    # company is not re-sent.
    if lead_row and lead_row.get("talentdb_exported_at"):
        print(f"[flow] already_exported at {lead_row['talentdb_exported_at']} — "
              f"nothing to send. Done.")
        return

    # ---- Step 3: ONE Places call + link the posting to a practice --------
    if not settings.google_maps_api_key:
        sys.exit("GOOGLE_MAPS_API_KEY not set — cannot fetch Places data.")
    print(f"[3/7] ONE Places Text Search: {query!r}")
    places = _one_places_call(query)
    # Record the spend even though this is operator-mode (0 credits), same as
    # the POC harness.
    try:
        from src.usage import record_places
        record_places(kind="places_search", calls=1, company_id=None,
                      metadata={"query": query, "results": len(places),
                                "flow": "posting_to_talentdb"})
    except Exception:
        pass
    if not places:
        sys.exit("Google returned 0 results — no practice to build. (1 call still spent.)")

    practice_model = _map_google_place(places[0])
    print(f"      top result: {practice_model.name!r} — {practice_model.address}")
    # Upsert into the shared universe, attributed to this tenant.
    upsert_practices([practice_model], touched_by=None, company_id=company_id)
    place_id = practice_model.place_id
    practice_pk = _practice_id_by_place(place_id)
    if not practice_pk:
        sys.exit(f"practice upsert ok but could not resolve id for place_id={place_id}.")

    # Link the posting → practice with a name-similarity confidence, the same
    # matcher the auto-linker uses.
    conf = round(score(normalise_employer(employer), normalise_employer(practice_model.name)), 2)
    status = "auto" if conf >= AUTO_SCORE else "review"
    client.table("job_postings").update({
        "practice_id": practice_pk,
        "match_confidence": conf,
        "match_status": status,
        "match_method": "posting_to_talentdb",
        "matched_at": _now(),
    }).eq("id", posting_id).execute()
    print(f"      linked practices.id={practice_pk} place_id={place_id} "
          f"(match conf={conf} → {status})")

    # ---- Step 4: analyze the practice with AI ----------------------------
    analysis = await analyze_practice(
        place_id=place_id,
        name=practice_model.name,
        website=practice_model.website,
        category=practice_model.category,
        city=practice_model.city,
        state=practice_model.state,
        rating=practice_model.rating,
        review_count=practice_model.review_count or 0,
        company_id=company_id,
        user_id=None,
    )
    update_practice_analysis(place_id, analysis, touched_by=None, company_id=company_id)
    print(f"[4/7] analyzed: tier={analysis.get('icp_tier')} "
          f"vertical={analysis.get('icp_vertical')} "
          f"urgency={analysis.get('urgency_score')} "
          f"hiring_signal={analysis.get('hiring_signal_score')}")

    # ---- Step 5: playbook (call script) + email draft --------------------
    script = await generate_script(
        name=practice_model.name,
        category=practice_model.category,
        summary=analysis.get("summary"),
        pain_points=analysis.get("pain_points"),
        sales_angles=analysis.get("sales_angles"),
        city=practice_model.city,
        state=practice_model.state,
        rating=practice_model.rating,
        review_count=practice_model.review_count,
        website_doctor_name=analysis.get("website_doctor_name"),
        company_id=company_id,
        user_id=None,
    )
    update_practice_fields(
        place_id, {"call_script": json.dumps(script)},
        touched_by=None, company_id=company_id,
    )

    draft = await generate_email_draft(
        name=practice_model.name,
        category=practice_model.category,
        summary=analysis.get("summary"),
        pain_points=analysis.get("pain_points"),
        sales_angles=analysis.get("sales_angles"),
        company_id=company_id,
        user_id=None,
    )
    update_practice_fields(
        place_id,
        {"email_draft": json.dumps(draft), "email_draft_updated_at": _now()},
        touched_by=None, company_id=company_id,
    )
    print(f"[5/7] playbook + email generated "
          f"(email subject: {draft.get('subject')!r})")

    # ---- Step 6: trigger owner enrichment (async write-back) -------------
    if skip_enrich:
        print("[6/7] enrichment SKIPPED (--skip-enrich)")
    else:
        from src.clay import trigger_enrichment
        from src.models import Practice
        try:
            result = await trigger_enrichment(Practice(**get_practice(place_id)))
            if result.get("skipped"):
                print(f"[6/7] enrichment skipped — {result.get('reason')}")
            else:
                update_practice_fields(
                    place_id, {"enrichment_status": "pending"},
                    touched_by=None, company_id=company_id,
                )
                print("[6/7] enrichment triggered → status=pending "
                      "(Clay writes owner_* back later via /api/webhooks/clay)")
        except Exception as e:  # noqa: BLE001 — enrichment must not block the push
            print(f"[6/7] enrichment FAILED (non-blocking) — {e}")

    # ---- Step 7: push the signed Lead to Talent-DB -----------------------
    if skip_webhook:
        print("[7/7] webhook SKIPPED (--skip-webhook) — everything kept in-system "
              "(practice, analysis, playbook, email, enrichment). "
              "talentdb_exported_at left NULL, so it can be sent later.")
        print("\n[flow] done (in-system only).")
        return
    if not talentdb.is_configured():
        sys.exit("Talent-DB webhook not configured "
                 "(TALENTDB_WEBHOOK_URL / TALENTDB_WEBHOOK_SECRET) — cannot push.")
    # Re-fetch the practice so the payload carries the freshly written analysis /
    # script / email fields, and re-fetch the lead row for provider_count.
    practice = get_practice(place_id)
    lead_row = lead_store.find_lead_by_posting(company_id, posting_id) or lead_row
    # Same call the button makes: one Talent-DB lead per reachable contact on
    # the practice (or the single legacy owner_* lead when there are none), and
    # it owns both markers — per-contact, plus the lead-level one once every
    # eligible contact was accepted.
    result = await talentdb_push.push_lead_fanout(practice, posting, lead_row,
                                                  company_id)
    if result.get("ok"):
        print(f"[7/7] Talent-DB accepted {result.get('sent')} lead(s) → "
              f"localEntityId={result.get('local_entity_id')} (marker set)")
    else:
        print(f"[7/7] Talent-DB rejected → status={result.get('status')} "
              f"message={result.get('message')} (marker NOT set — safe to retry)")

    print("\n[flow] done.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Walk one job posting all the way to a Talent-DB Lead "
                    "(Places → qualify → analyze → playbook/email → enrich → webhook)."
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--posting-file", help="path to a JSON file (a job_postings-shaped object)")
    src.add_argument("--posting-json", help="the posting as an inline JSON string")
    ap.add_argument("--company-id", default=None,
                    help="tenant to attribute to (default: settings.lead_company_id)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and make NO external calls or writes (default-safe)")
    ap.add_argument("--yes", action="store_true",
                    help="required to run live — confirms real Places/OpenAI/Clay/Talent-DB spend")
    ap.add_argument("--skip-qualify", action="store_true",
                    help="skip the qualifier AI pass (no verdict / provider_count)")
    ap.add_argument("--skip-enrich", action="store_true",
                    help="skip the Clay enrichment trigger")
    ap.add_argument("--skip-webhook", action="store_true",
                    help="run the full pipeline but DO NOT push to Talent-DB — keep "
                         "everything in-system (leaves talentdb_exported_at NULL)")
    args = ap.parse_args()

    company_id = args.company_id or settings.lead_company_id
    if not company_id:
        sys.exit("No company_id — pass --company-id or set LEAD_COMPANY_ID.")

    posting = _load_posting(args.posting_file, args.posting_json)

    dry_run = args.dry_run or not args.yes
    if not args.dry_run and not args.yes:
        print("[flow] REFUSING to run live without --yes. "
              "Showing the dry-run plan instead.\n")

    asyncio.run(run(
        posting, company_id,
        dry_run=dry_run,
        skip_qualify=args.skip_qualify,
        skip_enrich=args.skip_enrich,
        skip_webhook=args.skip_webhook,
    ))


if __name__ == "__main__":
    main()
