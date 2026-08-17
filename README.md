# HV Sales Intel

Map-based sales intelligence tool for **Health & Virtuals** — a healthcare staffing / talent-acquisition company. Helps reps find healthcare practices, analyze staffing signals with AI, generate cold-call playbooks, and track each practice through a CRM pipeline.

The app runs end-to-end with **zero external config** (mock fallbacks everywhere). Wire in API keys to enable live data.

---

## What's shipped

| Feature | Entry point |
| --- | --- |
| City/keyword search → Leaflet map + sidebar | [web/app/page.tsx](web/app/page.tsx) |
| Google Places text search + mock fallback | [src/places.py](src/places.py) |
| Per-practice AI analysis (pain points, sales angles, 3 scores) | [src/analyzer.py](src/analyzer.py) |
| Multi-source review collection (Google + first-party + Yelp/Healthgrades/etc.) | [src/reviews.py](src/reviews.py) |
| Website crawl (up to 10 pages, prioritized careers/team/about) | [src/crawler.py](src/crawler.py) |
| Cold-call playbook generator (5 sections) | [src/scriptgen.py](src/scriptgen.py) |
| Call Prep page (3-column: info / script / notes) | [web/app/practice/[place_id]/page.tsx](web/app/practice/[place_id]/page.tsx) |
| CRM pipeline (9 statuses, auto + manual transitions) | [api/index.py](api/index.py) |
| RingCentral click-to-call (desktop app deep link + web fallback) | [web/lib/ringcentral.ts](web/lib/ringcentral.ts) |
| Rescan — re-pull latest Google Places data for a practice | `POST /api/practices/{place_id}/rescan` |
| Supabase persistence (wide schema, Phase 2/3 columns nullable) | [supabase/schema.sql](supabase/schema.sql) |

---

## Architecture

```
hv-sales-intel/
├── api/
│   └── index.py            FastAPI app (Vercel-compatible)
├── src/
│   ├── settings.py         Env vars via pydantic-settings
│   ├── models.py           Practice pydantic model
│   ├── places.py           Google Places search + get + mock fallback
│   ├── mock_practices.json 50 realistic healthcare practices
│   ├── crawler.py          Website crawler (httpx + BeautifulSoup)
│   ├── reviews.py          Google Places reviews + first-party + third-party discovery
│   ├── analyzer.py         Orchestrates crawl → reviews → GPT → scores
│   ├── scriptgen.py        GPT-generated 5-section cold-call playbook
│   └── storage.py          Supabase CRUD (upsert, query, get, update)
├── supabase/
│   └── schema.sql          practices table DDL
├── web/                    Next.js 14 App Router (client-rendered)
│   ├── app/
│   │   ├── layout.tsx      Root layout (Fraunces + Plus Jakarta Sans)
│   │   ├── page.tsx        Map + sidebar home view
│   │   └── practice/
│   │       └── [place_id]/page.tsx   Call Prep page
│   ├── components/         Cards, filters, map, script, notes, call button
│   └── lib/
│       ├── api.ts          All fetch calls to backend; mock fallbacks
│       ├── types.ts        Practice + Script types, JSON-array helper
│       ├── mock-data.ts    Client-side mock practices
│       ├── ringcentral.ts  Click-to-call deep link + web fallback
│       └── utils.ts        cn() classname helper
├── docs/
│   ├── specs/              Dated design docs (Phase 1/2/3 + supplements)
│   └── superpowers/plans/  Dated implementation plans
├── requirements.txt        Python deps
└── vercel.json             Rewrites /api/* → api/index.py
```

### Request flow

```
Browser ──► Next.js (web/) ──► FastAPI (api/index.py) ──► Supabase
                                     │
                                     ├── Google Places API (search, get, reviews)
                                     ├── httpx website crawler (10 pages)
                                     ├── DuckDuckGo HTML (third-party review discovery)
                                     └── OpenAI (analysis + script generation)
```

Every external integration has a mock fallback so the app renders without any keys.

---

## Running locally

### Backend

```bash
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000
```

### Frontend

```bash
cd web
npm install
npm run dev
```

Visit http://localhost:3000. With no `.env` set, the app runs on mocks (50 practices, canned analysis, canned scripts).

---

## Environment variables

### Backend (`.env` at repo root)

| Var | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | `""` | Empty → mock search + no Google reviews. Set → Places API (New) searchText, place details, reviews. |
| `SUPABASE_URL` | `""` | Empty → storage calls no-op (app still runs). |
| `SUPABASE_KEY` | `""` | Paired with URL. |
| `OPENAI_API_KEY` | `""` | Empty → mock analysis + mock script. Set → GPT-powered. |
| `OPENAI_MODEL` | `"gpt-4o"` | Model used for both analysis and script generation. |

### Frontend (`web/.env.local`)

| Var | Default | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `""` | Backend base URL. Empty → all API calls fall through to client-side mocks. |
| `NEXT_PUBLIC_RINGCENTRAL_WEB_APP_URL` | `"https://app.ringcentral.com"` | Override for self-hosted RingCentral instance. |

---

## API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health check |
| GET | `/api/practices?city=&category=&min_rating=&limit=` | List from Supabase |
| POST | `/api/practices/search` — `{ query, refresh? }` | Google Places search (or mock) + upsert |
| GET | `/api/practices/{place_id}` | Single practice |
| POST | `/api/practices/{place_id}/analyze` — `{ force?, rescan? }` | Crawl + reviews + GPT → scores. `rescan:true` re-pulls Google Places first. |
| POST | `/api/practices/{place_id}/rescan` | Refresh a stored practice from Google Places (no analysis) |
| GET | `/api/practices/{place_id}/script` | Cached script or generate + store |
| POST | `/api/practices/{place_id}/script` | Force regenerate |
| PATCH | `/api/practices/{place_id}` — `{ status?, notes? }` | Update CRM fields |

---

## CRM pipeline

```
NEW → RESEARCHED → SCRIPT READY → CONTACTED → FOLLOW UP
    → MEETING SET → PROPOSAL → CLOSED WON | CLOSED LOST
```

- **Auto** transitions: NEW → RESEARCHED (on analyze), NEW/RESEARCHED → SCRIPT READY (on script gen). Never regresses an already-advanced status.
- **Manual** transitions: rep sets CONTACTED onwards via dropdown on the Call Prep page.
- **Sidebar filter** defaults to "Active" (hides `CLOSED LOST`).

---

## Design docs

Dated, approved design docs live in [docs/specs/](docs/specs/). They are **historical** — read them in order to understand how features were scoped.

- [Phase 1 — Lead Discovery + Map UI](docs/specs/2026-04-16-phase1-lead-discovery-design.md)
- [Phase 2 — AI Business Analysis](docs/specs/2026-04-16-phase2-ai-analysis-design.md)
- [Phase 3 — Cold Call Playbook + CRM](docs/specs/2026-04-17-phase3-call-playbook-crm-design.md)
- [Post-Phase-3 additions](docs/specs/2026-04-22-post-phase3-additions.md) — features added after the Phase 3 spec was approved

Implementation plans: [docs/superpowers/plans/](docs/superpowers/plans/).

---

## Deployment

`vercel.json` rewrites `/api/*` to the FastAPI handler. The Next.js app and Python API deploy as a single Vercel project.
