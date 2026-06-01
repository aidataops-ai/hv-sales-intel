# Phase 3: Cold Call Playbook + CRM Pipeline — Design

**Date:** 2026-04-17
**Status:** Approved

## Goal

Give Apex & Virtuals' sales reps a dedicated Call Prep page with a GPT-generated cold call playbook (opening, discovery questions, pitch, objection handling, closing), a notepad for call notes, and a CRM pipeline to track each practice from NEW through CLOSED WON/LOST.

## Non-goals (deferred to later phases)

- Scheduled scraping, review sentiment trending, competitor insight (Phase 4)
- Email/outreach automation
- Multi-user auth or rep assignment
- Call recording or VoIP integration

## Call Script Generation

When a rep opens the Call Prep page for a practice, the system checks for a cached script. If none exists (or if the practice was re-analyzed since the last script), GPT-4o-mini generates a structured playbook using the practice's Phase 2 analysis data.

### Playbook sections (5 sections, stored as JSON array):

1. **Opening** — personalized intro referencing the practice by name, category, and a specific detail from the website crawl or reviews
2. **Discovery Questions** — 3-4 questions designed to surface staffing pain points identified in the analysis
3. **Pitch** — tailored value prop for Apex & Virtuals staffing services, directly addressing the practice's specific pain points and sales angles
4. **Objection Handling** — 3-4 common objections with prepared rebuttals (e.g., "We already have a recruiter," "We can't afford it," "We're not hiring right now")
5. **Closing** — next steps language, meeting request, follow-up framing

### Caching behavior:

- Script stored in `call_script` text column (JSON string).
- When `analyze_practice` runs and updates scores, it sets `call_script` to null — triggering regeneration on next Call Prep page visit.
- "Regenerate Script" button on the page forces a fresh generation.
- Mock fallback: when `OPENAI_API_KEY` is empty, returns a generic but category-appropriate playbook.

## CRM Pipeline

### Statuses (in order):

`NEW → RESEARCHED → SCRIPT READY → CONTACTED → FOLLOW UP → MEETING SET → PROPOSAL → CLOSED WON → CLOSED LOST`

### Auto-transitions:

- `NEW` — default when practice is first added to the system
- `RESEARCHED` — auto-set when AI analysis completes (Phase 2 analyze endpoint)
- `SCRIPT READY` — auto-set when call script is generated

### Manual transitions:

- `CONTACTED` through `CLOSED WON` / `CLOSED LOST` — set by the rep via dropdown on the Call Prep page

### Status badge colors on practice card:

- Gray: NEW
- Blue: RESEARCHED, SCRIPT READY
- Amber: CONTACTED, FOLLOW UP
- Teal: MEETING SET, PROPOSAL
- Green: CLOSED WON
- Rose: CLOSED LOST

### Sidebar filter:

Add a status filter dropdown alongside existing category and rating filters. Default shows all statuses except CLOSED LOST.

## Call Prep Page (`/practice/[place_id]`)

Full-width page with three columns on cream background.

### Layout:

```
┌──────────────────────────────────────────────────────────────┐
│  ← Back to Map    Apex&Virtuals    [Status: CONTACTED ▾]   │
├────────────────┬─────────────────────┬───────────────────────┤
│  PRACTICE INFO │   CALL PLAYBOOK     │   NOTES & ACTIONS     │
│  (280px)       │   (flex-1)          │   (320px)             │
│                │                     │                       │
│  Name          │   Opening           │   Call Notes           │
│  Rating/reviews│   Discovery Qs      │   [textarea]           │
│  Phone/Website │   Pitch             │   [Save Notes]         │
│  Analysis      │   Objections        │                       │
│  Scores        │   Closing           │   Activity History     │
│  Pain points   │                     │   timestamped entries  │
│  Sales angles  │   [Regenerate]      │                       │
└────────────────┴─────────────────────┴───────────────────────┘
```

**Left column (280px):** Practice details + Phase 2 analysis summary. Read-only. Phone/website as action buttons. Score bars. Pain points and sales angles lists.

**Center column (flex-1):** The full call playbook. Each section has a header icon and title. Scrollable. "Regenerate Script" button at the bottom.

**Right column (320px):** 
- Status dropdown at the top of the page header (not in this column).
- Call notes textarea. Saves to `notes` field on blur or "Save Notes" button click.
- Activity history: timestamped entries showing status changes (e.g., "Apr 16 — Analyzed", "Apr 17 — Script generated", "Apr 17 — Status: CONTACTED").

### Navigation:

- "Call Prep" button on each practice card (next to Call/Website/Analyze)
- Clicking the practice name on the card also navigates to the Call Prep page
- "← Back to Map" link in the page header returns to the main map view

## Architecture

### New schema column:

```sql
ALTER TABLE practices ADD COLUMN IF NOT EXISTS call_script text;
```

### New/modified backend files:

```
src/
├── scriptgen.py         (create) GPT playbook generator + mock fallback
├── analyzer.py          (modify) Clear call_script on re-analysis, auto-set status to RESEARCHED
├── storage.py           (modify) Add update_practice_status, update_practice_notes, get/set script

api/
└── index.py             (modify) Add GET/POST script endpoints, PATCH practice endpoint
```

### New/modified frontend files:

```
web/
├── app/
│   ├── page.tsx                  (modify) Status filter, card name links
│   └── practice/
│       └── [place_id]/
│           └── page.tsx          (create) Call Prep page
├── components/
│   ├── practice-card.tsx         (modify) Add Call Prep button, status badge, name as link
│   ├── status-badge.tsx          (create) Colored status pill
│   ├── script-view.tsx           (create) Playbook renderer (5 sections)
│   ├── notes-panel.tsx           (create) Notepad + save + activity history
│   ├── filter-bar.tsx            (modify) Add status filter dropdown
│   └── top-bar.tsx               (no change)
├── lib/
│   ├── types.ts                  (modify) Add call_script field, Script type
│   └── api.ts                    (modify) Add getScript, regenerateScript, updateStatus, updateNotes
```

## API Endpoints

### Get or generate script:
```
GET /api/practices/{place_id}/script
```
- If `call_script` is not null, returns cached script.
- If null, generates via GPT, stores, sets status to SCRIPT READY, returns.
- Returns `{ sections: [{ title, icon, content }] }`

### Force regenerate script:
```
POST /api/practices/{place_id}/script
```
- Always regenerates. Stores new script. Returns it.

### Update practice (status + notes):
```
PATCH /api/practices/{place_id}
Body: { "status": "CONTACTED", "notes": "Spoke with Dr. Smith..." }
```
- Updates whichever fields are provided. Returns updated practice.

## Auto-status transitions

- `analyze_practice` in `analyzer.py`: after storing scores, set `status = 'RESEARCHED'` if status is currently `NEW`.
- Script generation endpoint: after storing script, set `status = 'SCRIPT READY'` if status is currently `NEW` or `RESEARCHED`.
- These auto-transitions never override a more advanced status (e.g., if status is already `CONTACTED`, don't regress to `RESEARCHED`).

## Mock Fallback

When `OPENAI_API_KEY` is empty, `scriptgen.py` returns a generic but category-appropriate playbook with realistic placeholder content for all 5 sections. Same pattern as Phase 1/2 mock fallbacks.

## Decisions Log

- **Full playbook over single script** — reps need structure (opening → closing), not just a paragraph to read
- **Cached + auto-regenerate** — saves GPT calls, but stays fresh when new analysis data arrives
- **Dedicated page over drawer** — the playbook + notes + practice info need room; a drawer would be cramped
- **Auto-status for early stages** — NEW → RESEARCHED → SCRIPT READY happen automatically, manual from CONTACTED onward
- **Three-column layout** — practice context (left), script (center focus), notes/actions (right) mirrors how a rep preps for a call
- **Status filter default excludes CLOSED LOST** — keeps the sidebar focused on active opportunities
