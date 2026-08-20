# Multi-contact support — Clay per-contact webhook → `practice_contacts` → UI cards (2026-08-21)

**Status:** implemented on `feat/practice-contacts`. Adds
`supabase/migrations/2026-08-21-practice-contacts.sql` (files-only — the user
applies it by hand), `src/contacts.py`, a second accepted shape on
`POST /api/webhooks/clay`, contacts on the practice-detail endpoint, and a
`ContactCard` on the practice detail page. **Nothing that leaves the app
changes** — TalentDB push and CSV export are untouched.

## Why

Clay is switching from one callback per practice to **one callback per person**.
The same webhook, `POST /api/webhooks/clay`, now fires once per contact with:

```json
{"place_id": "...", "first_name": "...", "last_name": "...",
 "url": "https://linkedin.com/in/...", "work_email": "...",
 "personal_email": "...", "title": "..."}
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

**Out — deliberately:** anything that leaves the app. No changes to
`src/talentdb.py`, `scripts/talentdb_export.py`, or any push path; they keep
reading `owner_*`. CSV export likewise. See *Future work* for the fan-out that
is already decided but not built.

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

## Future work

**TalentDB fan-out — decided, deferred.** The user has already made the product
call: **one TalentDB lead per contact** ("3 people = 3 leads"), each carrying the
same practice and posting data with the person fields mapped per contact. It is
out of this change only because this phase touches nothing that leaves the app.

Sketch, for whoever picks it up:

- an optional `contact=` parameter threaded through `talentdb.build_fields` /
  `import_lead` (note the **duplicate `build_fields`** in
  `scripts/talentdb_export.py` — dedupe first or they drift);
- a `talentdb_contact_exports (lead_id, contact_id)` marker table, the
  per-contact analogue of `company_job_leads.talentdb_exported_at`;
- a legacy single-lead fallback when a practice has no contact rows.

Parked open questions on that work:

1. **`personal_email` fallback policy** — does a contact with only a personal
   email get pushed at all?
2. **LinkedIn field name on the receiver** — unconfirmed.
3. **Late-arriving contacts** — a person who shows up after the practice's other
   contacts were already exported.

**Also future:** a recipient picker in the email panel, so outreach can be aimed
at a chosen contact instead of the mirrored primary.
