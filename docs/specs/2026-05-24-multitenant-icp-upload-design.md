# Multi-tenant ICP Upload — Design

**Date:** 2026-05-24
**Status:** Draft — awaiting review

## Goal

Let any company sign up, upload (or paste) their Ideal Customer Profile document, and immediately get a personalized lead list scored against *their* ICP — not the hardcoded H&V universal one. The shared lead universe (Google Places businesses) is deduped across every tenant; the analysis, scoring, status, notes, and exports are private to each company.

## Non-goals

- True subdomain routing (`acme.app.com`). v1 uses a session-based "current company" cookie. Subdomains can layer on later without a schema change.
- Org-tree / parent-child companies. Each tenant is flat.
- Per-user billing or seat enforcement. v1 is invitation-only inside a company.
- Live multi-region. Same Supabase project as today.
- PDF/DOCX OCR pipeline. v1 accepts paste or `.md` / `.txt`; PDF parsing is a follow-up.

## Core architectural decision

> *One shared `practices` table for the raw business data. Per-company tables for everything an SDR or analyzer writes.*

This collapses thousands of duplicate Google fetches into one row per place — but lets two companies hold completely different analyses, statuses, and call logs against the same place.

## Data model

### New tables

```sql
-- A tenant.
create table companies (
  id            uuid primary key default gen_random_uuid(),
  slug          text unique not null,
  name          text not null,
  branding      jsonb,                  -- {display_name, accent_color, logo_url}
  icp_doc_text  text,                   -- raw upload (or paste) for audit / re-parse
  icp_parsed    jsonb,                  -- structured ICP — see schema below
  scoring_config jsonb,                 -- dimension weight overrides; null = use defaults
  created_by    uuid references auth.users(id),
  created_at    timestamptz default now(),
  archived_at   timestamptz
);

-- Membership. A user can belong to multiple companies; each membership has a role.
create table company_members (
  company_id    uuid references companies(id) on delete cascade,
  user_id       uuid references auth.users(id) on delete cascade,
  role          text not null check (role in ('admin','sdr')),
  joined_at     timestamptz default now(),
  primary key (company_id, user_id)
);
create index on company_members (user_id);

-- Per-(company, place) AI analysis.
create table company_practice_analyses (
  id                  bigserial primary key,
  company_id          uuid not null references companies(id) on delete cascade,
  practice_id         bigint not null references practices(id) on delete cascade,
  lead_score          int,
  classification      text,             -- Strong ICP / Qualified / Weak / Poor fit
  icp_breakdown       jsonb,
  icp_vertical        text,
  icp_tier            text,
  summary             text,
  pain_points         jsonb,
  sales_angles        jsonb,
  website_contacts    jsonb,
  analysis_input_hash text,
  analyzed_at         timestamptz default now(),
  unique (company_id, practice_id)
);
create index on company_practice_analyses (company_id, lead_score desc);

-- Per-(company, place) CRM / workflow state.
create table company_practice_state (
  id                      bigserial primary key,
  company_id              uuid not null references companies(id) on delete cascade,
  practice_id             bigint not null references practices(id) on delete cascade,
  status                  text default 'NEW',
  notes                   text,
  tags                    text[] not null default '{}',
  call_count              int not null default 0,
  call_notes              text,
  call_script             text,
  email                   text,
  email_draft             text,
  email_draft_updated_at  timestamptz,
  salesforce_lead_id      text,
  salesforce_lead_url     text,
  assigned_to             uuid references auth.users(id),
  last_touched_by         uuid references auth.users(id),
  last_touched_at         timestamptz,
  export_count            int not null default 0,
  last_exported_at        timestamptz,
  last_exported_by        uuid references auth.users(id),
  enrichment_status       text,
  enriched_at             timestamptz,
  owner_name              text,
  owner_email             text,
  owner_phone             text,
  owner_title             text,
  owner_linkedin          text,
  unique (company_id, practice_id)
);
create index on company_practice_state (company_id, status);
create index on company_practice_state (company_id, tags) using gin (tags);

-- Per-company email log (replaces today's email_messages.practice_id linkage).
create table company_email_messages (
  id            bigserial primary key,
  company_id    uuid not null references companies(id) on delete cascade,
  practice_id   bigint not null references practices(id) on delete cascade,
  user_id       uuid references auth.users(id),
  direction     text not null check (direction in ('out','in')),
  subject       text,
  body          text,
  message_id    text,
  in_reply_to   text,
  sent_at       timestamptz default now(),
  error         text
);
create index on company_email_messages (company_id, practice_id, sent_at desc);
```

### Slimming `practices`

After migration, `practices` contains **only** the raw Google Places fields + dedup key:

```sql
-- Stays:
id, place_id, name, address, city, state, phone, website,
rating, review_count, category, lat, lng, opening_hours,
website_doctor_name, website_doctor_phone,
created_at, updated_at

-- Migrates OUT to company_practice_analyses:
lead_score, urgency_score, hiring_signal_score, summary, pain_points,
sales_angles, icp_breakdown, icp_vertical, icp_tier, analysis_input_hash,
website_contacts

-- Migrates OUT to company_practice_state:
status, notes, tags, call_count, call_notes, call_script,
email, email_draft, email_draft_updated_at,
salesforce_lead_id, salesforce_lead_url, salesforce_owner_id,
salesforce_owner_name, salesforce_synced_at,
assigned_to, assigned_at, assigned_by,
last_touched_by, last_touched_at,
export_count, last_exported_at, last_exported_by,
enrichment_status, enriched_at,
owner_name, owner_email, owner_phone, owner_title, owner_linkedin
```

This makes the dedup story trivial: `place_id` is the unique key, no per-company columns to clobber.

## ICP document → structured config

### `icp_parsed` JSON schema

This is what GPT extracts from the uploaded ICP. It's also what drives bulk scans and the analyzer prompt.

```jsonc
{
  "verticals_in_scope": ["dental", "alf_nh"],
  "verticals_adjacent": ["medical"],
  "geographies": {
    "focus_states":     ["FL"],
    "operating_states": ["TX", "CA", "NY", "OH"],
    "outside_us":       "exclude"
  },
  "size_categories": {
    "primary":       ["A", "B"],
    "opportunistic": ["C", "D"]
  },
  "dimension_weights": {
    "vertical_fit":          15,
    "operational_pain":      20,
    "decision_maker_access": 15,
    "remote_readiness":      15,
    "role_clarity":          15,
    "budget_maturity":       10,
    "compliance_boundary":   10
  },
  "in_scope_keywords": [
    "assisted living", "memory care", "dental clinic", "endodontist"
  ],
  "disqualifiers": [
    "wants licensed clinical work",
    "no digital systems",
    "single-employee operation",
    "outside US"
  ],
  "primary_decision_makers": [
    "owner / managing partner",
    "practice manager",
    "office administrator"
  ],
  "service_catalog": [
    "Virtual Scheduler",
    "Virtual Dental Assistant",
    "Patient Care Coordinator",
    "Executive Assistant",
    "HR & Payroll Assistant",
    "Medical Billing Coordinator"
  ],
  "brand_voice": "warm, direct, not pushy",
  "company_self_description": "managed remote-staffing for healthcare and hospitality"
}
```

### Parser

```
POST /api/companies/{id}/icp/parse
  body: { source: "paste" | "url", content: "<raw text or storage url>" }
```

Backend hits GPT-4.1 with a fixed system prompt that says:
> "Read this Ideal Customer Profile document and return JSON matching the schema below. Use empty arrays / defaults where the document doesn't specify. Do not invent verticals — pick from {medical, mental_health, dental, alf_nh, hotel_resort, medspa_wellness, other}."

The parsed JSON is stored to `companies.icp_parsed`. The admin sees it on a review page and can edit any field before activating.

### File upload (v1 vs v2)

**v1 (demo):** textarea paste only. The admin pastes the ICP doc as text. Fast to ship, no Storage / OCR.

**v2:** Supabase Storage bucket per company, signed-URL access. Backend route reads the file, extracts text (PDF via `pypdf`, DOCX via `python-docx`, MD/TXT verbatim), then runs the parser above.

## Per-company analyzer + scorer

### Analyzer prompt assembly

`src/analyzer.py::SYSTEM_PROMPT` is no longer a constant — it's templated per request from the company's `icp_parsed`:

```python
def build_system_prompt(company: dict) -> str:
    icp = company["icp_parsed"]
    return f"""You are a sales intelligence analyst for {company["name"]}, {icp["company_self_description"]}. Focus market: {", ".join(icp["geographies"]["focus_states"])}.

VERTICALS in scope: {", ".join(icp["verticals_in_scope"])}
ADJACENT verticals (partial credit): {", ".join(icp["verticals_adjacent"])}

DISQUALIFIERS — if any of these apply, score compliance_boundary near 0 and operational_pain low:
{format_bullets(icp["disqualifiers"])}

PRIMARY DECISION-MAKERS — score decision_maker_access high when these roles are visible on the website:
{format_bullets(icp["primary_decision_makers"])}

SERVICES we can pitch (use in sales_angles):
{format_bullets(icp["service_catalog"])}

... (rest of the bucket-scoring instructions, unchanged) ...
"""
```

The 7-dimension rubric stays the same. What changes is the *vocabulary* and *judgments* GPT applies.

### Per-company weights

`src/icp_scorer.py` reads weights from `companies.scoring_config` (falling back to defaults when null). Each dimension's max score is configurable.

```python
def score_icp(practice, ai_scores, company_config):
    weights = (company_config or {}).get("dimension_weights", DEFAULT_WEIGHTS)
    breakdown = [
        _vertical_fit(practice, company_config),
        _scale("Operational pain",      ai_scores["op_pain"],   weights["operational_pain"]),
        _scale("Decision-maker access", ai_scores["dm_access"], weights["decision_maker_access"]),
        # ...
    ]
    ...
```

`_vertical_fit` becomes data-driven: in-scope vertical + focus state = full credit, adjacent vertical or operating-not-focus state = partial, anything else = 0.

## Auth + current-company resolution

### Sign-up

```
POST /api/signup
  body: { email, password, company_name }
  → creates auth.users row
  → creates companies row (slug = derived from company_name)
  → creates company_members row (role = 'admin')
  → returns session cookie
```

### Current company per session

After login, the user picks (or is auto-assigned to) one company. The choice is stored as an httpOnly cookie `apex_current_company`, refreshed by the AuthProvider.

`get_current_user` is extended to also resolve `current_company_id`:

```python
async def get_current_user(request: Request) -> dict:
    profile = ...existing JWT → profile resolution...
    cid = request.cookies.get("apex_current_company")
    # Validate the user is a member of the requested company
    if cid:
        member = client.table("company_members") \
            .select("role,company_id").eq("user_id", profile["id"]) \
            .eq("company_id", cid).maybe_single().execute()
        if member.data:
            return {**profile, "company_id": cid, "role": member.data["role"]}
    # Default to first membership.
    first = client.table("company_members") \
        .select("company_id,role").eq("user_id", profile["id"]) \
        .order("joined_at").limit(1).execute()
    if first.data:
        return {**profile, "company_id": first.data[0]["company_id"], "role": first.data[0]["role"]}
    raise HTTPException(403, "User belongs to no company")
```

`require_admin` now checks `user["role"] == "admin"` within the current company, not globally.

### Company switcher

A new dropdown in the top-bar (next to the user avatar) lists every company the signed-in user belongs to. Switching sets the cookie + reloads.

## Backend read/write changes

Every read needs the company filter; every write needs the company stamp. The cleanest way is a small **`with_company(client, company_id)`** helper that returns a builder that auto-applies `.eq("company_id", company_id)`.

Endpoint-by-endpoint:

| Endpoint | Today | After |
|---|---|---|
| `GET /api/practices` | `select * from practices` | `practices ⋈ company_practice_analyses ⋈ company_practice_state` filtered by `company_id` |
| `POST /api/practices/search` | upsert into `practices` | upsert into `practices`; NO per-company write (analysis happens later) |
| `POST /api/practices/{id}/analyze` | write to `practices` | write to `company_practice_analyses` (upsert by `(company, practice)`) |
| `POST /api/practices/{id}/call/log` | write to `practices` | write to `company_practice_state` |
| `PATCH /api/practices/{id}` | write to `practices` | write to `company_practice_state` |
| `GET /api/practices/export.csv` | scan `practices` | scan with company filter; `export_count` lives on `company_practice_state` |
| `POST /api/webhooks/clay` | write owner_* to `practices` | needs `company_id` in the Clay payload (we add a query param when triggering); writes to `company_practice_state` |

The Salesforce sync still uses `salesforce_lead_id`, but the column lives on `company_practice_state`. Each company's leads go to that company's SF org (whose creds also live per-company — `companies.salesforce_config` jsonb).

## RLS (row-level security)

Postgres RLS belts the multi-tenancy so a code bug can't leak data across companies:

```sql
alter table company_practice_analyses enable row level security;
alter table company_practice_state    enable row level security;
alter table company_email_messages    enable row level security;

create policy "tenant_isolation_analyses"
  on company_practice_analyses
  using (
    company_id in (
      select company_id from company_members where user_id = auth.uid()
    )
  );

create policy "tenant_isolation_state"
  on company_practice_state
  using (
    company_id in (
      select company_id from company_members where user_id = auth.uid()
    )
  );
```

The backend uses the **anon client** for reads (RLS-bound to the logged-in user) and the **service-role client** only for cross-tenant work (sign-up, parser, admin tools). Today everything uses service-role; we have to split.

## Frontend changes

```
web/app/
├── signup/page.tsx              NEW — email + password + company name
├── onboarding/page.tsx          NEW — wizard:
│                                  1. paste / upload ICP
│                                  2. review parsed JSON
│                                  3. confirm weights
│                                  4. CTA "Run first Bulk Scan"
├── admin/
│   ├── company/page.tsx         NEW — edit ICP, switch active scoring config,
│   │                              upload new doc, see audit log
│   └── users/page.tsx           (existing) — scoped to current company
└── ... (rest unchanged)

web/components/
├── company-switcher.tsx         NEW — dropdown in topbar
└── icp-review-form.tsx          NEW — JSON form-fields with weight sliders
```

The Bulk Scan modal is already most of the way there — it just needs to read the active company's `icp_parsed.verticals_in_scope` and `geographies` as defaults.

## Migration path

This is the riskiest part — existing data has to move without downtime.

```sql
-- Step 1: create the new tables (above).

-- Step 2: create the "default" company that owns all current data.
insert into companies (slug, name, icp_parsed)
values ('default', 'Default', '{... seed with the universal H&V ICP ...}'::jsonb);

-- Step 3: backfill memberships. Every existing user joins the default company
-- with their current role.
insert into company_members (company_id, user_id, role)
select (select id from companies where slug='default'), id,
       case when role='admin' then 'admin' else 'sdr' end
from profiles;

-- Step 4: backfill analyses + state from practices.
with c as (select id from companies where slug='default')
insert into company_practice_analyses
  (company_id, practice_id, lead_score, summary, pain_points, sales_angles,
   icp_breakdown, icp_vertical, icp_tier, analysis_input_hash, website_contacts,
   analyzed_at)
select c.id, p.id, p.lead_score, p.summary, p.pain_points, p.sales_angles,
       p.icp_breakdown, p.icp_vertical, p.icp_tier, p.analysis_input_hash,
       case when p.website_contacts is not null then p.website_contacts::jsonb else null end,
       p.updated_at
from practices p cross join c
where p.lead_score is not null;

-- Same shape for company_practice_state. Then drop the migrated columns
-- from practices in a second deploy after the new code is live.
```

Two-phase deploy:
1. Deploy code that **dual-writes**: every write hits both `practices` (legacy) and the new tables.
2. Backfill + verify reads return identical results from both sides.
3. Deploy code that **reads from new** + stops writing to legacy.
4. Drop the legacy columns in a final migration.

## Demo cut (~3 days)

If we want this live for a single demo without the full lift:

1. Schema migration (new tables, no column drops).
2. Hardcode one company per Vercel deployment via env var `DEFAULT_COMPANY_SLUG`. No sign-up, no switcher.
3. ICP upload via paste-only textarea on a `/admin/company` page. GPT parses → admin reviews → save.
4. Analyzer + scorer read the active company's parsed ICP.
5. All other endpoints stay single-tenant for now — the schema is ready when we want to add the second tenant.

This unlocks the "upload your ICP, see your-flavored leads" demo without the multi-tenant plumbing.

## Open questions

1. **One Vercel deployment per company, or one shared multi-tenant deployment?** Per-company means dead-simple branding + DNS but heavier ops. Shared means one URL + a company switcher. Recommendation: shared, with custom-domain mapping as a follow-up.
2. **Where do per-company secrets live?** Salesforce creds, RingCentral, MS Graph all need per-company values. Add a `companies.integration_secrets jsonb` column encrypted at rest? Or push to Vercel env per tenant? Shared deployment forces option 1.
3. **ICP parser model.** Defaults to `gpt-4.1`; do we let companies override?
4. **Scoring transparency for the SDR.** Today the breakdown shows fixed dimensions. With per-company weights, an SDR seeing "Operational pain: 14/22" might confuse them. Surface the weights in the UI or hide them?
5. **Bulk Scan billing.** Per-company quotas? Shared Google Places API key vs per-company keys?
6. **PDF parsing in v2.** `pypdf` is OK for clean PDFs, but scanned ones need OCR (`pytesseract` or a hosted API). Worth it for the demo, or paste-only forever?

## Risks

- **Cross-tenant data leak.** RLS + integration tests against multi-company fixtures are mandatory before any second company onboards. Code review needs a "did this query filter by company_id?" checklist.
- **Schema churn breaks Salesforce sync.** Current SF code reads `practices.salesforce_lead_id`. The migration must keep that column populated until SF code is updated.
- **GPT parses an off-list vertical.** Mitigation: the parser system prompt strictly enumerates allowed values; backend validates the JSON shape before saving.
- **Customers paste their entire CRM dump as "ICP."** Token cost spikes. Cap parser input at 16k chars; truncate noisily.

## Implementation references (when we build)

- `src/auth.py::get_current_user` — extend to resolve `company_id`
- `src/storage.py` — every query needs a `company_id` parameter
- `src/icp_scorer.py` — read weights from company config
- `src/analyzer.py::SYSTEM_PROMPT` — template per request
- `web/lib/auth.tsx::AuthProvider` — add `currentCompany` state + switcher
- `web/components/top-bar.tsx` — host the company switcher
- `supabase/schema.sql` — additions only; column drops in a separate migration
