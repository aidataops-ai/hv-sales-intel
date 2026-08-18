-- Cleanup: drop the two dead prototype tables.
--
-- `leads` (6,708 rows) and `hot_leads` (105 rows) are leftovers from the
-- pre-multitenant prototype. The live lead pipeline writes tenant-scoped
-- verdicts to `company_job_leads` (2026-08-05-job-posting-leads.sql); these
-- two tables have had ZERO code references for the entire current codebase —
-- verified 2026-08-17 by grepping every `.table(...)` call site in src/,
-- api/ and scripts/, and corroborated by the Supabase advisor flagging
-- `idx_leads_created` as never used.
--
-- Dependency audit against the live database (2026-08-17), all clear:
--   * foreign keys referencing either table ..... 0
--   * views depending on either table ........... 0
--   * RLS policies on either table .............. none
--   * triggers ................................... 1, owned by the table
--     itself (dropped with it)
--
-- Deliberately NO `cascade`: if some future object grows a dependency on
-- these tables between this file being written and being applied, the drop
-- should fail loudly rather than silently take the dependent with it.
--
-- THIS DELETES THE ROWS PERMANENTLY. They are prototype-era data with no
-- reader; if a copy is wanted anyway, snapshot before applying:
--   \copy (select * from public.leads)     to 'leads-archive.csv'     csv header
--   \copy (select * from public.hot_leads) to 'hot_leads-archive.csv' csv header
--
-- NOT in this file, on purpose: `company_search_targets` (the old pipeline's
-- matrix). Production still runs the old collector against it — it is the
-- rollback plan, and its drop is a separate migration gated on the
-- staging→main promotion plus a soak period (see
-- docs/refactor/instant-signals-targets.md §6).

drop table if exists public.hot_leads;
drop table if exists public.leads;

-- Sanity report — both selects should return zero rows after apply.
select relname
from pg_class
where relname in ('leads', 'hot_leads') and relkind = 'r';
