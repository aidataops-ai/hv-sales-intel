-- One-time cleanup: merge duplicate practice rows that share the same physical
-- business (Google sometimes returns two place_ids for the same listing).
--
-- Dedup key: (lower(trim(name)), lower(trim(address)), digits-only phone).
-- All three must match for two rows to be considered duplicates.
--
-- This script is DEFENSIVE: every optional column is wrapped in a DO block
-- that checks for existence first, so it runs even if your DB hasn't applied
-- every recent migration yet. Safe to re-run.
--
-- HOW TO RUN
-- ----------
-- 1. Run section 1 (PREVIEW) and review the merge plan.
-- 2. Run section 2 (MERGE) inside a transaction. Each step checks column
--    existence; missing columns are silently skipped.
-- 3. If the result looks right, COMMIT. Otherwise ROLLBACK.
-- 4. Run section 3 (DROP TEMP) to clean up.
--
-- If a previous run errored mid-transaction, run `rollback;` first.

-- =============================================================================
-- 0. DIAGNOSTIC — inspect the raw rows that look like duplicates.
--    Run this first to confirm the name/address/phone variations and see
--    what the dedup key actually is for each row. Edit the WHERE clause if
--    you want to look at a different name.
-- =============================================================================

select
  place_id,
  name,
  address,
  phone,
  call_count,
  status,
  -- recompute the dedup key inline so you can see the three parts
  regexp_replace(
    regexp_replace(lower(trim(coalesce(name, ''))), '[^a-z0-9 ]', '', 'g'),
    '\s+', ' ', 'g'
  ) as norm_name,
  regexp_replace(
    regexp_replace(
      regexp_replace(lower(trim(coalesce(address, ''))), ',\s*usa\s*$', '', 'g'),
      '[^a-z0-9 ]', '', 'g'
    ),
    '\s+', ' ', 'g'
  ) as norm_address,
  right(regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g'), 10) as norm_phone
from practices
where lower(name) like '%houston dental care%'   -- ← edit me
order by name, address;


-- =============================================================================
-- 1. PREVIEW — see what would be merged. Run alone first.
-- =============================================================================

-- If you already created _dupe_plan in a previous run, drop it first so the
-- new (tighter) dedup key gets used:
--   drop table if exists _dupe_plan;

create temp table if not exists _dupe_plan as
with normalized as (
  select
    place_id,
    name,
    address,
    phone,
    coalesce(call_count, 0) as cc,
    last_touched_at,
    updated_at,
    -- Normalize the three parts hard so cosmetic differences don't matter:
    --   name    : lowercase, trim, collapse whitespace, strip punctuation
    --   address : lowercase, trim, drop trailing ", USA", collapse whitespace,
    --             strip punctuation
    --   phone   : digits only, then take the RIGHTMOST 10 digits — handles
    --             "+1 7135551234" vs "7135551234"
    regexp_replace(
      regexp_replace(lower(trim(coalesce(name, ''))), '[^a-z0-9 ]', '', 'g'),
      '\s+', ' ', 'g'
    ) || '|' ||
    regexp_replace(
      regexp_replace(
        regexp_replace(lower(trim(coalesce(address, ''))), ',\s*usa\s*$', '', 'g'),
        '[^a-z0-9 ]', '', 'g'
      ),
      '\s+', ' ', 'g'
    ) || '|' ||
    right(regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g'), 10)
      as dedup_key
  from practices
),
ranked as (
  select
    *,
    row_number() over (
      partition by dedup_key
      order by cc desc, last_touched_at desc nulls last, updated_at desc nulls last, place_id
    ) as rn,
    count(*) over (partition by dedup_key) as group_size
  from normalized
)
select
  dedup_key,
  group_size,
  place_id,
  name,
  case when rn = 1 then 'WINNER' else 'LOSER' end as role,
  cc as call_count,
  last_touched_at,
  rn
from ranked
where group_size > 1
order by dedup_key, rn;

-- Summary
select count(distinct dedup_key) as dupe_groups,
       sum(case when role = 'LOSER' then 1 else 0 end) as rows_to_delete
from _dupe_plan;

-- Detailed preview (first 50 rows)
select * from _dupe_plan limit 50;


-- =============================================================================
-- 2. MERGE — copy missing data from losers to winners, then delete losers.
-- =============================================================================
-- Wrap in BEGIN/COMMIT so you can ROLLBACK if anything looks wrong.
-- Each step checks that the target column exists; missing ones are skipped.

begin;

-- Pair each loser with its winner.
create temp table if not exists _dupe_pairs as
select
  w.place_id as winner,
  l.place_id as loser
from _dupe_plan w
join _dupe_plan l using (dedup_key)
where w.role = 'WINNER' and l.role = 'LOSER';


-- Helper: run an UPDATE that copies one column from losers to winner (only
-- when winner's value is NULL). Skips silently if the column doesn't exist.
do $main$
declare
  col text;
  optional_cols text[] := array[
    'salesforce_lead_id',
    'salesforce_lead_url',
    'salesforce_owner_id',
    'salesforce_owner_name',
    'salesforce_synced_at',
    'icp_vertical',
    'icp_tier',
    'summary',
    'pain_points',
    'sales_angles',
    'lead_score',
    'urgency_score',
    'hiring_signal_score',
    'owner_name',
    'owner_email',
    'owner_phone',
    'owner_title',
    'owner_linkedin',
    'enrichment_status',
    'enriched_at',
    'website_doctor_name',
    'website_doctor_phone',
    'website_contacts'
  ];
begin
  foreach col in array optional_cols loop
    if exists (
      select 1 from information_schema.columns
      where table_schema = 'public'
        and table_name = 'practices'
        and column_name = col
    ) then
      execute format($f$
        update practices w
        set %1$I = coalesce(w.%1$I, sub.%1$I)
        from (
          select p.winner, max(l.%1$I) as %1$I
          from _dupe_pairs p
          join practices l on l.place_id = p.loser
          group by p.winner
        ) sub
        where w.place_id = sub.winner;
      $f$, col);
    end if;
  end loop;
end
$main$;


-- 2b) Sum call_count across the group (always exists).
update practices w
set call_count = coalesce(w.call_count, 0) + coalesce(sub.extra, 0)
from (
  select p.winner, sum(coalesce(l.call_count, 0)) as extra
  from _dupe_pairs p
  join practices l on l.place_id = p.loser
  group by p.winner
) sub
where w.place_id = sub.winner;


-- 2c) Concat call_notes (winner first, then non-empty loser notes).
update practices w
set call_notes = case
  when sub.notes is null or sub.notes = '' then w.call_notes
  when w.call_notes is null or w.call_notes = '' then sub.notes
  else w.call_notes || E'\n' || sub.notes
end
from (
  select p.winner,
         string_agg(l.call_notes, E'\n') filter (where l.call_notes is not null and l.call_notes <> '') as notes
  from _dupe_pairs p
  join practices l on l.place_id = p.loser
  group by p.winner
) sub
where w.place_id = sub.winner;


-- 2d) Union tags.
update practices w
set tags = (
  select coalesce(array_agg(distinct t), '{}'::text[])
  from unnest(coalesce(w.tags, '{}'::text[]) || coalesce(sub.loser_tags, '{}'::text[])) t
)
from (
  select p.winner, array_agg(distinct unnest_tag) as loser_tags
  from _dupe_pairs p
  join practices l on l.place_id = p.loser
  cross join lateral unnest(coalesce(l.tags, '{}'::text[])) as unnest_tag
  group by p.winner
) sub
where w.place_id = sub.winner;


-- 2e) email_messages: re-point loser rows to the winner so threads are preserved.
update email_messages em
set practice_id = (select id from practices where place_id = p.winner)
from _dupe_pairs p
join practices loser on loser.place_id = p.loser
where em.practice_id = loser.id;


-- 2f) Delete the losers.
delete from practices where place_id in (select loser from _dupe_pairs);

-- Review row counts. If everything looks right:
--   COMMIT;
-- Otherwise:
--   ROLLBACK;


-- =============================================================================
-- 3. CLEANUP — drop the temp tables.
-- =============================================================================

-- Run AFTER you commit or rollback above:
--   drop table _dupe_plan;
--   drop table _dupe_pairs;
