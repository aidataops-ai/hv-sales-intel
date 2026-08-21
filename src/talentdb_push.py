"""One lead in, N Talent-DB leads out — the per-contact fan-out.

`src/talentdb.py` sends exactly one POST per call. This module is the layer
above it that decides *how many* POSTs one of our leads is worth: a practice
with three contact rows becomes three Talent-DB leads carrying the same company,
posting, scoring and track data, differing only in the person (see
`talentdb._contact_person_fields`). Every caller that pushes a lead — the two
Import-Lead endpoints, the ad-hoc push scripts — goes through
`push_lead_fanout`, so "how many leads is this" is answered in one place.

**A practice with no contact rows keeps the old behaviour exactly**: one lead
built from the `practices.owner_*` mirror, guarded by `_postable_email`. So does
a practice whose contacts are all unreachable — a person with neither a personal
nor a work email cannot be mailed, is skipped, and if that empties the eligible
set the legacy single lead goes out instead of nothing.

## The two markers

The receiver mints a fresh record per POST (we send no `salesforceId`), so a
re-send is a duplicate lead on their side, not an update. Dedup is entirely
ours, and it takes two markers because a fan-out can half-succeed:

* `company_job_leads.talentdb_exported_at` — the **lead-level gate**, unchanged
  in meaning and still what the endpoints' `already_exported` early-return and
  the scripts' skip check read. Set here only when **every eligible contact was
  accepted**. Two of three succeeded → the marker stays NULL and the lead stays
  in the un-exported universe, which is what gets it retried.
* `talentdb_contact_exports (lead_id, contact_id)` — the **per-person record**
  inside that lead. The retry above skips the people already accepted, so it
  posts only the one that failed. Without it, "retry the lead" would mean
  "duplicate the two that worked".

`resend_contacts=True` re-enters a lead ignoring nothing but the contact
markers' skip — i.e. it re-posts people already sent. Leaving it False while
re-entering an already-marked lead (what the scripts' `--resend` does) is the
**late-arriving-contact** path: Clay finds a fourth person a week later, the
re-run posts that person and nobody else.

Contact markers are only consulted and written when there is a `lead` to key
them to. A practice pushed with no lead row (the practice-detail button on a
practice with no linked posting) fans out every time — there is nothing to
dedup on, exactly as with the lead-level marker today.
"""

from __future__ import annotations

import logging

from src import contacts, lead_store, talentdb

log = logging.getLogger("hvsi.talentdb_push")


def eligible_contacts(rows: list[dict] | None) -> list[dict]:
    """The contacts we can actually mail, in the order they arrived.

    A contact with neither a personal nor a work email (placeholders scrubbed)
    is dropped — the same rule as the legacy path's "no email → don't post",
    applied per person. `contacts.contact_email` is the truthiness test only;
    which address ends up in the `Email` field is `talentdb`'s business, and it
    is deliberately not this one.
    """
    return [c for c in (rows or []) if contacts.contact_email(c)]


def _summarize(results: list[dict], sent: int) -> dict:
    """Collapse N per-contact results into the single dict callers expect.

    Shaped for `api.index._talentdb_response`: `ok` if anything landed, and on a
    partial failure the first failure's status/message surfaces, so the button's
    warning names a real problem instead of averaging it away. `local_entity_id`
    is the first one the receiver returned — with N records there is no single
    id, and the endpoints use it only as a convenience link.
    """
    failures = [r for r in results if not r.get("ok")]
    first_failure = failures[0] if failures else {}
    entity_id = next((r.get("local_entity_id") for r in results
                      if r.get("local_entity_id") is not None), None)
    return {
        "ok": any(r.get("ok") for r in results),
        "status": first_failure.get("status") or "ok",
        "message": first_failure.get("message"),
        "local_entity_id": entity_id,
        "sent": sent,
        "results": results,
    }


async def push_lead_fanout(
    practice: dict | None,
    posting: dict | None,
    lead: dict | None,
    company_id: str | None,
    *,
    mark: bool = True,
    resend_contacts: bool = False,
) -> dict:
    """Push this lead once per eligible contact; return one combined result.

    Returns the `talentdb.import_lead` shape (`ok` / `status` / `message` /
    `local_entity_id`) plus `sent` (how many POSTs actually landed this call)
    and `results` (the per-contact results, in send order). A contact skipped
    because it was already exported counts as ok but is NOT counted in `sent`.

    `mark=False` writes neither marker — the dry-run / repeatable-testing knob
    the scripts expose as `--no-mark`.
    """
    rows = contacts.list_contacts_for_practice((practice or {}).get("id"))
    eligible = eligible_contacts(rows)

    # No reachable person → the legacy single lead from the owner_* mirror,
    # `_postable_email` guard and all. This is the untouched pre-fan-out path.
    if not eligible:
        result = await talentdb.import_lead(practice, posting, lead)
        if result.get("ok") and mark and lead:
            lead_store.mark_lead_exported(company_id, lead["id"])
        return {**result, "sent": 1 if result.get("ok") else 0,
                "results": [result]}

    lead_id = (lead or {}).get("id")
    already = set()
    if lead_id and not resend_contacts:
        already = contacts.list_exported_contact_ids(lead_id)

    log.info("[talentdb_push.fanout] practice=%s lead=%s contacts=%d "
             "eligible=%d already_sent=%d",
             (practice or {}).get("id"), lead_id, len(rows), len(eligible),
             len(already))

    results: list[dict] = []
    sent = 0
    all_ok = True
    for contact in eligible:
        contact_id = contact.get("id")
        if contact_id in already:
            results.append({"ok": True, "status": "already_exported",
                            "message": None, "local_entity_id": None,
                            "contact_id": contact_id})
            continue

        result = await talentdb.import_lead(practice, posting, lead,
                                            contact=contact)
        result = {**result, "contact_id": contact_id}
        results.append(result)
        if result.get("ok"):
            sent += 1
            if mark and lead_id and contact_id:
                contacts.mark_contact_exported(lead_id, contact_id)
        else:
            all_ok = False

    # The lead-level gate closes only on a clean sweep. A partial failure leaves
    # it open so the next run re-enters the lead — and the contact markers above
    # keep that re-entry from duplicating the people who already landed.
    if all_ok and mark and lead:
        lead_store.mark_lead_exported(company_id, lead["id"])

    return _summarize(results, sent)
