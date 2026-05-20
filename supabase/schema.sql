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

-- Search query cache (avoid re-billing Google for repeated queries)
create table if not exists searches (
  id bigserial primary key,
  query_norm text unique not null,
  query_raw text not null,
  place_ids text[] not null,
  searched_at timestamptz default now()
);
create index if not exists idx_searches_query_norm on searches (query_norm);

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
