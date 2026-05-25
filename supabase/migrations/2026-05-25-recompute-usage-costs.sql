-- One-shot recompute of usage_events.cost_cents using the corrected
-- pricing constants (gpt-4o moved 250/1000 → 500/1500). Run this in
-- the Supabase SQL editor if you don't want to click the "Recompute
-- costs" button on /admin/usage.
--
-- Idempotent: re-running with the same pricing is a no-op.
-- Skips rows where the recompute would be identical.

with priced as (
  select
    id,
    case
      when kind like 'openai_%' then (
        coalesce(input_tokens, 0) * case lower(coalesce(model, ''))
          when 'gpt-4.1'      then 200
          when 'gpt-4.1-mini' then 40
          when 'gpt-4o'       then 500
          when 'gpt-4o-mini'  then 15
          else                     200   -- default band (mirrors gpt-4.1)
        end / 1000000.0
        + coalesce(output_tokens, 0) * case lower(coalesce(model, ''))
          when 'gpt-4.1'      then 800
          when 'gpt-4.1-mini' then 160
          when 'gpt-4o'       then 1500
          when 'gpt-4o-mini'  then 60
          else                     800
        end / 1000000.0
      )
      when kind = 'places_search'  then coalesce(calls, 1) * 3.2
      when kind = 'places_details' then coalesce(calls, 1) * 1.7
      else cost_cents
    end as new_cost
  from usage_events
)
update usage_events u
set    cost_cents = round(p.new_cost::numeric, 4)
from   priced p
where  u.id = p.id
  and  abs(coalesce(u.cost_cents, 0) - p.new_cost) > 0.0001;

-- Sanity check: should report > 0 rows changed for gpt-4o entries that
-- existed before the pricing fix; 0 if there were no historical events.
select count(*) as updated_rows_estimate from usage_events
 where lower(coalesce(model, '')) = 'gpt-4o';
