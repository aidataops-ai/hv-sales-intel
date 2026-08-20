-- practice_contacts — one row per person Clay finds at a practice.
--
-- See docs/specs/2026-08-21-practice-contacts.md.
--
-- WHY: Clay is switching from one callback per practice to one callback per
-- PERSON — the same `POST /api/webhooks/clay` now fires once for each contact
-- with {place_id, first_name, last_name, url (LinkedIn), work_email,
-- personal_email, title}. The flat `owner_*` columns on `practices` can hold
-- exactly one contact, so today the second callback overwrites the first and
-- the practice ends up with whichever person happened to arrive last. This
-- table is where the other people go.
--
-- THE INVARIANT: one row per (practice, person-identity). `dedupe_key` is
-- computed in the app — `src/contacts.py::contact_dedupe_key` — with the
-- precedence normalized-LinkedIn -> work_email -> personal_email ->
-- normalized-name, and the pair (practice_id, dedupe_key) is a plain UNIQUE
-- CONSTRAINT rather than a set of partial unique indexes on the identity
-- columns. That is deliberate: the webhook upserts through PostgREST, whose
-- `on_conflict` takes a column list and cannot arbitrate a partial index, so
-- an identity expressed as partial indexes would have no usable conflict
-- target. Computing the key app-side keeps the precedence in one readable
-- function and gives the upsert a single, ordinary conflict target.
--
-- SHARED TABLE — no company_id, by design. A contact at a practice is a fact
-- about the practice, not about the tenant that happened to enrich it, the
-- same precedent as `practices` and `job_postings` (see the rationale at
-- supabase/schema.sql:386-393). RLS below is defence-in-depth only: the
-- backend writes with the service-role key, which bypasses it.
--
-- RELATIONSHIP TO `practices.website_contacts`: they COEXIST. That column is
-- AI-extracted from the practice's own website by `src/analyzer.py` and stored
-- as JSON text; this table is vendor-sourced people from Clay. Different
-- provenance, different trust, different refresh cadence — this table does not
-- supersede or backfill that column in this phase.
--
-- RELATIONSHIP TO `practices.owner_*`: those columns STAY. They become a
-- mirror of the practice's "primary" contact (the first contact row, by
-- (created_at, id), that has a real work email; else the first seen), written
-- by the webhook after each upsert. Every existing consumer — TalentDB push,
-- call scripts, CSV export, the list UI — keeps reading `owner_*` and is
-- untouched by this change.
--
-- NOTHING EXISTING IS MODIFIED — no column is added, dropped, or retyped on
-- `practices`. Idempotent. Re-runnable.
--
-- Rollback: `drop table if exists practice_contacts;` — the `owner_*` mirror
-- survives it, so the pre-change one-contact behaviour is fully intact.

-- ---------------------------------------------------------------
-- 1) practice_contacts — the people.
--
-- `source` is the vendor that produced the row ('clay' today) so a second
-- provider, or a hand-entered contact, can be told apart later without a
-- schema change. `unique (practice_id, dedupe_key)` doubles as the FK-side
-- index: `practice_id` is its leading column, so the "contacts for this
-- practice" read and the `on delete cascade` both get an index scan, and no
-- separate index is warranted.
-- ---------------------------------------------------------------
create table if not exists practice_contacts (
  id             bigserial primary key,
  practice_id    bigint not null references practices(id) on delete cascade,
  first_name     text,
  last_name      text,
  title          text,
  linkedin_url   text,
  work_email     text,
  personal_email text,
  source         text not null default 'clay',
  dedupe_key     text not null,   -- app-computed; src/contacts.py::contact_dedupe_key
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (practice_id, dedupe_key)
);

comment on table practice_contacts is
  'One row per person Clay returns for a practice. Shared across tenants like '
  '`practices`. Identity is (practice_id, dedupe_key), the key computed in '
  'src/contacts.py::contact_dedupe_key. Coexists with the AI-extracted '
  'practices.website_contacts; practices.owner_* mirrors the primary contact. '
  'See docs/specs/2026-08-21-practice-contacts.md.';

-- ---------------------------------------------------------------
-- 2) RLS — shared-table read, mirroring `practices` (schema.sql:386-393).
--
-- Authenticated read only; writes arrive through the service-role key. The
-- `auth.role()` call is wrapped in a scalar subselect so the planner hoists it
-- into a once-per-statement InitPlan instead of re-evaluating it per row —
-- same form as every other policy in this schema, see
-- supabase/migrations/2026-08-15-rls-initplan-fk-indexes.sql.
-- ---------------------------------------------------------------
alter table practice_contacts enable row level security;

drop policy if exists "practice_contacts_authenticated_read" on practice_contacts;
create policy "practice_contacts_authenticated_read"
  on practice_contacts for select
  using ((select auth.role()) = 'authenticated' or (select auth.role()) = 'service_role');

-- ---------------------------------------------------------------
-- Sanity report — 0 on a fresh apply; the webhook fills it.
-- ---------------------------------------------------------------
select count(*) as contacts from practice_contacts;
