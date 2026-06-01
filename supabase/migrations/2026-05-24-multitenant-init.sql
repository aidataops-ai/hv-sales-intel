-- Multi-tenant ICP initialization — Phase 1 backfill.
--
-- Idempotent: safe to re-run. Each insert is guarded by `not exists` or
-- `on conflict do nothing`. No legacy columns are dropped here — Phase 8
-- (`2026-05-26-multitenant-cutover.sql`) does that after the read paths
-- have been migrated.
--
-- Order:
--   1. Make sure the schema.sql additions have already been applied.
--   2. Run this script in the Supabase SQL editor.
--   3. Verify the row counts in the validation block at the bottom.

begin;

-- ---------------------------------------------------------------------------
-- 1. The default "apex" company. Holds every existing practice + analysis.
-- ---------------------------------------------------------------------------

insert into companies (slug, name, branding, icp_parsed, scoring_config)
values (
  'apex',
  'Apex',
  jsonb_build_object(
    'display_name', 'ApexVirtuals',
    'short_name',   'Apex',
    'accent_color', '#0d9488'
  ),
  -- Seed with a permissive ICP roughly matching today's hardcoded Apex
  -- behavior so the analyzer keeps scoring the same way until an admin
  -- uploads a tighter ICP.
  jsonb_build_object(
    'verticals_in_scope', jsonb_build_array(
      'medical', 'mental_health', 'dental', 'alf_nh',
      'hotel_resort', 'medspa_wellness'
    ),
    'verticals_adjacent', jsonb_build_array(),
    'geographies', jsonb_build_object(
      'focus_states',     jsonb_build_array('FL'),
      'operating_states', jsonb_build_array(
        'AL','AK','AZ','AR','CA','CO','CT','DE','DC','FL','GA','HI','ID',
        'IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO',
        'MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA',
        'RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY'
      ),
      'outside_us', 'exclude'
    ),
    'size_categories', jsonb_build_object(
      'primary',       jsonb_build_array('A','B'),
      'opportunistic', jsonb_build_array('C','D')
    ),
    'dimension_weights', jsonb_build_object(
      'vertical_fit',          15,
      'operational_pain',      20,
      'decision_maker_access', 15,
      'remote_readiness',      15,
      'role_clarity',          15,
      'budget_maturity',       10,
      'compliance_boundary',   10
    ),
    'in_scope_keywords', jsonb_build_array(),
    'disqualifiers', jsonb_build_array(
      'wants licensed clinical work',
      'no digital systems / paper-only workflows',
      'no single decision-maker',
      'outside US'
    ),
    'primary_decision_makers', jsonb_build_array(
      'owner / managing partner',
      'practice manager',
      'office administrator',
      'general manager',
      'director'
    ),
    'service_catalog', jsonb_build_array(
      'Virtual Scheduler',
      'Virtual Dental Assistant',
      'Virtual Wellness/Hospitality Assistant',
      'Patient Care Coordinator',
      'Executive Assistant',
      'HR & Payroll Assistant',
      'SDR',
      'Medical Billing Coordinator'
    ),
    'brand_voice', 'warm, direct, not pushy',
    'company_self_description',
      'ApexVirtuals — a managed remote-staffing company that places non-clinical virtual assistants (front desk, scheduler, admin, billing, coordinator) into US-based service businesses'
  ),
  null  -- scoring_config defaults to code-side weights when null
)
on conflict (slug) do nothing;


-- ---------------------------------------------------------------------------
-- 2. Memberships — every existing profile joins the apex company with their
-- current role. The profiles table's role values today are 'admin' / 'sdr'
-- (after the rename); fall back to 'sdr' for anything else.
-- ---------------------------------------------------------------------------

insert into company_members (company_id, user_id, role)
select
  (select id from companies where slug = 'apex'),
  p.id,
  case
    when lower(coalesce(p.role, 'sdr')) = 'admin' then 'admin'
    else 'sdr'
  end
from profiles p
on conflict (company_id, user_id) do nothing;


-- ---------------------------------------------------------------------------
-- 3. Per-(apex, practice) analyses — backfill from existing practices columns.
--    Only rows that have a lead_score get an analysis row.
-- ---------------------------------------------------------------------------

with c as (select id from companies where slug = 'apex')
insert into company_practice_analyses (
  company_id, practice_id,
  lead_score, classification, icp_breakdown,
  icp_vertical, icp_tier,
  summary, pain_points, sales_angles, website_contacts,
  urgency_score, hiring_signal_score,
  analysis_input_hash, analyzed_at
)
select
  c.id,
  p.id,
  p.lead_score,
  case
    when p.lead_score >= 85 then 'Strong ICP'
    when p.lead_score >= 70 then 'Qualified with conditions'
    when p.lead_score >= 55 then 'Weak / exploratory'
    when p.lead_score is not null then 'Poor fit'
  end,
  -- icp_breakdown lives in `practices.icp_breakdown` as jsonb already (per
  -- the schema below). pain_points / sales_angles / website_contacts are
  -- TEXT today storing JSON strings — coerce to jsonb for the new column.
  p.icp_breakdown,
  p.icp_vertical,
  p.icp_tier,
  p.summary,
  case when p.pain_points  is not null then p.pain_points::jsonb  else null end,
  case when p.sales_angles is not null then p.sales_angles::jsonb else null end,
  case when p.website_contacts is not null then p.website_contacts::jsonb else null end,
  p.urgency_score,
  p.hiring_signal_score,
  p.analysis_input_hash,
  coalesce(p.updated_at, now())
from practices p
cross join c
where p.lead_score is not null
on conflict (company_id, practice_id) do nothing;


-- ---------------------------------------------------------------------------
-- 4. Per-(apex, practice) state — backfill from every existing practices row.
--    This gives every practice a workflow record even if it was never
--    analyzed, so the sidebar / filters keep showing them.
-- ---------------------------------------------------------------------------

with c as (select id from companies where slug = 'apex')
insert into company_practice_state (
  company_id, practice_id,
  status, notes, tags,
  call_count, call_notes, call_script,
  email, email_draft, email_draft_updated_at,
  salesforce_lead_id, salesforce_lead_url,
  salesforce_owner_id, salesforce_owner_name, salesforce_synced_at,
  assigned_to, assigned_at, assigned_by,
  last_touched_by, last_touched_at,
  export_count, last_exported_at, last_exported_by,
  enrichment_status, enriched_at,
  owner_name, owner_email, owner_phone, owner_title, owner_linkedin
)
select
  c.id, p.id,
  coalesce(p.status, 'NEW'), p.notes, coalesce(p.tags, '{}'::text[]),
  coalesce(p.call_count, 0), p.call_notes, p.call_script,
  p.email, p.email_draft, p.email_draft_updated_at,
  p.salesforce_lead_id, p.salesforce_lead_url,
  p.salesforce_owner_id, p.salesforce_owner_name, p.salesforce_synced_at,
  p.assigned_to, p.assigned_at, p.assigned_by,
  p.last_touched_by, p.last_touched_at,
  coalesce(p.export_count, 0), p.last_exported_at, p.last_exported_by,
  p.enrichment_status, p.enriched_at,
  p.owner_name, p.owner_email, p.owner_phone, p.owner_title, p.owner_linkedin
from practices p
cross join c
on conflict (company_id, practice_id) do nothing;


-- ---------------------------------------------------------------------------
-- 5. Email log — copy existing email_messages into company_email_messages
--    under the apex company.
-- ---------------------------------------------------------------------------

with c as (select id from companies where slug = 'apex')
insert into company_email_messages (
  company_id, practice_id, user_id, direction,
  subject, body, message_id, in_reply_to, sent_at, error
)
select
  c.id, em.practice_id, em.user_id, em.direction,
  em.subject, em.body, em.message_id, em.in_reply_to, em.sent_at, em.error
from email_messages em
cross join c
where not exists (
  -- Idempotent guard: if a row already exists by message_id, skip.
  -- Inbound rows can have null message_id, so fall back to (practice_id, sent_at).
  select 1 from company_email_messages e
   where e.company_id = c.id
     and ((em.message_id is not null and e.message_id = em.message_id)
          or (em.message_id is null
              and e.practice_id = em.practice_id
              and e.sent_at = em.sent_at
              and coalesce(e.direction, '') = coalesce(em.direction, '')))
);


commit;


-- =============================================================================
-- VALIDATION — re-run any time to sanity-check.
-- =============================================================================

select 'companies'            as table_name, count(*) from companies
union all
select 'company_members',     count(*) from company_members
union all
select 'analyses (apex)',     count(*) from company_practice_analyses
  where company_id = (select id from companies where slug='apex')
union all
select 'state (apex)',        count(*) from company_practice_state
  where company_id = (select id from companies where slug='apex')
union all
select 'emails (apex)',       count(*) from company_email_messages
  where company_id = (select id from companies where slug='apex')
union all
select 'profiles (existing)', count(*) from profiles
union all
select 'practices (existing)',count(*) from practices
union all
select 'practices analyzed (existing)',
       count(*) from practices where lead_score is not null
union all
select 'email_messages (existing)', count(*) from email_messages
order by 1;

-- Expected:
--   companies                       = 1
--   company_members                 = count(profiles)
--   analyses (apex)                 = count(practices where lead_score not null)
--   state (apex)                    = count(practices)
--   emails (apex)                   = count(email_messages)
