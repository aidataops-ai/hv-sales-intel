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
-- =============================================================================

alter table companies                 enable row level security;
alter table company_members           enable row level security;
alter table company_practice_analyses enable row level security;
alter table company_practice_state    enable row level security;
alter table company_email_messages    enable row level security;
alter table practices                 enable row level security;

-- A user can see / edit a company iff they're a member of it.
drop policy if exists "tenant_membership_companies" on companies;
create policy "tenant_membership_companies"
  on companies for all
  using (id in (select company_id from company_members where user_id = auth.uid()));

drop policy if exists "tenant_membership_members" on company_members;
create policy "tenant_membership_members"
  on company_members for all
  using (user_id = auth.uid()
         or company_id in (select company_id from company_members where user_id = auth.uid()));

drop policy if exists "tenant_isolation_analyses" on company_practice_analyses;
create policy "tenant_isolation_analyses"
  on company_practice_analyses for all
  using (company_id in (select company_id from company_members where user_id = auth.uid()));

drop policy if exists "tenant_isolation_state" on company_practice_state;
create policy "tenant_isolation_state"
  on company_practice_state for all
  using (company_id in (select company_id from company_members where user_id = auth.uid()));

drop policy if exists "tenant_isolation_emails" on company_email_messages;
create policy "tenant_isolation_emails"
  on company_email_messages for all
  using (company_id in (select company_id from company_members where user_id = auth.uid()));

-- `practices` is intentionally world-readable across tenants — same
-- business should dedup to one row regardless of which company first
-- discovered it. Writes still need authentication. Documenting the
-- intent here with a permissive policy.
drop policy if exists "practices_authenticated_read" on practices;
create policy "practices_authenticated_read"
  on practices for select
  using (auth.role() = 'authenticated' or auth.role() = 'service_role');

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
