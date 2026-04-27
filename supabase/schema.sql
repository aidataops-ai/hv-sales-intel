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
  role text not null default 'rep' check (role in ('admin', 'rep')),
  created_at timestamptz default now()
);

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, email, name, role)
  values (new.id, new.email, new.raw_user_meta_data->>'name', 'rep')
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
