# Apex Sales Intel — Pricing & Profitability Model

**Status:** Strategy doc, not yet implemented in code.
**Last updated:** 2026-05-25

This document captures the pricing thesis for Apex Sales Intel. It pairs with `src/usage.py` (which records the variable costs that the unit economics below are derived from) and the `/admin/usage` dashboard.

---

## 1. What we're actually selling

> **AI-native outbound infrastructure for niche vertical sales teams.**

We are **not** selling:
- Lead scraping
- Google Places access
- GPT analysis

Each of those is a commodity. The defensible value is the layer **above** them:

- Multi-tenant ICP scoring
- Vertical-specific enrichment
- Workflow automation (search → score → call prep → email → CRM sync)
- Call prep generation
- Salesforce / Clay / RingCentral / M365 integration
- Cross-tenant deduplication on the shared discovery layer
- Operational intelligence (usage, costs, attribution)

This positioning means we **do not** price like Apollo, Clay, or a lead database. We price like:

- AI workflow SaaS
- Vertical GTM infrastructure
- An outbound operating system

---

## 2. Cost structure (current logged values)

From `usage_events` after ~one week of live runs (snapshot 2026-05-25):

### Discovery (Google Places API)
- ~$0.06–$0.10 per **Places Text Search** batch (2–3 paged calls).
- Returns up to 60 leads per batch.
- **Discovery is the cheap layer, not the expensive one.**

### AI analysis (per lead)

| Metric | Range |
|---|---|
| Input tokens | 2,000 – 5,000 |
| Output tokens | 250 – 450 |
| Cost per analyze | $0.005 – $0.01 |

Working average: **$0.0075 per fully analyzed lead.**

---

## 3. Unit economics

### Discovery-only lead (search, dedup, store)

| Component | Cost |
|---|---:|
| Places search (amortized) | $0.002 |
| Dedup + storage | negligible |
| **Total** | **~$0.002–$0.003** |

### Fully-analyzed, call-ready lead

| Component | Cost |
|---|---:|
| Places | $0.002 |
| GPT analysis | $0.0075 |
| Enrichment overhead | $0.002 |
| Infra (DB, bandwidth, Vercel) | $0.001 |
| **Total** | **~$0.012–$0.015** |

**Marginal cost to produce a fully-analyzed, ICP-scored, call-ready lead: ~$0.015.**

This number is the foundation of every pricing tier below.

---

## 4. Pricing architecture

**Seat + dynamic credits.** Not unlimited. Not pure per-lead.

| Layer | What it covers | Why |
|---|---|---|
| **Platform fee (seats)** | CRM, workflows, integrations, users, exports, pipeline, collaboration | Predictable revenue; covers fixed eng + support cost |
| **Credits** | Places searches, AI analysis, enrichment, exports | Variable cost passes through with margin; users feel proportional value |

Pure per-lead pricing invites comparison to cheap databases and compresses margins. Credits abstract the infrastructure the way OpenAI / Clay / Apollo / Instantly / Smartlead already do.

---

## 5. SaaS tiers

### Starter — $299/mo
| | |
|---|---|
| Seats | 3 |
| Included credits | 10,000 |
| Bulk Scan | yes (limited) |
| Salesforce sync | no |
| **Target** | Small agencies, local outbound shops |
| **Gross margin** | ~85–90% |

### Growth — $999/mo *(expected core tier)*
| | |
|---|---|
| Seats | 10 |
| Included credits | 50,000 |
| Bulk Scan | yes |
| Salesforce sync | yes |
| **Gross margin** | ~80–88% |

### Scale — $2,500–$5,000/mo
| | |
|---|---|
| Seats | 25 |
| Included credits | 200,000 |
| Priority compute | yes |
| API access | yes |
| **Gross margin** | ~80–85% |

### Enterprise — custom
- Dedicated enrichment pipelines
- White labeling
- Managed bulk scans
- API + SSO
- Per-tenant Salesforce / Clay / M365 isolation (Phase 9 of the multi-tenant rollout)

---

## 6. Credit cost table

Credits reflect **both** the API cost **and** the operational value. This prevents abuse and aligns incentives with how the product is actually used.

| Action | Credits | Underlying cost (¢) | Cost per credit (¢) |
|---|---:|---:|---:|
| Places query (one paged search) | **25** | ~6–10 | ~0.3 |
| New lead discovered (per Place returned) | **1** | ~0.15 | 0.15 |
| AI analyze a lead | **5** | ~0.75 | 0.15 |
| Call-prep script generation | **3** | ~0.5 | 0.17 |
| Email draft | **2** | ~0.3 | 0.15 |
| Clay owner enrichment | **10** | (varies; ~2–5) | ~0.3 |
| CSV export row | **1** | ~0.01 | ~0.01 |

Round numbers per credit: **~$0.0015 ≈ 0.15 ¢**, which yields a **margin of ~10×** over raw infra at the per-action level.

**Credit packs sold separately:**
- $100 (≈ 67k credits)
- $500 (≈ 350k credits)
- $2,000 (≈ 1.5M credits)

---

## 7. Five-thousand-lead upload — worked example

A common pricing question: a tenant uploads 5,000 company names + cities and asks the system to analyze them all.

| Component | Cost |
|---|---:|
| Places resolution + details (5,000 × ~$0.005–0.01) | $25–50 |
| AI analysis (5,000 × $0.0075) | ~$37.50 |
| Storage + bandwidth | $10–20 |
| **Total infra cost** | **$75–110** |

**Recommended price for 5,000 analyzed leads: $500–$1,500.**

The 5× to 15× markup is justified by:
- Saved SDR labor (weeks of manual research compressed into minutes)
- Reproducibility of scoring across all 5,000 leads
- Workflow integration (the leads land in their pipeline, not a CSV)

Enterprise managed-outbound packages (5,000 leads + reviewed scripts + warm-up + cadence): $5,000–$15,000.

---

## 8. Margin targets

| Stage | Gross margin |
|---|---:|
| Early beta | 70% |
| Stable product | 80–85% |
| Mature SaaS | 85–90% |

The infra layer is **cheap enough that it will not be the long-term cost driver.** Expect later cost growth in:
- Support
- Compute bursts (large bulk scans)
- Enrichment vendor costs (Clay credits, third-party data)
- Outbound mail infrastructure (warmup, deliverability)
- SDR onboarding / customer success

These are people + vendor costs, not OpenAI tokens.

---

## 9. Positioning

> **"AI-native vertical outbound operating system."**

NOT:
- Lead scraper
- Maps scraper
- GPT analyzer
- ICP definition tool

The positioning supports:
- Higher ACVs (operating system pricing vs. tool pricing)
- Higher margins (workflow value vs. raw infra)
- Enterprise expansion (per-tenant integrations, white-label)
- Lower churn (replacing it requires replacing a workflow, not a feature)

---

## 10. Strategic moat

The defensible piece is **NOT**:
- Places API access
- GPT prompts
- Lead discovery

It **IS**:
- Per-tenant AI scoring (each company's ICP, vocabulary, weights)
- Reproducible, deterministic, audited outputs (bucketed AI + seed + content-hash caching)
- Per-tenant workflow memory (status, notes, call history)
- Shared, deduped discovery layer (Google bill is linear with effort, not with tenant count)

That's the "tenant-specific AI scoring + workflow memory" thesis. Phase 6 of the multi-tenant rollout (per-tenant analyzer prompt) is what activates the first half of that moat in production.

---

## 11. Implementation hooks

Where this doc connects to the code today:

- **Variable cost source of truth** → `src/usage.py` (constants), `usage_events` table.
- **Admin transparency** → `/admin/usage` shows per-period spend by kind and model.
- **Recompute** → `POST /api/admin/usage/recompute-costs` re-prices history after pricing edits.
- **What's missing for credits** → a `credits_per_action` table + per-event credit decrement, a `companies.credit_balance` column, and a `/admin/billing` page. None of this is built yet; it's the next deliverable when we move from demo to billable beta.

---

## 12. Final recommendation

**Ship as: monthly platform + credits + enterprise.**

| Path | When |
|---|---|
| Demo today | No billing — show /admin/usage cost dashboard to prove the unit economics |
| Beta (60–90 days) | Manual invoicing against Growth tier ($999/mo) for first 5–10 design partners |
| GA | Self-serve Starter + Growth via Stripe; Scale + Enterprise stay sales-led |
