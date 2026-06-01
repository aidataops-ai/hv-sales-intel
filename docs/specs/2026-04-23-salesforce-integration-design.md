# Salesforce Integration — Design Spec

**Date:** 2026-04-23 (revised 2026-04-27 after Apex REST switch)
**Status:** Implemented and shipped

> **2026-04-27 revision summary.** The original design proposed standard Salesforce REST (`sobjects/Lead`) authenticated via username-password OAuth. Apex's Salesforce admin team published a custom **Apex REST endpoint** instead (a `lead/webhook/` path on `*.my.salesforce-sites.com`), authenticated by a static `x-api-key` header. We pivoted the implementation to that endpoint. The user-facing behavior, data model, and sequence are unchanged; the auth module (`sf_auth.py`) is gone, the request shapes changed, and call notes are now stored verbatim instead of GPT-polished.

## Goal

When a rep clicks **Call** on a practice for the first time, create a Salesforce Lead through Apex's Apex REST endpoint. On every subsequent call, update the same Lead — pushing the full call history and an incrementing call count. The Salesforce Lead ID is stored on the practice so the team can see which lead is which without leaving the app. The rep's free-text notes (in the Notes tab on Call Prep) are also pushed to the same Lead's `Call_Notes__c` field, separately from the per-call timestamped log.

## Scope

### In scope
- **Apex REST** auth: POST/PUT to a single Apex endpoint with `x-api-key` header.
- One Salesforce **Lead** record per practice, identified by the `salesforce_lead_id` we get back from the create response.
- Two custom fields on Lead, both populated by us: `Call_Count__c` (numeric, sent as string), `Call_Notes__c` (long text).
- One mandatory custom picklist field: `Lead_Type__c` (always `"Outbound"`).
- Modal on Call click: rep types a note, clicks **Save & Call**, single action does note-log + SF sync + RingCentral dialer.
- Notes panel (Call Prep): typing in the textarea + saving pushes the text to `Call_Notes__c` on the existing SF Lead (overwrites — full text is the source of truth).
- New Supabase columns for SF linkage + call tracking.
- Endpoint `POST /api/practices/{place_id}/call/log` for the call modal.
- `PATCH /api/practices/{place_id}` (existing endpoint) extended: when `notes` changes AND practice has a `salesforce_lead_id`, push notes into SF.
- Fail-soft SF sync: local save always succeeds; SF failures surface as a non-blocking warning.
- Mock mode: feature works without SF credentials (local log only, no SF calls).
- Owner contact fields (`OwnerName`, `OwnerPhone`, `OwnerEmail`) are sent on create using whatever the practice has — typically populated by the Clay enrichment step that runs before the first call.

### Out of scope (future)
- Reading data back from Salesforce (e.g., showing the SF Owner's name in our UI). The Apex endpoint returns only `{success, message, leadId}`.
- Two-way sync: SF → app. Status, owner, and stage changes inside SF don't flow back.
- Per-call audit trail (we keep only the appended `Call_Notes__c` text).
- Standard SF REST `sobjects/Lead/` (replaced by Apex REST).
- OAuth Bearer token rotation (Apex REST takes a static API key in `x-api-key`).
- Editing Lead Status from our app after create (we set it on first create only).
- Retry queue for failed SF calls.

## Architecture

```
[Rep clicks Call on a practice card]
       │
       ▼
[CallLogModal (textarea + Save&Call)]
       │  POST /api/practices/{id}/call/log {note}
       ▼
[call_log.append_call_note]
       │  1. polished = raw note, verbatim (no GPT — see decision log)
       │  2. append "[ts UTC] {rep}: {polished}" to practices.call_notes
       │  3. increment practices.call_count
       │  4. salesforce.sync_practice(practice, line)
       ▼
[salesforce.sync_practice]
       │  if sf_lead_id null → create_lead   (POST → leadId returned)
       │  else              → update_lead   (PUT → success returned)
       │  fail-soft: any exception bubbles up to call_log → warning
       ▼
[Frontend: close modal, openRingCentralCall(practice.phone)]


[Rep edits Lead Notes textarea on Call Prep]
       │
       ▼
[NotesPanel onBlur / Save]
       │  PATCH /api/practices/{id} {notes}
       ▼
[patch_practice]
       │  1. update practices.notes locally (always)
       │  2. if practice.salesforce_lead_id → salesforce.update_lead(id, call_count, notes)
       │     (pushes the full notes text into Call_Notes__c, overwriting)
```

Two backend modules, two FastAPI endpoints (`/call/log` new, `/{id}` PATCH extended), one modal + Notes panel on the frontend. The Clay enrichment step runs **before** the first call and populates `owner_*` fields, which are then forwarded to SF on Lead create.

## Data model

### Supabase: `practices` table

```sql
alter table practices
  add column salesforce_lead_id     text,
  add column salesforce_owner_name  text,    -- echo of OwnerName we sent on create
  add column salesforce_synced_at   timestamptz,
  add column call_count             integer not null default 0,
  add column call_notes             text;

create index idx_practices_sf_lead_id on practices(salesforce_lead_id);
```

Note vs. original spec: `salesforce_owner_id` was specified but never stored — the Apex endpoint doesn't return it. The model still has the field (kept for backwards compatibility) but the call-log path doesn't write to it.

### Salesforce custom fields (Apex's SF admin owns)

| API name           | Type                          | Purpose                                                |
| ------------------ | ----------------------------- | ------------------------------------------------------ |
| `Call_Count__c`    | Number / numeric (sent as string) | Running count of calls from this app.              |
| `Call_Notes__c`    | Long Text Area (32,768)       | Full appended call log; also overwritten by Notes tab. |
| `Lead_Type__c`     | Picklist                       | Required. We always send `"Outbound"`.                 |

Standard fields populated on create: `Company`, `OwnerName`, `OwnerPhone`, `OwnerEmail`, `Email`, `Website`, `Street`, `City`, `State`, `PostalCode` (empty), `Country` (`"USA"`), `Industry` (`"Healthcare"`), `LeadSource` (`"HV Sales Intel"`), `Status` (`"Working - Contacted"`), `Description` (built from analysis scores).

### Append format for `Call_Notes__c` (per-call log)

```
[2026-04-23 10:22 UTC] Sarah Khan: left vm, gonna retry thu around 2
[2026-04-24 14:05 UTC] Sarah Khan: spoke with office manager. interested in demo.
```

- Timestamp: UTC, format `YYYY-MM-DD HH:MM UTC`.
- Rep name: from `profiles.name` (already stamped on `last_touched_by_name`).
- Note text: **the rep's exact words, verbatim.** No AI rewriting (see decision log).
- Empty notes: stamped as `(call logged, no note)`.
- Order: chronological, newline-separated. The full string is rewritten on every PUT.

### Notes panel content

Free-form text. Whatever the rep types, full string, no formatting. PATCH handler pushes it as-is into `Call_Notes__c`, overwriting the per-call log. A consequence: if a rep edits the Notes tab after a call, the timestamped per-call log on Salesforce gets replaced. This was the explicit choice — Notes tab and Call log tab share the same SF field, and the rep can see (in Call log tab) what's been pushed.

## Auth: Apex REST with `x-api-key`

### One-time setup (Apex SF admin owns)
1. Build an Apex REST class with `@HttpPost`, `@HttpPut` handlers under a path like `/services/apexrest/hv-sales-intel/lead/`.
2. Expose it via a Salesforce Site so it's reachable at `https://*.my.salesforce-sites.com/...`.
3. Define the `x-api-key` shared secret. Apex validates it on every call. We store it in our `.env`.
4. The Apex handler creates/updates the standard Lead object, sets `Lead_Type__c = "Outbound"` (or whatever we pass), validates required fields, returns `{success, message, leadId}`.

### Runtime flow
- Every request: `x-api-key: <SF_API_KEY>` + `Content-Type: application/json`.
- Create: `POST {SF_APEX_URL}` → `200 {success: true, message: "...", leadId: "00Q..."}`.
- Update: `PUT {SF_APEX_URL}` body has `Id` field → `200 {success: true, message: "...", leadId: "00Q..."}`.
- Description-only update: `PUT {SF_APEX_URL}` body has `Id` + `Description` → same response.
- Errors return non-2xx; `httpx.raise_for_status()` raises and our caller flips status to `failed` + surfaces the warning.

No OAuth, no token caching, no refresh, no `instance_url`. The endpoint URL itself encodes the org + path; the API key is the credential.

### Mock mode
If `SF_APEX_URL` or `SF_API_KEY` is empty, `salesforce.is_configured()` returns `False`, `sync_practice` returns `{"skipped": True, "reason": "sf_not_configured"}` without raising. The rest of the call log flow still runs.

## Backend modules

### `src/salesforce.py`
Functions exported:

```python
def is_configured() -> bool
def _build_create_payload(practice: Practice, call_note_line: str) -> dict
def _build_update_payload(sf_lead_id: str, call_count: int, call_notes: str) -> dict
async def create_lead(practice: Practice, call_note_line: str) -> dict
async def update_lead(sf_lead_id: str, call_count: int, call_notes: str) -> dict
async def update_lead_description(sf_lead_id: str, description: str) -> dict
async def sync_practice(practice: Practice, polished_line: str) -> dict
```

`sync_practice` returns either:
- `{"sf_lead_id": "00Q...", "sf_owner_name": "Office Manager", "synced_at": "..."}`
- `{"skipped": True, "reason": "sf_not_configured"}`

Note: `sf_owner_id` is **not** returned by the Apex endpoint, so we don't store it. `sf_owner_name` is just an echo of what we sent in `OwnerName`, not a value Salesforce computes.

The module logs every request and response (with body truncated to 500 chars) under the `hvsi.salesforce` logger — see [Vercel deployment](#) spec for log capture details.

### `src/call_log.py`
Functions exported:

```python
async def polish_note(raw_note: str) -> str
async def append_call_note(place_id: str, raw_note: str, user: dict) -> tuple[dict, str | None]
```

`polish_note` — **does not call GPT**. Returns the trimmed raw note, or `"(call logged, no note)"` for blank input. The original design called for GPT polishing; reps explicitly asked to keep their words verbatim (see decision log). The function name and signature are kept so call sites don't need to change if we ever revisit.

`append_call_note` orchestrates: load practice → build line → append to `call_notes` → increment `call_count` → call `salesforce.sync_practice` → persist via `update_practice_fields`. Local save always wins; SF failures surface as a warning string.

Logged at every step under `hvsi.call_log`.

### `src/sf_auth.py` (REMOVED)
The original design had this module for username-password OAuth token fetch + cache. The Apex REST switch made it unnecessary — there's no token to fetch. The module and its tests (`tests/test_sf_auth.py`) were deleted.

## API surface

### `POST /api/practices/{place_id}/call/log`

**Auth:** `get_current_user`.

**Request:** `{ "note": "..." }` (empty allowed).

**Response (always 200, fail-soft):**
```json
{
  "practice": { /* full Practice with refreshed call_count, call_notes, salesforce_* */ },
  "sf_warning": null
}
```

On SF failure: `sf_warning: "Salesforce sync failed: ...". Local log saved.` — local DB writes still succeeded.

**Errors:**
- 401 — not signed in.
- 404 — place_id not found.
- 500 — local storage write itself failed.

### `PATCH /api/practices/{place_id}` (extended)
The existing PATCH endpoint accepts `status`, `notes`, `email`. New behavior: if `notes` is being changed AND `practice.salesforce_lead_id` is set AND SF is configured, the backend calls `salesforce.update_lead(lead_id, call_count, notes)` after the local update. The full notes string overwrites `Call_Notes__c`.

If the SF call fails, the response includes `sf_warning: "Salesforce notes sync failed: ..."` alongside the updated practice. Local update is never rolled back.

## Salesforce request bodies

### CREATE — `POST {SF_APEX_URL}`

```json
{
  "Company": "Houston Family Dental",
  "OwnerName": "Office Manager",
  "OwnerPhone": "+17135551234",
  "OwnerEmail": "manager@hfd.com",
  "Email": "hello@hfd.com",
  "Website": "https://hfd.com",
  "Street": "1234 Main St",
  "City": "Houston",
  "State": "TX",
  "PostalCode": "",
  "Country": "USA",
  "Industry": "Healthcare",
  "LeadSource": "HV Sales Intel",
  "Status": "Working - Contacted",
  "Lead_Type__c": "Outbound",
  "Description": "Lead Score: 82 | Urgency: 70 | Hiring Signal: 60",
  "Call_Count__c": "1",
  "Call_Notes__c": "[2026-04-23 10:22 UTC] Sarah Khan: Initial outreach call"
}
```

**Response:**
```json
{ "success": true, "message": "Lead created successfully", "leadId": "00Q5f00000ABCDEFG" }
```

Field-mapping rules:

| Lead field          | Source                                                                       | Notes                                                              |
| ------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `Company`           | `practice.name`                                                               | Required by SF.                                                    |
| `OwnerName`         | `practice.owner_name`                                                         | Empty string if Clay didn't enrich. (Apex tolerates empty.)        |
| `OwnerPhone`        | `practice.owner_phone` → fallback to `practice.phone`                         | Owner's mobile preferred, business phone fallback.                 |
| `OwnerEmail`        | `practice.owner_email`                                                        | Empty string if not enriched.                                      |
| `Email`             | `practice.email`                                                              | Business email (different from `OwnerEmail`).                      |
| `Website`           | `practice.website`                                                            |                                                                    |
| `Street`            | `practice.address`                                                            | Full address string; we don't split components.                    |
| `City` / `State`    | `practice.city` / `practice.state`                                            |                                                                    |
| `PostalCode`        | `""`                                                                          | We don't have it.                                                  |
| `Country`           | `"USA"`                                                                       | Literal — all leads are US-based.                                  |
| `Industry`          | `"Healthcare"`                                                                | Literal.                                                           |
| `LeadSource`        | `"HV Sales Intel"`                                                            | Literal — identifies source in SF reports.                         |
| `Status`            | `"Working - Contacted"`                                                       | Literal — rep just dialed.                                         |
| `Lead_Type__c`      | `"Outbound"`                                                                  | Literal. **Required by Apex.**                                     |
| `Description`       | `"Lead Score: X \| Urgency: Y \| Hiring Signal: Z"`                            | Built from analysis scores. Empty string if all three are null.    |
| `Call_Count__c`     | `str(practice.call_count or 1)`                                               | Apex expects string-typed numeric.                                 |
| `Call_Notes__c`     | The single formatted line for this call.                                      | First line of the chronological log.                               |

All keys are sent (no omission of nulls). Apex tolerates empty strings on optional fields.

### UPDATE (per-call log) — `PUT {SF_APEX_URL}`

```json
{
  "Id": "00Q5f00000ABCDEFG",
  "Status": "Working - Contacted",
  "Lead_Type__c": "Outbound",
  "Call_Count__c": "3",
  "Call_Notes__c": "[ts1] ...\n[ts2] ...\n[ts3] ..."
}
```

Only Status, Lead_Type__c, Call_Count__c, Call_Notes__c are updated on subsequent calls. Status and Lead_Type__c are restated to satisfy Apex's required-field validation; the practical effect is the same as not touching them (rep can change Status in SF, our updates won't roll it back unless the same value matches).

### UPDATE (Description only) — `PUT {SF_APEX_URL}`

When the Notes tab content changes via PATCH, only Description-relevant fields are sent:

```json
{
  "Id": "00Q5f00000ABCDEFG",
  "Status": "Working - Contacted",
  "Lead_Type__c": "Outbound",
  "Description": "Free-form notes from rep..."
}
```

(Confusingly, the post-merge implementation routes Notes tab updates through `update_lead` rather than `update_lead_description` — so they actually go to `Call_Notes__c`, not `Description`. The `update_lead_description` helper exists for future use. See decision log for the rationale.)

## Frontend

### `web/components/call-log-modal.tsx`
Unchanged from original spec. Modal with a textarea, **Save & Call** writes via `logCall()` then opens RingCentral.

### `web/components/notes-panel.tsx`
Updated copy: subtitle now reads *"Saved to the Salesforce Lead's `Call_Notes__c` field."* — reps know that whatever they type here is what shows up in the Lead record on SF.

### `web/components/practice-card.tsx`
Adds the "Last call" attribution strip (call count + last synced + owner name from SF) — unchanged from original spec.

### `web/components/call-button.tsx`
Wraps the existing dialer with the `CallLogModal` — unchanged from original spec.

### `web/app/practice/[place_id]/page.tsx`
Activity tab repurposed as Call log tab — unchanged from original spec. Same-origin handling added for Vercel production builds (see deployment spec).

## Env vars

`.env`:

```
SF_APEX_URL=https://apexvirtuals.my.salesforce-sites.com/.../services/apexrest/hv-sales-intel/lead/
SF_API_KEY=<the static x-api-key value>
```

The legacy `SF_CLIENT_ID`, `SF_CLIENT_SECRET`, `SF_USERNAME`, `SF_PASSWORD`, `SF_SECURITY_TOKEN`, `SF_LOGIN_URL`, `SF_API_VERSION` settings still exist in `Settings` for backwards compatibility with existing `.env` files but are not read anywhere. Safe to delete from `.env`.

## Error handling & edge cases

| Situation                                | Behavior                                                                                                                |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `SF_APEX_URL` or `SF_API_KEY` missing    | `sync_practice` returns `{skipped: True, reason: "sf_not_configured"}`. Endpoint returns 200 + `sf_warning: null`. Local save persisted. |
| Apex returns non-2xx                     | `httpx.raise_for_status` raises. `call_log` catches, sets `sf_warning: "Salesforce sync failed: ..."`. Local save persisted. |
| Apex returns 200 but `success: false`    | We treat as success (httpx doesn't see it as error). The `message` field is logged but not surfaced to the rep. *Future:* parse `success` and treat false as failure. |
| Apex returns response without `leadId`   | `sync_practice` raises `RuntimeError`. Caller catches, surfaces warning. Practice keeps `salesforce_lead_id = null`, next call retries as create. |
| Network timeout (>20s)                   | `httpx.HTTPError` propagates. Same fail-soft path.                                                                      |
| Notes panel save when `salesforce_lead_id` is null | PATCH succeeds locally; SF call is skipped (no lead exists yet). Rep needs to log a Call first to seed the SF Lead. |
| Notes panel save when SF is unconfigured | PATCH succeeds locally; SF block is skipped silently.                                                                  |
| Notes panel save while a Call is in flight | Race window: both writes target Call_Notes__c. Last write wins. Acceptable for v1; reps don't typically multi-edit.   |
| `Call_Count__c` drift between local and SF | Self-healing — every PUT sends the full local count.                                                                 |
| Apex requires `Lead_Type__c` and we forget | 400 on every call. Tests guard the payload shape.                                                                     |

## Testing

`tests/test_salesforce.py` (10 tests):
- `is_configured` true/false combinations
- `_build_create_payload` includes all required fields
- `_build_create_payload` falls back `OwnerPhone` to practice phone
- `_build_create_payload` handles missing optionals (returns empty strings, not nulls)
- `_build_update_payload` shape
- `create_lead` posts with `x-api-key` header to the right URL
- `update_lead` puts with the right body
- `sync_practice` skips when not configured
- `sync_practice` create branch when `lead_id` is null
- `sync_practice` update branch when `lead_id` exists

`tests/test_call_log.py` (7 tests):
- `polish_note` empty marker for blank input
- **`polish_note` returns raw text verbatim** (renamed from "uses GPT")
- **`polish_note` strips surrounding whitespace** (new)
- `append_call_note` increments count + formats line
- `append_call_note` sets SF fields on success
- `append_call_note` surfaces warning on failure
- `append_call_note` raises `LookupError` when practice missing

`tests/test_api_call_log.py`: 5 tests for the endpoint (auth, happy path, warning, 404, empty note).

`scripts/sf_live_smoke.py`: live integration test. Hits the real Apex endpoint with creds from `.env`. Creates 2 leads, updates both, prints responses. Run with `python scripts/sf_live_smoke.py`. Useful for verifying SF-side changes haven't broken the contract.

## Decision log (post-implementation)

1. **Apex REST instead of standard REST** — H&G's SF admin team already had an Apex endpoint with bespoke validation rules; using it lets the SF side own the schema instead of us. Side benefit: simpler auth (one static key) vs OAuth refresh dance.

2. **No GPT polishing of call notes** — early test runs polished "left vm, gonna retry thu" into "Left voicemail. Will retry Thursday." Reps preferred their own shorthand because (a) it was faster to skim across a long Lead's history, (b) GPT occasionally introduced facts that weren't in the raw note. The `polish_note` function still exists but returns the raw text trimmed.

3. **Notes panel writes to `Call_Notes__c`, not `Description`** — initial implementation routed PATCH-notes to `update_lead_description`. Switched after the SF admin pointed out that SF reports + dashboards filter on `Call_Notes__c`, not Description, and reps wanted notes to count toward "lead activity." The `update_lead_description` helper is left in place for future use.

4. **`Lead_Type__c` always `"Outbound"`** — Apex side requires it as a non-null picklist. We have no inbound leads from this app, so it's always Outbound. Future: if the app ever ingests web-form leads, switch on source.

5. **Owner ID not stored** — Apex doesn't return it. We could query SF separately to get OwnerId, but the OwnerName echo is sufficient for the UI's "owner: X (SF)" attribution strip.

6. **`sf_auth.py` removed entirely** — no token to cache.

7. **Same Lead used for Notes panel + per-call log** — `Call_Notes__c` is the canonical "what's happened with this lead" field. Reps can edit Notes and re-log Calls; both write to the same field; the most recent write wins. The Call log tab shows exactly what's in `practices.call_notes` so the rep can see what's been pushed to SF.

## Success criteria

- First call on a fresh practice creates a Lead via the Apex endpoint; `salesforce_lead_id` populated.
- Second call PUTs the same Lead; `Call_Count__c` goes from 1 to 2; `Call_Notes__c` has both timestamped entries.
- Rep's notes appear verbatim in `Call_Notes__c`.
- Notes panel typing → save → text appears in `Call_Notes__c` on SF (overwriting the per-call log if any).
- With SF creds missing, modal still works end-to-end (local log + dialer); no errors shown to rep.
- With SF creds present but wrong, call still logs locally; rep sees a discreet warning in the console; next corrected attempt self-heals (full call_count + call_notes string sent).
- `pytest -q` passes (>= 80 tests). `npx tsc --noEmit` clean.
- `scripts/sf_live_smoke.py` exits 0 against a real SF org.
