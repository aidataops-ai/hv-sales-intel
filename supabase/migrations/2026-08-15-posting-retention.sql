-- Posting-description retention — reclaim the growth driver under the 500 MB cap.
--
-- See docs/refactor/supabase-data-layer.md, "Deployment reality" item 2.
--
-- WHY
-- ---
-- The Supabase free tier caps the database at 500 MB. We sit at ~105 MB today
-- and `job_postings` (45 MB) is the growth driver: the collector stores every
-- posting it sees, the qualifier judges every posting once per tenant, and the
-- full `description` body is kept forever — for discards too, and discards
-- outnumber keeps roughly 13:1. Nothing ever deletes.
--
-- The body text of a DISCARDED posting has no reader. What analytics needs is
-- the verdict row in `company_job_leads` (decision, band, reason, reject
-- reason, track) — and that row is untouched here. Nulling `description` is
-- also what keeps the posting row itself alive, which matters: the row is the
-- record that this posting was already judged, and deleting it would let the
-- qualifier re-claim and re-bill the same posting on the next run.
--
-- THE INVARIANT (the whole point of this file)
-- --------------------------------------------
-- A posting's description is pruned only when ALL of these hold:
--
--   1. it has at least one verdict in `company_job_leads`, AND
--   2. EVERY verdict on it, ACROSS ALL TENANTS, is `decision = 'discard'`, AND
--   3. it has not been seen by the collector for `days` (default 30), AND
--   4. its description is not already null.
--
-- Restated as the two things that must never happen:
--
--   * An UNJUDGED posting is never touched. `lead_qualifier._posting_row`
--     builds its prompt snippet from `description`, and `claim_unqualified`
--     hands it exactly the postings this tenant has no verdict for. Pruning an
--     unjudged posting would silently degrade every future verdict on it to
--     title-and-employer only. Condition (1) is that guarantee.
--
--   * A KEPT posting is never touched — for any tenant. Postings are shared
--     across tenants (ADR-04) while verdicts are tenant-private, so tenant A
--     discarding a posting says nothing about tenant B, whose operator may
--     have it open in the signals detail view, or be about to push it to
--     Talent-DB as `posting_description`. Condition (2) is written as
--     "no verdict that is anything other than 'discard' exists", which is
--     strictly safer than "a discard exists": it also protects a row whose
--     `decision` is NULL (the column is nullable; the qualifier drops rows it
--     can't parse a decision for, so this should not occur — but the invariant
--     should not depend on that).
--
-- THE ONE RESIDUAL RISK, STATED HONESTLY
-- ---------------------------------------
-- "Judged" is per-tenant, so a posting every *current* tenant discarded is
-- prunable even though a *different* tenant has never judged it — and
-- `claim_unqualified` is a per-tenant anti-join, so that tenant could still
-- claim it and qualify it against a NULL snippet. Two things bound this:
-- `claim_unqualified` scans `job_postings` newest-first over a bounded window
-- (`limit * 20`), so 30-day-stale postings are far outside it in practice; and
-- (3) below only prunes postings the boards have stopped returning, which are
-- exactly the ones nobody wants re-qualified. If a second tenant is ever
-- onboarded onto a large existing posting universe, raise `days` for that
-- window or pause the cron — do not narrow the invariant.
--
-- WHY `last_seen_at` AND NOT `first_seen_at` / `posted_at`
-- --------------------------------------------------------
-- `upsert_postings` (src/lead_store.py) re-upserts the FULL row — description
-- included — every time a board returns a posting again, and stamps
-- `last_seen_at` while leaving `first_seen_at` at its insert default. Pruning
-- on `first_seen_at` would therefore fight the collector: null the body today,
-- the next sweep writes it back, and we pay the write amplification and the
-- dead tuples for zero net space. `last_seen_at` inverts that — a posting the
-- collector has not returned in 30 days is off the boards, so nothing will
-- resurrect it. `posted_at` is board-supplied and nullable, so it is not a
-- reliable clock for this.
--
-- SPACE IS RECLAIMED LAZILY. `description` is TOASTed; setting it to NULL
-- makes the old value dead, and autovacuum returns the space to the table (not
-- to the OS). If you need the disk back immediately after the backfill, run
-- `vacuum (analyze, verbose) job_postings;` — or `vacuum full`, which takes an
-- ACCESS EXCLUSIVE lock and needs free space equal to the table, so only do
-- that from a maintenance window, never from this migration (VACUUM cannot run
-- inside a transaction block).
--
-- INDEXES: none added. The scan is served by `idx_job_postings_seen`
-- (last_seen_at desc) and both EXISTS subqueries by `idx_leads_posting`
-- (posting_id), both from 2026-08-05-job-posting-leads.sql.
--
-- Idempotent. Re-runnable. Safe to run twice: the function is CREATE OR
-- REPLACE, the cron job is dropped before it is scheduled, and the backfill
-- prunes nothing on a second pass because condition (4) already excludes
-- every row it touched.


-- ---------------------------------------------------------------
-- 1) The prune function.
--
-- Returns the number of postings pruned, so the backfill below and every cron
-- run leave a countable trace.
--
-- SECURITY DEFINER with a pinned `search_path`: `job_postings` has RLS enabled
-- (2026-08-05), and pg_cron runs jobs as the database owner. Definer rights
-- make the behaviour identical whether the function is invoked by cron or by
-- the backend's service-role key, and the pinned search_path is what stops a
-- caller-controlled `search_path` from redirecting the table names. EXECUTE is
-- revoked from everyone except service_role — it is a bulk mutation, and no
-- authenticated end user has any business calling it.
-- ---------------------------------------------------------------
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
       -- the body back. See the `last_seen_at` note in the header. A NULL
       -- `last_seen_at` (shouldn't happen — every upsert stamps it) makes this
       -- NULL and excludes the row, which is the safe direction.
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


-- ---------------------------------------------------------------
-- 2) Schedule it daily via pg_cron.
--
-- Both blocks are guarded rather than bare statements: `create extension`
-- needs privileges this file may not be applied with, and the whole migration
-- (function + backfill) is still worth applying on an instance where pg_cron
-- is unavailable — the operator can then schedule it externally. A NOTICE is
-- raised instead of an error in that case.
--
-- 04:17 UTC is deliberately off the hour: the collect/qualify crons run on the
-- hour, and a bulk UPDATE competing with them for the free tier's shared-CPU
-- Nano instance is the one way this maintenance job could be user-visible.
--
-- TO UNSCHEDULE:
--     select cron.unschedule('prune-discarded-posting-descriptions');
-- TO INSPECT:
--     select * from cron.job where jobname = 'prune-discarded-posting-descriptions';
--     select * from cron.job_run_details order by start_time desc limit 20;
-- TO CHANGE THE WINDOW: re-run this file with a different `days` argument in
-- the schedule below — it drops and re-creates the job by name.
-- ---------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_extension where extname = 'pg_cron') then
    if not exists (select 1 from pg_available_extensions where name = 'pg_cron') then
      raise notice 'pg_cron is not available on this instance — schedule '
                   'prune_discarded_posting_descriptions() externally.';
      return;
    end if;
    execute 'create extension if not exists pg_cron';
  end if;
exception
  when insufficient_privilege then
    raise notice 'Not privileged to create pg_cron — enable it in the Supabase '
                 'dashboard (Database > Extensions), then re-run this file.';
end $$;

do $$
begin
  -- Guarded on the catalog, not on `to_regproc('cron.schedule')`: pg_cron
  -- ships two overloads of that name, and `to_regproc` raises "more than one
  -- function named" on an ambiguous lookup rather than returning NULL. Every
  -- cron reference below is dynamic (EXECUTE), so this block is a clean no-op
  -- on an instance without pg_cron instead of a hard failure.
  if not exists (select 1 from pg_extension where extname = 'pg_cron') then
    raise notice 'pg_cron not installed — skipping the daily schedule.';
    return;
  end if;

  -- Idempotent (re)schedule. `cron.unschedule(name)` RAISES when the job does
  -- not exist, so drop it by id through a query that simply matches no rows on
  -- a first run.
  execute $q$
    select cron.unschedule(jobid)
      from cron.job
     where jobname = 'prune-discarded-posting-descriptions'
  $q$;

  execute $q$
    select cron.schedule(
      'prune-discarded-posting-descriptions',
      '17 4 * * *',
      $job$select public.prune_discarded_posting_descriptions(30)$job$
    )
  $q$;
end $$;


-- ---------------------------------------------------------------
-- 3) One-off backfill.
--
-- Prunes the whole existing backlog in one statement — this is the call that
-- actually reclaims the current 45 MB table's dead weight. Re-running it is
-- harmless (it returns 0 once there is nothing left to prune).
-- ---------------------------------------------------------------
select public.prune_discarded_posting_descriptions(30) as backfill_pruned;


-- ---------------------------------------------------------------
-- Sanity report
--
-- `protected_kept` and `protected_unjudged` are the invariant, stated as
-- numbers: both must stay non-zero-ish and, more importantly, `with_body`
-- must never dip below their sum.
-- ---------------------------------------------------------------
select
  (select count(*) from job_postings)                              as postings,
  (select count(*) from job_postings where description is not null) as with_body,
  (select count(*) from job_postings p
    where exists (select 1 from company_job_leads l
                   where l.posting_id = p.id and l.decision = 'keep'))
                                                                   as protected_kept,
  (select count(*) from job_postings p
    where not exists (select 1 from company_job_leads l
                       where l.posting_id = p.id))                 as protected_unjudged;
