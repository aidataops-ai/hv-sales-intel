-- Job-Posting Leads — drop the multi-stage `status` workflow and assignment.
--
-- The base migration (2026-08-05-job-posting-leads.sql) uses
-- `create table if not exists`, so it never alters a table that already exists.
-- This ALTER migration brings a deployed `company_job_leads` in line with the
-- current schema: the six-state `status` pipeline is replaced by a lightweight
-- `disposition` flag (undecided/approved/rejected), and `assigned_to` /
-- `assigned_at` are removed. See
-- docs/specs/2026-08-05-hiring-signal-collector-design.md.
--
-- Idempotent. Re-runnable. A no-op on a fresh install seeded from the base
-- migration, whose table is already created with `disposition` and no
-- assignment columns.

-- 1) New disposition column, backfilled from the old status pipeline. Every
--    state past the approve gate (approved/contacted/replied/booked) collapses
--    to 'approved'; 'rejected' stays; everything else ('new', or a fresh table)
--    lands at 'undecided'.
alter table company_job_leads
  add column if not exists disposition text not null default 'undecided';

do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_name = 'company_job_leads' and column_name = 'status'
  ) then
    update company_job_leads set disposition = case
      when status = 'rejected' then 'rejected'
      when status in ('approved','contacted','replied','booked') then 'approved'
      else 'undecided'
    end;
  end if;
end $$;

alter table company_job_leads
  drop constraint if exists company_job_leads_disposition_check;
alter table company_job_leads
  add constraint company_job_leads_disposition_check
  check (disposition in ('undecided','approved','rejected'));

-- 2) Drop the indexes that reference the columns we're removing.
drop index if exists idx_leads_feed;      -- keyed on status
drop index if exists idx_leads_assigned;  -- keyed on assigned_to

-- 3) Drop the old workflow columns.
alter table company_job_leads drop column if exists status;
alter table company_job_leads drop column if exists assigned_to;
alter table company_job_leads drop column if exists assigned_at;

-- 4) Recreate the feed index on disposition.
create index if not exists idx_leads_feed
  on company_job_leads (company_id, disposition, band_rank, created_at desc);
