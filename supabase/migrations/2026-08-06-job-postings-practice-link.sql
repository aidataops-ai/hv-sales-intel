-- Job postings -> practices link.
--
-- A posting's employer IS a practice in the Places universe. That mapping is a
-- property of the posting itself (shared across tenants, like job_postings),
-- not of any one tenant's lead — so it lives here, not on company_job_leads.
--
-- Written by scripts/link_postings.py: normalised employer_name_norm matched to
-- a same-city practice.name, scoped to KEPT INDEPENDENT leads (the only
-- population where the link is well-defined — a hospital system has no single
-- place_id to point at). Auto-linked at >= 0.90, flagged 'review' at 0.80-0.90.
--
-- Nullable throughout: most postings never link (systems, unscanned cities,
-- practices below the Places 60-cap). Idempotent.

alter table job_postings
  add column if not exists practice_id      bigint references practices(id) on delete set null,
  add column if not exists match_confidence numeric(3, 2),
  add column if not exists match_status     text check (match_status in ('auto', 'review')),
  add column if not exists match_method     text,   -- e.g. 'name_city_v1' — provenance for re-matching
  add column if not exists matched_at       timestamptz;

create index if not exists idx_job_postings_practice on job_postings (practice_id);
