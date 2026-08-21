# Multi-contact support — Clay per-contact webhook → `practice_contacts` → UI cards (2026-08-21)

**Status:** implemented on `feat/practice-contacts`, in two phases.

*Phase 1 — ingestion.* `supabase/migrations/2026-08-21-practice-contacts.sql`
(files-only — the user applies it by hand), `src/contacts.py`, a second accepted
shape on `POST /api/webhooks/clay`, contacts on the practice-detail endpoint,
and a `ContactCard` on the practice detail page. Nothing that leaves the app
changed.

*Phase 2 — the TalentDB per-contact fan-out*, same branch, same day:
`supabase/migrations/2026-08-21-talentdb-contact-exports.sql`,
`src/talentdb_push.py`, an optional `contact=` through `src/talentdb.py` and
`scripts/talentdb_export.py`. See **TalentDB per-contact fan-out** below; the
CSV export and `scripts/backfill_talentdb.py` stay on the `owner_*` mirror.

## Why

Clay is switching from one callback per practice to **one callback per person**.
The same webhook, `POST /api/webhooks/clay`, now fires once per contact with:

```json
{"place_id": "...", "first_name": "...", "last_name": "...",
 "url": "https://linkedin.com/in/...", "work_email": "...",
 "personal_email": "...", "phone": "...", "title": "..."}
```

Today that webhook writes the flat `owner_name` / `owner_email` / `owner_phone`
/ `owner_title` / `owner_linkedin` columns on `practices` — a one-contact model
(`docs/specs/2026-04-24-clay-enrichment-design.md`). Under the new behaviour the
second callback for a practice overwrites the first, and the practice keeps
whichever person happened to arrive last. Everything downstream — TalentDB push,
the call-script playbook, CSV export, the leads list — reads those columns.

So the change has two halves: **store every person** (new table), and **keep the
old columns meaningful** (a mirror) so no consumer has to move yet.

## Scope

**In:**

1. The webhook accepts **both** payload shapes during (and after) the transition.
2. `practice_contacts` stores one row per person.
3. The practice-detail page grows a "Contact Info" section — one card per person.

**Out of phase 1, deliberately:** anything that leaves the app — `src/talentdb.py`,
`scripts/talentdb_export.py` and the CSV export all kept reading `owner_*`.
Phase 2 took the push paths (see **TalentDB per-contact fan-out**); the CSV
export and the backfill script stay on the mirror for good.

## Decisions

### 1. A shared `practice_contacts` table, not a JSON column

A contact Clay found is a **fact about the practice**, not about the tenant that
happened to pay for the enrichment — the same reasoning that makes `practices`
and `job_postings` shared, cross-tenant tables (`supabase/schema.sql:386-393`).
So: no `company_id`, FK to `practices(id)` with `on delete cascade`, and an
authenticated-read RLS policy as defence-in-depth only (the backend writes with
the service-role key).

A table rather than a JSON blob on `practices` because these rows get read one
person at a time, get deduped on arrival, and are the future join target for the
per-contact TalentDB lead.

**It coexists with `practices.website_contacts`.** That column is AI-extracted
from the practice's own website by `src/analyzer.py` and stored as JSON text.
Different source, different trust, different refresh cadence. This table does
not supersede it, backfill from it, or merge with it in this phase.

### 2. Identity: an app-computed `dedupe_key` + a plain unique constraint

`unique (practice_id, dedupe_key)`, where `dedupe_key` is computed in
`src/contacts.py::contact_dedupe_key` with the precedence:

**normalized LinkedIn URL → `work_email` → `personal_email` → normalized name.**

The obvious alternative — partial unique indexes on each identity column
(`unique (practice_id, lower(work_email)) where work_email is not null`, …) —
was rejected for a mechanical reason: the webhook upserts through **PostgREST,
whose `on_conflict` takes a column list and cannot arbitrate a partial index**.
There would be no usable conflict target. Computing the key in the app keeps the
whole precedence in one readable, unit-testable function and gives the upsert one
ordinary conflict target. The constraint also **doubles as the FK-side index** —
`practice_id` is its leading column, so "contacts for this practice" and the
cascade delete are both index scans and no extra index is created.

**Known limitation, accepted:** the same human arriving first with only a
LinkedIn URL and later with only an email produces two rows, because the two
payloads compute different keys. The case that actually matters — Clay re-sending
the *same* row — is stable, since the same payload always yields the same key.

### 3. The webhook accepts both shapes

One Pydantic model; the new fields are all optional; a `_is_new_shape` predicate
branches on their presence. The **old branch is byte-for-byte unchanged**, so a
legacy payload behaves exactly as it did before this change. A hybrid payload
carrying both vocabularies resolves to the new branch. Both shapes stay accepted
indefinitely — there is no cutover date and no flag to flip.

### 4. `owner_*` becomes a mirror of the primary contact

This is what keeps every untouched consumer working. After each contact upsert,
the mirror is recomputed **from the table**:

- **Primary** = first row by `(created_at, id)` that has a real `work_email`;
  else the first row, period.
- **Skip the mirror write** when the primary has no work email *and* the practice
  already has a real `owner_email`. Two failure modes this prevents: clobbering a
  real work email with a personal-only contact, and writing person A's name next
  to person B's email.
- **`owner_phone` is never touched** — Clay's per-contact payload has no phone
  field, and the existing value came from elsewhere.

### 5. Contacts on the detail endpoint only

`GET /api/practices/{place_id}` returns the contact rows. List endpoints keep
serving the existing `owner_*` snippet — fetching contacts for every row of a
list would be an N+1 for data the list does not render.

Frontend: a new `ContactCard` on the detail page; the existing `OwnerMiniCard`
stays exactly as it is on list cards.

### 6. Rollout order, and fail-soft helpers

The user applies the migration in the Supabase SQL editor **first**, then the
code deploys. The new contact helpers are fail-soft: if the table is missing
(mis-ordered deploy) the webhook degrades to the old mirror-only behaviour and
still returns success. A Clay callback must never 500 because of this feature.

Rollback is `drop table if exists practice_contacts;` — the `owner_*` mirror
survives it, so the pre-change one-contact behaviour is fully intact.

## TalentDB per-contact fan-out (implemented 2026-08-21)

The product call was already made — **one TalentDB lead per contact** ("3 people
= 3 leads") — and it is now built, on the same branch. A lead (practice +
posting) with N reachable contacts becomes N TalentDB leads: same company,
posting, scoring, track, everything. **Only the person differs.**

The fan-out lives in one place, `src/talentdb_push.py::push_lead_fanout`, which
every push path calls — both Import-Lead endpoints and the ad-hoc scripts.
`talentdb.import_lead` still sends exactly one POST; it just takes an optional
`contact=`.

### The mapping

`talentdb.build_fields(practice, posting, lead, contact=…)` overrides exactly
this block; every other key in the envelope is unchanged and identical across
the N leads.

| TalentDB field | Source | Note |
| --- | --- | --- |
| `FirstName` | `contact.first_name` | |
| `LastName` | `contact.last_name` | falls back to the company name, as the legacy path does — the receiver requires it |
| `Title` | `contact.title` | |
| `Email` | `contact.`**`personal_email`** | deliberate: the personal address is what the `Email` field carries |
| `work_email` | `contact.work_email` | **new key**, snake_case like the other custom fields |
| `linkedin_url` | `contact.linkedin_url` | **new key** |
| `Phone` | `contact.phone` | the person's direct line |
| `alternate_phone` | `practice.phone` | the office line; omitted when identical to `Phone` |

Both new keys are in `talentdb.CSV_COLUMNS` too (`work_email` after `Email`,
`linkedin_url` after `Website`, alongside `td_lead_id` after
`source_practice_id`), so an exported CSV still round-trips into a TalentDB CSV
import.

`practices.owner_phone` is **not** consulted on the contact path. It is a mirror
of *some* contact, and mixing it in would put person A's number on person B's
lead. Blank strings are trimmed to None and dropped by `_omit_missing`, so an
absent field is an absent key, never `""`.

### Eligibility, and the zero-contact fallback

A contact with **neither** a personal nor a work email (placeholders scrubbed by
`_scrub_email`) is skipped — the per-person form of the existing "no email →
don't post" rule. `contacts.contact_email` is the truthiness test only; which
address lands in `Email` is the table above, not that function.

A contact with **no direct phone** is also skipped (`skipped_no_phone`; user
decision 2026-08-22): a person the SDRs cannot call is not worth a Talent-DB
lead, and the practice office line does not count — that number belongs to the
practice, not the person.

If the eligible set is **empty** — no contact rows at all, or none reachable —
the push falls back to the **legacy single lead** from `practices.owner_*`,
guarded by `_postable_email` exactly as before. That path is byte-identical to
what shipped before this change, which answers parked question 1: a
personal-email-only contact *is* pushed, and a contact with no email at all is
not, but their absence never silently drops the lead.

### Dedupe: two markers, because a fan-out can half-succeed

The receiver mints a fresh record per POST (we send no `salesforceId`), so a
re-send is a duplicate, not an update. Dedup is entirely ours:

- **`company_job_leads.talentdb_exported_at`** — still THE lead-level gate, and
  still what the endpoints' `already_exported` early-return and the scripts'
  skip check read. It now means *fully sent*: set only when **every eligible
  contact was accepted**. Two of three landed → the marker stays NULL, so the
  lead stays in the un-exported universe and the next run picks it up.
- **`talentdb_contact_exports (lead_id, contact_id)`** — the per-person record
  inside that lead (`supabase/migrations/2026-08-21-talentdb-contact-exports.sql`).
  The retry above skips the people already accepted and posts only the one that
  failed. Without it, "retry the lead" would mean "duplicate the two that worked".

`--resend` (scripts) re-enters an already-marked lead but **still consults the
contact markers**, which makes it the answer to parked question 3: when Clay
finds a fourth person a week after the first three shipped, `--resend` posts
that person and nobody else. There is deliberately no knob that re-posts a
person already sent — the receiver has no upsert key, so it could only create
duplicates.

Contact markers are only read and written when there **is** a lead row to key
them to. A practice pushed with no linked posting fans out every time, exactly
as it is already exempt from the lead-level marker.

### `td_lead_id` — the upsert key we never had

`talentdb_contact_exports.td_lead_id` holds **TalentDB's own record id for that
(lead, contact) pair**, read off the webhook response and stored next to the
marker. Whenever the same pair is posted again the stored id goes out in the
payload as the `td_lead_id` field, so the receiver **updates that record instead
of minting a second one** — the closest thing to an upsert key on a contract
that has none. A pair we have never sent has no id, and the field is then
**omitted from the payload entirely** rather than sent empty.

Threaded through as an optional argument everywhere the person is:
`build_fields` / `build_envelope` / `import_lead` in `src/talentdb.py`, the
inline mirror in `scripts/talentdb_export.py`, and both directions of
`push_lead_fanout` (read the stored id in, store the response's id out via
`contacts.mark_contact_exported(..., td_lead_id=…)`). A marker write that
captured no id **leaves the column alone** rather than nulling it — losing a
stored id would silently turn the next re-post back into a duplicate.

**The source response field is TBD.** It is deliberately *not* `localEntityId`.
Extraction is therefore a single placeholder that currently returns None —
`src/talentdb.py::_td_lead_id_from_response`, mirrored in
`scripts/talentdb_export.py` — so today nothing is stored and nothing is echoed,
and naming the field is a one-line change in those two functions.

### Explicitly unchanged

- **The CSV export** (`GET /api/leads/export`, `api/index.py`) — one row per
  lead from the `owner_*` mirror. It gains the two new columns but not the
  fan-out; a CSV is a per-lead artifact and the mirror keeps it correct.
- **`scripts/backfill_talentdb.py`** — single-row, `owner_*`-based, untouched.
- **`practices.owner_*` itself** — still mirrored from the primary contact by
  the Clay webhook, still what the UI, the call script and the email panel read.

Parked open question that remains: the **LinkedIn field name on the receiver**
is still unconfirmed; we send `linkedin_url` and the receiver drops what it does
not recognize.

## Future work

**A recipient picker in the email panel**, so outreach can be aimed at a chosen
contact instead of the mirrored primary.
