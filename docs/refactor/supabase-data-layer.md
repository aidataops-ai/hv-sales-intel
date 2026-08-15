# Refactor: Supabase data layer — clients, round trips, queries, pagination

Status: investigation complete (2026-08-15), implementation not started.
Scope: everything between a request arriving and Supabase answering — client
lifecycle, auth resolution, query shapes, pagination, N+1 loops, API response
shapes, and the frontend fetch patterns that multiply them. Companion to
`instant-signals-targets.md` (the previous module refactor).

Live scale at time of audit (prod project `ovzzusccogpuyfyybaml`):
`company_job_leads` 29.4k rows · `job_postings` 29.3k · `practices` 23.2k ·
`leads` 6.7k · `usage_events` 4.1k. **Anything unpaginated against these
tables is already truncated at PostgREST's silent 1,000-row cap.**

## Deployment reality: Vercel functions + Supabase free tier

This app runs as Vercel serverless functions against a free-tier Supabase
project. Four limits bind, and they change what the fixes below are *for* —
several stop being latency niceties and become cost/survival requirements.

**1. Supabase free egress: 5 GB/month — and the qualify stage alone would
blow it ~7×.** Egress is all data leaving Supabase, whether to a Vercel
function or a GitHub Actions runner. The two §2 cron hogs, measured against
live table sizes:

- `claim_unqualified` in steady state scans essentially the whole
  `job_postings` table newest-first with `select("*")` (nearly every row is
  already qualified, so it keeps paging), transferring on the order of the
  table's 45 MB **per qualify run**.
- `load_practices_by_city` streams the whole service-line-tagged practices
  bank (~23 MB table) per run, filtering client-side.

At the hourly cadence the pipeline is built for, that is roughly
**(30-50 MB × 24 × 30) ≈ 25-35 GB/month against a 5 GB cap** — the single
largest cost item in the codebase, and it applies wherever the pipeline
runs (GH Actions egress counts the same). The frontend adds real egress
too: the practices map query pulls 500 `select("*")` rows (call scripts,
email drafts) per page load. Consequence: **the §2 query-diet items for the
two cron reads are promoted into Phase 2** — they are prerequisites for
running the pipeline at all on this tier, not polish.

**2. Supabase free database: 500 MB — at 105 MB today, and `job_postings`
(45 MB) is the growth driver.** Every judged posting is stored, discards
included (~13:1 discard ratio), full `description` and all. At the new
pipeline's collection scale the cap is months away. New item this
investigation adds: a **retention migration** — null out `description` (or
delete the row) for discarded postings older than ~30 days; the verdict
row in `company_job_leads` is what analytics needs, not the body text.

**3. Supabase free compute is a shared-CPU Nano instance.** Per-row RLS
`auth.*()` re-evaluation (§5), `count="exact"` over the 29k-row join on
every list page (§2), and Python-side aggregation that forces full scans
(§1) all land on that tiny instance — the DB-side fixes matter *more* here,
not less. A slow query also holds one of a small number of pooled
connections longer.

**4. Vercel functions are short-lived, per-instance, and capped in
seconds.** Four consequences:

- The **120s default PostgREST timeout (§0.3) exceeds the function
  ceiling** on the Hobby plan (seconds to low minutes depending on config)
  — a hung query kills the invocation with no error path. The explicit
  timeout must sit well under the function limit (~10-15s).
- **In-process caches only live per warm instance.** The `_get_client`
  singleton still pays (6-27 constructions → 1 per warm instance), but the
  auth fix should be **stateless first**: verify the JWT locally (drops the
  GoTrue round trip) + merge profile/membership into one query — a TTL
  cache is a bonus on warm paths, not the mechanism.
- **CSV exports must fit the duration cap.** The practices export's ~15k
  sequential write round trips (§3) plus a 23k-row paged read cannot finish
  inside a Hobby function limit — the bulk-write fix is what makes exports
  work at all, and the "streaming" responses should stream for real instead
  of materializing everything first.
- Frontend polling/refetch patterns (§4) burn Vercel invocations *and*
  Supabase egress — every invocation also pays the 3-4 auth round trips.

## 0. Why the app feels slow — the two amplifiers

Every finding in this document is multiplied by these two. Fix them first;
they discount everything else without restructuring anything.

### 0.1 A new Supabase client (and TLS handshake) per call

`storage._get_client()` (`src/storage.py:50-62`) calls `create_client`
unconditionally — no caching. ~44 call sites across `storage.py` (21),
`lead_store.py` (16), `lead_targets.py` (16), `practice_matcher.py`,
`credits.py`, `usage.py`, `api/index.py`. Each construction builds a fresh
sync client with its own brand-new `httpx.Client` connection pool, so **no
HTTP connection is ever reused**: every PostgREST query pays a fresh TCP+TLS
handshake (~40-150ms), and the pool is abandoned to the GC.

- One `PATCH /api/practices/{id}` = 6 client constructions.
- One analyze request = 10-12.
- The HTTP cron collect loop = ~3 per (term × location) cell ≈ **99
  constructions per claimed location**.

`src/auth.py:12-22` already memoizes its admin client in a module global —
that is the exact fix, applied to `_get_client`.

### 0.2 Auth burns 3-4 blocking round trips on every request

`get_current_user` (`src/auth.py:85`) runs per authenticated request:
GoTrue `auth.get_user(token)` (`:103`) → `profiles.select("*")` (`:108`) →
1-2 `company_members` lookups (`:148-168`), strictly sequential, no caching.

Worse: it is `async def`, so FastAPI runs it **on the event loop** — its
synchronous httpx calls stall *all* concurrent requests, including sync
routes that would otherwise be threadpooled. Fixes, in order of effect:

1. Make it a plain `def` (moves to threadpool — one-word diff).
2. Verify the JWT locally instead of the GoTrue round trip.
3. Merge profile + membership into one query
   (`profiles.select("*, memberships:company_members(...)")`) or add a
   short TTL cache keyed by token.

### 0.3 Also in the foundation bucket

- **No `ClientOptions` anywhere**: PostgREST timeout defaults to **120s**
  (longer than any serverless ceiling) and there are zero transport retries.
  Pass an explicit timeout at all three `create_client` sites
  (`src/auth.py:18`, `src/storage.py:62`, `api/index.py:673`).
- **OpenAI clients constructed per call, never closed, mostly no timeout**:
  `src/analyzer.py:145`, `src/email_gen.py:45`, `src/icp_parser.py:137`,
  `src/scriptgen.py:154` (`AsyncOpenAI` per call); `lead_qualifier.py:225`
  is per-batch. Cache one module-level client each; add timeouts.
- **Blocking sync DB calls inside `async def` routes** throughout
  `api/index.py` (search, analyze, patch, email endpoints, imports — full
  list in the audit). Interim rule: any route that only does sync DB work
  should be `def`, not `async def`.
- Cold start: `openai` (~88ms) + `bs4` (~21ms) imported unconditionally at
  module scope via analyzer/email_gen/scriptgen/crawler — even `/api/health`
  pays it. `jobspy`/`pandas` are already correctly lazy.

## 1. Correctness: the 1,000-row cap is already lying to us

PostgREST silently truncates at 1,000 rows; `.limit(20000)` does not
override it. `storage._paginated_query` (`src/storage.py:304`) exists but
has only 4 call sites (plus 7 hand-rolled duplicates of the same loop).

| # | Site | What is wrong |
|---|------|---------------|
| 1 | `lead_store.lead_analytics` — `src/lead_store.py:779` `.limit(20_000)` | With 29.4k lead rows, **the analytics page is computed over the newest 1,000**: `total` pins at 1000, keep_rate is a window ratio, `per_day` loses its tail, the `days` param is a no-op. Wrong today. |
| 2 | `GET /api/admin/usage` — `api/index.py:1042-1046` `.limit(5000)` | 4.1k `usage_events` → **cost totals under-reported today**, and `select("*")` drags jsonb metadata for rows the page never renders. |
| 3 | `GET /api/admin/users` — `api/index.py:171` | Loads the whole 23k-row `practices` table (truncated to 1,000) to count `last_touched_by` in Python → `practices_touched` wrong for every user. Wants a `group by` aggregate. |
| 4 | `storage.increment_export_counts` — `src/storage.py:778-783` | Unchunked `.in_(place_ids)` with up to 50k ids: URL blows up → silent no-op, or response truncates at 1,000 → **export dedup (`max_exports=0`) broken for big exports**. Then one UPDATE per row (§3). `lead_store`'s version chunks at 500 — this one doesn't. |
| 5 | `storage.find_duplicate_place_ids` — `src/storage.py:122-127` | Unbounded name-match against 23k practices; truncation silently admits duplicate place_ids — the exact failure the function exists to prevent. Latent. |
| 6 | `storage._ensure_state_rows_for_practices` — `src/storage.py:501-504` | Unchunked read means a >1000-place upsert silently skips seeding state rows. Latent. |
| 7 | `lead_store.collector_health` — `src/lead_store.py:874` `.limit(5000)` | Safe at ~160 locations, breaks near national scale; sibling query 20 lines down was already converted. Latent. |

## 2. Overfetch: wide rows nobody reads

- **`claim_unqualified`** (`src/lead_store.py:259`) — the qualify cron's
  scan pages `job_postings.select("*")` — full `description` bodies — over a
  `limit*20` window just to test ids against a Python set. ~95% of scanned
  rows need only `id`. Fix: `select("id")` for the scan + column-list fetch
  of survivors, or the §4 anti-join RPC.
- **`practice_matcher.load_practices_by_city`** (`src/practice_matcher.py:110-117`)
  — pages the **entire** service-line-tagged practices table (~20+ round
  trips) and applies the `only_cities` filter **in Python, after the rows
  arrive**, on every qualify run. The city list is already computed and
  passed in — it's just never sent to the server. One-line `.in_()` fix.
- **`PROFILE_JOIN_SELECT` = `*`** (`src/storage.py:47`) — the practices
  list/export paths carry `call_script`, `email_draft`, `notes`,
  `website_contacts` for up to 500 rows/page; the list UI provably renders
  none of them. Add a `_PRACTICE_LIST_COLS` mirroring `LEAD_LIST_SELECT`
  (`lead_store.py:88`), which was built for exactly this reason.
- **`leads_for_export`** (`src/lead_store.py:692`) — uses full `LEAD_SELECT`
  (8KB `draft` + full posting `description` + a practice embed) even though
  the route re-fetches full practices anyway (`api/index.py:2981-2989`) and
  the CSV uses a handful of columns.
- **`newest_posting_for_practice`** (`src/lead_store.py:562`) — `select("*")`
  drags a full job description into **every practice-detail page load**
  (`api/index.py:1568`); caller reads only `posting["id"]`.
- **`count="exact"` on every page** of both list endpoints
  (`lead_store.py:458-467` over a 29k join, `storage.py:672-678` over 23k)
  feeding only `total`/`has_more`. Use `count="planned"` or fetch
  `limit+1`; both sites already have failure fallbacks, so degrading is safe.
- **`filter_options`** (`src/lead_store.py:638-668`) — pages **every kept
  lead** (1000/batch) on every signals page load to produce three small
  distinct lists. Wants `select distinct` in an RPC/view, or a cache.

## 3. Round-trip structure: N+1 and redundant reads

Biggest N+1s (all also multiplied by §0.1):

- **`storage.increment_export_counts`** — 1 + **3 writes per row**
  (`practices` update, `_practice_id_by_place` re-select, state upsert):
  a full CSV export ≈ **15,000 round trips** in one invocation
  (`src/storage.py:788-820`). Bulk-group by count value → 2-3 calls.
  Same shape in `lead_store.increment_export_counts` (`:738-750`).
- **`POST /api/practices/search`** — one `get_practice` per Places result
  (`api/index.py:1540-1542`); the batch helper
  `get_practices_by_place_ids` already exists and is used elsewhere.
  Bulk-scan runs this ~60×, ≈1,200 avoidable selects per sweep.
- **`practice_matcher.link_postings`** — one UPDATE per matched posting
  (`src/practice_matcher.py:240-252`) on the qualify cron; the clear path
  directly below already does chunked `.in_()` — copy it.
- **`add_tags`** — up to 5 round trips to append one tag string
  (`src/storage.py:1054-1089` + `_add_tags_per_company`), called from ~8
  mutation endpoints. Wants an RPC (`tags = array(select distinct unnest(tags || $1))`)
  or at minimum threading the known `practice_id` through instead of
  re-resolving it (`_practice_id_by_place` runs 2-3× per write path).
- **Read-modify-write**: `update_lead_workflow` re-fetches after update and
  the PATCH route pre-checks existence — 3 queries to flip one column
  (`src/lead_store.py:520-528`, `api/index.py:3098`); PostgREST returns the
  updated row already. Same pattern on clay webhook, enrich, rescan
  (`upsert then get_practice` — have `upsert_practices` return rows).
- **`GET /api/admin/leads/config`** — `sweep_status` re-reads
  `search_locations` that `list_config` just fetched (`lead_targets.py:896`);
  pass the rows in. Practice detail route chains 3 queries that are one
  embed (`api/index.py:1555-1582`); signal import re-fetches a posting the
  lead row already embeds (`:3131`).
- **RPC gaps** (only credits uses RPC today): `claim_unqualified` anti-join
  (`not exists` returning exactly `limit` rows — the docstring already
  concedes this), facets + analytics aggregates (`group by` server-side),
  atomic increments (export counts, zero-streak — both docstrings cite
  "no += in PostgREST" as why they read-then-write).

## 4. Frontend multipliers (web/)

- `AuthProvider` serializes `/api/me` → `/api/me/companies` (comment claims
  parallel, code awaits — `web/lib/auth.tsx:76-82`); with `/api/me/credits`
  the shell spends ~9 backend round trips of pure auth before any page data.
  Wants `Promise.all` now, one `/api/session` endpoint later.
- Practices page fires the same filtered query twice (limit 10 + limit 500
  for the map — `web/app/page.tsx:90-93`); slice the 500 client-side.
- Enrichment polling: `getPractice` per card every 5s × 36 polls × the
  heaviest detail route (≈216 round trips per enriching practice)
  (`web/lib/use-enrichment-poll.ts`).
- Config page refetches the **entire** config (4 queries) after every
  single-field mutation even though every PATCH returns the updated row —
  ~11 round trips to flip one boolean; bulk state toggles fan out one PATCH
  per row (~64 round trips to enable a state). Splice returned rows into
  state (the signals list page at `web/app/signals/page.tsx:90` already
  does this correctly); add a bulk PATCH route for the toggles.
- Email panel refetches the whole thread after send/poll/mark-replied even
  though each response already contains the new row
  (`web/components/email-panel.tsx:61-83`).

## 5. Database-side (Supabase advisors, 2026-08-15)

- **RLS initplan** (WARN): every tenant policy re-evaluates
  `auth.*()`/`current_setting()` **per row** on `job_postings`,
  `company_job_leads`, `practices`, `company_practice_state`, all dimension
  tables. Mechanical fix: wrap in `(select ...)` in each policy. One
  migration.
- Unindexed FKs (INFO): mostly audit columns (`last_touched_by` etc.) plus
  `target_runs.location_id`, `company_practice_state.practice_id`,
  `company_practice_analyses.practice_id` — the latter three sit on real
  join paths; fold into the same migration.
- Unused indexes (INFO): `idx_practices_city`, `idx_leads_created`, etc. —
  drop candidates once query shapes settle (they may become used after §2).

## 6. What one page load costs today (traced)

| Page | Round trips today | Achievable |
|------|------------------|-----------|
| Practices list (first load) | ~27 (18 auth + 9 data, incl. duplicate query + full-table users scan) | ~6 |
| Signals list (first load) | ~20-25 (15 auth + 5 + full facet scan) | ~8 |
| Practice detail | ~24 (15 auth + 9 data, `get_practice` ×4) | ~8 |

Each round trip currently opens its own TLS connection (§0.1). That —
compounded with §0.2 — is the honest answer to "why is the app slow."

## 7. Proposed phasing (one PR each, staging first)

Reordered for the free-tier constraints above: the egress-critical cron
reads move up into Phase 2, and the auth fix is stateless-first.

1. **Foundation** — memoize `_get_client`; `ClientOptions` timeout (~10-15s,
   under the Vercel function ceiling); auth as plain `def` + local JWT
   verify + merged profile/membership query (stateless — per-instance TTL
   cache only as a bonus); cache OpenAI clients (+timeouts). Small diff,
   discounts everything.
2. **Correctness + cost survival** — §1 rows 1-7 (paginate or aggregate the
   truncated reads; chunk + bulk the export-count writes so exports fit the
   function duration cap) **plus the two egress hogs**: `claim_unqualified`
   scans on `select("id")` (or the anti-join RPC) and the matcher's city
   filter pushed server-side. **Plus the retention migration** for
   discarded postings' descriptions. This phase is what makes the hourly
   pipeline affordable inside 5 GB/month and keeps the DB under 500 MB.
3. **Query diet** — the rest of §2: column lists on list/export/detail
   paths, `count="planned"`, facets RPC/aggregate.
4. **Round-trip consolidation** — §3 N+1s and redundant reads; §4 frontend
   splice-not-refetch, parallel auth, bulk toggle route, session endpoint
   (also trims Vercel invocation count).
5. **DB migration** — §5 RLS initplan + FK indexes on the Nano instance
   (+ unused-index cleanup last).

Verification gates per phase: `pytest` (baseline: 20 pre-existing failures
in auth/call_log/enrich/practices families), `npm run build`, and a
before/after round-trip trace of the three §6 pages.

## 8. Execution: parallel subtask waves (started 2026-08-15)

Implementation runs as parallel agent sessions, each in an isolated
worktree off `staging`, partitioned by **file ownership** (not by phase)
so concurrent diffs never contend for the same regions. The orchestrator
reviews each diff, merges to `staging`, and re-runs the gates between
waves. Migrations are files only — nothing is applied to the live DB
without an explicit go.

**Wave 1 (all independent, in flight):**
| Task | Owns |
|---|---|
| Client singleton + 15s timeout | `storage._get_client`, `api` anon client |
| Auth stateless fast path (def, merged query, local JWT verify) | `src/auth.py`, settings field |
| OpenAI client caching + timeouts | analyzer / email_gen / icp_parser / scriptgen / lead_qualifier |
| Posting-description retention migration | new SQL file |
| RLS initplan + FK index migration | new SQL file |
| Frontend quick wins (parallel auth, dedup practices query, splice-not-refetch, poll backoff) | `web/` only |

**Wave 2 (after Wave 1 merges; overlapping files, split by function):**
truncation fixes (`lead_analytics`, admin usage/users, export counts
chunk+bulk, dup checks) · egress hogs (`claim_unqualified` id-scan or
anti-join RPC, matcher server-side city filter) · column diet + planned
counts + facets aggregate.

**Wave 3:** round-trip consolidation (api route reshapes, `add_tags` RPC,
practice-detail join, session endpoint, bulk toggle route + frontend
wiring).
