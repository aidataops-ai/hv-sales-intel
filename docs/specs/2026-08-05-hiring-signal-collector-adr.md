# Job-Posting Leads — Architecture Decision Record

**Date:** 2026-08-05
**Status:** Proposed — awaiting review
**Scope:** v1 — a standalone lead module inside hv-sales-intel

---

## Context

A practice that has just posted a front-desk, scheduling, or billing role has published
confirmed operational pain, an approved budget, and urgency. v1 captures those postings
from LinkedIn and Indeed, qualifies them with AI, and shows them to an operator.

**v1 scope is deliberately narrow: collect, qualify, show.** No connection to practices,
no scoring, no enrichment. Those are separate decisions for later.

---

## ADR-01 — A standalone module, fully separate from the practice pipeline

**Decision.** Job-posting leads get their own tables, their own API routes, and their own
UI section. They do not touch `practices`, the analyzer, the scorer, or anything in the
existing pipeline.

**Rationale.** A posting and a practice are different objects with different lifecycles:

| | Practice | Job-posting lead |
|---|---|---|
| Identity | Google `place_id` | board `(source, external_id)` |
| Shape | rating, reviews, lat/lng, hours | title, salary, work mode, posted date |
| Lifecycle | permanent | perishable — postings close over time |
| Workflow | research → call → email | triage → approve/reject → outreach |
| Discovery | operator-initiated search | continuous background collection |

Coupling them would force every lead through place resolution — billable, fuzzy, and
permanently impossible for the ~12% of postings with no employer name — before anyone
had even looked at it.

**Consequence.** A lead is workable the moment it is qualified. Nothing blocks on an
external lookup. The module shares only what every module shares: auth, tenancy, the
usage ledger.

**Accepted.** Posting leads have no 0–100 score. Ranking comes from the qualifier's own
output (ADR-07).

---

## ADR-02 — Two sources: Indeed and LinkedIn, via `python-jobspy`

**Decision.** Add `python-jobspy` and collect from Indeed and LinkedIn.

**Rationale.** Measured during evaluation:

| | Indeed | LinkedIn |
|---|---:|---:|
| latency per query | ~1.5 s | ~22 s |
| structured salary | ~44% | 0% |
| structured posting date | 100% | ~71% |
| employers that are plausible independents | 87% | 58% |

The two boards overlap by only **~5% at the employer level**, so running both roughly
doubles reachable supply rather than adding at the margin. Indeed is primary; LinkedIn
is a supplement on a slower rotation.

**Risk accepted.** `python-jobspy` reaches Indeed through an undocumented mobile-app API
using a key embedded in the library. That key can be rotated without notice, at which
point Indeed collection stops until the library ships a fix.

**Mitigation.** Pin the version; alert on zero-row runs; keep LinkedIn independent so a
rotation degrades the pipeline rather than halting it.

---

## ADR-03 — Search targets are defined in config files

**Decision.** The search matrix is `(role term × location)`, defined in checked-in JSON
config and seeded per tenant into `company_search_targets`. Collection reads the table.

**Rationale.** Config is explicit, reviewable in a diff, and testable. Search-term
quality is the single largest driver of lead quality and needs tuning by hand against
observed results.

**Consequence.** Per-tenant divergence is achieved by editing that tenant's
`company_search_targets` rows, not by forking config files.

**Important.** Terms must be **what practices actually post**, not what we sell.
Searching a service name returns near-zero results, or competitors advertising the same
service. Terms are job titles: "dental receptionist", "patient scheduler".

---

## ADR-04 — Shared raw postings, per-tenant lead rows

**Decision.** Two layers:

- `job_postings` — one row per posting, **shared** across tenants, keyed
  `(source, external_id)`
- `company_job_leads` — one row per (company, posting): the qualification verdict **and**
  the workflow state

**Rationale.** Two tenants with overlapping terms will surface the same posting; storing
it twice wastes calls and creates divergent copies. Qualification and workflow are
tenant-private.

**Divergence from the practices convention, deliberately.** `practices` splits analyses
and state into separate tables. Here they are one row, because the lead feed is the hot
path and a single-table read beats a join on every page load. The cost is that
re-qualification must write **only** the verdict columns and never touch workflow
columns, or it would clobber an SDR's status.

---

## ADR-05 — One AI pass, at intake

**Decision.** A single qualifier reads the raw posting and answers two questions: is the
employer one we sell to, and is the role one we can serve. Batched, ~172 in / ~175 out
tokens per posting.

**Consequence.** Qualification is the only per-lead cost in v1.

---

## ADR-06 — Qualification model and settings

**Decision.** `gpt-5.6-terra`, `reasoning_effort="medium"`, JSON-mode, batched at 20
postings per call.

**Rationale.** Benchmarked against higher reasoning effort on identical inputs: both
scored identically on every accuracy test (employer classification, role suitability,
control groups), while high effort cost ~57% more output tokens and ~81% more wall
clock. The only decisions that differed sat below the human-review threshold.

**Constraint.** The model accepts only the default `temperature` of 1, so runs are not
byte-reproducible. Measured impact is small — 95% decision stability, median per-posting
confidence drift 0.01 — but a single run cannot distinguish a real improvement from
sampling variance.

---

## ADR-07 — Ranking comes from confidence bands and signal attributes

**Decision.** Leads are triaged on the qualifier's confidence:

| Band | Range | Behaviour |
|---|---|---|
| Ready | ≥ 0.85 | Surfaces as a normal new lead |
| Check | 0.70–0.85 | Surfaces flagged for a glance |
| Decide | < 0.70 | Held in a review queue |

Secondary sort: posting recency, then salary present, then work mode.

**Rationale.** The confidence score is well calibrated but not readable. Measured across
repeated runs: **zero of 32 high-confidence postings ever changed decision**, while every
observed decision flip occurred in the mid and low bands. It also reliably self-flags its
own errors — misclassifications consistently scored lowest in their batch.

A decimal is not actionable; nobody can act on 0.76 versus 0.82. Bands convert a
calibrated signal into a workflow decision.

**The 0.85 boundary is an initial estimate** and should be tuned against
booked-versus-rejected outcomes once that data exists.

---

## ADR-08 — Work mode is determined from evidence and drives the pitch

**Decision.** The qualifier receives the board's remote flag, the location string, and a
description excerpt, and classifies `onsite | remote | hybrid` from that evidence. It may
return `null` when the posting genuinely does not say. It must never default.

**Rationale.** A prompt that says "guess, defaulting to onsite" returns onsite for
essentially everything, including postings explicitly flagged remote. Work mode selects
the pitch: an on-site posting with an advertised wage supports a direct cost comparison;
a remote posting does not.

Measured: on-site is ~89% of supply, and ~28% of on-site postings carry a salary.

---

## ADR-09 — Bounded, queue-driven cron stages

**Decision.** Two idempotent background stages, each cron-triggered, each claiming a
bounded batch: `collect` → `qualify`.

**Rationale.** The API deploys as serverless functions behind
`rewrites: /api/(.*) -> /api/index.py`. A full sweep is minutes of wall clock and will
not fit one invocation. Stages that drain a bounded queue keep every invocation short and
make retries free.

**Consequence.** Every stage must be safe to re-run and may not assume the previous one
completed.

---

## ADR-10 — Model calls metered through the existing usage ledger

**Decision.** Qualifier calls record via `usage.record_openai(kind="openai_qualify")`,
attributed to `company_id` and debiting tenant credits.

**Rationale.** The platform already meters every billable external call and prices credits
from it. A new subsystem making unmetered calls would put real cost outside the billing
model. This is shared billing infrastructure, not a coupling to the practice pipeline.

---

## ADR-11 — Tenant isolation consistent with existing tables

**Decision.** `company_job_leads` and `company_search_targets` get RLS scoped by
`company_members`. `job_postings` is authenticated-read across tenants.

**Rationale.** Raw postings are public data and should dedupe to one row regardless of
which tenant collected them first. Verdicts and workflow state are tenant-private. The
backend writes with the service-role key and enforces `company_id` in code; RLS is
defence in depth.

---

## Deferred — explicitly out of v1

| Question | Note |
|---|---|
| **Linking leads to `practices`** | The obvious next step. Cheapest version is a free name+city match against existing rows; a billable Places lookup is the fuller one. Not in v1. |
| **Scoring leads with the analyzer** | Needs a website and reviews, which a posting does not have. Would require linkage first. |
| **Generating search config from tenant data** | Config files are the v1 source of truth. |
| **Merging the two lead queues into one view** | Ship side by side, then decide from behaviour. |
| **Career-page / ATS scraping, more boards** | Two sources with ~5% overlap already give broad coverage. |

---

## Known open issues

1. **Ambiguous role terms pull non-target industries.** "Coordinator", "scheduler" and
   "specialist" are not healthcare-specific and return construction, IT and consulting
   employers. The qualifier rejects them correctly, but each costs tokens. Extend the
   config prefilter as they appear.

2. **~12% of Indeed postings have no employer name** (confidential listings). They can be
   qualified on role and location, but outreach cannot address the business by name.
   Decide whether to surface or suppress them.

3. **No score means no obvious sort.** ADR-07 defines the substitute, but it is untested
   against operator behaviour — watch what people actually sort by once it ships.

4. **Postings close, and v1 does not track it.** A lead stays in the feed after the
   employer fills the role. Mitigation is presentational: the feed shows the posting
   date so an operator can judge staleness before calling. Revisit if operators report
   reaching filled roles.
