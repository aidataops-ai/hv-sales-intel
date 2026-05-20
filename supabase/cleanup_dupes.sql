-- One-time cleanup: merge duplicate practice rows that share the same physical
-- business (Google sometimes returns two place_ids for the same listing).
--
-- Dedup key: (lower(trim(name)), lower(trim(address)), digits-only phone).
-- All three must match for two rows to be considered duplicates.
--
-- HOW TO RUN
-- ----------
-- 1. Run section 1 (PREVIEW) and review the merge plan.
-- 2. Run section 2 (MERGE) inside a transaction.
-- 3. If the result looks right, COMMIT. Otherwise ROLLBACK.
-- 4. Run section 3 (DROP TEMP) to clean up.
--
-- The application code (src/storage.py::find_duplicate_place_ids) prevents
-- new dupes; this script cleans up the ones already in the DB.

-- =============================================================================
-- 1. PREVIEW — see what would be merged. Run alone first.
-- =============================================================================

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
    lower(coalesce(trim(name), '')) || '|' ||
    lower(coalesce(trim(address), '')) || '|' ||
    regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g') as dedup_key
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

begin;

-- Pair each loser with its winner.
create temp table if not exists _dupe_pairs as
select
  w.place_id as winner,
  l.place_id as loser
from _dupe_plan w
join _dupe_plan l using (dedup_key)
where w.role = 'WINNER' and l.role = 'LOSER';

-- 2a) Salesforce: if winner has no SF lead but a loser does, copy it over.
update practices w
set
  salesforce_lead_id    = coalesce(w.salesforce_lead_id,    sub.salesforce_lead_id),
  salesforce_lead_url   = coalesce(w.salesforce_lead_url,   sub.salesforce_lead_url),
  salesforce_owner_id   = coalesce(w.salesforce_owner_id,   sub.salesforce_owner_id),
  salesforce_owner_name = coalesce(w.salesforce_owner_name, sub.salesforce_owner_name),
  salesforce_synced_at  = coalesce(w.salesforce_synced_at,  sub.salesforce_synced_at)
from (
  select
    p.winner,
    max(l.salesforce_lead_id)    as salesforce_lead_id,
    max(l.salesforce_lead_url)   as salesforce_lead_url,
    max(l.salesforce_owner_id)   as salesforce_owner_id,
    max(l.salesforce_owner_name) as salesforce_owner_name,
    max(l.salesforce_synced_at)  as salesforce_synced_at
  from _dupe_pairs p
  join practices l on l.place_id = p.loser
  group by p.winner
) sub
where w.place_id = sub.winner;

-- 2b) Sum call_count across the group.
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

-- 2e) Copy missing analysis fields (winner stays winner if it already has them).
update practices w
set
  lead_score          = coalesce(w.lead_score,          sub.lead_score),
  urgency_score       = coalesce(w.urgency_score,       sub.urgency_score),
  hiring_signal_score = coalesce(w.hiring_signal_score, sub.hiring_signal_score),
  icp_vertical        = coalesce(w.icp_vertical,        sub.icp_vertical),
  icp_tier            = coalesce(w.icp_tier,            sub.icp_tier),
  summary             = coalesce(w.summary,             sub.summary),
  pain_points         = coalesce(w.pain_points,         sub.pain_points),
  sales_angles        = coalesce(w.sales_angles,        sub.sales_angles)
from (
  select
    p.winner,
    max(l.lead_score)          as lead_score,
    max(l.urgency_score)       as urgency_score,
    max(l.hiring_signal_score) as hiring_signal_score,
    max(l.icp_vertical)        as icp_vertical,
    max(l.icp_tier)            as icp_tier,
    max(l.summary)             as summary,
    max(l.pain_points)         as pain_points,
    max(l.sales_angles)        as sales_angles
  from _dupe_pairs p
  join practices l on l.place_id = p.loser
  group by p.winner
) sub
where w.place_id = sub.winner;

-- 2f) Copy missing owner enrichment.
update practices w
set
  owner_name        = coalesce(w.owner_name,        sub.owner_name),
  owner_email       = coalesce(w.owner_email,       sub.owner_email),
  owner_phone       = coalesce(w.owner_phone,       sub.owner_phone),
  owner_title       = coalesce(w.owner_title,       sub.owner_title),
  owner_linkedin    = coalesce(w.owner_linkedin,    sub.owner_linkedin),
  enrichment_status = coalesce(w.enrichment_status, sub.enrichment_status),
  enriched_at       = coalesce(w.enriched_at,       sub.enriched_at)
from (
  select
    p.winner,
    max(l.owner_name)        as owner_name,
    max(l.owner_email)       as owner_email,
    max(l.owner_phone)       as owner_phone,
    max(l.owner_title)       as owner_title,
    max(l.owner_linkedin)    as owner_linkedin,
    max(l.enrichment_status) as enrichment_status,
    max(l.enriched_at)       as enriched_at
  from _dupe_pairs p
  join practices l on l.place_id = p.loser
  group by p.winner
) sub
where w.place_id = sub.winner;

-- 2g) email_messages: re-point loser rows to the winner so threads are preserved.
update email_messages em
set practice_id = (select id from practices where place_id = p.winner)
from _dupe_pairs p
join practices loser on loser.place_id = p.loser
where em.practice_id = loser.id;

-- 2h) Delete the losers.
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
