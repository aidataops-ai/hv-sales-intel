# Job-Posting Leads — Design & Implementation Plan (v1)

**Date:** 2026-08-05
**Status:** Draft — awaiting review
**Decisions:** see `2026-08-05-hiring-signal-collector-adr.md`

---

## Goal

Continuously collect job postings from LinkedIn and Indeed, qualify each one with AI,
and show the good ones in a dedicated Leads section of the app.

**v1 is collect → qualify → show. Nothing else.**

## Not in v1

- No connection to `practices`, the analyzer, or the scorer
- No place resolution, no enrichment, no ICP scoring
- No auto-outreach — every lead is human-approved before anything is sent

The module shares only what every module shares: auth, tenancy, the usage ledger.

---

## 1. Pipeline

```
   config/search/*.json
          |  seeded per tenant -> company_search_targets
          v
   [1] COLLECT   role term x location  ->  Indeed, LinkedIn
          |      deterministic prefilter (config)
          v
     job_postings                        (shared, raw)
          |
   [2] QUALIFY   gpt-5.6-terra, batched, per tenant
          |
     company_job_leads                   (verdict + workflow, disposition='undecided')
          |
          v
     LEADS UI  ->  triage -> approve -> copy draft -> outreach
```

Two background stages, both bounded and idempotent. A lead is workable the moment it is
qualified.

---

## 2. Configuration

Three checked-in files. `src/lead_config.py` is the only module that reads them;
everything downstream reads `company_search_targets`.

### `config/leads/roles.json`

```json
{
  "terms": [
    { "term": "medical receptionist",           "service_line": "Virtual Medical Assistant" },
    { "term": "front office assistant",         "service_line": "Virtual Medical Assistant" },
    { "term": "prior authorization specialist", "service_line": "Virtual Medical Assistant" },
    { "term": "medical billing specialist",     "service_line": "Virtual Medical Assistant" },
    { "term": "dental receptionist",            "service_line": "Virtual Dental Assistant" },
    { "term": "dental front office",            "service_line": "Virtual Dental Assistant" },
    { "term": "treatment coordinator",          "service_line": "Virtual Dental Assistant" },
    { "term": "home health scheduler",          "service_line": "Virtual Home Health Operations Coordinator" }
  ]
}
```

Narrow ambiguous terms rather than deleting them — `staffing coordinator` returns
recruiting agencies; `home health staffing coordinator` does not.

### `config/leads/geography.json`

Statewide **and** city queries. Statewide relevance ranking favours large employers with
many postings; city-level queries surface the single-location practices.

```json
{
  "states": [
    { "code": "FL", "enabled": true,
      "statewide_query": "Florida, USA",
      "cities": ["Jacksonville, FL", "Miami, FL", "Tampa, FL", "Orlando, FL", "..."] }
  ],
  "search": { "hours_old": 168, "results_wanted": 40, "distance_miles": 50 }
}
```

### `config/leads/filters.json`

Deterministic drops applied before any model call. Every row killed here is saved spend.

```json
{
  "negative_patterns": [
    "veterinar", "animal (hospital|clinic)", "budtender", "cannabis",
    "construction", "air conditioning", "staffing", "recruit", "warehouse"
  ],
  "sources": { "indeed": { "enabled": true }, "linkedin": { "enabled": true } }
}
```

---

## 3. Data model

Two new tables plus a targets table. **Nothing existing is modified.**

```sql
-- Raw postings, shared across tenants.
create table if not exists job_postings (
  id                 bigserial primary key,
  source             text not null,            -- 'indeed' | 'linkedin'
  external_id        text not null,
  url                text,

  title              text not null,
  employer_name      text,                     -- null on confidential postings
  employer_name_norm text,
  location_raw       text,
  city               text,
  state              char(2),

  posted_at          timestamptz,
  salary_min         numeric,
  salary_max         numeric,
  salary_interval    text,                     -- hourly | yearly | ...
  board_remote_flag  boolean,
  description        text,

  first_seen_at      timestamptz default now(),
  last_seen_at       timestamptz default now(),
  unique (source, external_id)
);
create index if not exists idx_job_postings_recent
  on job_postings (posted_at desc);

-- One row per (company, posting): verdict AND workflow (ADR-04).
create table if not exists company_job_leads (
  id              bigserial primary key,
  company_id      uuid not null references companies(id) on delete cascade,
  posting_id      bigint not null references job_postings(id) on delete cascade,

  -- verdict columns — written by the qualifier, safe to overwrite on re-qualify
  decision        text check (decision in ('keep','discard')),
  confidence      numeric(3,2),
  confidence_band text check (confidence_band in ('ready','check','decide')),
  reason          text,
  employer_type   text,     -- independent | group | system | dso | agency | other
  role_suitable   boolean,
  work_mode       text check (work_mode in ('onsite','remote','hybrid')),
  service_line    text,
  provider_count  int,
  draft           text,
  model           text,
  qualified_at    timestamptz,

  -- workflow columns — written by operators, NEVER touched by re-qualification
  disposition     text not null default 'undecided'
                  check (disposition in ('undecided','approved','rejected')),
  reject_reason   text,
  notes           text,
  last_touched_by uuid references auth.users(id),
  last_touched_at timestamptz,
  contacted_at    timestamptz,

  -- CSV export tracking, same semantics as practices.export_count
  export_count    int not null default 0,
  last_exported_at timestamptz,
  last_exported_by uuid references auth.users(id),

  created_at      timestamptz default now(),
  unique (company_id, posting_id)
);
create index if not exists idx_leads_feed
  on company_job_leads (company_id, disposition, confidence_band, created_at desc);

-- Search targets, seeded from config, rotated by the collector.
create table if not exists company_search_targets (
  id           bigserial primary key,
  company_id   uuid not null references companies(id) on delete cascade,
  term         text not null,
  service_line text,
  location     text not null,
  state        char(2),
  granularity  text check (granularity in ('state','city')),
  enabled      boolean not null default true,
  last_run_at  timestamptz,
  unique (company_id, term, location)
);
create index if not exists idx_targets_rotation
  on company_search_targets (company_id, enabled, last_run_at nulls first);
```

> The verdict/workflow column split inside one table is load-bearing. Re-qualification
> writes only the first group. A single `UPDATE ... SET disposition = ...` from the
> qualifier would silently reset an SDR's pipeline.

### RLS (ADR-11)

```sql
alter table job_postings           enable row level security;
alter table company_job_leads      enable row level security;
alter table company_search_targets enable row level security;

drop policy if exists "job_postings_authenticated_read" on job_postings;
create policy "job_postings_authenticated_read"
  on job_postings for select
  using (auth.role() = 'authenticated' or auth.role() = 'service_role');

drop policy if exists "tenant_isolation_job_leads" on company_job_leads;
create policy "tenant_isolation_job_leads"
  on company_job_leads for all
  using (company_id in (select company_id from company_members where user_id = auth.uid()));

drop policy if exists "tenant_isolation_search_targets" on company_search_targets;
create policy "tenant_isolation_search_targets"
  on company_search_targets for all
  using (company_id in (select company_id from company_members where user_id = auth.uid()));
```

---

## 4. UI

Hand-built on Tailwind, following the existing app. **No component library** — the
project uses none (no shadcn, no Radix), and `tailwind.config.ts` enforces a strict
5-colour palette (`#353535`, `#3c6e71`, `#ffffff`, `#d9d9d9`, `#284b63`) by remapping
every Tailwind colour name onto those families. New components must stay on it.

Reuse rather than rebuild:

| Existing | Used for |
|---|---|
| `cn()` in `lib/utils.ts` | class merging (`clsx` + `tailwind-merge`) |
| `timeAgo()` in `lib/utils.ts` | the "2d ago" column |
| `status-badge.tsx` | pattern for the band + decision badges — a `Record<string,string>` of light/dark class pairs |
| `filter-bar.tsx`, `search-bar.tsx`, `tags-filter.tsx` | filter row |
| `pagination.tsx` | table paging |
| `notes-panel.tsx` | detail panel |
| `export-button.tsx` | CSV export — **generalised with props**, see §4.4 |
| `top-bar.tsx`, `company-switcher.tsx`, `theme-toggle.tsx` | shell, unchanged |

Dark mode is per-component `dark:` variants, not a theme provider — every new component
writes both branches.

### 4.1 Entry point

An **Instant Signals** button on the practices homepage (`web/app/page.tsx`), in the
header row beside the existing controls, using a lucide icon. It links to `/signals`.

> The route and label say "signals"; the tables and API say "leads". Deliberate — the
> operator-facing name is Instant Signals, the internal noun is a lead.

### 4.2 Signals table — `/signals`

A **table**, not the card grid used for practices — these are scanned in volume and
compared row to row.

| Column | Notes |
|---|---|
| Employer | bold; source badge (Indeed / LinkedIn) beside it |
| Role | posting title, links out to the original posting |
| City | `city, state` |
| Track | service line |
| Salary | `$18–23/hr`, em dash when absent |
| Mode | on-site / remote / hybrid |
| Band | Ready / Check / Decide badge |
| Posted | `timeAgo()` |
| Decision | undecided / approved / rejected |
| Actions | Approve · Reject · View |

Sort: band first, then posting recency. Column headers sortable. `pagination.tsx` below.

**Approve** opens the drafted message with a copy button and sets `disposition='approved'`.
**Reject** asks for a reason — those reasons are the tuning signal for the prompt and the
config prefilter, so keep the field one click away.

The posting date column is how an operator judges staleness; v1 does not remove older
leads automatically.

### 4.3 Filters

Two are the priority:

- **Cities — multi-select.** Free multi-select over the distinct cities present in the
  tenant's leads, not a fixed list. Chips show the active selection.
- **Tracks — multi-select.** Same shape, over service lines.

Both combine as `city IN (...) AND service_line IN (...)`.

Also: band · work mode · source · salary present · free-text
search over employer and title.

Filter state lives in the query string so a filtered view is shareable and survives a
refresh — and so export can reuse it verbatim (§4.4).

### 4.4 Export CSV

Replicates the practices export, including its `export_count` behaviour so an operator
can pull only leads they have not downloaded before.

**Frontend — generalise the existing component.** `export-button.tsx` is already the
right shape (popover, `max_exports` input, hidden-anchor download that preserves the
session cookie). Lift the hard-coded endpoint and filename into props:

```tsx
<ExportButton
  endpoint="/api/leads/export.csv"
  filename="instant-signals"
  params={activeFilters}          // same query string the table is showing
/>
```

The practices page keeps working by passing its current values as defaults.

**Backend — mirror `export_practices_csv`.** Same `StreamingResponse` + `csv.writer`
generator, same `max_exports` semantics (empty = all, `0` = never-exported only,
`N` = `export_count <= N`), and the same post-export stamping of `export_count`,
`last_exported_at`, `last_exported_by`.

**The export must honour the active filters.** Exporting the whole table when the
operator is looking at "Miami + Tampa, Dental track" is the obvious trap — the endpoint
takes the same filter params as `GET /api/leads`.

Columns: employer, title, city, state, source, url, posted_at, salary_min, salary_max,
salary_interval, work_mode, service_line, employer_type, provider_count, confidence,
confidence_band, reason, draft, disposition, created_at.

### 4.5 Detail — `/signals/{id}`

Full posting text, every qualifier field, the draft, the approve/reject decision, notes,
history, and a link to the original posting.

### 4.6 Analytics — `/signals/analytics`

Leads per day by source and track · qualifier keep-rate · band distribution · decision
breakdown · reject-reason breakdown · collector health (last run per source, targets swept,
zero-row alerts).

---

## 5. Modules

| File | Responsibility |
|---|---|
| `src/lead_config.py` | Loads and validates `config/leads/*`. **Only reader of those files.** |
| `src/lead_targets.py` | `seed_search_targets(company_id)`; rotation query |
| `src/job_boards.py` | `search_jobs(term, location, sources, ...)` — wraps `python-jobspy`; normalises rows; per-source `external_id`; config prefilter |
| `src/lead_qualifier.py` | Prompt, batched OpenAI call, validation, `usage.record_openai(kind="openai_qualify")` |
| `src/lead_store.py` | Reads/writes the three tables; stage claiming; disposition transitions |

New dependency: `python-jobspy`, pinned.

New settings in `src/settings.py`:

```python
qualifier_model: str = "gpt-5.6-terra"
qualifier_reasoning_effort: str = "medium"
qualifier_batch_size: int = 20
lead_collect_batch: int = 40
lead_qualify_batch: int = 60
```

---

## 6. Endpoints

| Route | Does |
|---|---|
| `POST /api/cron/leads/collect` | Claims least-recently-run targets, searches, upserts `job_postings` |
| `POST /api/cron/leads/qualify` | Claims unqualified postings for the tenant, batches to the model, writes `company_job_leads` |
| `GET /api/leads` | Tenant-scoped feed; filters + paging |
| `GET /api/leads/{id}` | Lead detail |
| `PATCH /api/leads/{id}` | Decision (approve/reject), reject reason, notes |
| `GET /api/leads/export.csv` | Filtered CSV export; honours the same filters as the feed plus `max_exports`; stamps `export_count` |
| `GET /api/leads/analytics` | Feed for the analytics page |
| `POST /api/admin/leads/seed-targets` | Re-seed targets after a config change |

Operator routes use `Depends(get_current_user)` and existing `company_id` resolution.
Cron routes authenticate with a shared secret header, matching the existing webhook
pattern.

**Scheduling.** Indeed is ~15× faster than LinkedIn, so they rotate separately — Indeed
frequently and broadly, LinkedIn on a slower cycle.

---

## 7. Qualifier contract

Input per posting: title, employer name, location, board remote flag, salary,
description excerpt, and the target's `service_line`.

Two independent questions; **both** must pass:

1. **Employer** — a small independent practice, or a hospital system / multi-site group /
   DSO / staffing agency / out-of-scope business?
2. **Role** — could a remote, non-clinical person perform the core duties? Roles requiring
   a licence or physical presence (nursing, imaging, hygiene, phlebotomy, caregiving,
   chairside assisting) are rejected **even at a perfect-fit employer**.

The second test is not optional. Without it the qualifier keeps clinical roles at
genuinely independent practices — employer right, lead unusable. A title containing
"Assistant" is not sufficient evidence either way; the description decides. An
administrative role *at* a clinical practice passes.

Output: `decision`, `confidence`, `reason`, `employer_type`, `role_suitable`,
`work_mode`, `service_line`, `provider_count`, and an outreach draft for keeps. JSON
mode; every enum validated before persistence so a malformed field degrades one row
rather than failing a batch.

**Draft guidance.** Match the pitch to work mode: on-site with a stated wage leads with
the cost comparison against that number; remote leads with speed of placement and
vetting.

---

## 8. Delivery plan

| Phase | Work | Verification |
|---|---|---|
| **1. Schema** | Migration for §3 + RLS | Policies deny cross-tenant reads; existing tests pass |
| **2. Config + collection** | `lead_config.py`, `lead_targets.py`, `job_boards.py`, prefilter, `collect` endpoint | One tenant, one state: postings land, dedupe on `(source, external_id)` holds, re-run inserts nothing. **Assert no module outside `lead_config.py` reads `config/leads/`** |
| **3. Qualification** | `lead_qualifier.py`, `qualify` endpoint, usage metering | Hand-check 20 verdicts: systems/DSOs/agencies rejected, clinical roles rejected, work mode matches the posting text. **Accuracy gate — do not proceed on impressions** |
| **4. Signals table** | `/signals` table, city + track multi-selects, filters in the query string, approve/reject, draft copy, detail page | An operator works a lead end to end without touching the database |
| **4b. Export** | Generalise `export-button.tsx` with props; `export.csv` endpoint honouring active filters | Export from a filtered view contains exactly those rows; re-export with `0` skips them |
| **5. Analytics** | Analytics page, collector health | Funnel, band distribution and reject reasons render |
| **6. Hardening** | Zero-row alerting, prefilter tuning from reject reasons, cron wiring | A zero-row run raises an alert — this is the Indeed-key failure mode |

Phases 1–3 are backend-only and can ship behind a flag. **Phase 4 is where the feature
becomes real** — everything before it is invisible to an operator.

---

## 9. Cost model

| | |
|---|---|
| Collection | free — bandwidth only |
| Qualification | ~172 in / ~175 out tokens per posting, per tenant |

**Qualification is the only per-lead cost in v1.** Collection volume is the lever:
targets per run × frequency.

`unique (company_id, posting_id)` enforces at the database that a posting is never
re-qualified for the same tenant.

Metered through `usage_events`, debiting tenant credits (ADR-10), so the admin usage page
reflects it unchanged.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| **Indeed API key rotated upstream** — collection stops silently | Pin the library; alert on zero-row runs; LinkedIn keeps running independently |
| **Re-qualification clobbers SDR state** | Qualifier writes verdict columns only — enforce in `lead_store.py`, cover with a test |
| **Ambiguous terms burn tokens** | Config prefilter ahead of the model; tune from reject reasons |
| **Board markup / API changes** | Isolated in `job_boards.py`; pinned dependency; zero-row alerting |
| **Cost runs away on a large tenant** | Bounded batch sizes; credits already enforce a ceiling |

---

## 11. Open questions

1. **Search recency window.** `hours_old: 168` (7 days) controls how far back collection
   looks. Separate from anything about lead lifetime — v1 keeps leads indefinitely.
2. **Confidence threshold.** 0.85 is an initial estimate; tune against
   booked-versus-rejected outcomes once available.
3. **Confidential postings** (~12%, no employer name) — surface or suppress? They can be
   qualified on role and location but not addressed by name in outreach.
4. **City coverage per state.** Which cities, and how many, is a yield question. Start
   with the largest markets and expand where leads convert.

### Resolved during implementation (2026-08-05)

**3 — Confidential postings are surfaced, not suppressed.** The role, the city and the
posting URL are all workable without an employer name, and suppression would discard
~12% of supply to save a label. They render as "Confidential posting" in the table and
carry an explanatory line on the detail page so nobody wastes a call looking for a name
that was never published. The decision is a config key, not a code path —
`config/leads/filters.json → options.include_confidential` — so reversing it is one
line and one deploy.

**4 — Florida only, the 30 largest cities plus the statewide query.** Carried over from
the evaluation prototype, which measured that a statewide query does not saturate: a
Tampa-level query surfaced 10 employers the statewide results missed, Miami 6. The
caveat is recorded in `config/leads/geography.json` — population rank is not
independent-practice density, and Naples, Sarasota, Ocala and Pensacola are outside the
top 30 but are exactly the affluent, older-patient markets where independents cluster.
Revisit once yield-per-city data exists.

**Two additions the design did not anticipate:**

- **`company_job_leads.band_rank`** (smallint, 1/2/3). ADR-07's default feed order is
  band first, then recency — and `ready` / `check` / `decide` do not sort alphabetically
  in that order. Without a rank column every page load would re-sort in application
  code, which breaks pagination.
- **Cron stages answer GET as well as POST**, and accept `Authorization: Bearer` as well
  as `X-Cron-Secret`. Vercel's scheduler only ever issues a GET and can only send the
  bearer header. Both stages are idempotent, so GET costs nothing in correctness.

**One decision worth flagging rather than burying:** `gpt-5.6-terra` has no entry in
`usage.OPENAI_COST_PER_MILLION_TOKENS`, so qualification is costed and billed against
the `default` band. Published pricing for the model could not be confirmed, and a
guessed band would put wrong numbers straight into the credit ledger. At ~172 in / ~175
out tokens per posting the absolute error is small, but the real band belongs there
before anyone reasons about lead-level unit economics.
