-- Performance: RLS initplan + FK indexes on real join paths.
--
-- See docs/refactor/supabase-data-layer.md §5 (Supabase advisors, 2026-08-15).
--
-- THE INITPLAN PROBLEM. `auth.uid()` and `auth.role()` (and `current_setting()`,
-- which they wrap) are STABLE, not IMMUTABLE, so when they appear as a bare call
-- inside an RLS `USING` expression the planner treats them as part of the per-row
-- filter and re-executes them for every row the policy is checked against. On a
-- 200k-row `job_postings` scan that is 200k function calls plus 200k re-runs of
-- the `company_members` membership subquery hanging off them. Wrapping the call
-- in a scalar subselect — `(select auth.uid())` — makes the planner hoist it into
-- an InitPlan that runs exactly ONCE per statement and folds the result into the
-- row filter as a constant. The predicate is semantically identical (a scalar
-- subselect over a single STABLE call returns the same value inside one
-- statement); only the evaluation count changes. This migration therefore
-- reproduces every listed policy's existing USING clause verbatim and changes
-- nothing but that wrapping.
--
-- Idempotent / re-runnable: `ALTER POLICY ... USING` is declarative (re-applying
-- the same expression is a no-op) and the indexes use `if not exists`. No policy
-- is dropped, so there is never a window where a table sits unprotected.
--
-- Prerequisite migrations (the policies must already exist):
--   supabase/schema.sql                                (companies, company_members,
--                                                       company_practice_analyses,
--                                                       company_practice_state,
--                                                       company_email_messages, practices)
--   supabase/migrations/2026-05-29-credits.sql         (credit_transactions)
--   supabase/migrations/2026-08-05-job-posting-leads.sql
--                                                      (job_postings, company_job_leads,
--                                                       company_search_targets)
--   supabase/migrations/2026-08-13-target-dimensions.sql
--                                                      (search_terms, search_locations,
--                                                       target_runs, target_overrides)


-- ---------------------------------------------------------------
-- 1) RLS initplan — 14 policies, ALTER only (no drop/create needed:
--    every one of them changes only its USING expression, and none of
--    them carries a WITH CHECK clause, so `ALTER POLICY ... USING`
--    expresses the whole change and leaves WITH CHECK untouched/NULL —
--    which for a FOR ALL policy means the USING expression keeps
--    doubling as the write check, exactly as before).
-- ---------------------------------------------------------------

-- companies: a user can see / edit a company iff they're a member of it.
alter policy "tenant_membership_companies" on companies
  using (id in (select company_id from company_members where user_id = (select auth.uid())));

-- company_members: your own membership row, plus the rosters of your companies.
alter policy "tenant_membership_members" on company_members
  using (user_id = (select auth.uid())
         or company_id in (select company_id from company_members where user_id = (select auth.uid())));

alter policy "tenant_isolation_analyses" on company_practice_analyses
  using (company_id in (select company_id from company_members where user_id = (select auth.uid())));

alter policy "tenant_isolation_state" on company_practice_state
  using (company_id in (select company_id from company_members where user_id = (select auth.uid())));

alter policy "tenant_isolation_emails" on company_email_messages
  using (company_id in (select company_id from company_members where user_id = (select auth.uid())));

-- practices stays intentionally world-readable across tenants (same business
-- dedups to one row); the policy only asserts authentication. Two calls, two
-- subselects — kept as an OR of two equalities rather than folded into an
-- IN-list so the predicate stays literally the one that shipped.
alter policy "practices_authenticated_read" on practices
  using ((select auth.role()) = 'authenticated' or (select auth.role()) = 'service_role');

alter policy "tenant_credit_tx_read" on credit_transactions
  using (company_id in (select company_id from company_members where user_id = (select auth.uid())));

-- job_postings is the shared discovery universe — same read-if-authenticated
-- shape as practices. This is the hottest table in the app, so it is also where
-- the per-row re-evaluation cost was largest.
alter policy "job_postings_authenticated_read" on job_postings
  using ((select auth.role()) = 'authenticated' or (select auth.role()) = 'service_role');

alter policy "tenant_isolation_job_leads" on company_job_leads
  using (company_id in (
    select company_id from company_members where user_id = (select auth.uid())
  ));

alter policy "tenant_isolation_search_targets" on company_search_targets
  using (company_id in (
    select company_id from company_members where user_id = (select auth.uid())
  ));

-- Dimension tables (2026-08-13-target-dimensions.sql).
alter policy "tenant_isolation_search_terms" on search_terms
  using (company_id in (
    select company_id from company_members where user_id = (select auth.uid())
  ));

alter policy "tenant_isolation_search_locations" on search_locations
  using (company_id in (
    select company_id from company_members where user_id = (select auth.uid())
  ));

-- target_runs / target_overrides have no company_id of their own; tenancy is
-- reached through search_terms. The EXISTS body is unchanged — only the
-- auth.uid() leaf is hoisted.
alter policy "tenant_isolation_target_runs" on target_runs
  using (exists (
    select 1 from search_terms t
    join company_members m on m.company_id = t.company_id
    where t.id = target_runs.term_id and m.user_id = (select auth.uid())
  ));

alter policy "tenant_isolation_target_overrides" on target_overrides
  using (exists (
    select 1 from search_terms t
    join company_members m on m.company_id = t.company_id
    where t.id = target_overrides.term_id and m.user_id = (select auth.uid())
  ));

-- NOTE: no policy in this repo uses `current_setting(...)` directly — the
-- advisor's `current_setting` hits are all inside Supabase's own `auth.uid()` /
-- `auth.role()` helpers, so hoisting the helper call hoists the setting lookup
-- with it. If a hand-written `current_setting('request.jwt.claims', true)`
-- policy is ever added, it needs the same `(select ...)` wrapping.
--
-- NOTE: `credit_transactions.tenant_credit_tx_no_direct_write` is deliberately
-- untouched — its `with check (false)` is a constant and has no initplan cost.


-- ---------------------------------------------------------------
-- 2) Unindexed FKs — join-path ones only.
--
-- Postgres does not auto-index the referencing side of a foreign key. These
-- four columns are all read as join keys (practice detail joins state and
-- analyses back to practices; the collector joins runs/overrides to
-- search_locations), and they are also what makes `on delete cascade` do a
-- seq scan per deleted parent row.
--
-- Plain (non-CONCURRENT) creates: the Supabase migration runner wraps each
-- migration in a transaction, and `create index concurrently` cannot run
-- inside one. These tables are small enough on the Nano instance that the
-- brief ACCESS EXCLUSIVE hold is acceptable; if company_practice_state grows
-- past a few million rows, run its index by hand with CONCURRENTLY instead.
-- ---------------------------------------------------------------

create index if not exists idx_target_runs_location
  on target_runs (location_id);

create index if not exists idx_target_overrides_location
  on target_overrides (location_id);

create index if not exists idx_cps_practice
  on company_practice_state (practice_id);

create index if not exists idx_cpa_practice
  on company_practice_analyses (practice_id);

-- DELIBERATELY SKIPPED — the user-audit FK columns the advisor also flags:
--   company_practice_state.assigned_by, .last_touched_by, .last_exported_by
--   company_job_leads.last_touched_by, .last_exported_by
--   companies.created_by
--   usage_events.user_id, credit_transactions.user_id
-- These are write-time attribution stamps. Nothing joins on them and nothing
-- filters by them (the UI filters by `assigned_to`, which is already covered by
-- idx_cps_company_assigned); an auth.users row is essentially never deleted, so
-- the cascade argument doesn't apply either. Indexing them would only add write
-- amplification on the hottest write paths. Revisit only if an
-- "activity by user" view ever ships.


-- ---------------------------------------------------------------
-- 3) Unused indexes — NOT dropped here.
--
-- The advisor's "unused index" list is measured against TODAY's query shapes,
-- and §2/§3 of the refactor plan (column lists, facet aggregates, server-side
-- city filtering, the anti-join RPC) deliberately change those shapes. Dropping
-- now would very likely delete indexes the post-refactor queries want back.
-- Candidates to re-check AFTER the query diet lands and pg_stat_user_indexes
-- has a week of fresh counts:
--   idx_practices_city         (may become used once the matcher's city filter
--                               is pushed server-side — see plan §2)
--   idx_practices_category
--   idx_practices_score
--   idx_practices_sf_lead_id
--   idx_cpa_company_vertical
--   idx_cps_company_sf
--   idx_job_postings_seen
--   idx_credit_tx_kind
-- Treat this list as illustrative, not authoritative: re-run
-- `select * from pg_stat_user_indexes where idx_scan = 0` (or the Supabase
-- advisor) at drop time rather than trusting these names.


-- ---------------------------------------------------------------
-- Sanity report — every listed policy should show initplan = true, and all
-- four indexes should be present.
-- ---------------------------------------------------------------
select
  tablename,
  policyname,
  -- pg_get_expr deparses a scalar subselect as "( SELECT auth.uid() AS uid)";
  -- the regex tolerates whitespace differences across PG versions.
  (qual ~ '\(\s*SELECT auth\.') as initplan_hoisted
from pg_policies
where schemaname = 'public'
  and policyname in (
  'tenant_membership_companies', 'tenant_membership_members',
  'tenant_isolation_analyses', 'tenant_isolation_state',
  'tenant_isolation_emails', 'practices_authenticated_read',
  'tenant_credit_tx_read', 'job_postings_authenticated_read',
  'tenant_isolation_job_leads', 'tenant_isolation_search_targets',
  'tenant_isolation_search_terms', 'tenant_isolation_search_locations',
  'tenant_isolation_target_runs', 'tenant_isolation_target_overrides'
)
order by tablename, policyname;

select indexname
from pg_indexes
where schemaname = 'public'
  and indexname in (
  'idx_target_runs_location', 'idx_target_overrides_location',
  'idx_cps_practice', 'idx_cpa_practice'
)
order by indexname;
