-- Adds the cached_input_tokens column to usage_events and recomputes
-- cost_cents using the latest OpenAI pricing bands (per the screenshot
-- from developers.openai.com/api/docs/pricing). Idempotent. Re-runnable.
--
-- Pricing reference (¢ per 1M tokens, from src/usage.py):
--   o4-mini       input=400  cached=100   output=1600
--   gpt-4.1       input=300  cached=75    output=1200
--   gpt-4.1-mini  input=80   cached=20    output=320
--   gpt-4.1-nano  input=20   cached=5     output=80
--   gpt-4o        input=375  cached=187.5 output=1500
--   gpt-4o-mini   input=30   cached=15    output=120
-- Places: text-search = 3.2¢/call, place-details = 1.7¢/call.

alter table usage_events
  add column if not exists cached_input_tokens int;


with bands as (
  select
    id,
    coalesce(input_tokens, 0)        as in_tok,
    coalesce(output_tokens, 0)       as out_tok,
    coalesce(cached_input_tokens, 0) as cached_tok,
    case lower(coalesce(model, ''))
      when 'o4-mini'      then 400
      when 'gpt-4.1'      then 300
      when 'gpt-4.1-mini' then 80
      when 'gpt-4.1-nano' then 20
      when 'gpt-4o'       then 375
      when 'gpt-4o-mini'  then 30
      else                     300
    end as input_rate,
    case lower(coalesce(model, ''))
      when 'o4-mini'      then 100
      when 'gpt-4.1'      then 75
      when 'gpt-4.1-mini' then 20
      when 'gpt-4.1-nano' then 5
      when 'gpt-4o'       then 187.5
      when 'gpt-4o-mini'  then 15
      else                     75
    end as cached_rate,
    case lower(coalesce(model, ''))
      when 'o4-mini'      then 1600
      when 'gpt-4.1'      then 1200
      when 'gpt-4.1-mini' then 320
      when 'gpt-4.1-nano' then 80
      when 'gpt-4o'       then 1500
      when 'gpt-4o-mini'  then 120
      else                     1200
    end as output_rate,
    kind,
    calls,
    cost_cents
  from usage_events
),
priced as (
  select
    id,
    case
      when kind like 'openai_%' then (
        greatest(in_tok - cached_tok, 0) * input_rate / 1000000.0
        + cached_tok * cached_rate / 1000000.0
        + out_tok * output_rate / 1000000.0
      )
      when kind = 'places_search'  then coalesce(calls, 1) * 3.2
      when kind = 'places_details' then coalesce(calls, 1) * 1.7
      else cost_cents
    end as new_cost
  from bands
)
update usage_events u
set    cost_cents = round(p.new_cost::numeric, 4)
from   priced p
where  u.id = p.id
  and  abs(coalesce(u.cost_cents, 0) - p.new_cost) > 0.0001;


-- Quick sanity report.
select kind, count(*), round(sum(coalesce(cost_cents, 0))::numeric, 2) as total_cents
from   usage_events
group by kind
order by kind;
