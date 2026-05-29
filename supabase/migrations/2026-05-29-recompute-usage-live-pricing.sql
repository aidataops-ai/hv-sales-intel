-- Recompute usage_events.cost_cents using the LIVE OpenAI pricing
-- fetched from developers.openai.com/api/docs/pricing and the
-- per-model docs pages on 2026-05-29.
--
-- The previous bands (in 2026-05-26-recompute-usage-with-cached-input.sql)
-- overstated every model by ~1/3 to 1/2 — they were built off a screenshot
-- from an older OpenAI pricing snapshot, before the GPT-4.x family was
-- repriced down ahead of the GPT-5.x rollout. Recomputing brings the
-- recorded usage cost in line with what OpenAI actually bills.
--
-- Pricing reference (¢ per 1M tokens):
--   Legacy GPT-4.x family (still live on the API):
--     gpt-4.1        input=200  cached=50    output=800
--     gpt-4.1-mini   input=40   cached=10    output=160
--     gpt-4.1-nano   input=10   cached=2.5   output=40
--     gpt-4o         input=250  cached=125   output=1000
--     gpt-4o-mini    input=15   cached=7.5   output=60
--     o4-mini        input=110  cached=27.5  output=440
--   Current GPT-5.x flagship line:
--     gpt-5.5        input=500  cached=50    output=3000
--     gpt-5.5-pro    input=3000 cached=750   output=18000
--     gpt-5.4        input=250  cached=25    output=1500
--     gpt-5.4-mini   input=75   cached=7.5   output=450
--     gpt-5.4-nano   input=20   cached=2     output=125
--     gpt-5.4-pro    input=3000 cached=750   output=18000
-- Places: text-search = 3.2¢/call, place-details = 1.7¢/call (unchanged).
--
-- Idempotent. Safe to re-run.

with bands as (
  select
    id,
    coalesce(input_tokens, 0)        as in_tok,
    coalesce(output_tokens, 0)       as out_tok,
    coalesce(cached_input_tokens, 0) as cached_tok,
    case lower(coalesce(model, ''))
      when 'gpt-4.1'      then 200
      when 'gpt-4.1-mini' then 40
      when 'gpt-4.1-nano' then 10
      when 'gpt-4o'       then 250
      when 'gpt-4o-mini'  then 15
      when 'o4-mini'      then 110
      when 'gpt-5.5'      then 500
      when 'gpt-5.5-pro'  then 3000
      when 'gpt-5.4'      then 250
      when 'gpt-5.4-mini' then 75
      when 'gpt-5.4-nano' then 20
      when 'gpt-5.4-pro'  then 3000
      else                     200
    end as input_rate,
    case lower(coalesce(model, ''))
      when 'gpt-4.1'      then 50
      when 'gpt-4.1-mini' then 10
      when 'gpt-4.1-nano' then 2.5
      when 'gpt-4o'       then 125
      when 'gpt-4o-mini'  then 7.5
      when 'o4-mini'      then 27.5
      when 'gpt-5.5'      then 50
      when 'gpt-5.5-pro'  then 750
      when 'gpt-5.4'      then 25
      when 'gpt-5.4-mini' then 7.5
      when 'gpt-5.4-nano' then 2
      when 'gpt-5.4-pro'  then 750
      else                     50
    end as cached_rate,
    case lower(coalesce(model, ''))
      when 'gpt-4.1'      then 800
      when 'gpt-4.1-mini' then 160
      when 'gpt-4.1-nano' then 40
      when 'gpt-4o'       then 1000
      when 'gpt-4o-mini'  then 60
      when 'o4-mini'      then 440
      when 'gpt-5.5'      then 3000
      when 'gpt-5.5-pro'  then 18000
      when 'gpt-5.4'      then 1500
      when 'gpt-5.4-mini' then 450
      when 'gpt-5.4-nano' then 125
      when 'gpt-5.4-pro'  then 18000
      else                     800
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
