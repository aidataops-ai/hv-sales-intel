# Apex Sales Intel — Solution Brief

**What it is.** A map-driven, multi-tenant lead-intelligence workbench. Any company signs up, uploads their Ideal Customer Profile in plain text, and immediately gets a personalized lead list — discovered, AI-scored, and worked through a CRM pipeline. The AI does the heavy lifting on research, call prep, first-touch drafting, and per-tenant scoring.

## Scope (what ships today)

### Discovery
- **Search by query** — type a natural query (e.g. *"dental clinics in Houston"*) — pins on an interactive map, sidebar list, hot leads colored by score.
- **Bulk Scan** — modal that runs many targeted queries in sequence to get past the per-search result ceiling. Two modes:
  - **State sweep** — pick a vertical + one or more states; the rep can paste a custom city list to supplement the ~2,300 cities baked in (50 states + DC).
  - **Specialty grid** — cartesian of (states × specialties) for a chosen vertical.
- **Deduplication** — repeat listings from the same business under different place_ids are collapsed at upsert time; an existing-dupe cleanup script is shipped.

### AI scoring (Universal ICP, 7 dimensions, 100 pts)
Each lead is scored against a per-tenant ICP definition. Six dimensions are AI-inferred, one is deterministic.

| # | Dimension | Max | Source |
|---|---|---:|---|
| 1 | Vertical fit | 15 | deterministic (vertical × tier × geography) |
| 2 | Operational pain | 20 | AI |
| 3 | Decision-maker access | 15 | AI |
| 4 | Remote readiness | 15 | AI |
| 5 | Role clarity | 15 | AI |
| 6 | Budget maturity | 10 | AI |
| 7 | Compliance boundary | 10 | AI |

Five verticals supported: **medical**, **mental health**, **dental**, **assisted living / nursing**, **hotels / resorts**, **medspa / wellness**. Each lead is also classified into A / B / C / D tier (size × growth-stage). Public-facing **ICP rubric page** documents every dimension in detail.

**Reproducible scoring.** GPT outputs are constrained to six categorical buckets `{0, 20, 40, 60, 80, 100}`, temperature 0 + fixed seed, and results are cached against a hash of the input fields — so clicking Re-analyze on an unchanged practice returns the same total every time.

### Per-tenant ICP definition
- Admin pastes their full ICP document into a textarea.
- GPT-4.1 extracts structured criteria (verticals, geographies, dimension weights, in-scope keywords, disqualifiers, primary decision-makers, service catalog, brand voice).
- Admin reviews + edits each field with chip pickers, sliders, and live "weights must total 100" enforcement.
- Saved to `companies.icp_parsed`; the analyzer prompt is templated from it on every future Analyze.

### Call Prep
- **Five-section playbook** generated per practice (Opening / Discovery / Pitch / Objections / Closing) — tailored to the practice's ICP analysis, pain points, sales angles, owner enrichment, and review excerpts.
- **AI-extracted website contacts** — the analyzer pulls a list of decision-makers (name + title + direct phone + email) from the practice's website, surfaces them in the playbook by name + title, lists fallbacks if the primary isn't reachable.
- Notepad + auto-advancing CRM status.

### Email outreach
- Personalized first-touch draft, edit-and-send from inside the app.
- On-demand reply detection, threaded per lead via `internetMessageId`.

### CRM pipeline
- Nine statuses from `NEW` to `CLOSED WON / LOST` with auto-advance on key events.
- Tags (`RESEARCHED`, `SCRIPT_READY`, `CONTACTED`, `REPLIED`, etc.) layered on top so a lead can carry multiple workflow milestones.
- Every mutating action stamps `last_touched_by` + timestamp.

### Bulk export
- **CSV download** with 38 columns covering Google Places data, ICP scoring, owner enrichment, CRM state, Salesforce IDs, call counts, and per-row export tracking.
- Built-in **deduplication filter** — `max_exports` parameter lets a rep re-run the export with `0` to skip leads they've already pulled. Every row's `export_count` increments after each successful download; `last_exported_by` / `last_exported_at` stamp who did it and when.

### Admin
- **Company onboarding** — self-service signup creates a new tenant. The signer becomes admin.
- **Company switcher** — admins who belong to multiple companies switch between them from the topbar.
- **User management** — admins create / disable rep accounts.
- **Integrations** — credential forms for Salesforce (Apex REST + x-api-key) and Clay (HTTP API source + inbound secret), per tenant.
- **Usage & cost** — admin dashboard showing per-period spend on Places API + OpenAI, broken down by kind and model, with cost-per-event drill-down to drive pricing decisions. A *Recompute costs* button re-prices historical rows after any pricing edit.

### Salesforce + Clay sync
- Click-to-call creates / updates a Lead in the tenant's Salesforce via Apex REST. `Call_Count__c` + `Call_Notes__c` accumulate across calls.
- Clay owner enrichment runs on demand from each practice card; the inbound webhook updates owner contact fields when Clay finishes its waterfall.

### Other integrations
- **Click-to-call** via RingCentral.
- **Click-to-send** via Microsoft 365 / Graph.

## Architecture

### Multi-tenant data model
- Shared `practices` table (Google Places data deduped across every tenant — same business never written twice).
- Per-(company, practice) tables for analysis and CRM state:
  - `company_practice_analyses` — AI scores, breakdowns, summary, pain points, sales angles, website contacts.
  - `company_practice_state` — status, notes, tags, call log, email draft, Salesforce IDs, owner enrichment, export tracking.
  - `company_email_messages` — inbound + outbound emails per tenant.
- RLS policies guard the per-company tables; the shared `practices` table is intentionally readable across tenants for dedup.

### Tenant resolution
- Sign-up creates `auth.users` + `companies` + `company_members` rows in one shot.
- Every authenticated request resolves `(user, company)` from the `apex_current_company` cookie (set by the switcher) or the user's oldest membership.
- `role` is per-company, not global — an admin in tenant A can be an SDR in tenant B.

### Per-tenant analyzer prompt
- The analyzer reads the active company's `icp_parsed` and templates the system prompt from it — each tenant's GPT call speaks their vocabulary, scores their pain signals, and pitches their service catalogue.
- Dimension weights are per-company; the total still sums to 100 but the distribution moves.

### AI + Places usage logging
- Every Places search, Place Details, and OpenAI completion writes one row to `usage_events` with company / user / model / token counts.
- Cost is computed from `src/usage.py` pricing constants (¢ per 1M tokens for OpenAI; ¢ per call for Places).
- The admin Usage page aggregates by kind + model + period; a *Recompute* button re-prices history after a pricing edit.

## Data flow (high level)

1. Rep signs in. App loads their active company's lead list — sidebar shows just their tenant's rows even though `practices` is shared.
2. Rep searches (single query) or runs a Bulk Scan (state sweep / specialty grid).
3. Backend queries Google Places, dedupes against existing rows, upserts new ones into `practices`, seeds blank `company_practice_state` rows for the active tenant.
4. Rep clicks **Analyze** → AI runs against the tenant's ICP → writes a `company_practice_analyses` row + bumps status to `RESEARCHED`.
5. Rep opens Call Prep → playbook generated from the tenant's analysis + ICP + owner enrichment + review excerpts.
6. Rep clicks **Call** → if no SF Lead yet, the backend creates one via Apex REST and stores the ID + URL. Click-to-call opens RingCentral.
7. Rep drafts + sends an email; the message goes via Graph and replies are polled on demand.
8. Rep bulk-exports the lead list to CSV at any point; `export_count` increments per row so the next export can skip duplicates.

## Tech stack

- **Frontend** — Next.js 14 App Router, Tailwind, Leaflet map, React Server Components avoided in favor of a client-state model.
- **Backend** — FastAPI on Vercel serverless functions, Pydantic models, async httpx for outbound HTTP.
- **DB** — Supabase Postgres with Supabase Auth, RLS for tenant isolation, paginated PostgREST queries to bypass the 1k-row cap.
- **AI** — OpenAI Chat Completions (GPT-4.1 by default), temperature 0 + seed 42 for reproducibility, JSON-mode for structured outputs.
- **Discovery** — Google Places API (New) Text Search + Place Details.
- **Enrichment** — Clay HTTP API source + signed inbound webhook.
- **CRM sync** — Salesforce Apex REST with static `x-api-key` (per-tenant override planned).
- **Hosting** — Vercel single deployment serves both UI (`/`) and API (`/api/*`) via rewrites.

## Mock mode
- Full UI runs without any external keys — analyzer, scriptgen, email-gen, Places search, and Clay all fall back to category-appropriate mock data, with structured logs so the operator sees which calls were mock vs real.

## What this enables

- Demo: paste any company's ICP, see leads scored against *their* criteria within minutes, no engineering needed.
- Self-serve onboarding for new tenants.
- Predictable per-tenant unit economics — Usage & cost page makes the marginal cost per lead transparent so pricing can be set with margin.
- Cross-tenant lead dedup keeps the Google Places bill linear with discovery effort, not with tenant count.

## Related docs

- [Pricing & profitability model](pricing-model.md) — unit economics, tier proposals (Starter / Growth / Scale / Enterprise), credit pricing, 5k-lead worked example, margin targets, positioning thesis.
- [ICP scoring rubric](icp-scoring.md) — public-facing dimension definitions for the SDR.
