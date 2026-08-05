-- Job-Posting Leads — v1 schema.
--
-- See docs/specs/2026-08-05-hiring-signal-collector-{adr,design}.md.
--
-- Three new tables. NOTHING EXISTING IS MODIFIED — this module is fully
-- separate from the practice pipeline (ADR-01) and shares only auth,
-- tenancy and the usage ledger.
--
--   1. `job_postings`           — raw postings, SHARED across tenants (ADR-04)
--   2. `company_job_leads`      — per-(company, posting) verdict AND workflow
--   3. `company_search_targets` — the (term x location) matrix, per tenant
--
-- Idempotent. Re-runnable.

-- ---------------------------------------------------------------
-- 1) Raw postings, shared across tenants.
--
-- Keyed (source, external_id) because the two boards live in different
-- id namespaces: Indeed's is the 16-hex `jk` query param, LinkedIn's is
-- the trailing numeric id in /jobs/view/... A single global unique on
-- external_id would collide across sources.
-- ---------------------------------------------------------------
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

  -- Provenance: which query surfaced this row. Useful when tuning the
  -- term list against observed yield.
  search_term        text,
  search_location    text,
  service_line_hint  text,

  first_seen_at      timestamptz default now(),
  last_seen_at       timestamptz default now(),
  unique (source, external_id)
);

create index if not exists idx_job_postings_recent
  on job_postings (posted_at desc);
create index if not exists idx_job_postings_seen
  on job_postings (last_seen_at desc);


-- ---------------------------------------------------------------
-- 2) One row per (company, posting): verdict AND workflow (ADR-04).
--
-- The column split below is LOAD-BEARING. Re-qualification writes only
-- the verdict group; a single `update ... set status = ...` from the
-- qualifier would silently reset an SDR's pipeline.
-- ---------------------------------------------------------------
create table if not exists company_job_leads (
  id              bigserial primary key,
  company_id      uuid not null references companies(id) on delete cascade,
  posting_id      bigint not null references job_postings(id) on delete cascade,

  -- verdict columns — written by the qualifier, safe to overwrite on re-qualify
  decision        text check (decision in ('keep','discard')),
  confidence      numeric(3,2),
  confidence_band text check (confidence_band in ('ready','check','decide')),
  -- Sort key for the band. ADR-07's default feed order is band first, then
  -- posting recency, and 'ready' < 'check' < 'decide' is not the alphabetical
  -- order of those words — without a rank column every page load would have to
  -- re-sort in application code, which breaks pagination.
  band_rank       smallint,   -- 1 = ready, 2 = check, 3 = decide
  reason          text,
  employer_type   text,     -- independent | group | system | dso | vet | agency | other
  role_suitable   boolean,
  work_mode       text check (work_mode in ('onsite','remote','hybrid')),
  service_line    text,
  provider_count  int,
  draft           text,
  model           text,
  qualified_at    timestamptz,

  -- workflow columns — written by operators, NEVER touched by re-qualification
  status          text not null default 'new'
                  check (status in ('new','approved','contacted','replied',
                                    'booked','rejected')),
  reject_reason   text,
  notes           text,
  assigned_to     uuid references auth.users(id),
  assigned_at     timestamptz,
  last_touched_by uuid references auth.users(id),
  last_touched_at timestamptz,
  contacted_at    timestamptz,

  -- CSV export tracking, same semantics as practices.export_count
  export_count     int not null default 0,
  last_exported_at timestamptz,
  last_exported_by uuid references auth.users(id),

  created_at      timestamptz default now(),
  -- Enforces at the database that a posting is never re-qualified (and
  -- so never re-billed) for the same tenant.
  unique (company_id, posting_id)
);

create index if not exists idx_leads_feed
  on company_job_leads (company_id, status, band_rank, created_at desc);
create index if not exists idx_leads_assigned
  on company_job_leads (company_id, assigned_to);
create index if not exists idx_leads_posting
  on company_job_leads (posting_id);


-- ---------------------------------------------------------------
-- 3) Search targets, seeded from config/leads/, rotated by the collector.
--
-- Collection reads THIS TABLE, never the config files (ADR-03). Per-tenant
-- divergence is achieved by editing rows here, not by forking config.
-- ---------------------------------------------------------------
create table if not exists company_search_targets (
  id           bigserial primary key,
  company_id   uuid not null references companies(id) on delete cascade,
  term         text not null,
  service_line text,
  location     text not null,
  state        char(2),
  granularity  text check (granularity in ('state','city')),
  source       text,                 -- null = every enabled source
  enabled      boolean not null default true,
  last_run_at  timestamptz,
  last_row_count int,                -- rows kept on the last run; 0 = alert
  unique (company_id, term, location)
);

create index if not exists idx_targets_rotation
  on company_search_targets (company_id, enabled, last_run_at nulls first);


-- ---------------------------------------------------------------
-- 4) RLS (ADR-11)
--
-- Raw postings are public data and dedupe to one row regardless of which
-- tenant collected them first, so they are authenticated-read across
-- tenants. Verdicts and workflow state are tenant-private.
--
-- The backend writes with the service-role key and enforces company_id in
-- code; RLS here is defence in depth.
-- ---------------------------------------------------------------
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
  using (company_id in (
    select company_id from company_members where user_id = auth.uid()
  ));

drop policy if exists "tenant_isolation_search_targets" on company_search_targets;
create policy "tenant_isolation_search_targets"
  on company_search_targets for all
  using (company_id in (
    select company_id from company_members where user_id = auth.uid()
  ));


-- ---------------------------------------------------------------
-- Sanity report
-- ---------------------------------------------------------------
select
  (select count(*) from job_postings)           as postings,
  (select count(*) from company_job_leads)      as leads,
  (select count(*) from company_search_targets) as targets;
