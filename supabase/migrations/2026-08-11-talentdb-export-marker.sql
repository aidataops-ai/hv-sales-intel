-- Talent-DB "Import Lead" export marker.
--
-- One posting is exported to Talent-DB once per company. This timestamp is the
-- dedup key: set on a successful push, checked before the next one, so repeat
-- clicks (and the practice-detail + signals paths landing on the same posting)
-- don't create duplicate leads on the receiving side — we send no salesforceId,
-- so there is no upsert key on their end to rely on.
--
-- Lives on company_job_leads because export is a per-(company, posting) action,
-- the same grain as the lead itself. NULL = never exported. We store only this
-- marker, none of the data Talent-DB returns.
--
-- See docs/specs/2026-08-11-talentdb-lead-webhook-design.md. Idempotent.

alter table company_job_leads
  add column if not exists talentdb_exported_at timestamptz;
