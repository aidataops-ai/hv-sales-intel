-- Instant Signals — target dimensions (Phase 1 of the refactor).
--
-- See docs/refactor/instant-signals-targets.md §2.
--
-- `company_search_targets` stores the search matrix as a materialized
-- cartesian product (term x location), which grows multiplicatively with
-- every state or track added. A DB table should hold facts you cannot
-- recompute — every matrix row is derivable from its two dimensions, so this
-- migration introduces `search_terms` and `search_locations` (the WHAT and
-- WHERE) plus `target_runs` (sparse observational state) and
-- `target_overrides` (rare hand-pinned per-cell offs), and the collector
-- computes the product at claim time instead of storing it.
--
-- NOTHING EXISTING IS MODIFIED — `company_search_targets` is left untouched
-- and is the rollback plan (see plan §6) until the post-soak drop PR.
--
-- Idempotent. Re-runnable.

-- ---------------------------------------------------------------
-- 1) search_terms — WHAT to search (~21 rows per tenant).
-- ---------------------------------------------------------------
create table if not exists search_terms (
  id           bigserial primary key,
  company_id   uuid not null references companies(id) on delete cascade,
  term         text not null,
  service_line text not null,
  enabled      boolean not null default true,
  created_at   timestamptz not null default now(),
  unique (company_id, term)
);

-- ---------------------------------------------------------------
-- 2) search_locations — WHERE to search (~31 rows per state per tenant).
--
-- Per-source cursor + yield-decay streak live here. Two sources are columns,
-- not a child table, so claim stays one indexed select with no join —
-- revisit as a (location_id, source) child table if a 3rd board lands.
-- ---------------------------------------------------------------
create table if not exists search_locations (
  id                   bigserial primary key,
  company_id           uuid not null references companies(id) on delete cascade,
  location             text not null,            -- "Tampa, FL" / "Florida, USA"
  state                char(2) not null,
  granularity          text not null check (granularity in ('state','city')),
  enabled              boolean not null default true,
  last_indeed_at       timestamptz,
  last_linkedin_at     timestamptz,
  indeed_zero_streak   int not null default 0,
  linkedin_zero_streak int not null default 0,
  created_at           timestamptz not null default now(),
  unique (company_id, location)
);

create index if not exists idx_locations_indeed
  on search_locations (company_id, enabled, last_indeed_at nulls first);
create index if not exists idx_locations_linkedin
  on search_locations (company_id, enabled, last_linkedin_at nulls first);

-- ---------------------------------------------------------------
-- 3) target_runs — sparse observational state: only cells that have
-- actually run. Zero-row tripwire (ADR-02) + per-source yield. Never read
-- by claim ordering.
-- ---------------------------------------------------------------
create table if not exists target_runs (
  term_id        bigint not null references search_terms(id) on delete cascade,
  location_id    bigint not null references search_locations(id) on delete cascade,
  source         text not null,               -- 'indeed' | 'linkedin'
  last_run_at    timestamptz,
  last_row_count int,                          -- rows the board returned
  last_new_count int,                          -- rows not already in job_postings
  primary key (term_id, location_id, source)
);

-- ---------------------------------------------------------------
-- 4) target_overrides — rare hand-pinned per-cell offs. Empty until an
-- operator pins one.
-- ---------------------------------------------------------------
create table if not exists target_overrides (
  term_id     bigint not null references search_terms(id) on delete cascade,
  location_id bigint not null references search_locations(id) on delete cascade,
  enabled     boolean not null,
  primary key (term_id, location_id)
);

-- ---------------------------------------------------------------
-- 5) Backfill from company_search_targets. Old table untouched; this is a
-- one-time reconstruction of the dimensions from the existing matrix.
-- ---------------------------------------------------------------

-- Terms: distinct per tenant; enabled if ANY cell with that term was
-- enabled.
insert into search_terms (company_id, term, service_line, enabled)
select company_id, term, min(service_line), bool_or(enabled)
from company_search_targets
group by company_id, term
on conflict (company_id, term) do nothing;

-- Locations: carry rotation position (max last_run_at) into BOTH cursors so
-- the rotation resumes where it left off instead of restarting.
insert into search_locations
  (company_id, location, state, granularity, enabled,
   last_indeed_at, last_linkedin_at)
select company_id, location, min(state), min(granularity), bool_or(enabled),
       max(last_run_at), max(last_run_at)
from company_search_targets
group by company_id, location
on conflict (company_id, location) do nothing;

-- Overrides: cells disabled while their term AND location remain enabled —
-- deliberate per-cell pins, not track/city-level offs.
insert into target_overrides (term_id, location_id, enabled)
select t.id, l.id, false
from company_search_targets c
join search_terms t on t.company_id = c.company_id and t.term = c.term
join search_locations l on l.company_id = c.company_id and l.location = c.location
where c.enabled = false and t.enabled and l.enabled
on conflict (term_id, location_id) do nothing;

-- `target_runs` starts empty — historical `last_row_count` was per-cell, not
-- per-source, so it cannot be attributed; the tripwire re-arms within one
-- sweep.

-- ---------------------------------------------------------------
-- 6) RLS (mirrors company_search_targets — ADR-11).
-- ---------------------------------------------------------------
alter table search_terms      enable row level security;
alter table search_locations  enable row level security;
alter table target_runs       enable row level security;
alter table target_overrides  enable row level security;

drop policy if exists "tenant_isolation_search_terms" on search_terms;
create policy "tenant_isolation_search_terms"
  on search_terms for all
  using (company_id in (
    select company_id from company_members where user_id = auth.uid()
  ));

drop policy if exists "tenant_isolation_search_locations" on search_locations;
create policy "tenant_isolation_search_locations"
  on search_locations for all
  using (company_id in (
    select company_id from company_members where user_id = auth.uid()
  ));

drop policy if exists "tenant_isolation_target_runs" on target_runs;
create policy "tenant_isolation_target_runs"
  on target_runs for all
  using (exists (
    select 1 from search_terms t
    join company_members m on m.company_id = t.company_id
    where t.id = target_runs.term_id and m.user_id = auth.uid()
  ));

drop policy if exists "tenant_isolation_target_overrides" on target_overrides;
create policy "tenant_isolation_target_overrides"
  on target_overrides for all
  using (exists (
    select 1 from search_terms t
    join company_members m on m.company_id = t.company_id
    where t.id = target_overrides.term_id and m.user_id = auth.uid()
  ));

-- ---------------------------------------------------------------
-- Sanity report
-- ---------------------------------------------------------------
select
  (select count(*) from search_terms)      as terms,
  (select count(*) from search_locations)  as locations,
  (select count(*) from target_overrides)  as overrides;
