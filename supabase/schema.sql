-- Phase 1: Lead Discovery
create table if not exists practices (
  id bigserial primary key,
  place_id text unique not null,
  name text not null,
  address text,
  city text,
  state text,
  phone text,
  website text,
  rating numeric(2,1),
  review_count int default 0,
  category text,
  lat double precision,
  lng double precision,
  opening_hours text,

  -- Phase 2 (AI analysis) — columns exist but nullable
  summary text,
  pain_points text,
  sales_angles text,
  recommended_service text,
  lead_score int,
  urgency_score int,
  hiring_signal_score int,

  -- Phase 3 (Call Playbook + CRM)
  call_script text,

  -- Phase 3 (CRM) — columns exist but nullable
  status text default 'NEW',
  notes text,

  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_practices_place_id on practices (place_id);
create index if not exists idx_practices_category on practices (category);
create index if not exists idx_practices_city on practices (city);
create index if not exists idx_practices_score on practices (lead_score desc nulls last);

-- Auth + user attribution

create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  name text,
  role text not null default 'sdr' check (role in ('admin', 'sdr')),
  disabled_at timestamptz,
  created_at timestamptz default now()
);

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, email, name, role)
  values (new.id, new.email, new.raw_user_meta_data->>'name', 'sdr')
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

alter table practices add column if not exists last_touched_by uuid references profiles(id);
alter table practices add column if not exists last_touched_at timestamptz;

create index if not exists idx_profiles_role on profiles (role);

-- Email outreach

alter table practices add column if not exists email text;
alter table practices add column if not exists email_draft text;
alter table practices add column if not exists email_draft_updated_at timestamptz;

create table if not exists email_messages (
  id bigserial primary key,
  practice_id bigint not null references practices(id) on delete cascade,
  user_id uuid references profiles(id),
  direction text not null check (direction in ('out', 'in')),
  subject text,
  body text,
  message_id text,
  in_reply_to text,
  sent_at timestamptz default now(),
  error text
);

create index if not exists idx_email_messages_practice
  on email_messages (practice_id, sent_at desc);
create index if not exists idx_email_messages_message_id
  on email_messages (message_id);

-- ======================= Salesforce integration + call log =======================

alter table practices
  add column if not exists salesforce_lead_id     text,
  add column if not exists salesforce_lead_url    text,
  add column if not exists salesforce_owner_id    text,
  add column if not exists salesforce_owner_name  text,
  add column if not exists salesforce_synced_at   timestamptz,
  add column if not exists call_count             integer not null default 0,
  add column if not exists call_notes             text;

create index if not exists idx_practices_sf_lead_id on practices(salesforce_lead_id);

-- ======================= Clay owner enrichment =======================

alter table practices
  add column if not exists owner_name         text,
  add column if not exists owner_email        text,
  add column if not exists owner_phone        text,
  add column if not exists owner_title        text,
  add column if not exists owner_linkedin     text,
  add column if not exists enrichment_status  text,
  add column if not exists enriched_at        timestamptz;

-- ======================= Leads workspace + personalization =======================

-- Multi-tag visibility (orthogonal to status)
alter table practices add column if not exists tags text[] not null default '{}';
create index if not exists idx_practices_tags on practices using gin (tags);

-- Assignment workflow
alter table practices add column if not exists assigned_to uuid references profiles(id);
alter table practices add column if not exists assigned_at timestamptz;
alter table practices add column if not exists assigned_by uuid references profiles(id);
create index if not exists idx_practices_assigned_to on practices (assigned_to);

-- Website-extracted doctor info (separate from Google Places `phone`)
alter table practices add column if not exists website_doctor_name text;
alter table practices add column if not exists website_doctor_phone text;

-- ICP score breakdown (per-dimension reasoning) — populated by the analyzer
alter table practices add column if not exists icp_breakdown jsonb;

-- H&V Universal ICP — classified vertical + tier (populated by the analyzer)
alter table practices add column if not exists icp_vertical text;  -- medical | dental | alf_nh | hotel_resort | medspa_wellness
alter table practices add column if not exists icp_tier text;       -- A | B | C | D
create index if not exists idx_practices_icp_vertical on practices (icp_vertical);
create index if not exists idx_practices_icp_tier on practices (icp_tier);

-- Fingerprint of analyzer inputs (name/address/phone/website/category/state).
-- Re-analyze returns the cached result when this hash matches the existing
-- row, so clicking Re-analyze on an unchanged practice no longer produces
-- AI-driven score noise.
alter table practices add column if not exists analysis_input_hash text;

-- AI-extracted decision-maker contacts from the website (owner, manager,
-- lead provider, etc.). Stored as a JSON string for consistency with
-- pain_points / sales_angles: [{"name","title","phone","email"}].
-- Used to personalize the cold-call playbook.
alter table practices add column if not exists website_contacts text;

-- CSV export tracking. `export_count` increments by 1 for every row
-- included in a bulk export. `last_exported_at` + `last_exported_by`
-- record who pulled the row last and when. The export endpoint accepts
-- a `max_exports` filter so an operator can re-run the export later
-- with `max_exports=0` to skip previously-downloaded rows and avoid
-- duplicates. `last_exported_by` lets multi-SDR teams see who has
-- already pulled each lead.
alter table practices add column if not exists export_count integer not null default 0;
alter table practices add column if not exists last_exported_at timestamptz;
alter table practices add column if not exists last_exported_by uuid references profiles(id);
create index if not exists idx_practices_last_exported_by on practices (last_exported_by);

-- Search query cache (avoid re-billing Google for repeated queries)
create table if not exists searches (
  id bigserial primary key,
  query_norm text unique not null,
  query_raw text not null,
  place_ids text[] not null,
  searched_at timestamptz default now()
);
create index if not exists idx_searches_query_norm on searches (query_norm);

-- =============================================================================
-- Multi-tenant ICP foundation (added 2026-05-24)
--
-- See `docs/specs/2026-05-24-multitenant-icp-upload-design.md` for context.
-- This block creates the four new tables + RLS policies. The existing
-- `practices` table is intentionally untouched here — column drops happen
-- in a separate cutover migration (Phase 8 of the plan).
-- =============================================================================

create extension if not exists "pgcrypto";

-- A tenant.
create table if not exists companies (
  id            uuid primary key default gen_random_uuid(),
  slug          text unique not null,
  name          text not null,
  branding      jsonb,                  -- {display_name, accent_color, logo_url}
  icp_doc_text  text,                   -- raw upload / paste for audit + re-parse
  icp_parsed    jsonb,                  -- structured ICP — see icp_parser.py schema
  scoring_config jsonb,                 -- dimension-weight overrides; null = defaults
  integration_secrets jsonb,            -- per-tenant SF / RingCentral / etc.
  -- Prepaid credits — see supabase/migrations/2026-05-29-credits.sql
  -- for the ledger + RPCs. 1 credit = $0.33.
  credit_balance     numeric(14, 4) not null default 0,
  credits_purchased  numeric(14, 4) not null default 0,
  credits_consumed   numeric(14, 4) not null default 0,
  created_by    uuid references auth.users(id),
  created_at    timestamptz default now(),
  archived_at   timestamptz
);
create index if not exists idx_companies_slug on companies (slug);

-- Membership of a user in a company, with a per-company role.
create table if not exists company_members (
  company_id    uuid references companies(id) on delete cascade,
  user_id       uuid references auth.users(id) on delete cascade,
  role          text not null check (role in ('admin','sdr')),
  joined_at     timestamptz default now(),
  primary key (company_id, user_id)
);
create index if not exists idx_company_members_user on company_members (user_id);

-- Per-(company, practice) AI analysis. One row per company × practice.
create table if not exists company_practice_analyses (
  id                  bigserial primary key,
  company_id          uuid not null references companies(id) on delete cascade,
  practice_id         bigint not null references practices(id) on delete cascade,
  lead_score          int,
  classification      text,             -- Strong ICP / Qualified / Weak / Poor fit
  icp_breakdown       jsonb,
  icp_vertical        text,
  icp_tier            text,
  summary             text,
  pain_points         jsonb,
  sales_angles        jsonb,
  website_contacts    jsonb,
  urgency_score       int,              -- legacy alias retained for older UI
  hiring_signal_score int,              -- legacy alias retained for older UI
  analysis_input_hash text,
  analyzed_at         timestamptz default now(),
  unique (company_id, practice_id)
);
create index if not exists idx_cpa_company_score
  on company_practice_analyses (company_id, lead_score desc nulls last);
create index if not exists idx_cpa_company_vertical
  on company_practice_analyses (company_id, icp_vertical);

-- Per-(company, practice) CRM + workflow state.
create table if not exists company_practice_state (
  id                      bigserial primary key,
  company_id              uuid not null references companies(id) on delete cascade,
  practice_id             bigint not null references practices(id) on delete cascade,
  status                  text default 'NEW',
  notes                   text,
  tags                    text[] not null default '{}',
  call_count              int not null default 0,
  call_notes              text,
  call_script             text,
  email                   text,
  email_draft             text,
  email_draft_updated_at  timestamptz,
  salesforce_lead_id      text,
  salesforce_lead_url     text,
  salesforce_owner_id     text,
  salesforce_owner_name   text,
  salesforce_synced_at    timestamptz,
  assigned_to             uuid references auth.users(id),
  assigned_at             timestamptz,
  assigned_by             uuid references auth.users(id),
  last_touched_by         uuid references auth.users(id),
  last_touched_at         timestamptz,
  export_count            int not null default 0,
  last_exported_at        timestamptz,
  last_exported_by        uuid references auth.users(id),
  enrichment_status       text,
  enriched_at             timestamptz,
  owner_name              text,
  owner_email             text,
  owner_phone             text,
  owner_title             text,
  owner_linkedin          text,
  unique (company_id, practice_id)
);
create index if not exists idx_cps_company_status
  on company_practice_state (company_id, status);
create index if not exists idx_cps_company_tags
  on company_practice_state using gin (tags);
create index if not exists idx_cps_company_assigned
  on company_practice_state (company_id, assigned_to);
create index if not exists idx_cps_company_sf
  on company_practice_state (company_id, salesforce_lead_id);

-- Usage + cost log. One row per billable external call (Places search,
-- Places details, OpenAI completion). Aggregated by the /admin/usage
-- page so admins can see token + Places-API spend and tune pricing.
create table if not exists usage_events (
  id              bigserial primary key,
  company_id      uuid references companies(id) on delete cascade,
  user_id         uuid references auth.users(id) on delete set null,
  kind            text not null,        -- places_search | places_details | openai_analyze | openai_script | openai_email | openai_icp_parse
  model           text,                 -- OpenAI model name; null for Places
  input_tokens   int,
  output_tokens  int,
  cached_input_tokens int,           -- subset of input_tokens that hit the prompt cache
  calls           int default 1,         -- count of underlying API hits (Places pages > 1)
  cost_cents      numeric(12, 4),        -- estimated cost in cents (fractional)
  metadata        jsonb,                 -- free-form: query, place_id, error info
  created_at      timestamptz default now()
);
create index if not exists idx_usage_company_created on usage_events (company_id, created_at desc);
create index if not exists idx_usage_kind on usage_events (kind);

-- Per-company email log (replaces email_messages.practice_id linkage).
create table if not exists company_email_messages (
  id            bigserial primary key,
  company_id    uuid not null references companies(id) on delete cascade,
  practice_id   bigint not null references practices(id) on delete cascade,
  user_id       uuid references auth.users(id),
  direction     text not null check (direction in ('out','in')),
  subject       text,
  body          text,
  message_id    text,
  in_reply_to   text,
  sent_at       timestamptz default now(),
  error         text
);
create index if not exists idx_cem_company_practice
  on company_email_messages (company_id, practice_id, sent_at desc);
create index if not exists idx_cem_message_id
  on company_email_messages (message_id);

-- =============================================================================
-- RLS — tenant isolation for the per-company tables.
--
-- The backend uses the SERVICE-ROLE key for writes (bypasses RLS) and
-- enforces company_id in code. RLS is defense-in-depth so a code bug
-- that forgets a filter doesn't leak data via the anon client.
--
-- Every `auth.uid()` / `auth.role()` below is wrapped in a scalar subselect.
-- Those functions are STABLE, not IMMUTABLE, so a bare call is re-executed
-- per row the policy is checked against; `(select auth.uid())` makes the
-- planner hoist it into an InitPlan that runs once per statement. The
-- predicates are otherwise unchanged — see
-- supabase/migrations/2026-08-15-rls-initplan-fk-indexes.sql.
-- =============================================================================

alter table companies                 enable row level security;
alter table company_members           enable row level security;
alter table company_practice_analyses enable row level security;
alter table company_practice_state    enable row level security;
alter table company_email_messages    enable row level security;
alter table practices                 enable row level security;
-- credit_transactions RLS lives in the 2026-05-29-credits.sql migration
-- alongside the consume_credits / add_credits / debit_credits RPCs.
-- job_postings / company_job_leads / company_search_targets RLS lives in
-- 2026-08-05-job-posting-leads.sql. Those policies carry the same hoisted
-- form as the ones below.

-- A user can see / edit a company iff they're a member of it.
drop policy if exists "tenant_membership_companies" on companies;
create policy "tenant_membership_companies"
  on companies for all
  using (id in (select company_id from company_members where user_id = (select auth.uid())));

drop policy if exists "tenant_membership_members" on company_members;
create policy "tenant_membership_members"
  on company_members for all
  using (user_id = (select auth.uid())
         or company_id in (select company_id from company_members where user_id = (select auth.uid())));

drop policy if exists "tenant_isolation_analyses" on company_practice_analyses;
create policy "tenant_isolation_analyses"
  on company_practice_analyses for all
  using (company_id in (select company_id from company_members where user_id = (select auth.uid())));

drop policy if exists "tenant_isolation_state" on company_practice_state;
create policy "tenant_isolation_state"
  on company_practice_state for all
  using (company_id in (select company_id from company_members where user_id = (select auth.uid())));

drop policy if exists "tenant_isolation_emails" on company_email_messages;
create policy "tenant_isolation_emails"
  on company_email_messages for all
  using (company_id in (select company_id from company_members where user_id = (select auth.uid())));

-- `practices` is intentionally world-readable across tenants — same
-- business should dedup to one row regardless of which company first
-- discovered it. Writes still need authentication. Documenting the
-- intent here with a permissive policy.
drop policy if exists "practices_authenticated_read" on practices;
create policy "practices_authenticated_read"
  on practices for select
  using ((select auth.role()) = 'authenticated' or (select auth.role()) = 'service_role');

-- Backfill tags from existing state (idempotent — only writes empty tags)
update practices set tags = coalesce((
  select array_agg(distinct t) from unnest(array[
    case when lead_score is not null then 'RESEARCHED' end,
    case when call_script is not null then 'SCRIPT_READY' end,
    case when enrichment_status = 'enriched' then 'ENRICHED' end,
    case when call_count > 0 then 'CONTACTED' end,
    case when status = 'MEETING SET' then 'MEETING_SET' end,
    case when status = 'CLOSED WON' then 'CLOSED_WON' end,
    case when status = 'CLOSED LOST' then 'CLOSED_LOST' end
  ]) t where t is not null
), '{}'::text[]) where tags = '{}'::text[];

-- =============================================================================
-- Instant Signals — target dimensions (added 2026-08-13)
--
-- Mirrors supabase/migrations/2026-08-13-target-dimensions.sql. That file's
-- one-time backfill from `company_search_targets` is deliberately not
-- reproduced here — it reconstructs these tables from the old matrix, which
-- this file never defined.
-- =============================================================================

-- search_terms — WHAT to search (~21 rows per tenant).
create table if not exists search_terms (
  id           bigserial primary key,
  company_id   uuid not null references companies(id) on delete cascade,
  term         text not null,
  service_line text not null,
  enabled      boolean not null default true,
  created_at   timestamptz not null default now(),
  unique (company_id, term)
);

-- search_locations — WHERE to search (~31 rows per state per tenant).
-- Per-source cursor + yield-decay streak are columns, not a child table, so
-- claim stays one indexed select with no join.
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

-- target_runs — sparse observational state: only cells that have actually
-- run. Zero-row tripwire (ADR-02) + per-source yield.
create table if not exists target_runs (
  term_id        bigint not null references search_terms(id) on delete cascade,
  location_id    bigint not null references search_locations(id) on delete cascade,
  source         text not null,               -- 'indeed' | 'linkedin'
  last_run_at    timestamptz,
  last_row_count int,                          -- rows the board returned
  last_new_count int,                          -- rows not already in job_postings
  primary key (term_id, location_id, source)
);

-- target_overrides — rare hand-pinned per-cell offs.
create table if not exists target_overrides (
  term_id     bigint not null references search_terms(id) on delete cascade,
  location_id bigint not null references search_locations(id) on delete cascade,
  enabled     boolean not null,
  primary key (term_id, location_id)
);

alter table search_terms      enable row level security;
alter table search_locations  enable row level security;
alter table target_runs       enable row level security;
alter table target_overrides  enable row level security;

drop policy if exists "tenant_isolation_search_terms" on search_terms;
create policy "tenant_isolation_search_terms"
  on search_terms for all
  using (company_id in (
    select company_id from company_members where user_id = (select auth.uid())
  ));

drop policy if exists "tenant_isolation_search_locations" on search_locations;
create policy "tenant_isolation_search_locations"
  on search_locations for all
  using (company_id in (
    select company_id from company_members where user_id = (select auth.uid())
  ));

-- target_runs / target_overrides have no company_id of their own; tenancy is
-- reached through search_terms.
drop policy if exists "tenant_isolation_target_runs" on target_runs;
create policy "tenant_isolation_target_runs"
  on target_runs for all
  using (exists (
    select 1 from search_terms t
    join company_members m on m.company_id = t.company_id
    where t.id = target_runs.term_id and m.user_id = (select auth.uid())
  ));

drop policy if exists "tenant_isolation_target_overrides" on target_overrides;
create policy "tenant_isolation_target_overrides"
  on target_overrides for all
  using (exists (
    select 1 from search_terms t
    join company_members m on m.company_id = t.company_id
    where t.id = target_overrides.term_id and m.user_id = (select auth.uid())
  ));

-- =============================================================================
-- FK indexes on real join paths (added 2026-08-15)
--
-- Mirrors §2 of supabase/migrations/2026-08-15-rls-initplan-fk-indexes.sql;
-- §1 of that file (the RLS initplan hoisting) is already reflected in the
-- policy definitions above. Postgres does not auto-index the referencing side
-- of a foreign key, and these columns are all read as join keys — they are
-- also what makes `on delete cascade` do a seq scan per deleted parent row.
-- The user-audit FK columns the advisor also flags are deliberately skipped;
-- see that migration for the list and the reasoning.
-- =============================================================================

create index if not exists idx_target_runs_location
  on target_runs (location_id);

create index if not exists idx_target_overrides_location
  on target_overrides (location_id);

create index if not exists idx_cps_practice
  on company_practice_state (practice_id);

create index if not exists idx_cpa_practice
  on company_practice_analyses (practice_id);

-- practices.last_touched_by: GET /api/admin/users counts by this column, one
-- HEAD count per profile, which the index makes an index-only scan.
create index if not exists idx_practices_touched_by
  on practices (last_touched_by);

-- =============================================================================
-- Posting-description retention (added 2026-08-15)
--
-- Mirrors supabase/migrations/2026-08-15-posting-retention.sql, which also
-- schedules this function daily at 04:17 UTC via pg_cron
-- (`prune-discarded-posting-descriptions`) and runs a one-off backfill —
-- neither is reproduced here.
--
-- Requires `job_postings` / `company_job_leads` from
-- 2026-08-05-job-posting-leads.sql, so apply that file before this block.
--
-- THE INVARIANT: a posting's description is pruned only when it has at least
-- one verdict in `company_job_leads`, EVERY verdict on it across ALL tenants
-- is `decision = 'discard'`, it has not been seen for `days` (default 30),
-- and its description is not already null. An unjudged posting is never
-- touched (the qualifier builds its prompt from `description`), and neither
-- is one any tenant kept.
--
-- SECURITY DEFINER with a pinned `search_path`: `job_postings` has RLS
-- enabled and pg_cron runs jobs as the database owner, so definer rights make
-- the behaviour identical from cron and from the backend's service-role key.
-- EXECUTE is revoked from everyone except service_role.
-- =============================================================================

create or replace function public.prune_discarded_posting_descriptions(
  days int default 30
)
returns bigint
language sql
security definer
set search_path = public, pg_temp
as $fn$
  with pruned as (
    update job_postings p
       set description = null
     where p.description is not null
       -- (3) Stale: the boards stopped returning it, so no upsert will write
       -- the body back. A NULL `last_seen_at` (shouldn't happen — every
       -- upsert stamps it) makes this NULL and excludes the row, which is the
       -- safe direction.
       --
       -- `coalesce` and not just `greatest`: GREATEST *ignores* NULLs in
       -- Postgres, so `greatest(days, 0)` would silently turn an explicit NULL
       -- argument into a zero-day window and prune every judged posting.
       and p.last_seen_at < now()
                          - (greatest(coalesce(days, 30), 0) * interval '1 day')
       -- (1) judged by someone …
       and exists (
         select 1 from company_job_leads l
          where l.posting_id = p.id
       )
       -- (2) … and not one single non-discard verdict exists, in ANY tenant.
       -- `is distinct from` (not `<>`) so a NULL decision counts as a reason
       -- to keep the body, not as a discard.
       and not exists (
         select 1 from company_job_leads l
          where l.posting_id = p.id
            and l.decision is distinct from 'discard'
       )
    returning 1
  )
  select count(*) from pruned;
$fn$;

comment on function public.prune_discarded_posting_descriptions(int) is
  'Nulls job_postings.description for postings unseen for N days whose every '
  'company_job_leads verdict (all tenants) is a discard. Never touches an '
  'unjudged or kept posting. Returns rows pruned. See the 500 MB free-tier cap '
  'in docs/refactor/supabase-data-layer.md.';

revoke all on function public.prune_discarded_posting_descriptions(int) from public;
grant execute on function public.prune_discarded_posting_descriptions(int) to service_role;

-- =============================================================================
-- practice_contacts — multiple contacts per practice (added 2026-08-21)
--
-- Mirrors supabase/migrations/2026-08-21-practice-contacts.sql; that file
-- carries the full rationale. In short: Clay now calls the webhook once per
-- PERSON, and the flat `owner_*` columns on `practices` hold only one contact,
-- so the second callback would overwrite the first.
--
-- SHARED table (no company_id) for the same reason `practices` is — a contact
-- is a fact about the business, not about the tenant that enriched it. It
-- COEXISTS with `practices.website_contacts` (AI-extracted from the website by
-- src/analyzer.py, different provenance) and does not replace `practices.owner_*`,
-- which the webhook now maintains as a mirror of the primary contact so every
-- existing consumer keeps working.
--
-- Identity is (practice_id, dedupe_key) as a plain UNIQUE CONSTRAINT — the key
-- is computed in src/contacts.py::contact_dedupe_key (normalized-LinkedIn ->
-- work_email -> personal_email -> normalized-name) because PostgREST
-- `on_conflict` upserts cannot arbitrate partial unique indexes. The constraint
-- doubles as the FK-side index on `practice_id` (leading column).
--
-- See docs/specs/2026-08-21-practice-contacts.md.
-- =============================================================================

create table if not exists practice_contacts (
  id             bigserial primary key,
  practice_id    bigint not null references practices(id) on delete cascade,
  first_name     text,
  last_name      text,
  title          text,
  linkedin_url   text,
  work_email     text,
  personal_email text,
  phone          text,
  source         text not null default 'clay',
  dedupe_key     text not null,   -- app-computed; src/contacts.py::contact_dedupe_key
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (practice_id, dedupe_key)
);

-- phone landed after the first apply of the 2026-08-21 migration.
alter table practice_contacts add column if not exists phone text;

comment on table practice_contacts is
  'One row per person Clay returns for a practice. Shared across tenants like '
  '`practices`. Identity is (practice_id, dedupe_key), the key computed in '
  'src/contacts.py::contact_dedupe_key. Coexists with the AI-extracted '
  'practices.website_contacts; practices.owner_* mirrors the primary contact. '
  'See docs/specs/2026-08-21-practice-contacts.md.';

alter table practice_contacts enable row level security;

drop policy if exists "practice_contacts_authenticated_read" on practice_contacts;
create policy "practice_contacts_authenticated_read"
  on practice_contacts for select
  using ((select auth.role()) = 'authenticated' or (select auth.role()) = 'service_role');
