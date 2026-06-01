# Phase 2: AI Business Analysis — Design

**Date:** 2026-04-16
**Status:** Approved

## Goal

Add AI-powered analysis to each practice card so Apex & Virtuals' sales team can instantly see pain points, hiring signals, and staffing-specific sales angles — powered by website crawling, Google reviews, and GPT-4o. Works on-demand per practice or in bulk via "Score All."

## Non-goals (deferred to later phases)

- Cold call script generator (Phase 3)
- CRM status tracking, notes (Phase 3)
- Scheduled scraping, review sentiment trending, competitor insight (Phase 4)
- Recommendations for other Apex divisions (Billing, IT, Marketing, etc.) — this tool focuses solely on staffing signals for Apex & Virtuals

## Context: Apex & Virtuals

Apex & Virtuals is a healthcare staffing/talent acquisition company. The AI analysis focuses on finding practices that need staffing help — front desk, medical assistants, clinical staff, admin/VA positions. The `hiring_signal_score` is the most important signal.

## Data Collection Pipeline

When a user clicks "Analyze" on a practice card (or "Score All" for bulk):

1. **Website crawl** — `httpx` fetches the practice's website URL. Starts from homepage, discovers internal links, crawls up to 10 pages. Extracts text content (strips HTML). Prioritizes: homepage, careers/jobs, about, services, team pages.
2. **Google Reviews** — Places API Place Details endpoint with `reviews` field mask. Returns up to 5 most relevant reviews with text + rating.
3. **GPT-4o analysis** — Single prompt with all crawled text + review text. Returns structured JSON with: `summary`, `pain_points`, `sales_angles`, `lead_score`, `urgency_score`, `hiring_signal_score`.
4. **Upsert** — Write analysis results to Supabase `practices` table (Phase 2 columns already exist in schema).

## Scoring Dimensions

All scores are 0–100 integers.

- **lead_score** — Overall composite. Weighted: hiring signals (50%), urgency indicators (30%), practice size/growth (20%). Used for pin color and sorting.
- **urgency_score** — Needs staffing help NOW: negative reviews about wait times/staff shortages, outdated website, understaffed signals, complaints about responsiveness.
- **hiring_signal_score** — Specifically looks for roles Apex can fill: front desk, medical assistants, clinical staff, admin/VA positions, job postings, "we're hiring" pages, career page existence, recent job listings.

## GPT-4o Prompt Strategy

System prompt tells GPT-4o it is a healthcare sales intelligence analyst for Apex & Virtuals, a staffing/talent acquisition company. Instructs it to focus on hiring signals and staffing pain points. User prompt contains the crawled website text + review text. Returns strict JSON:

```json
{
  "summary": "1-2 sentence practice overview relevant to staffing needs",
  "pain_points": ["staffing-related pain point 1", "pain point 2", "..."],
  "sales_angles": ["pitch angle 1", "pitch angle 2", "..."],
  "lead_score": 72,
  "urgency_score": 65,
  "hiring_signal_score": 85
}
```

No prose outside the JSON structure. 2-4 pain points, 2-3 sales angles.

**Storage note:** `pain_points` and `sales_angles` are `text` columns in Postgres. Store the JSON arrays as serialized JSON strings. Frontend parses them back to arrays for display.

## Architecture

```
src/
├── crawler.py          Website crawler (httpx, max 10 pages, HTML→text)
├── reviews.py          Google Places reviews fetcher (Place Details)
├── analyzer.py         Orchestrator: crawl → reviews → GPT-4o → scores
├── places.py           (existing) — search
├── storage.py          (existing) — upsert/query
├── models.py           (existing) — Practice model (Phase 2 fields already defined)
└── settings.py         (existing) — add OPENAI_API_KEY

api/
└── index.py            Add POST /api/practices/{place_id}/analyze
```

## API Endpoint

```
POST /api/practices/{place_id}/analyze
Body (optional): { "force": true }
```

- If practice has no website, skips crawl, uses reviews only.
- If practice already has `lead_score` and `force` is not true, returns cached results.
- Returns the full updated practice object with Phase 2 fields populated.
- On error (crawl fails, GPT fails), returns partial results with error message — does not block.

## Mock Fallback

When `OPENAI_API_KEY` is empty, `analyzer.py` returns mock analysis data:
- Random scores (lead: 30-90, urgency: 20-80, hiring: 25-95)
- Canned summary, pain points, and sales angles appropriate to the practice category
- Same pattern as Phase 1 mock fallback — UI works with zero API keys

## New Env Var

```
OPENAI_API_KEY=     # empty = mock analysis mode
```

## UI Changes

### Practice card — post-analysis state

When analysis completes, the existing card expands inline:
- **Lead score badge** — colored pill next to practice name. Teal (0-49), amber (50-74), rose (75-100).
- **Summary** — 1-2 sentence overview below address.
- **Pain points** — bulleted list (2-4 items).
- **Sales angles** — bulleted list (2-3 items), framed as "pitch this."
- **Score breakdown** — three small horizontal bars labeled Lead / Urgency / Hiring Signal with numeric values.

### New card buttons

- **"Analyze"** button (teal outline) on each unscored practice card. Shows spinner while running. Replaces itself with "Re-analyze" after completion.

### Bulk action

- **"Score All"** button in the top bar, next to "Scan City." Analyzes every unscored practice in the current list sequentially. Shows progress: "Scoring 3/20..."

### Map pin colors

- Unscored practices: teal (current behavior)
- Scored 0-49: teal
- Scored 50-74: amber
- Scored 75+: rose (hot lead)

### Sidebar sorting

After scoring, practices auto-sort by `lead_score` descending (hot leads float to top). Unscored practices sort to the bottom.

## Frontend Changes

### New in `lib/api.ts`

- `analyzePractice(placeId: string, force?: boolean): Promise<Practice>` — calls POST analyze endpoint, mock fallback when no API URL.

### Component changes

- `practice-card.tsx` — add Analyze button, expanded analysis section, score badge, score bars.
- `top-bar.tsx` — add "Score All" button with progress state.
- `map-view.tsx` — pin color based on `lead_score` (teal/amber/rose).
- `page.tsx` — sort logic for scored vs unscored, bulk analyze handler.

## Decisions Log

- **Single-pass GPT-4o** over two-stage pipeline — simpler, one LLM call per practice, cost is marginal at sales team volume.
- **Full site crawl (10 pages)** over homepage-only — richer signal, especially for careers pages.
- **Google Places reviews** over scraping — clean, reliable, 5 most relevant reviews per practice.
- **Staffing-only focus** — tool recommends Apex & Virtuals staffing services only, not other Apex divisions.
- **On-demand + bulk** — individual Analyze button per card + Score All for the full list.
- **Inline expansion** over detail drawer — keeps the workflow in the sidebar, no context switch.
- **Mock fallback** — consistent with Phase 1 pattern, UI works with zero config.
