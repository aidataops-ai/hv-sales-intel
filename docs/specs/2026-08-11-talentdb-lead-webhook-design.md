# Talent-DB Inbound Lead Webhook — "Import Lead" Design Spec

**Date:** 2026-08-11
**Status:** Implemented (2026-08-11). Backend `src/talentdb.py` + two endpoints, dedup
marker migration (applied), and Import Lead buttons on practice detail + signals detail.
Tests in `tests/test_talentdb.py` (21 passing). Envelope matches the received JSON sample.

## Decisions (2026-08-11)

The received JSON sample is the source of truth; where it conflicts with an earlier verbal
decision, **the JSON wins** and the earlier decision is marked overridden.

- **Payload shape:** a **practice + linked job-posting passthrough** — core fields aliased
  to SF names, the rest under our internal snake_case names, **native JSON types preserved**
  (numbers/bools/`null`/`[]`/`{}`); **keys we have no value for are omitted**.
- **Endpoint: `POST /api/sales-intel/webhook`** (app-origin). It mints its own record and
  dedups on its side, so the envelope is just `{objectType, operation, fields}` — **no
  `salesforceId`, `salesforceUpdatedAt`, or `eventId`**. (The earlier `/api/salesforce/webhook`
  path required a 15–18 char `salesforceId` + non-empty `salesforceUpdatedAt`; this endpoint
  does not — verified 200 against staging 2026-08-11.)
- **Export dedup:** additionally, one posting is exported once per company
  (`company_job_leads.talentdb_exported_at`), so the button won't re-send.
- **`Email` / name:** `Email = owner_email → email` (omit if neither). **`FirstName` /
  `LastName` come from `owner_name`** — last whitespace token → `LastName`, the rest →
  `FirstName`; a single token → `LastName` only. **`LastName` is required by the receiver**,
  so it **falls back to the company name** when there's no `owner_name`; `FirstName` (not
  required) is simply omitted then.
- **Field set = the sample's keys, minus any we can't fill.** Don't add fields outside the
  sample (`lead_score`, `pain_points`, `icp_vertical`, `owner_*`, `address`, `notes`, etc.),
  and **omit sample keys we have no value for** (e.g. `FirstName` when empty).
- **Source appears twice:** `Lead_Type__c` = the **slug** (`hv-sales-intel-{linkedin|indeed}`,
  `hv-sales-intel` when no posting); `posting_source` = the **raw DB value**
  (`indeed`/`linkedin`, `null` when no posting).
- **Button never disabled** — no field is required on the receiver; always enabled, errors
  surface as a non-blocking warning.
- **Omit missing values** (2026-08-11, reverses the earlier "never omit") — a key we have no
  value for is dropped, not sent as `""`/`null`. Only fields we actually have go out.
- **Status:** always `"New"`.
- **Button placement:** both `/signals` lead rows and the practice detail view.
- **Environment:** **staging first** (`stagingapp.healthandvirtuals.com`), via env var.

### Linked job posting resolution

`job_postings` links to a practice via `job_postings.practice_id`
(`supabase/migrations/2026-08-06-job-postings-practice-link.sql`); `source ∈ indeed | linkedin`
(`supabase/migrations/2026-08-05-job-posting-leads.sql`).

| Context | Posting resolution |
|---|---|
| Signals lead row | the row's own posting — always present |
| Practice detail | newest `job_postings` where `practice_id = practices.id`, else **no posting** |

No-posting case (practice detail, unlinked practice): the button still works —
`Lead_Type__c` = `"hv-sales-intel"` (generic slug fallback), `posting_source` and all
`posting_source` and other `posting_*` fields are **omitted**. Envelope ids omitted regardless.

## Goal

Add an **Import Lead** button on the practice detail view that ships the current
practice — mapped into a Salesforce-style `Lead` envelope — to the external
**Talent-DB** app via its authenticated inbound webhook. One click → one signed
JSON POST → the practice becomes (or updates) a Lead in Talent-DB.

This is a **push from us → Talent-DB**. We are the sender; Talent-DB owns the
receiving contract (documented in the integration-handoff artifact).

## Relationship to the existing Salesforce integration

We already push practices to Salesforce via `src/salesforce.py` (Apex REST,
`x-api-key` header, no signing, triggered as a side effect of logging a call).
**This is a different destination and a different contract:**

| | Existing (`src/salesforce.py`) | New (Talent-DB webhook) |
|---|---|---|
| Endpoint | Apex REST on `*.my.salesforce-sites.com` | `app.healthandvirtuals.com/api/salesforce/webhook` |
| Auth | `x-api-key` header | **HMAC-SHA256** over raw body → `X-HV-Signature: sha256=<hex>` |
| Body | flat SF Lead JSON | `{objectType, operation, salesforceId, salesforceUpdatedAt, fields{}}` envelope |
| Trigger | side effect of "log a call" | explicit **Import Lead** button |
| Upsert key | SF-returned `leadId` | `salesforceId` we supply (our upsert key) |

→ We build this as a **new, parallel module** (`src/talentdb.py`), not by
extending `salesforce.py`. The existing `_build_create_payload` (`src/salesforce.py:29`)
is a useful mapping reference only.

> **Open decision:** whether these two integrations eventually converge (one
> destination) is out of scope here. This spec keeps them independent so neither
> breaks the other. We store the Talent-DB linkage in **new** columns, not the
> existing `salesforce_lead_id` (which belongs to the Apex integration).

## The receiving contract (from the Talent-DB handoff artifact)

- `POST /api/salesforce/webhook`, `Content-Type: application/json`, one record per call.
- **Signature:** `X-HV-Signature: sha256=<hex HMAC-SHA256(rawBody, SECRET)>`.
  Sign the **exact bytes sent** — serialize once, sign that string, send that string.
- **Envelope:**
  ```json
  {
    "eventId": "<salesforceId>:<iso ts>",        // optional, ≤64 chars, dedup key
    "objectType": "Lead",
    "operation": "upsert",                        // upsert | delete
    "salesforceId": "<stable external id>",       // UPSERT KEY
    "salesforceUpdatedAt": "2026-08-11T09:15:00.000Z",  // ISO-8601, required
    "fields": { "Email": "...", "LastName": "...", "Company": "...", ... }
  }
  ```
- **Required `fields`:** `Email`, `LastName`, `Company`. Missing any → HTTP 200 with
  `{ok:false, status:"error"}` (check the body, not just the HTTP code).
- **Responses:** 200 with body `status` ∈ `ok | skipped | error`; 400 bad JSON/schema;
  401 bad signature; 413 >256 KB; 415 wrong content-type; 429 rate limit
  (120 req / 10 min per IP); 503 secret not configured.
- **Behaviors:** upsert by `salesforceId`; stale-event guard on `salesforceUpdatedAt`
  (older-or-equal → `skipped`); stage is forward-only; idempotent by `eventId`;
  `delete` is a soft-remove.
- Success body: `{ok:true, status:"ok", message, localEntityId:<int>, eventId}`.

## Our data model (source fields)

Practice model: `src/models.py`, per-tenant state in `company_practice_state`.
Relevant fields for mapping:

- Company/name: `name`
- Contact person (Clay enrichment): `owner_name` (full name, **no first/last split**),
  `owner_email`, `owner_phone`, `owner_title`, `owner_linkedin`
- Practice-level: `email`, `phone`, `website`, `address`, `city`, `state`
  (**no zip, no domain column, no provider count, no EHR field**)
- Scoring: `lead_score` (0–100 ICP), `icp_vertical`, `icp_tier`
- CRM stage: `status` ∈ `NEW, RESEARCHED, SCRIPT READY, CONTACTED, FOLLOW UP,
  MEETING SET, PROPOSAL, CLOSED WON, CLOSED LOST` (`api/index.py:146`)

## Field mapping — from the received JSON sample (2026-08-11, AUTHORITATIVE)

The exact JSON supersedes the earlier proposed mapping. It is a **practice + linked
job-posting record passthrough**: a handful of core fields aliased to Salesforce names
(`Company`, `LastName`, `Email`, `FirstName`, `Phone`, `Website`, `City`, `State`,
`Rating`, `Status`), then our own fields under their **internal snake_case names**.
**Native JSON types are preserved** — numbers, booleans, `null`, `[]`, `{}` — not
stringified. **Keys we have no value for are omitted** (not sent as `""`/`null`).
**★ = needs confirmation, §7.**

### Envelope

The app-origin webhook (`/api/sales-intel/webhook`) mints its own record and dedups on its
side, so we send only:

| Key | Value / source |
|---|---|
| `objectType` | const `"Lead"` |
| `operation` | const `"upsert"` |
| `fields` | the object below |

No `salesforceId` / `salesforceUpdatedAt` / `eventId`. Our own `talentdb_exported_at` marker
additionally prevents re-sending the same posting (see Export dedup).

### `fields` — core (Salesforce-aliased)

| Key | Type | Our source |
|---|---|---|
| `Company` | str | `name` (or posting `employer_name`) |
| `FirstName` | str | `owner_name` minus its last token; **omit if no `owner_name`** |
| `LastName` | str | last token of `owner_name`; **falls back to `Company`** when no `owner_name` (receiver requires it) |
| `Email` | str | `owner_email` → `email`; **omit if neither** |
| `Phone` | str | `owner_phone` → `phone`; **omit if neither** |
| `Website` | str | `website` |
| `City` | str | `city` |
| `State` | str | `state` |
| `Rating` | num | `rating` |
| `Status` | str | const `"New"` |
| `Lead_Type__c` | str | **slug** from the linked posting's `source`: `hv-sales-intel-linkedin` / `hv-sales-intel-indeed`; `hv-sales-intel` when no posting |
| `industry` | str | mapped from the posting's track (`service_line_hint`): Medical / Dental / Chiropractor / Home Health / Assisted Living / Legal / Spas; omitted when unmapped |
| `country` | str | const `"USA"` (ISO alpha-3, hardcoded for now) |

### `fields` — linked job posting (`job_postings` row where `practice_id = practices.id`)

| Key | Type | Our source (`job_postings`.) |
|---|---|---|
| `posting_source` | str? | **raw DB value** `source` (`indeed` / `linkedin`); `null` when no linked posting |
| `posting_url` | str | `url` |
| `role_title` | str | `title` |
| `posted_at` | ts | `posted_at` |
| `board_remote` | bool | `board_remote_flag` |
| `posting_description` | str | `description` |
| `search_term` | str | `search_term` |
| `search_location` | str | `search_location` |
| `first_seen_at` | ts | `first_seen_at` |
| `last_seen_at` | ts | `last_seen_at` |
| `source_practice_id` | str | `str(practices.id)` |
| `match_confidence` | num | `match_confidence` |
| `match_status` | str | `match_status` |
| `matched_at` | ts | `matched_at` |

### `fields` — practice scoring / meta / CRM

| Key | Type | Our source |
|---|---|---|
| `urgency_score` | num | `urgency_score` |
| `hiring_signal_score` | num | `hiring_signal_score` |
| `icp_tier` | str | `icp_tier` |
| `icp_breakdown` | obj | `icp_breakdown` (jsonb — **not on Pydantic model**) |
| `enrichment_status` | str? | `enrichment_status` |
| `lat` / `lng` | num | `lat` / `lng` |
| `opening_hours` | str | `opening_hours` |
| `category` | str | `category` |
| `review_count` | num | `review_count` |
| `organization_size` | num | `organization_size` (new `practices` column; omitted until enriched) |
| `call_script` | obj? | `call_script` (jsonb — **not on Pydantic model**) |
| `email_draft` | str? | `email_draft` |
| `email_draft_updated_at` | ts? | `email_draft_updated_at` |
| `tags` | arr | `tags` |
| `source_assigned_at` | ts? | `company_practice_state.assigned_at` (**not on Pydantic model**) |
| `source_assigned_by` | str? | `company_practice_state.assigned_by` (**not on Pydantic model**) |
| `last_touched_by` | str? | `last_touched_by` |
| `last_touched_at` | ts? | `last_touched_at` |
| `export_count` | num | `export_count` |
| `last_exported_at` | ts? | `last_exported_at` |
| `last_exported_by` | str? | `last_exported_by` |
| `salesforce_owner_id` | str? | `salesforce_owner_id` |
| `salesforce_owner_name` | str? | `salesforce_owner_name` |
| `salesforce_lead_url` | str? | `salesforce_lead_url` |
| `summary` | str? | `summary` |
| `sales_angles` | arr | `sales_angles` (**stored as JSON string — parse to array**) |
| `website_contacts` | arr? | `website_contacts` (**stored as JSON string — parse**) |

**JSON-string columns to parse before sending** (our DB stores these as strings, the
sample sends them as real JSON): `icp_breakdown`, `call_script`, `sales_angles`,
`website_contacts`. Send parsed values (or `null`/`[]`/`{}`), never the raw string.

**Overridden by this JSON** (earlier decisions no longer apply): envelope carries **no**
`salesforceId`/`eventId`/`salesforceUpdatedAt`; `LastName` = **company name**, not an
`owner_name` split; source appears twice — **`Lead_Type__c`** = slug and
**`posting_source`** = raw DB value; the rest of the SF-style enrichment set
(`Industry`, `Domain__c`, `Street`, `ICP_Fit_Score__c`,
`Verticals__c`, `LinkedIn_URL__c`, `Title`, `Description`) is **not** in the payload; no
`owner_*` contact fields are sent at all.

### `Status` — always `"New"`

Every import sends `Status: "New"` (matches the sample); Talent-DB owns the funnel after.

## Implementation plan (after sign-off)

**Backend**
1. `src/settings.py`: add `talentdb_webhook_url`, `talentdb_webhook_secret`.
2. `src/talentdb.py` (new):
   - `is_configured()`
   - **Richer fetch than the `Practice` model.** The payload needs `practices.id`,
     `icp_breakdown`, `call_script`, `company_practice_state.assigned_at/assigned_by`,
     and the linked `job_postings` row — none of which are on the Pydantic `Practice`.
     Either extend the read (`storage.get_practice_full`) or query them alongside.
   - `_resolve_posting(practice_id) -> dict | None` — newest `job_postings` row where
     `practice_id = practices.id`. (Signals path passes its posting directly.)
   - `_build_envelope(practice_row, posting) -> dict` — returns
     `{objectType, operation, fields}` (no ids/timestamp); the `fields` mapping above;
     **parse the JSON-string columns** (`icp_breakdown`, `call_script`, `sales_angles`,
     `website_contacts`) into real JSON; preserve native types; **omit any key with no
     value**; when no linked posting, `Lead_Type__c` = `"hv-sales-intel"` and all
     `posting_*` keys are omitted.
   - `_source_slug(source) -> str` — `Lead_Type__c` value: `hv-sales-intel-{source}` for
     `indeed`/`linkedin`, `hv-sales-intel` for none. (`posting_source` sends the raw source.)
   - `_sign(raw: bytes) -> str` — `hmac.new(secret, raw, sha256).hexdigest()`,
     prefixed `sha256=`. Serialize the envelope **once** to bytes, sign those bytes,
     POST those bytes (not a re-serialized dict).
   - `import_lead(...) -> dict` — POST via `httpx.AsyncClient(timeout=20)`,
     parse `{ok, status, localEntityId, message}`; treat `status:"error"` /
     `ok:false` as a soft warning, not a crash (fail-soft like `salesforce.py`).
3. **Export marker for dedup (one column).** Add `talentdb_exported_at timestamptz` to
   `company_job_leads` (per-(company, posting) — the right grain). We do **not** store any
   data Talent-DB returns; this is only our own "already sent" marker. Migration in
   `supabase/`. See "Export dedup" below.
4. Endpoints (`api/index.py`), auth `Depends(get_current_user)`, tenant-scoped
   `company_id`. Guard on the marker → build payload → POST → on success set
   `talentdb_exported_at` → return `{talentdb_status, talentdb_warning}` for a toast.
   Errors non-blocking (marker only set on a real success).
   - Signals: `POST /api/leads/{lead_id}/import` — the lead IS a `(company, posting)` row;
     guard/set its `talentdb_exported_at` directly.
   - Practice detail: `POST /api/practices/{place_id}/import-lead` — resolves the newest
     linked posting; guard/set the marker on that `(company, posting)` row. No linked
     posting → nothing to dedup, always allowed.

### Export dedup — one posting exported once per company

- **Grain:** `(company_id, posting_id)` via `company_job_leads.talentdb_exported_at`.
  A posting already exported for this company is not re-sent. (Per-company, matching the
  tenant model — a different company may still export the same shared posting. ★ confirm
  scope: per-company vs global-once.)
- **Backend guard:** if `talentdb_exported_at` is set, skip the POST and return
  `status:"already_exported"` (idempotent). Marker is set **only after** a successful send,
  so a failed attempt can be retried.
- **Frontend:** the `/signals` leads API exposes the flag; an exported row shows
  **"Exported ✓"** and its button is disabled. Practice detail button likewise reflects the
  newest posting's marker.
- **No-posting practices** (practice detail, unlinked): no posting to mark → not deduped,
  button stays active. (Rare; flag if you want practice-level dedup instead.)

**Frontend (both placements)**
5. `web/lib/api.ts`: `importLead(placeId)` and `importLeadFromPosting(leadId)` via `apiFetch`.
6. Practice detail **topbar** (`web/app/practice/[place_id]/page.tsx`): **Import Lead**
   button (teal, next to Status / ThemeToggle) — always enabled; spinner while sending,
   then **"Imported ✓"**; a `talentdb_warning` surfaces as an alert.
7. `/signals` lead row (`web/lib/leads.ts` view + its row component): **Import Lead**
   action using that lead's posting; renders **"Exported ✓"** (disabled) when
   `talentdb_exported_at` is set. The leads list API must return the flag.

**Tests**
8. `tests/` — envelope builder mapping, `Lead_Type__c` slug + raw `posting_source`, HMAC
   signature (known vector), JSON-string parsing, fail-soft on `{ok:false}`, no-posting
   nulls, **dedup guard** (second export returns `already_exported`, marker set only on
   success), endpoint 404/auth.

## Signals CSV export — same mapping

`GET /api/leads/export.csv` emits **the Talent-DB `fields` keys as columns** (canonical
order in `talentdb.CSV_COLUMNS`). The envelope-only ids (`salesforceId`,
`salesforceUpdatedAt`) are **not** columns — the CSV is the `fields` payload only. Each row
reuses the same `talentdb.build_fields(practice, posting)` builder as the webhook — one
source of truth.
The export bulk-fetches full practices (`storage.get_practices_by_place_ids`) so the
analysis/CRM columns are populated, and reconstructs the posting from the flattened export
lead (`_posting_from_lead`). Structured fields (`tags`, `sales_angles`, `icp_breakdown`,
`website_contacts`) are written as JSON strings; booleans as `true`/`false`.

## §7 Open Questions (remaining)

**Everything is resolved.** Payload = practice + linked-posting passthrough; field set from
the sample minus keys we can't fill (omit missing values); native types; envelope =
`{objectType, operation, fields}` (no ids/timestamp); `Email = owner_email → email` (omit if
neither); `Phone = owner_phone → phone`; `LastName = name`; `FirstName` omitted;
`Lead_Type__c` = slug, `posting_source` = raw DB value; no-posting → `posting_*` omitted;
`Status:"New"`; export dedup via `company_job_leads.talentdb_exported_at` (per company);
staging via env var. Scope confirm (per-company vs global) is the only ★ and defaults to
per-company.

**Secret handling:** `TALENTDB_WEBHOOK_SECRET` (+ `TALENTDB_WEBHOOK_URL`) live only in the
backend env. Never committed, never logged.
