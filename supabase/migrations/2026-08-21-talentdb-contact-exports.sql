-- talentdb_contact_exports — one row per (lead, contact) already pushed.
--
-- See docs/specs/2026-08-21-practice-contacts.md.
--
-- WHY: the Talent-DB push fans out. A lead (practice + posting) with N contact
-- rows becomes N Talent-DB leads — same company/posting/scoring envelope, a
-- different person mapped into FirstName/LastName/Title/Email/Phone each time.
-- The existing lead-level marker, `company_job_leads.talentdb_exported_at`, is
-- one timestamp per (company, posting): it can say "this lead is done", but it
-- cannot say "we sent Ada and Grace, and the POST for Alan timed out". Without
-- a per-person record a retry either re-sends everybody (duplicate leads on the
-- receiver, which mints a fresh record per POST — we send no salesforceId) or
-- re-sends nobody (Alan is lost). This table is that record.
--
-- THE TWO MARKERS, AND WHICH ONE GATES WHAT:
--   * `company_job_leads.talentdb_exported_at` stays THE lead-level gate. It is
--     set only when EVERY eligible contact on the lead was accepted, so a lead
--     with a partial failure stays in the un-exported universe and gets picked
--     up by the next run.
--   * `talentdb_contact_exports` is the per-person record inside that lead. The
--     retry consults it and skips the people already accepted, so re-entering a
--     partially-failed lead posts only what is missing.
-- The lead marker is therefore derivable-looking but NOT derived: it answers
-- "is this lead finished", which needs the eligible set at send time, not the
-- sent set after the fact. Both are kept.
--
-- `--resend` (and `resend_contacts=True` in src/talentdb_push.py) is the escape
-- hatch that re-enters an already-marked lead; it still consults this table, so
-- the natural use is a LATE-ARRIVING CONTACT — Clay finds a third person a week
-- after the first two shipped, and the re-run posts only that third person.
--
-- SHAPE: `(lead_id, contact_id)` unique — the grain of one Talent-DB POST.
-- `lead_id` is per-(company, posting), so the tenant scoping comes free through
-- the FK and no `company_id` is duplicated here. Both FKs cascade: dropping a
-- lead or a contact drops the fact that we sent it, which is correct — the
-- receiver's copy is not ours to reconcile, and a resurrected row is a new
-- person as far as this side is concerned.
--
-- NOTHING EXISTING IS MODIFIED — no column is added, dropped, or retyped.
-- Idempotent. Re-runnable.
--
-- Rollback: `drop table if exists talentdb_contact_exports;` — the lead-level
-- marker survives it, so the pre-change one-lead-per-posting dedup is fully
-- intact and the push degrades to "re-send every contact on a retry".

-- ---------------------------------------------------------------
-- 1) talentdb_contact_exports — the people we have already pushed.
--
-- `unique (lead_id, contact_id)` doubles as the index for the read that runs
-- before every fan-out ("which contacts on this lead are already sent?"):
-- `lead_id` is its leading column. `contact_id` gets its own index below only
-- because it is an unindexed FK otherwise — the `on delete cascade` from
-- `practice_contacts` would be a sequential scan without it.
-- ---------------------------------------------------------------
create table if not exists talentdb_contact_exports (
  id          bigserial primary key,
  lead_id     bigint not null references company_job_leads(id) on delete cascade,
  contact_id  bigint not null references practice_contacts(id) on delete cascade,
  exported_at timestamptz not null default now(),
  unique (lead_id, contact_id)
);

create index if not exists idx_tce_contact on talentdb_contact_exports (contact_id);

comment on table talentdb_contact_exports is
  'One row per (company_job_leads.id, practice_contacts.id) already POSTed to '
  'Talent-DB. Partial-failure and resend safety for the per-contact fan-out; '
  'company_job_leads.talentdb_exported_at remains the lead-level gate and is '
  'set only when every eligible contact succeeded. '
  'See docs/specs/2026-08-21-practice-contacts.md.';

-- ---------------------------------------------------------------
-- 2) RLS — tenant isolation through the lead, mirroring company_job_leads.
--
-- No `company_id` column here, so membership is checked one hop away via
-- `lead_id`. Defence-in-depth only: the backend writes with the service-role
-- key, which bypasses it. `auth.uid()` is wrapped in a scalar subselect so the
-- planner hoists it into a once-per-statement InitPlan instead of re-evaluating
-- it per row — same form as every other policy in this schema, see
-- supabase/migrations/2026-08-15-rls-initplan-fk-indexes.sql.
-- ---------------------------------------------------------------
alter table talentdb_contact_exports enable row level security;

drop policy if exists "tenant_isolation_contact_exports" on talentdb_contact_exports;
create policy "tenant_isolation_contact_exports"
  on talentdb_contact_exports for select
  using (lead_id in (
    select l.id from company_job_leads l
    where l.company_id in (select company_id from company_members
                           where user_id = (select auth.uid()))
  ));

-- ---------------------------------------------------------------
-- Sanity report — 0 on a fresh apply; the fan-out fills it.
-- ---------------------------------------------------------------
select count(*) as contact_exports from talentdb_contact_exports;
