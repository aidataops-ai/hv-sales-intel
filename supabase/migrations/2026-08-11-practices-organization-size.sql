-- Organization size on practices — headcount of the business (number of people
-- in the organization). Populated by enrichment; feeds the Talent-DB webhook's
-- `organization_size` field and the signals CSV export. Nullable — omitted from
-- the payload until enrichment fills it. Idempotent.

alter table practices
  add column if not exists organization_size integer;
