# Instant Signals — Config Page (Editable Search Targets)

**Date:** 2026-08-10
**Status:** Draft — implementation in progress on `feat/instant-signals-config`
**Builds on:** `2026-08-05-hiring-signal-collector-design.md`, `...-adr.md` (ADR-03)

---

## Goal

Give an admin a screen inside Instant Signals to see and change **what the collector
searches for** — its states, cities, tracks, and keywords — without editing a checked-in
JSON file and waiting for a deploy.

Today the search matrix lives in `config/leads/*.json` and is seeded into the per-tenant
`company_search_targets` table (ADR-03). Editing it means a code change, a review, a
deploy, and a re-seed. The collector already reads **only the table** at run time, so the
table is the right and safe place to edit for day-to-day tuning. This feature exposes that
table through the UI.

**What ships:**

- A new page at `/signals/config` (admin only).
- A "Configure" button on the Instant Signals page that links to it.
- Read + write API over `company_search_targets`: list targets, add targets (a state, a
  set of cities, or a track + its keywords), and enable/disable a target.
- The config JSON files stay the **catalog / suggestions** surfaced in the UI. We add no
  new state, city, or track data to the files themselves in this change — the page draws
  on what is already there and lets the admin extend the live table.

## Not in scope

- **No writes back to `config/leads/*.json`.** Serverless can't write the repo, and the
  files are the reviewable seed of record. The page edits the DB table only.
- **No deletes.** Disabling a target is reversible and preserves the rotation
  (`last_run_at`); deleting a row would drop its history. Disable is the off switch.
- No change to collect/qualify. They keep reading `company_search_targets` exactly as now.

---

## 1. Background: the two-tier config (recap)

```
   config/leads/geography.json   states -> cities
   config/leads/roles.json       terms  -> service_line (track)
          |  lead_config (the only reader; validates + caches)
          |  lead_targets.build_targets()  =>  location x term matrix
          v  seed_search_targets() / ensure_targets()   (idempotent)
   company_search_targets   (per tenant, the runtime source of truth)
          |  claim_targets() — least-recently-run enabled rows
          v
   COLLECT -> job_postings -> QUALIFY -> leads -> /signals
```

`company_search_targets` columns (migration `2026-08-05-job-posting-leads.sql`):

| column | note |
|---|---|
| `company_id` | tenant |
| `term` | the search keyword (what practices post) |
| `service_line` | the track the term belongs to |
| `location` | the board location string, e.g. `"Tampa, FL"` |
| `state` | `char(2)` |
| `granularity` | `'state'` \| `'city'` |
| `enabled` | the on/off switch collect respects |
| `last_run_at` | rotation cursor, set before each search |
| `last_row_count` | zero-row tripwire |
| unique | `(company_id, term, location)` |

A "target" is one `(term × location)` search. "Add a state" means adding the state's
statewide + per-city locations, each crossed with the tenant's current terms. "Add a
track" means adding the track's keywords crossed with the tenant's current locations.

---

## 2. What the page shows

`/signals/config`, admin only (mirrors `RetriggerButton`: a non-admin sees nothing to
click, and every write is behind `require_admin`).

Three panels, all driven by one `GET /api/admin/leads/config` payload:

1. **Overview** — total targets, enabled count (= one full sweep), states covered,
   tracks covered, and the config-file `search` knobs (`hours_old`, `results_wanted`,
   `distance_miles`) shown read-only.

2. **Geography** — each state the tenant has targets for, its cities, and enabled/total
   counts. Affordances:
   - **Add state** — code (2 letters), name, statewide query, and a city list. Expanded
     against the tenant's current terms into rows and posted.
   - **Add cities** to an existing state — same expansion, city granularity only.
   - Enable/disable a whole state or city (bulk toggle of its rows).

3. **Tracks & keywords** — each `service_line` with its keywords. Affordances:
   - **Add track** — a service line name + one or more keywords, expanded against the
     tenant's current locations.
   - **Add keyword** to an existing track.
   - Enable/disable a track or a single keyword.

Below the panels, a filterable **targets table** (by state / track / enabled) with a
per-row enable toggle, so an operator can hand-tune individual `(term × location)` cells —
the exact thing ADR-03 says the table exists to allow.

The catalog from `config/leads/*.json` seeds the "Add …" forms (e.g. the Add-track form
pre-lists the five configured tracks and their keywords as one-click adds), so the common
case is picking from what's already curated, and free text is the escape hatch.

---

## 3. API surface

All under `require_admin`; all scoped to `admin["company_id"]`.

### `GET /api/admin/leads/config`

Returns the catalog (from `lead_config`) and the live targets (from the table):

```jsonc
{
  "catalog": {
    "states":  [ { "code": "FL", "name": "Florida", "enabled": true,
                   "statewide_query": "Florida, USA", "cities": ["Jacksonville, FL", ...] } ],
    "tracks":  [ { "service_line": "Virtual Medical Assistant",
                   "terms": ["medical assistant", ...] } ],
    "search":  { "hours_old": 168, "results_wanted": 40, "distance_miles": 50 },
    "sources": ["indeed", "linkedin"]
  },
  "targets": {
    "total": 651, "enabled": 651,
    "rows": [ { "id": 1, "term": "...", "service_line": "...", "location": "Tampa, FL",
                "state": "FL", "granularity": "city", "enabled": true,
                "last_run_at": "...", "last_row_count": 7 }, ... ]
  }
}
```

Rows are paginated server-side (`_paginated_query`) so the response is not clipped at
PostgREST's 1000-row ceiling.

### `POST /api/admin/leads/targets`

Add rows. The body is a list of explicit rows; the **frontend** does the cartesian
expansion (state × terms, or track × locations) and posts the result, so the endpoint
stays one primitive:

```jsonc
{ "rows": [ { "term": "medical assistant", "service_line": "Virtual Medical Assistant",
              "location": "Houston, TX", "state": "TX", "granularity": "city",
              "enabled": true }, ... ] }
```

Server-side it validates each row (`state` 2 letters, `granularity ∈ {state,city}`,
non-empty term/service_line/location), dedupes against existing `(term, location)` like
`seed_search_targets`, and chunk-upserts (`on_conflict="company_id,term,location"`,
`ignore_duplicates`). Returns `{ requested, inserted, skipped }`.

### `PATCH /api/admin/leads/targets/{id}`

`{ "enabled": bool }` — toggle one target. Bulk toggles (a whole state/track) are the
frontend firing a small batch of these, or a future `?filter=` variant if the batch
proves too chatty.

---

## 4. Backend module changes

All in `src/lead_targets.py` (it already owns the table; no new module):

- `catalog()` — assemble the `catalog` block from `lead_config` (`locations` regrouped
  back into states+cities, `role_terms` grouped into tracks, `search_params`,
  `enabled_sources`). Pure read, no DB.
- `list_targets(company_id)` — all rows via `_paginated_query`, ordered by state, location,
  term.
- `add_targets(company_id, rows)` — validate + dedupe + chunk-upsert; returns counts.
  Shares the chunking/dedupe shape of `seed_search_targets`.
- `set_target_enabled(company_id, target_id, enabled)` — scoped update, returns the row.

`api/index.py` gets the three routes above next to the existing
`/api/admin/leads/seed-targets`.

Frontend: `web/lib/leads.ts` gains `getSignalsConfig`, `addTargets`, `setTargetEnabled`;
`web/app/signals/config/page.tsx` is the page; `web/components/config-button.tsx` is the
admin-gated top-bar link, added to `web/app/signals/page.tsx`.

---

## 5. Known constraint — new tracks and the qualifier (open question §5.1)

The qualifier constrains its `service_line` output to `lead_config.service_lines()`, which
is derived from **roles.json**, not the DB table (`src/lead_config.py:89`). So a track
added **only** through this page will:

- ✅ be **searched** and its postings **collected** (collect reads the table), and
- ⚠️ **not** be a value the qualifier can assign as a verdict `service_line`, because the
  model's allowed set still comes from the config file.

So a DB-only new track drives collection immediately but its leads won't be *labelled*
with the new track until the service line is also added to `roles.json` and deployed. The
page surfaces this with an inline note on the Add-track form ("Searches immediately; to
label leads under a brand-new track, also add it to roles.json"). Adding **keywords to an
existing track** has no such caveat — the service line already exists in config.

**Decision for v1:** accept the split. The page's highest-value job is geography and
keyword tuning within the five existing tracks, which is fully supported. Brand-new tracks
are rare and still want a reviewed config change for the qualifier half. Revisit if
operators add tracks often — the fix is to source `service_lines()` from the union of
config and the tenant's distinct target service lines.

---

## 6. Rollout

1. Ship behind the admin gate; no migration (table already exists).
2. `ensure_targets` still seeds FL from config on an empty tenant, so nothing regresses.
3. Adding targets does **not** trigger a sweep — the admin adds, reviews the count, then
   hits **Run pipeline** (existing `RetriggerButton`) when ready.

## Open questions

- **§6.1** Bulk enable/disable: N× PATCH vs. a filtered bulk endpoint. Start with batched
  PATCH; promote to a bulk route if the state-level toggle is too chatty.
- **§6.2** Should the page ever *offer* to write a diff back to `config/leads/*.json` as a
  branch/PR (making the table edit reviewable and reproducible)? Out of scope now; noted
  because the table and file will drift over time and the file is the seed of record.
