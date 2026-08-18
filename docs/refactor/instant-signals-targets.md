# Refactor: Instant Signals — Target Model & Collector Strategy

**Date:** 2026-08-13
**Status:** Planned — ships as one PR
**Scope:** `company_search_targets` data model, collector claim/rotation strategy,
admin API, config page. Everything else (qualify, match, other engines) unchanged.

---

## 1. Why

The collector's search matrix is stored as a **materialized cartesian product**:
`company_search_targets` holds every `term × location` as a physical row. At 1
state that was 651 rows; at the current 5 states it is ~3,255, growing
multiplicatively with every state or track. On top of it sits a fixed
`--targets 40` batch chosen for a serverless constraint that no longer exists
(the pipeline runs on GitHub Actions), and a constant 7-day search window.

### Measured baseline (2026-08-13, run 31712422106 + 25-run history)

| Metric | Value |
|---|---|
| Run cadence / duration | hourly, 16–24 min (steady) |
| Collect | 40 targets, both boards, **13.7 min** (~20.5 s/target) |
| Qualify | 203 postings, 6.1 min, **15 keeps (7.4%)** |
| Redundancy | 2,567 rows fetched → **203 new (8% novelty)** |
| Cap saturation | `indeed=40 linkedin=40` per target — `results_wanted=40` binding → **coverage hole** in dense cities |
| Full both-board sweep (5 states) | ~18.5 h of scrape time — rotation is mandatory |
| Config change cost | add 1 state = ~651 inserts; disable a state = 651 PATCHes |

### Goals

1. **Store dimensions, compute the product** — terms (~21 rows) + locations
   (~155 rows) replace ~3,255 matrix rows.
2. **Spend time, not counts** — wall-clock budget + per-source staleness
   thresholds; exit early when nothing is due.
3. **Stop re-fetching the same week** — adaptive `hours_old` from each location's
   cursor kills the 92% redundancy and the results_wanted saturation.
4. **Instrument for the next decision** — per-source timing/novelty/yield so
   LinkedIn's fate and threshold tuning are data decisions.

---

## 2. Data model

Principle: a DB table should hold facts you cannot recompute. Every matrix row is
derivable from the two dimensions — so store the dimensions, compute the product
at claim time.

```sql
-- WHAT to search (~21 rows per tenant)
create table search_terms (
  id           bigserial primary key,
  company_id   uuid not null references companies(id) on delete cascade,
  term         text not null,
  service_line text not null,
  enabled      boolean not null default true,
  created_at   timestamptz not null default now(),
  unique (company_id, term)
);

-- WHERE to search (~31 rows per state per tenant); rotation cursors live here
create table search_locations (
  id                bigserial primary key,
  company_id        uuid not null references companies(id) on delete cascade,
  location          text not null,            -- "Tampa, FL" / "Florida, USA"
  state             char(2) not null,
  granularity       text not null check (granularity in ('state','city')),
  enabled           boolean not null default true,
  -- Per-source cursor + yield-decay streak. Two sources are columns, not a
  -- child table, so claim stays one indexed select with no join. Revisit as a
  -- (location_id, source) child table if a 3rd board lands.
  last_indeed_at        timestamptz,
  last_linkedin_at      timestamptz,
  indeed_zero_streak    int not null default 0,
  linkedin_zero_streak  int not null default 0,
  created_at        timestamptz not null default now(),
  unique (company_id, location)
);
create index idx_locations_indeed
  on search_locations (company_id, enabled, last_indeed_at nulls first);
create index idx_locations_linkedin
  on search_locations (company_id, enabled, last_linkedin_at nulls first);

-- Sparse observational state: only cells that have actually run. Zero-row
-- tripwire (ADR-02) + per-source yield. Never read by claim ordering.
create table target_runs (
  term_id        bigint not null references search_terms(id) on delete cascade,
  location_id    bigint not null references search_locations(id) on delete cascade,
  source         text not null,               -- 'indeed' | 'linkedin'
  last_run_at    timestamptz,
  last_row_count int,                          -- rows the board returned
  last_new_count int,                          -- rows not already in job_postings
  primary key (term_id, location_id, source)
);

-- Rare hand-pinned per-cell offs. Empty until an operator pins one.
create table target_overrides (
  term_id     bigint not null references search_terms(id) on delete cascade,
  location_id bigint not null references search_locations(id) on delete cascade,
  enabled     boolean not null,
  primary key (term_id, location_id)
);
```

**Effective enabled** for a cell =
`term.enabled AND location.enabled AND coalesce(override.enabled, true)`.

### Cost comparison

| Operation | Matrix (today) | Dimensions |
|---|---|---|
| Rows at 5 states | ~3,255 | ~176 |
| Add a state (30 cities + statewide) | ~651 inserts | 31 inserts |
| Add a keyword | 155 inserts | 1 insert |
| Disable a city / track | 21–651 PATCHes | 1 UPDATE |
| Config page payload | full matrix | ~176 rows |
| Seed a new tenant | 651-row upsert + dedup pre-read | ~176 inserts |

### Migration & backfill (same PR; old table untouched)

```sql
-- Terms: distinct per tenant; enabled if ANY cell with that term was enabled.
insert into search_terms (company_id, term, service_line, enabled)
select company_id, term, min(service_line), bool_or(enabled)
from company_search_targets
group by company_id, term;

-- Locations: carry rotation position (max last_run_at) into BOTH cursors so
-- the rotation resumes where it left off instead of restarting.
insert into search_locations
  (company_id, location, state, granularity, enabled,
   last_indeed_at, last_linkedin_at)
select company_id, location, min(state), min(granularity), bool_or(enabled),
       max(last_run_at), max(last_run_at)
from company_search_targets
group by company_id, location;

-- Overrides: cells disabled while their term AND location remain enabled —
-- deliberate per-cell pins, not track/city-level offs.
insert into target_overrides (term_id, location_id, enabled)
select t.id, l.id, false
from company_search_targets c
join search_terms t on t.company_id = c.company_id and t.term = c.term
join search_locations l on l.company_id = c.company_id and l.location = c.location
where c.enabled = false and t.enabled and l.enabled;
```

`target_runs` starts empty — historical `last_row_count` was per-cell, not
per-source, so it cannot be attributed; the tripwire re-arms within one sweep.

Seeding: `config/leads/*.json` stays the catalog of record; `seed_search_targets`
/ `ensure_targets` collapse to dimension inserts, idempotent via the unique
constraints. The chunked-upsert + 1000-row-cap-prone dedup machinery
(`lead_targets.py:64-100,423-444`) is deleted.

---

## 3. Collector strategy

**SLO:** posting → lead in UI within ~3 h (Indeed) / ~2 days (LinkedIn).

### Run anatomy (hourly GitHub Actions job, unchanged trigger)

```
job (hard stop 55 min)
├─ COLLECT  (wall-clock budget, default 40 min)
│    phase 1  INDEED    all locations stale > indeed threshold, stalest first
│    phase 2  LINKEDIN  remaining budget, stale > linkedin threshold, stalest first
├─ QUALIFY  drain ALL unqualified (incl. this run's — keeps Indeed leads same-run)
└─ MATCH    link keepers to practices (unchanged)
```

Qualify stays sequential in-job: a parallel job would add an hour of latency to
every lead for no gain.

### The claim loop

```
for source in (indeed, linkedin):
    while now() < deadline:
        loc = next enabled location where
              cursor[source] older than effective_threshold(loc, source)
              order by cursor[source] nulls first
        if loc is None: break                      # nothing due → exit early
        for term in enabled_terms:                 # minus overrides
            rows = search(term, loc, source, hours_old=window(loc, source))
            upsert postings; record target_runs(term, loc, source, rows, new)
        stamp cursor[source] on loc                # per-location, crash-safe
```

Properties: **self-limiting** (thresholds stop useless re-scans; quiet runs exit
early — also the Actions-minutes control), **self-healing** (a killed run leaves
stale cursors; the next resumes at the true stalest point), **self-balancing**
(new states enter as `null` = stalest and sweep first).

### Settings (env-tunable defaults in `settings.py`, not constants)

| Setting | Default | Note |
|---|---|---|
| `lead_budget_minutes` | 40 | collect wall-clock ceiling |
| `lead_indeed_stale_hours` | 6 | Indeed due-threshold |
| `lead_linkedin_stale_hours` | 24 | budget-limited at 5 states (~2-day effective sweep) either way |
| `lead_window_buffer_hours` | 12 | adaptive `hours_old` margin |
| `lead_zero_streak_cap` | 4 | yield-decay exponent cap |

The measured ~20.5 s/target cannot yet be split per source; the first
instrumented runs provide the split, then defaults are tuned via env with no
code change. (This is why instrumentation is folded into this PR rather than
being a precursor.)

### Adaptive search window — kills the 92% redundancy + cap saturation

```
window(loc, source) = clamp(hours_since(cursor[source]) + buffer, 24, 168)
# never-swept (null cursor) → 168 (full first sweep)
```

A location swept 6 h ago asks for an ~18 h window instead of 7 days: the top-40
cap stops binding (complete coverage rather than a relevance sample), redundant
rows drop, scrapes return faster.

### Yield decay — prunes dead cities without list curation

```
effective_threshold(loc, source) =
    base_threshold[source] * 2 ^ min(zero_streak[loc, source], cap)
```

A sweep returning zero rows across all terms increments the streak; any non-zero
sweep resets it. Dead suburbs decay toward weekly scans automatically; the
statewide query remains the safety net for cities not on the list.

### Retired

- `sources_for_run` run-index modulo weighting → replaced by the two-phase,
  per-source-threshold structure.
- `--targets N` → `--budget-minutes` (workflow input updated; manual dispatch
  can pass a smaller budget for a quick pass).

### Instrumentation (same PR)

Per-target log line and stats gain per-source elapsed + novelty:

```
[  9/…] medical assistant   St. Petersburg, FL   80 kept (12 new)
        indeed=40 (4.1s) linkedin=40 (15.2s)   28.9s
```

Run summary additions: per-source sweep period (from cursors), coverage (% of
enabled locations fresh within threshold), novelty %, qualify keep-rate. The
same numbers surface read-only on the config page.

### Steady-state expectation at 5 states (validate after merge)

| | Threshold | Expected per run | Effective sweep |
|---|---|---|---|
| Indeed | 6 h | ~26 locations | ~2–6 h |
| LinkedIn | 24 h | ~3–4 locations (budget-limited) | ~2 days |

If measured Indeed cost is materially above ~2 s/query, phase 1 becomes
budget-limited too — acceptable (sweep stretches, summary reports it), tuned via
`lead_indeed_stale_hours`.

---

## 4. Backend & API changes

### `src/lead_targets.py` — new surface

| Function | Replaces | Shape |
|---|---|---|
| `catalog()` | unchanged | checked-in config as UI suggestions |
| `list_config(company_id)` | `list_targets` | `{terms, locations, overrides}` — ~176 rows, no paginator |
| `add_terms` / `add_locations` | `add_targets` | validated dimension inserts; idempotent via unique constraints |
| `set_term_enabled` / `set_location_enabled` | `set_target_enabled` × N | one UPDATE flips a whole track/city |
| `set_override(term_id, location_id, enabled)` | per-cell toggle | upsert/delete in `target_overrides` |
| `claim_locations(company_id, source, deadline)` | `claim_targets` | stalest-first due locations for one source; stamps per location |
| `record_target_result(term_id, location_id, source, rows, new)` | old 2-arg version | upserts `target_runs`; updates zero-streaks |
| `sweep_status(company_id)` | `sweep_size` | per-source sweep period, coverage %, enabled counts |
| `seed_search_targets` / `ensure_targets` | same names | dimension inserts; chunking/dedup deleted |
| `resolve_company_id` | unchanged | single-tenant pin stays (deferred track) |

**Deleted:** `build_targets` cartesian, `sources_for_run`, dedup pre-reads,
chunked upserts.

**Claim contract unchanged for callers:** the collect loop still receives flat
dicts `{term, service_line, location, state, granularity, term_id, location_id}` —
`search_jobs` and `lead_store` never know the storage changed.

### `src/job_boards.py`

- `search_jobs(...)` accepts an `hours_old` override (adaptive window).
- Per-source `stats` gain `elapsed_s`; caller computes `new` from the upsert
  result and passes it to `record_target_result`.

### Admin API (`api/index.py`)

| Route | Change |
|---|---|
| `GET /api/admin/leads/config` | `{catalog, terms, locations, overrides, sweep}` instead of the full matrix |
| `POST /api/admin/leads/terms` | add keywords/tracks |
| `POST /api/admin/leads/locations` | add cities/states |
| `PATCH /api/admin/leads/terms/{id}` | `{enabled}` |
| `PATCH /api/admin/leads/locations/{id}` | `{enabled}` |
| `PUT /api/admin/leads/overrides` | `{term_id, location_id, enabled\|null}` — pin/unpin one cell |
| old `POST/PATCH /api/admin/leads/targets*` | removed |

All under `require_admin`, scoped to `admin["company_id"]`. Validation semantics
from `_clean_target_row` carry over per dimension (2-letter state, granularity
enum, non-empty strings; whole batch validated before any write).

### `scripts/run_leads.py` + workflow

- `--targets N` → `--budget-minutes M`; collect becomes the two-phase loop;
  per-target print gains per-source timing + new-count; summary gains
  sweep/coverage/novelty lines. `--stage`, `--sources`, `--preflight`, qualify
  drain-all unchanged.
- `.github/workflows/leads.yml`: `targets` input → `budget-minutes` (default 40);
  `timeout-minutes: 60` stays as the hard backstop.

---

## 5. Config page (`web/app/signals/config/page.tsx`, `web/lib/leads.ts`)

Net effect: the page gets *simpler* — more frontend code deleted than added.

| Problem today | Cause | After |
|---|---|---|
| Full-matrix download per load and after every click | `list_targets` ships every cell | ~176-row payload; refetch stays cheap enough to keep |
| 651-request PATCH storm on Enable/Disable-all | per-cell `enabled` → one PATCH per row | one PATCH to a dimension row |
| Client-side cartesian in 3 places (`addState`, `expand`, `addCity`) | server stores the product | deleted — client never builds rows |
| O(n·m) groupBy per render | cells re-derived into states/tracks | dimensions arrive at ~176 rows |

Page structure (same visual design):

- **Geography panel** — city chip ↔ one `search_locations` row; click =
  `PATCH /locations/{id}`; add city/state = `POST /locations` (no term crossing).
- **Tracks panel** — keyword pill ↔ one `search_terms` row; track toggle = one
  PATCH per term (≤7 requests, no longer per-cell). roles.json qualifier caveat
  note stays.
- **Sweep status strip (new)** — "Indeed: full sweep ~2.1 h · LinkedIn: ~2.0 d ·
  coverage 94% · last-run novelty 31%" — makes the freshness cost of adding
  states visible.
- **Overrides** — minimal "pinned cells" list, shown only if any exist.

Client: `addTerms`, `addLocations`, `setTermEnabled`, `setLocationEnabled`,
`setOverride` replace `addTargets`/`setTargetEnabled`; new types `SearchTerm`,
`SearchLocation`, `TargetOverride`, `SweepStatus`.

Out of scope: SWR/caching, skeletons, design changes (frontend-wide track).

---

## 6. Verification & rollout

**Principle: the old table is the rollback plan** — written by nothing after
merge, dropped by nothing until the soak passes.

### Pre-merge

1. **Unit tests** — claim ordering (nulls first, threshold filter, decay),
   window clamps, override precedence, add validation, seed idempotency.
2. **Backfill dry-run against prod (read-only):** run the backfill selects and
   assert distinct term/location counts match, and every cell's
   effective-enabled under the new model equals its old `enabled`.
3. **Local pipeline pass:** `run_leads.py --stage collect --budget-minutes 5 -v`
   against the migrated schema — verify claim order, stamping, window values,
   identical posting upserts.

### Merge sequence

1. Apply migration (create + backfill; old table untouched).
2. Deploy code (all reads/writes move to the new tables).
3. Manually dispatch one workflow run with a small budget; read the log.

### 24-hour soak checklist

| Check | Expect |
|---|---|
| Zero-row alert | not firing |
| Novelty % | rising well above the 8% baseline |
| `indeed=40`/`linkedin=40` saturation | largely gone |
| Sweep periods | Indeed in hours; LinkedIn ≈ ~2 days |
| Per-source elapsed | recorded → tune `lead_*_stale_hours` via env |
| Config page | ~176-row load; city toggle = 1 PATCH; add-state = 31 inserts |
| Qualify keep-rate | comparable to ~7% baseline |

### Post-soak (separate tiny PR)

`drop table company_search_targets;` — and retire the
"adding a term requires re-seed" operational caveat (adding a term is now one
live insert; no re-seed exists).

### Rollback

Code revert restores the old paths against `company_search_targets`, which still
holds pre-migration state (stale by at most the soak window; rotation
self-heals). New tables can be dropped or left inert.

### Risks

| Risk | Mitigation |
|---|---|
| Backfill mis-reconstructs enabled state | pre-merge assertion #2; old table retained |
| Adaptive window misses late-indexed postings | 24 h floor + 12 h buffer; 168 cap on first sweep; novelty metric exposes gaps |
| Indeed cost worse than estimated → phase 1 budget-limited | env-tunable thresholds; summary reports real sweep period |
| Two sources hardcoded as columns | acknowledged; child-table refactor only if a 3rd board lands |

---

## 7. Deferred (not this PR, with triggers)

| Trigger | Move |
|---|---|
| LinkedIn sweep > ~5 days or Actions bill > ~$50/mo | Always-on worker; Actions stays for manual/backfill |
| 2 weeks of per-source yield data | LinkedIn scope decision: everywhere / focus states / drop |
| Real multi-state tenants | ICP-driven cadence tiers; ICP → targets derivation |
| ~20+ states | Shard collect by state-group (disjoint claims) |
| Separate track | `claim_unqualified` SQL anti-join; Supabase client singleton; lazy `openai` import; 1000-row-cap sweep; analyze-path concurrency |

### Decision 2026-08-13 — LinkedIn goes statewide-only (measured)

First instrumented runs on the staging pipeline measured: **Indeed 1.85s/term,
LinkedIn 23.0s/term**; all-time keep rates **Indeed 10.6% (915/8,623) vs
LinkedIn 1.6% (260/16,081)** — ~62 qualifier calls per LinkedIn keep vs ~9 for
Indeed. Per-city LinkedIn was simultaneously the freshness bottleneck (~3 days
per 155-location cycle at the production budget) and the worst cost-per-keep
in the pipeline, while producing 65% of the qualifier bill for 22% of keeps.

**Change:** `lead_linkedin_statewide_only = True` (env-tunable) — LinkedIn
claims only `granularity='state'` rows (5 queries/cycle ≈ 46 min), its
threshold drops 24h → 6h, and `sweep_status` reports coverage against the
scoped set. Result: posting → lead ≤ ~7h on BOTH boards. City-level coverage
stays on Indeed, where the keeps come from. Revisit if per-source yield data
ever shows a metro where city-level LinkedIn earns its 23s/term.

> Superseded 2026-08-14: the flag's default flipped back to `False` when the
> city tier returned as a *recall* layer with its own slow threshold — see
> the next decision. The measurement above still stands; what changed is
> that a uniform threshold was the real problem, not city coverage itself.

### Decision 2026-08-14 — Source-split workflows + LinkedIn city recall tier

Two changes shipped together, each enabling the other.

**Why:** run history showed the hourly on-the-hour cron actually delivers
**~18 of 24 runs/day** (GitHub skips congested schedule slots, clustering
overnight UTC), and the user wanted LinkedIn back at city level without
giving up the freshness the statewide-only decision bought.

**1. One workflow per board.** `leads-indeed.yml` (hourly at `:17`, full
40-min budget, qualify attached) and `leads-linkedin.yml` (2-hourly at
`:41`, 35-min budget, collect-only). `leads.yml` stays dispatch-only — it is
what the app's manual retrigger endpoint fires, and it grew a `sources`
input. Off-hour cron minutes reduce the skip rate; separate `concurrency`
groups; the per-source cursor columns mean the jobs never contend for rows.
Effects: Indeed's phase reserve (60%) becomes moot in the scheduled path (a
single-source run takes the whole budget), Indeed city freshness improves
~6.6h → ~5h, and a broken board no longer eats the other board's window.
The schedules only fire once the files land on `main`; on staging both are
dispatch-only.

**2. LinkedIn runs two tiers.** Statewide rows stay the *instant* tier
(6h threshold — a statewide query already sees every city's postings).
City rows return as a *recall* tier on `lead_linkedin_city_stale_hours`
(default 72h): they only add postings the statewide query dropped past the
board's ~40-results-per-query cap in dense markets. A full city pass is ~33h
of scrape (33 terms × 155 cities × 23s), so the dedicated LinkedIn workflow
turns the recall wheel in ~4 days — *recall depth on a slow rotation, not
signal latency*: a fresh posting still lands ≤ ~7h via statewide. Claim
orders `granularity desc` first for LinkedIn so the instant tier can never
be buried behind the (permanently staler) city backlog; `claim_locations`
and `sweep_status` both judge city rows against the recall threshold.
`lead_linkedin_statewide_only=True` remains the escape hatch that drops the
city tier entirely.

### Confirmed by the pre-merge branch review, deferred deliberately

Findings from the 2026-08-13 adversarial review that are real but out of this
refactor's scope — each needs its own change with its own owner:

- **`scripts/seed_practices.py` is hardcoded to FL** (`.eq('state','FL')` in
  `demand_locations`, and `_loc_key` strips state) — the practice bank never
  expands into GA/NC/SC/TN from observed leads, so new-state leads go
  unmatched until the seeding script is made multi-state.
- **City facet/filter collapses cross-state duplicates** — `job_postings.city`
  is used without state in the leads facet and `cities` filter
  (`lead_store.py`), so Greenville NC and Greenville SC merge into one entry
  whose selection returns both states' leads (feed + CSV export). Fix is a
  `"City, ST"` facet key, which touches the feed API and the filter UI.
- **ICP scorer hardcodes FL as focus market** — already covered by the
  "ICP-driven cadence tiers / ICP → targets" deferred row; the same wiring
  must feed `geographies.focus_states` into `icp_scorer._vertical_fit`.
- **Metro-overlap city curation** — several new GA/SC cities sit inside
  another listed city's 50-mile search radius (six Atlanta-metro entries,
  four Charleston-metro). Yield decay cannot prune them (they return plenty
  of already-seen rows, not zero rows). Wants either curation or a
  novelty-based (not zero-row-based) decay signal — revisit with the
  per-source novelty data this refactor starts collecting.
