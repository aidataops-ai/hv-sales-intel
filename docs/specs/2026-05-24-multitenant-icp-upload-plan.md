# Multi-tenant ICP Upload — Implementation Plan

**Date:** 2026-05-24
**Status:** Draft — awaiting review
**Parent:** [2026-05-24-multitenant-icp-upload-design.md](2026-05-24-multitenant-icp-upload-design.md)

## Scope locked

- **One go** — ship the full multi-tenant lift (no "demo cut" detour).
- **Subdomain routing — later.** v1 uses a session cookie + topbar company switcher.
- **Paste-only ICP upload.** No PDF / DOCX / OCR in v1.

## Sequencing principle

Eight phases, each shippable independently. Phases 1-3 are invisible to users (zero behavior change in production). Phases 4-7 land user-visible features in order. Phase 8 cleans up the legacy columns. After every phase, the app is in a deployable state — if we need to pause, we pause cleanly.

| # | Phase | User-visible? | Estimate |
|---|---|---|---:|
| 1 | Schema + migrations + seed default company | No | 1 day |
| 2 | Auth: company resolution + sign-up + switcher | Switcher visible behind feature flag | 1.5 days |
| 3 | Dual-write storage layer | No | 1.5 days |
| 4 | Read-path migration to per-company tables | No (same UX, new source) | 2 days |
| 5 | ICP upload + GPT parser + review UI | Yes | 1.5 days |
| 6 | Per-company analyzer + scorer | Yes | 1 day |
| 7 | Sign-up + onboarding wizard + Bulk Scan ICP defaults | Yes | 1.5 days |
| 8 | Drop legacy columns + remove dual-write | No | 0.5 days |

**Total: ~10-11 days of focused work.** A rep can demo end-to-end after Phase 7. Phase 8 is housekeeping.

---

## Phase 1 — Schema + migrations + seed default company

**Goal:** New tables exist in Supabase, RLS policies are on, every existing user is mapped to a "default" company, every existing practice's analysis + state is mirrored into the new per-company tables. Application code is unchanged.

**Deliverables**
- `supabase/schema.sql` additions (no column drops):
  - `companies`, `company_members`, `company_practice_analyses`, `company_practice_state`, `company_email_messages`
  - All indexes per the design doc
  - RLS policies on the three per-company tables (members-of policy)
- `supabase/migrations/2026-05-24-multitenant-init.sql` — one-time script that:
  - Inserts the "default" `companies` row (slug = `default`, name = `Default`, `icp_parsed` seeded with the universal ICP defaults)
  - Inserts `company_members` rows for every `profiles` row (admin → admin, sdr → sdr)
  - Backfills `company_practice_analyses` from existing `practices` columns where `lead_score is not null`
  - Backfills `company_practice_state` from every existing `practices` row
  - Re-points `email_messages` into `company_email_messages` with `company_id = default`

**Acceptance**
- `pytest` green (no app changes yet).
- Row counts: `company_practice_analyses` ≈ count of analyzed practices; `company_practice_state` ≈ count of total practices; `company_members` = count of profiles.
- Spot-check three practices: same `lead_score`, `status`, `call_count` in both legacy and new tables.

**Touches**
- `supabase/schema.sql`
- `supabase/migrations/2026-05-24-multitenant-init.sql` (new)

---

## Phase 2 — Auth: company resolution + sign-up + switcher

**Goal:** Every authenticated request resolves to a `(user, company)` pair. Users can sign themselves up (creating a brand-new company), see a switcher in the topbar, and switch between companies they belong to.

**Deliverables**
- Backend
  - `src/auth.py::get_current_user` resolves `current_company_id` from the `apex_current_company` cookie or falls back to the user's first membership. Returns `{..., company_id, role}`.
  - `src/auth.py::require_admin` keys off the membership role (not the global `profiles.role`).
  - New `POST /api/signup` — creates auth user + companies row + first company_members row + returns session.
  - New `GET /api/me/companies` — lists all companies the user belongs to.
  - New `POST /api/me/companies/{id}/switch` — sets the cookie.
- Frontend
  - `web/components/company-switcher.tsx` — dropdown in topbar listing the user's companies, current one highlighted, "Sign out of company" + "Create new company" actions.
  - `web/app/signup/page.tsx` — minimal form (company name, admin email, password).
  - `web/lib/auth.tsx` — add `currentCompany` + `companies` to `AuthContextValue`; re-hydrate on switch.

**Feature flag:** the switcher is rendered only when `companies.length > 1` so existing single-tenant users see no change.

**Acceptance**
- A logged-in user with one membership sees no switcher and behaves identically to today.
- Sign-up flow creates the company, makes the new user admin of it, lands on the app.
- Switching companies changes the cookie; the next page load reads from a different `company_id` (verified in network panel even though Phase 4 hasn't migrated reads yet).

**Touches**
- `src/auth.py`, `api/index.py`
- `web/lib/auth.tsx`, `web/components/company-switcher.tsx`, `web/components/top-bar.tsx`, `web/app/signup/page.tsx`
- `web/middleware.ts` — `/signup` becomes public

---

## Phase 3 — Dual-write storage layer

**Goal:** Every existing write path writes to BOTH the legacy `practices` columns AND the corresponding new per-company table. Reads still come from `practices` (so users see no change). Guarantees zero data loss when we flip reads in Phase 4.

**Deliverables**
- `src/storage.py`:
  - `update_practice_analysis(place_id, analysis, *, touched_by, company_id)` — writes legacy + upserts `company_practice_analyses (company_id, practice_id)`.
  - `update_practice_fields(place_id, fields, *, touched_by, company_id)` — writes legacy + upserts `company_practice_state`.
  - `add_tags(place_id, new_tags, *, company_id)` — writes legacy `practices.tags` AND `company_practice_state.tags`.
  - `insert_email_message(..., company_id)` — writes legacy `email_messages` AND `company_email_messages`.
  - `increment_export_counts(place_ids, user_id, *, company_id)` — writes legacy + per-company.
- `api/index.py`: every endpoint that calls one of the above passes `company_id=user["company_id"]`.

**Acceptance**
- Manually re-analyze a practice. Assert `practices.lead_score` AND `company_practice_analyses.lead_score` both updated, identical.
- Log a call. Assert both `practices.call_count` and `company_practice_state.call_count` incremented.
- A user belonging to two companies who acts under company A only mutates rows for `(A, place)`, leaves `(B, place)` untouched.

**Touches**
- `src/storage.py`, `api/index.py`

---

## Phase 4 — Read-path migration

**Goal:** Every read filters by the current company and joins the per-company tables instead of pulling per-company fields from `practices`. Dual-write from Phase 3 stays on as a safety net.

**Deliverables**
- New helper `src/storage.py::query_practices_for_company(company_id, …)` that joins
  ```sql
  practices ⋈ company_practice_analyses ⋈ company_practice_state
  ```
  with `LEFT JOIN`s (so a practice with no analysis row still shows up as unanalyzed in the sidebar).
- All endpoints updated:
  - `GET /api/practices` — `query_practices_for_company(user.company_id, …)`
  - `GET /api/practices/{place_id}` — single-row variant
  - `GET /api/practices/export.csv` — same join, plus the existing pagination
  - `POST /api/practices/search` — UNCHANGED for `practices` upsert; after upsert, creates blank `company_practice_state` rows for the current company so the new practices show up in their sidebar
  - `POST /api/practices/{place_id}/analyze` — read existing analysis from `company_practice_analyses` for cache check, write to same
  - `GET /api/practices/{place_id}/script` — read `company_practice_state.call_script`
  - email + clay webhook endpoints — `company_id` resolved from the practice's clay trigger metadata (we'll pass it in the Clay payload)
- `find_duplicate_place_ids` — UNCHANGED. `practices` is the shared dedup store; no company filter.

**Acceptance**
- Two test companies, each with their own analyses + statuses on the same practice. User A sees A's data; User B sees B's data. Verified end-to-end.
- Sidebar list, Practice detail, Call Prep, Email panel, CSV export, Bulk Scan all work for both companies.
- Integration test: company A's analyzer never returns company B's `lead_score`.

**Touches**
- `src/storage.py`, `api/index.py`

---

## Phase 5 — ICP upload + GPT parser + review UI

**Goal:** A company admin can paste their ICP document, the system parses it to structured JSON via GPT-4.1, the admin reviews/edits the result, and it gets saved to `companies.icp_parsed`. Activates the per-company prompt/scorer in Phase 6.

**Deliverables**
- Backend
  - `src/icp_parser.py` — new module with `parse_icp_doc(raw_text) -> dict` (strict schema, GPT-4.1, temperature=0, seed=42).
  - The schema validator rejects unknown verticals, clamps weights to integers summing to 100, etc.
  - `POST /api/companies/{id}/icp/parse` — admin only. Body: `{raw_text}`. Returns the parsed JSON (NOT saved).
  - `PUT /api/companies/{id}/icp` — admin only. Body: full parsed JSON. Saves to `companies.icp_parsed`.
  - `GET /api/companies/{id}` — returns the company row including the current `icp_parsed`.
- Frontend
  - `web/app/admin/company/page.tsx` — three-pane layout:
    - Left: textarea to paste the ICP doc + Parse button.
    - Middle: structured form of the parsed JSON (verticals chip-select, states chip-select, weights sliders, disqualifiers list, etc.).
    - Right: live preview of the analyzer system prompt that will be sent for this company.
  - `web/components/icp-review-form.tsx` — reusable component for editing the parsed structure.

**Acceptance**
- Paste the H&V universal ICP text → see it parse into the expected verticals, geographies, weights, disqualifiers.
- Edit a vertical, change a weight slider — the live prompt preview updates.
- Save → DB has the new `icp_parsed`. No analyses re-run automatically (next manual Analyze picks up the new prompt).

**Touches**
- `src/icp_parser.py` (new), `api/index.py`
- `web/app/admin/company/page.tsx`, `web/components/icp-review-form.tsx`

---

## Phase 6 — Per-company analyzer + scorer

**Goal:** The Analyze and Bulk Scan flows actually use each company's `icp_parsed` instead of the hardcoded H&V prompt. Lead scores reflect the company's weight config.

**Deliverables**
- `src/analyzer.py`
  - `build_system_prompt(company)` replaces the constant `SYSTEM_PROMPT`.
  - `analyze_practice(...)` accepts `company` and passes it through.
- `src/icp_scorer.py`
  - `score_icp(practice, ai_scores, company_config)` — weights default to current constants when `scoring_config` is null.
  - `_vertical_fit` reads `verticals_in_scope` / `verticals_adjacent` / `geographies` from the company config.
- `api/index.py`
  - `/api/practices/{id}/analyze` resolves the company before calling `analyze_practice`.
- Frontend
  - Practice card breakdown still shows the same 7 dimensions; the labels under each row reflect the company's weight (e.g. `12/18` instead of `12/20`).

**Acceptance**
- Two companies with different `dimension_weights` analyze the same practice and get different breakdowns AND different totals (totals always within 0-100).
- A company that puts `dental` in `verticals_in_scope` and `medical` in `verticals_adjacent` scores a dental practice higher on Vertical fit than a medical one.
- An empty `scoring_config` falls back to today's hardcoded weights — i.e. existing analyses don't regress.

**Touches**
- `src/analyzer.py`, `src/icp_scorer.py`, `api/index.py`
- `web/components/practice-card.tsx` — minor label tweaks if needed

---

## Phase 7 — Sign-up + onboarding wizard + Bulk Scan ICP defaults

**Goal:** A new visitor can go from `/signup` to a populated lead list in one flow, without ever editing a config file.

**Deliverables**
- Frontend
  - `web/app/signup/page.tsx` already exists from Phase 2; gets restyled (logo, marketing copy).
  - `web/app/onboarding/page.tsx` — wizard:
    1. **Welcome** — pick a preset ("Healthcare staffing", "Hospitality staffing", "Start blank") OR jump straight to paste.
    2. **Paste your ICP** — textarea + Parse button.
    3. **Review parsed criteria** — read-only summary + "Edit details" link to `/admin/company`.
    4. **First Bulk Scan** — pre-filled with the company's `verticals_in_scope` and `geographies.focus_states`. One click to run.
    5. **Done** — redirect to `/` with a small "Welcome, your first 240 leads are scoring now" banner.
  - Sticky nav so the user can leave the wizard half-way and come back.
  - `web/components/bulk-scan-modal.tsx` — when opened, defaults State picker to the active company's `geographies.focus_states` and Vertical to the first entry in `verticals_in_scope`.

**Acceptance**
- Brand-new email goes `/signup` → company created → onboarding wizard → ICP pasted → parsed → Bulk Scan runs → leads land in the sidebar with the company's score, all in <2 minutes.
- Existing companies see no change to their experience (they already onboarded).

**Touches**
- `web/app/signup/page.tsx`, `web/app/onboarding/page.tsx` (new), `web/middleware.ts` (allow `/onboarding` only for authed users with no analyses yet), `web/components/bulk-scan-modal.tsx`

---

## Phase 8 — Drop legacy columns + remove dual-write

**Goal:** `practices` table is slim, dual-write code is gone, schema reflects the final design.

**Deliverables**
- `supabase/migrations/2026-05-26-multitenant-cutover.sql`:
  - `alter table practices drop column lead_score, urgency_score, hiring_signal_score, summary, pain_points, sales_angles, icp_breakdown, icp_vertical, icp_tier, analysis_input_hash, website_contacts, status, notes, tags, call_count, call_notes, call_script, email, email_draft, email_draft_updated_at, salesforce_lead_id, salesforce_lead_url, salesforce_owner_id, salesforce_owner_name, salesforce_synced_at, assigned_to, assigned_at, assigned_by, last_touched_by, last_touched_at, export_count, last_exported_at, last_exported_by, enrichment_status, enriched_at, owner_name, owner_email, owner_phone, owner_title, owner_linkedin`
  - `drop table email_messages` (replaced by `company_email_messages`).
- `src/storage.py` — every dual-write helper drops the legacy half. Keeps the per-company write.
- Test pass.

**Acceptance**
- `pytest` green.
- Smoke test in prod: search, analyze, call log, email send, export — all work, no errors.
- `\d practices` in Supabase shows only the slim Google Places columns.

**Touches**
- `supabase/migrations/2026-05-26-multitenant-cutover.sql` (new), `src/storage.py`

---

## Cross-cutting concerns

### Testing strategy

- New fixture `sample_two_companies` in `tests/conftest.py` — two companies, four users (admin + sdr per company), seeded analyses + state for both.
- New test file `tests/test_multitenant_isolation.py`:
  - Read-after-write: A's writes never appear in B's reads.
  - Switch endpoint: cookie change actually changes `company_id` on subsequent requests.
  - RLS policy: anon-key client cannot select rows for a company the user isn't a member of (even with a manually crafted request).
- `tests/test_icp_parser.py` — golden tests for the H&V universal ICP, a hotel-ops sample, a dental DSO sample.
- All existing tests get a `company_id` argument passthrough; behavior unchanged.

### Integration risk: Salesforce

- Each company has its own SF org. `companies.integration_secrets jsonb` (encrypted via Supabase Vault, not implemented in this plan) holds `{salesforce: {apex_url, api_key, lead_view_base_url}}`.
- For the first multi-tenant deploy, only the default company has real SF creds. New companies get a "Salesforce not configured" notice; the call log still works locally.

### Integration risk: Clay webhooks

- Clay webhook payload gains `company_id` (we add a query param when triggering: `POST {CLAY_TABLE_WEBHOOK_URL}?company_id=<id>`; Clay echoes it in the callback).
- `POST /api/webhooks/clay` writes to the correct `company_practice_state` row.

### Cost guardrails

- ICP parser caps input at 16k chars; truncates with a warning.
- Per-company AI spend tracking: log `[analyzer.done] company=<id>` so we can grep usage if it spikes.
- No quota enforcement in v1 — add when needed.

### Data deletion

- `companies.archived_at` instead of hard delete. Archived companies are hidden from the switcher but data is preserved.
- Hard delete via SQL only, with documented runbook.

---

## What I want a sign-off on before I touch code

1. **Phase ordering OK?** If you'd rather see user-visible value sooner, we can move Phase 5 (ICP upload UI) before Phase 4 (read migration), at the cost of the new UI showing "no data yet" for the new company until Phase 4 lands.
2. **Default company name.** Backfill uses slug `default` / name `Default`. Could be `apex` / `Apex` to match the brand.
3. **Preset ICPs in the onboarding wizard.** Worth seeding 3-5 (Healthcare staffing, Hospitality, Dental, MedSpa, Senior Living) so a curious visitor can demo it without writing their own? Adds ~half a day.
4. **Should an SDR ever switch companies?** Or is the switcher admin-only? Both work; admin-only is simpler.
5. **RLS for `practices`.** The shared table itself doesn't need RLS (it's intentionally world-readable across tenants), but should we still enable it with a permissive policy as documentation? Lean yes for safety.

Tell me which phase to start with and I'll open the first PR.
