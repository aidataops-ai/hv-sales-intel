# Phase 1: Lead Discovery + Map UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a map-based healthcare practice discovery tool with a FastAPI backend (Google Places + mock fallback + Supabase) and a Next.js frontend (Leaflet map + Ivory-themed sidebar + search/filter), deployable to Vercel.

**Architecture:** Python backend in `api/` + `src/` handles data (Places API or mock, Supabase storage). Next.js 14 App Router in `web/` renders the map + sidebar UI. Server components fetch from the Python API; mock data on both sides means the app runs with zero external config.

**Tech Stack:** FastAPI, httpx, supabase-py, Next.js 14, React 18, TypeScript, Tailwind CSS, react-leaflet, Leaflet, Fraunces + Plus Jakarta Sans (Google Fonts).

**Reference spec:** [docs/specs/2026-04-16-phase1-lead-discovery-design.md](../../specs/2026-04-16-phase1-lead-discovery-design.md)

---

## File Structure

```
hv-sales-intel/
├── api/
│   └── index.py                 FastAPI app (Vercel-compatible)
├── src/
│   ├── settings.py              Env vars via pydantic-settings
│   ├── models.py                Practice Pydantic model
│   ├── places.py                Google Places client + mock fallback
│   ├── mock_practices.json      50 realistic healthcare practices
│   └── storage.py               Supabase CRUD (upsert, query, get)
├── supabase/
│   └── schema.sql               practices table DDL
├── web/                         Next.js 14 App Router
│   ├── app/
│   │   ├── layout.tsx           Root layout, Fraunces + Jakarta fonts
│   │   ├── page.tsx             Map + sidebar (server component)
│   │   ├── actions.ts           Server actions (search, list)
│   │   └── globals.css          Tailwind + Ivory theme tokens
│   ├── components/
│   │   ├── top-bar.tsx          Logo + search + Scan City button
│   │   ├── map-view.tsx         Leaflet map with teardrop SVG pins
│   │   ├── practice-card.tsx    Sidebar card (rating, phone, actions)
│   │   ├── practice-list.tsx    Scrollable frosted-glass sidebar
│   │   ├── search-bar.tsx       Keyword + location input
│   │   └── filter-bar.tsx       Category dropdown + min-rating slider
│   ├── lib/
│   │   ├── api.ts               Fetch functions (ONLY module that calls fetch)
│   │   ├── types.ts             Practice TypeScript type
│   │   └── mock-data.ts         Client-side mock (mirrors backend mock)
│   ├── public/
│   │   └── marker.svg           Teardrop pin (fallback for non-dynamic)
│   ├── .env.example
│   ├── next.config.mjs
│   ├── package.json
│   ├── postcss.config.mjs
│   ├── tailwind.config.ts
│   └── tsconfig.json
├── requirements.txt
├── .env.example
└── vercel.json
```

**Responsibility boundaries:**
- `lib/api.ts` is the ONLY module that touches `fetch`. Mock fallback lives here so components never branch on env.
- `src/places.py` is the ONLY module that calls Google Places API. Mock fallback lives here so the API layer never branches.
- `src/storage.py` is the ONLY module that touches Supabase. Returns empty results gracefully when Supabase is unconfigured.
- Each component file has one default export and one clear job.

---

## Task 1: Python backend scaffold + settings

**Files:**
- Create: `requirements.txt`
- Create: `src/settings.py`
- Create: `.env.example`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi>=0.111,<1
uvicorn[standard]>=0.29,<1
httpx>=0.27,<1
supabase>=2.4,<3
pydantic-settings>=2.2,<3
python-dotenv>=1.0,<2
```

- [ ] **Step 2: Create Python venv and install deps**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

Expected: all packages install successfully.

- [ ] **Step 3: Create `src/settings.py`**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_maps_api_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
```

- [ ] **Step 4: Create `.env.example`**

```
GOOGLE_MAPS_API_KEY=
SUPABASE_URL=
SUPABASE_KEY=
```

- [ ] **Step 5: Verify import**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "from src.settings import settings; print('OK:', settings.google_maps_api_key == '')"
```

Expected: `OK: True`

- [ ] **Step 6: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add requirements.txt src/settings.py .env.example
git commit -m "feat: python backend scaffold + pydantic settings"
```

---

## Task 2: Supabase schema

**Files:**
- Create: `supabase/schema.sql`

- [ ] **Step 1: Create `supabase/schema.sql`**

```sql
-- Phase 1: Lead Discovery
create table if not exists practices (
  id bigserial primary key,
  place_id text unique not null,
  name text not null,
  address text,
  city text,
  state text,
  phone text,
  website text,
  rating numeric(2,1),
  review_count int default 0,
  category text,
  lat double precision,
  lng double precision,
  opening_hours text,

  -- Phase 2 (AI analysis) — columns exist but nullable
  summary text,
  pain_points text,
  sales_angles text,
  recommended_service text,
  lead_score int,
  urgency_score int,
  hiring_signal_score int,

  -- Phase 3 (CRM) — columns exist but nullable
  status text default 'NEW',
  notes text,

  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_practices_place_id on practices (place_id);
create index if not exists idx_practices_category on practices (category);
create index if not exists idx_practices_city on practices (city);
create index if not exists idx_practices_score on practices (lead_score desc nulls last);
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add supabase/schema.sql
git commit -m "feat: supabase practices table schema"
```

---

## Task 3: Practice model

**Files:**
- Create: `src/models.py`

- [ ] **Step 1: Create `src/models.py`**

```python
from pydantic import BaseModel


class Practice(BaseModel):
    place_id: str
    name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int = 0
    category: str | None = None
    lat: float | None = None
    lng: float | None = None
    opening_hours: str | None = None

    # Phase 2 (AI)
    summary: str | None = None
    pain_points: str | None = None
    sales_angles: str | None = None
    recommended_service: str | None = None
    lead_score: int | None = None
    urgency_score: int | None = None
    hiring_signal_score: int | None = None

    # Phase 3 (CRM)
    status: str = "NEW"
    notes: str | None = None
```

- [ ] **Step 2: Verify import**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "from src.models import Practice; p = Practice(place_id='test', name='Test'); print('OK:', p.name)"
```

Expected: `OK: Test`

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/models.py
git commit -m "feat: Practice pydantic model"
```

---

## Task 4: Mock data (50 healthcare practices)

**Files:**
- Create: `src/mock_practices.json`

- [ ] **Step 1: Create `src/mock_practices.json`**

Generate a JSON array of 50 practice objects with this distribution:
- 15 dental (cities: Houston, Dallas, Miami, Los Angeles, New York)
- 10 chiropractic (cities: Houston, Austin, Tampa, San Diego, Columbus)
- 10 urgent care (cities: Houston, Dallas, Orlando, San Francisco, Cleveland)
- 5 psychiatry/mental health (cities: Houston, Miami, Los Angeles, New York, Chicago)
- 5 primary care (cities: Houston, Austin, Tampa, Columbus, Cleveland)
- 5 specialty (cities: Houston, Dallas, Miami, Los Angeles, New York)

Each object must match this shape:
```json
{
  "place_id": "mock_dental_houston_001",
  "name": "Bright Smile Dental",
  "address": "1200 Main St, Houston, TX 77002",
  "city": "Houston",
  "state": "TX",
  "phone": "(713) 555-0101",
  "website": "https://brightsmiledental.example.com",
  "rating": 4.7,
  "review_count": 312,
  "category": "dental",
  "lat": 29.7604,
  "lng": -95.3698,
  "opening_hours": "Mon-Fri 8am-6pm, Sat 9am-2pm"
}
```

Rules:
- `place_id` prefixed with `mock_` and unique per practice.
- Lat/lng must be real coordinates within the named city.
- Ratings between 3.2 and 4.9. Review counts between 12 and 800.
- Realistic practice names (no "Practice 1", "Practice 2").
- Realistic addresses using real street names from each city.

- [ ] **Step 2: Verify count and shape**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import json
data = json.load(open('src/mock_practices.json'))
print(f'Count: {len(data)}')
cats = {}
for p in data:
    cats[p['category']] = cats.get(p['category'], 0) + 1
print('Categories:', cats)
print('Sample:', data[0]['name'], data[0]['place_id'])
"
```

Expected: `Count: 50`, correct category distribution, valid sample.

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/mock_practices.json
git commit -m "feat: 50 mock healthcare practices (TX/FL/CA/NY/OH)"
```

---

## Task 5: Google Places client + mock fallback (`places.py`)

**Files:**
- Create: `src/places.py`

- [ ] **Step 1: Create `src/places.py`**

```python
import json
from pathlib import Path

import httpx

from src.models import Practice
from src.settings import settings

MOCK_PATH = Path(__file__).parent / "mock_practices.json"

FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.location,places.rating,places.userRatingCount,"
    "places.nationalPhoneNumber,places.websiteUri,"
    "places.types,places.regularOpeningHours"
)


async def search_places(query: str) -> list[Practice]:
    """Search for practices. Uses Google Places API if key is set, else mock data."""
    if settings.google_maps_api_key:
        return await _google_search(query)
    return _mock_search(query)


async def _google_search(query: str) -> list[Practice]:
    """Call Google Places Text Search (New) API."""
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": FIELD_MASK,
        "Content-Type": "application/json",
    }
    body = {"textQuery": query, "maxResultCount": 20}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, headers=headers, timeout=15)
        resp.raise_for_status()

    results = resp.json().get("places", [])
    practices = []
    for p in results:
        loc = p.get("location", {})
        hours_periods = p.get("regularOpeningHours", {}).get("weekdayDescriptions", [])
        practices.append(
            Practice(
                place_id=p["id"],
                name=p.get("displayName", {}).get("text", "Unknown"),
                address=p.get("formattedAddress"),
                city=_extract_city(p.get("formattedAddress", "")),
                state=_extract_state(p.get("formattedAddress", "")),
                phone=p.get("nationalPhoneNumber"),
                website=p.get("websiteUri"),
                rating=p.get("rating"),
                review_count=p.get("userRatingCount", 0),
                category=_classify_types(p.get("types", [])),
                lat=loc.get("latitude"),
                lng=loc.get("longitude"),
                opening_hours="; ".join(hours_periods) if hours_periods else None,
            )
        )
    return practices


def _mock_search(query: str) -> list[Practice]:
    """Filter mock data by keyword matching on name, category, city."""
    with open(MOCK_PATH) as f:
        raw = json.load(f)
    query_lower = query.lower()
    tokens = query_lower.split()
    matches = []
    for item in raw:
        searchable = f"{item['name']} {item['category']} {item['city']}".lower()
        if any(tok in searchable for tok in tokens):
            matches.append(Practice(**item))
    return matches if matches else [Practice(**item) for item in raw[:20]]


def _extract_city(address: str) -> str | None:
    """Best-effort city extraction from formatted address."""
    parts = address.split(",")
    return parts[-2].strip().rsplit(" ", 1)[0].strip() if len(parts) >= 3 else None


def _extract_state(address: str) -> str | None:
    """Best-effort state extraction from formatted address."""
    parts = address.split(",")
    if len(parts) >= 3:
        state_zip = parts[-2].strip()
        tokens = state_zip.split()
        return tokens[-1] if tokens else None
    return None


def _classify_types(types: list[str]) -> str:
    """Map Google Places types to our category taxonomy."""
    type_set = set(types)
    if type_set & {"dentist", "dental_clinic"}:
        return "dental"
    if type_set & {"physiotherapist", "chiropractor"}:
        return "chiropractic"
    if type_set & {"hospital", "urgent_care_center", "emergency_room"}:
        return "urgent_care"
    if type_set & {"psychiatrist", "psychologist", "mental_health"}:
        return "mental_health"
    if type_set & {"doctor", "general_practitioner", "primary_care"}:
        return "primary_care"
    return "specialty"
```

- [ ] **Step 2: Verify mock search**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import asyncio
from src.places import search_places
results = asyncio.run(search_places('dental houston'))
print(f'Found: {len(results)}')
for r in results[:3]:
    print(f'  {r.name} ({r.category}) — {r.city}')
"
```

Expected: returns dental practices in Houston from mock data.

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/places.py
git commit -m "feat: Google Places client with mock fallback"
```

---

## Task 6: Supabase storage layer (`storage.py`)

**Files:**
- Create: `src/storage.py`

- [ ] **Step 1: Create `src/storage.py`**

```python
from supabase import create_client

from src.models import Practice
from src.settings import settings


def _get_client():
    """Return Supabase client or None if unconfigured."""
    if settings.supabase_url and settings.supabase_key:
        return create_client(settings.supabase_url, settings.supabase_key)
    return None


def upsert_practices(practices: list[Practice]) -> int:
    """Upsert practices into Supabase. Returns count upserted. No-ops if unconfigured."""
    client = _get_client()
    if not client or not practices:
        return 0
    rows = [p.model_dump() for p in practices]
    result = client.table("practices").upsert(rows, on_conflict="place_id").execute()
    return len(result.data) if result.data else 0


def query_practices(
    city: str | None = None,
    category: str | None = None,
    min_rating: float | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query practices from Supabase with optional filters. Returns [] if unconfigured."""
    client = _get_client()
    if not client:
        return []
    q = client.table("practices").select("*")
    if city:
        q = q.ilike("city", f"%{city}%")
    if category:
        q = q.eq("category", category)
    if min_rating:
        q = q.gte("rating", min_rating)
    q = q.order("rating", desc=True).limit(limit)
    result = q.execute()
    return result.data if result.data else []


def get_practice(place_id: str) -> dict | None:
    """Get a single practice by place_id. Returns None if unconfigured or not found."""
    client = _get_client()
    if not client:
        return None
    result = client.table("practices").select("*").eq("place_id", place_id).single().execute()
    return result.data
```

- [ ] **Step 2: Verify import (no Supabase configured = graceful no-op)**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
from src.storage import query_practices, upsert_practices
print('Query (no supabase):', query_practices())
print('Upsert (no supabase):', upsert_practices([]))
"
```

Expected: `Query (no supabase): []` and `Upsert (no supabase): 0`

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/storage.py
git commit -m "feat: Supabase storage layer (upsert, query, get)"
```

---

## Task 7: FastAPI app (`api/index.py`)

**Files:**
- Create: `api/index.py`
- Create: `vercel.json`

- [ ] **Step 1: Create `api/index.py`**

```python
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.places import search_places
from src.storage import upsert_practices, query_practices, get_practice

app = FastAPI(title="HV Sales Intel", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/practices")
def list_practices(
    city: str | None = Query(None),
    category: str | None = Query(None),
    min_rating: float | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List practices from Supabase with optional filters."""
    rows = query_practices(city=city, category=category, min_rating=min_rating, limit=limit)
    return {"practices": rows, "count": len(rows)}


class SearchRequest(BaseModel):
    query: str


@app.post("/api/practices/search")
async def search(body: SearchRequest):
    """Search via Google Places (or mock). Upserts results into Supabase."""
    practices = await search_places(body.query)
    upserted = upsert_practices(practices)
    return {
        "practices": [p.model_dump() for p in practices],
        "count": len(practices),
        "upserted": upserted,
    }


@app.get("/api/practices/{place_id}")
def get_single(place_id: str):
    """Get a single practice by place_id."""
    row = get_practice(place_id)
    if not row:
        raise HTTPException(status_code=404, detail="Practice not found")
    return row
```

- [ ] **Step 2: Create `vercel.json`**

```json
{
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/index.py" }
  ]
}
```

- [ ] **Step 3: Smoke-test the API locally**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
uvicorn api.index:app --reload --port 8000 &
sleep 2
curl -s http://localhost:8000/api/health
```

Expected: `{"status":"ok"}`

Run:
```bash
curl -s -X POST http://localhost:8000/api/practices/search \
  -H "Content-Type: application/json" \
  -d '{"query": "dental houston"}'
```

Expected: JSON with `practices` array containing mock dental practices in Houston.

Kill the server after testing.

- [ ] **Step 4: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add api/index.py vercel.json
git commit -m "feat: FastAPI app with health/search/list/get endpoints"
```

---

## Task 8: Scaffold the Next.js project

**Files:**
- Create: `web/` (entire Next.js project via scaffold)

- [ ] **Step 1: Run create-next-app**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
npx --yes create-next-app@14 web \
  --typescript \
  --tailwind \
  --app \
  --eslint \
  --import-alias "@/*" \
  --no-git \
  --use-npm \
  --no-src-dir
```

Expected: `web/` directory created with `package.json`, `app/`, `tailwind.config.ts`, `tsconfig.json`.

If Windows interactive prompt appears, answer: TypeScript **yes**, ESLint **yes**, Tailwind **yes**, `src/` **no**, App Router **yes**, import alias `@/*`.

- [ ] **Step 2: Verify scaffold**

Run: `ls "c:/Users/Moiz Ahmed/hv-sales-intel/web/app"`
Expected: lists `layout.tsx`, `page.tsx`, `globals.css`.

- [ ] **Step 3: Replace default home page with placeholder**

Replace `web/app/page.tsx`:

```tsx
export default function Page() {
  return (
    <main className="min-h-screen grid place-items-center" style={{ background: "#faf8f4" }}>
      <h1 className="text-3xl font-semibold tracking-tight text-teal-700">
        HV Sales Intel
      </h1>
    </main>
  )
}
```

- [ ] **Step 4: Smoke-run**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npm run dev`
Expected: starts on `http://localhost:3000`. Verify page shows "HV Sales Intel" in teal. Ctrl+C to stop.

- [ ] **Step 5: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/
git commit -m "feat(web): scaffold Next.js 14 app"
```

---

## Task 9: Install frontend dependencies

**Files:**
- Modify: `web/package.json` (via npm install)

- [ ] **Step 1: Install Leaflet + react-leaflet**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel/web"
npm install leaflet react-leaflet
npm install -D @types/leaflet
```

- [ ] **Step 2: Install utility deps**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel/web"
npm install clsx tailwind-merge lucide-react
```

- [ ] **Step 3: Verify**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel/web"
cat package.json | grep -E "(leaflet|react-leaflet|clsx|tailwind-merge|lucide)"
```

Expected: all 5 packages listed in dependencies.

- [ ] **Step 4: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/package.json web/package-lock.json
git commit -m "feat(web): add leaflet, react-leaflet, UI utility deps"
```

---

## Task 10: Tailwind Ivory theme + global styles + fonts

**Files:**
- Replace: `web/tailwind.config.ts`
- Replace: `web/app/globals.css`
- Replace: `web/app/layout.tsx`
- Create: `web/lib/utils.ts`

- [ ] **Step 1: Replace `web/tailwind.config.ts`**

```ts
import type { Config } from "tailwindcss"

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        cream: "#faf8f4",
        ivory: {
          50: "#fefdfb",
          100: "#faf8f4",
          200: "#f5f0e8",
          300: "#ebe4d6",
        },
        teal: {
          DEFAULT: "#0d9488",
          50: "#f0fdfa",
          100: "#ccfbf1",
          500: "#14b8a6",
          600: "#0d9488",
          700: "#0f766e",
          800: "#115e59",
        },
        rose: {
          DEFAULT: "#e11d48",
          500: "#f43f5e",
          600: "#e11d48",
        },
        amber: {
          400: "#fbbf24",
          500: "#f59e0b",
        },
      },
      fontFamily: {
        serif: ["var(--font-fraunces)", "Georgia", "serif"],
        sans: ["var(--font-jakarta)", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
}
export default config
```

- [ ] **Step 2: Replace `web/app/globals.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body {
    @apply bg-cream text-gray-800 font-sans antialiased;
  }
}

/* Frosted-glass sidebar panel */
.glass-panel {
  @apply bg-white/80 backdrop-blur-md border border-white/50 shadow-lg;
}

/* Leaflet container must have explicit height */
.leaflet-container {
  width: 100%;
  height: 100%;
}

/* Custom scrollbar for sidebar */
.sidebar-scroll::-webkit-scrollbar {
  width: 6px;
}
.sidebar-scroll::-webkit-scrollbar-thumb {
  @apply bg-gray-300 rounded-full;
}
```

- [ ] **Step 3: Replace `web/app/layout.tsx`**

```tsx
import type { Metadata } from "next"
import { Fraunces, Plus_Jakarta_Sans } from "next/font/google"
import "./globals.css"

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
})

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-jakarta",
  display: "swap",
})

export const metadata: Metadata = {
  title: "HV Sales Intel",
  description: "Healthcare practice discovery for Apex & Virtuals",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${fraunces.variable} ${jakarta.variable}`}>
      <body>{children}</body>
    </html>
  )
}
```

- [ ] **Step 4: Create `web/lib/utils.ts`**

```ts
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

- [ ] **Step 5: Verify fonts render**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npm run dev`
Expected: page loads with cream background. Inspect `<html>` — should have `--font-fraunces` and `--font-jakarta` CSS variables. Ctrl+C to stop.

- [ ] **Step 6: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/tailwind.config.ts web/app/globals.css web/app/layout.tsx web/lib/utils.ts
git commit -m "feat(web): Ivory theme — Fraunces + Jakarta fonts, cream bg, glass panel"
```

---

## Task 11: TypeScript types + API client + mock data

**Files:**
- Create: `web/lib/types.ts`
- Create: `web/lib/api.ts`
- Create: `web/lib/mock-data.ts`
- Create: `web/.env.example`

- [ ] **Step 1: Create `web/lib/types.ts`**

```ts
export interface Practice {
  place_id: string
  name: string
  address: string | null
  city: string | null
  state: string | null
  phone: string | null
  website: string | null
  rating: number | null
  review_count: number
  category: string | null
  lat: number | null
  lng: number | null
  opening_hours: string | null
  status: string
}
```

- [ ] **Step 2: Create `web/lib/mock-data.ts`**

```ts
import type { Practice } from "./types"

// 20 representative practices for client-side fallback (subset of backend mock)
export const mockPractices: Practice[] = [
  {
    place_id: "mock_dental_houston_001",
    name: "Bright Smile Dental",
    address: "1200 Main St, Houston, TX 77002",
    city: "Houston",
    state: "TX",
    phone: "(713) 555-0101",
    website: "https://brightsmiledental.example.com",
    rating: 4.7,
    review_count: 312,
    category: "dental",
    lat: 29.7604,
    lng: -95.3698,
    opening_hours: "Mon-Fri 8am-6pm, Sat 9am-2pm",
    status: "NEW",
  },
  // ... add 19 more from mock_practices.json, covering all categories and multiple cities
]
```

Populate with 20 practices from the backend `mock_practices.json`, ensuring representation across all 6 categories and at least 4 cities.

- [ ] **Step 3: Create `web/lib/api.ts`**

```ts
import type { Practice } from "./types"
import { mockPractices } from "./mock-data"

const API_URL = process.env.NEXT_PUBLIC_API_URL || ""

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_URL) throw new Error("NO_API")
  const res = await fetch(`${API_URL}${path}`, init)
  if (!res.ok) throw new Error(`API ${res.status}`)
  return res.json()
}

export async function searchPractices(query: string): Promise<Practice[]> {
  try {
    const data = await apiFetch<{ practices: Practice[] }>("/api/practices/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    })
    return data.practices
  } catch {
    // Mock fallback: filter client-side
    const q = query.toLowerCase()
    const tokens = q.split(/\s+/)
    const matches = mockPractices.filter((p) => {
      const hay = `${p.name} ${p.category} ${p.city}`.toLowerCase()
      return tokens.some((t) => hay.includes(t))
    })
    return matches.length > 0 ? matches : mockPractices
  }
}

export async function listPractices(params?: {
  city?: string
  category?: string
  min_rating?: number
}): Promise<Practice[]> {
  try {
    const qs = new URLSearchParams()
    if (params?.city) qs.set("city", params.city)
    if (params?.category) qs.set("category", params.category)
    if (params?.min_rating) qs.set("min_rating", String(params.min_rating))
    const data = await apiFetch<{ practices: Practice[] }>(`/api/practices?${qs}`)
    return data.practices
  } catch {
    return mockPractices
  }
}
```

- [ ] **Step 4: Create `web/.env.example`**

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

- [ ] **Step 5: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/lib/types.ts web/lib/api.ts web/lib/mock-data.ts web/.env.example
git commit -m "feat(web): Practice type, API client with mock fallback, client mock data"
```

---

## Task 12: Map component (Leaflet + teardrop pins)

**Files:**
- Create: `web/components/map-view.tsx`

- [ ] **Step 1: Create `web/components/map-view.tsx`**

```tsx
"use client"

import { useEffect, useRef } from "react"
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import type { Practice } from "@/lib/types"

// Teardrop SVG pin — teal with rating inside
function createPinIcon(rating: number | null): L.DivIcon {
  const label = rating ? rating.toFixed(1) : "—"
  return L.divIcon({
    className: "",
    iconSize: [32, 42],
    iconAnchor: [16, 42],
    popupAnchor: [0, -42],
    html: `
      <svg width="32" height="42" viewBox="0 0 32 42" xmlns="http://www.w3.org/2000/svg">
        <path d="M16 0C7.16 0 0 7.16 0 16c0 12 16 26 16 26s16-14 16-26C32 7.16 24.84 0 16 0z"
              fill="#0d9488" stroke="#fff" stroke-width="1.5"/>
        <text x="16" y="18" text-anchor="middle" fill="white"
              font-family="system-ui" font-size="10" font-weight="600">${label}</text>
      </svg>
    `,
  })
}

function FitBounds({ practices }: { practices: Practice[] }) {
  const map = useMap()
  useEffect(() => {
    const pts = practices
      .filter((p) => p.lat != null && p.lng != null)
      .map((p) => [p.lat!, p.lng!] as [number, number])
    if (pts.length > 0) {
      map.fitBounds(pts, { padding: [40, 40], maxZoom: 13 })
    }
  }, [practices, map])
  return null
}

interface MapViewProps {
  practices: Practice[]
  selectedId: string | null
  onSelect: (placeId: string) => void
}

export default function MapView({ practices, selectedId, onSelect }: MapViewProps) {
  const markerRefs = useRef<Record<string, L.Marker>>({}))

  // Pan to selected marker when sidebar card is clicked
  useEffect(() => {
    if (selectedId && markerRefs.current[selectedId]) {
      const marker = markerRefs.current[selectedId]
      marker.openPopup()
    }
  }, [selectedId])

  return (
    <MapContainer
      center={[29.76, -95.37]}
      zoom={10}
      className="w-full h-full z-0"
      zoomControl={false}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitBounds practices={practices} />
      {practices
        .filter((p) => p.lat != null && p.lng != null)
        .map((p) => (
          <Marker
            key={p.place_id}
            position={[p.lat!, p.lng!]}
            icon={createPinIcon(p.rating)}
            ref={(ref) => {
              if (ref) markerRefs.current[p.place_id] = ref
            }}
            eventHandlers={{
              click: () => onSelect(p.place_id),
            }}
          >
            <Popup>
              <strong className="font-serif">{p.name}</strong>
              <br />
              {p.address}
            </Popup>
          </Marker>
        ))}
    </MapContainer>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/map-view.tsx
git commit -m "feat(web): Leaflet map with teardrop SVG pins and auto-fit bounds"
```

---

## Task 13: Practice card + sidebar list

**Files:**
- Create: `web/components/practice-card.tsx`
- Create: `web/components/practice-list.tsx`

- [ ] **Step 1: Create `web/components/practice-card.tsx`**

```tsx
import { Phone, Globe, Star } from "lucide-react"
import type { Practice } from "@/lib/types"
import { cn } from "@/lib/utils"

function StarRating({ rating }: { rating: number | null }) {
  if (!rating) return null
  const full = Math.floor(rating)
  return (
    <span className="inline-flex items-center gap-0.5">
      {Array.from({ length: 5 }, (_, i) => (
        <Star
          key={i}
          className={cn(
            "w-3.5 h-3.5",
            i < full ? "fill-amber-400 text-amber-400" : "text-gray-300"
          )}
        />
      ))}
      <span className="ml-1 text-sm font-medium text-gray-700">{rating}</span>
    </span>
  )
}

interface PracticeCardProps {
  practice: Practice
  isSelected: boolean
  onSelect: (placeId: string) => void
}

export default function PracticeCard({ practice, isSelected, onSelect }: PracticeCardProps) {
  return (
    <button
      onClick={() => onSelect(practice.place_id)}
      className={cn(
        "w-full text-left p-4 rounded-xl transition-all",
        "hover:bg-ivory-200/60",
        isSelected ? "bg-teal-50 ring-1 ring-teal-600/30" : "bg-white/60"
      )}
    >
      <h3 className="font-serif font-semibold text-gray-900 text-base leading-tight">
        {practice.name}
      </h3>
      <p className="text-xs text-gray-500 mt-0.5">{practice.address}</p>

      <div className="flex items-center gap-3 mt-2">
        <StarRating rating={practice.rating} />
        {practice.review_count > 0 && (
          <span className="text-xs text-gray-400">({practice.review_count})</span>
        )}
      </div>

      {practice.category && (
        <span className="inline-block mt-2 text-xs px-2 py-0.5 rounded-full bg-teal-50 text-teal-700 font-medium capitalize">
          {practice.category.replace("_", " ")}
        </span>
      )}

      <div className="flex gap-2 mt-3">
        {practice.phone && (
          <a
            href={`tel:${practice.phone}`}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 transition"
          >
            <Phone className="w-3 h-3" /> Call
          </a>
        )}
        {practice.website && (
          <a
            href={practice.website}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-gray-100 text-gray-700 hover:bg-gray-200 transition"
          >
            <Globe className="w-3 h-3" /> Website
          </a>
        )}
      </div>
    </button>
  )
}
```

- [ ] **Step 2: Create `web/components/practice-list.tsx`**

```tsx
import type { Practice } from "@/lib/types"
import PracticeCard from "./practice-card"

interface PracticeListProps {
  practices: Practice[]
  selectedId: string | null
  onSelect: (placeId: string) => void
  cityLabel: string
}

export default function PracticeList({
  practices,
  selectedId,
  onSelect,
  cityLabel,
}: PracticeListProps) {
  return (
    <aside className="absolute top-16 left-4 bottom-4 w-[390px] z-10 glass-panel rounded-2xl flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-5 pt-5 pb-3 border-b border-gray-200/50">
        <h2 className="font-serif text-lg font-semibold text-gray-900">
          {cityLabel || "All practices"}
        </h2>
        <p className="text-sm text-gray-500 mt-0.5">
          {practices.length} practice{practices.length !== 1 ? "s" : ""}
        </p>
      </div>

      {/* Scrollable card list */}
      <div className="flex-1 overflow-y-auto sidebar-scroll p-3 space-y-2">
        {practices.length === 0 ? (
          <p className="text-center text-gray-400 py-10 text-sm">
            No practices found. Try a different search.
          </p>
        ) : (
          practices.map((p) => (
            <PracticeCard
              key={p.place_id}
              practice={p}
              isSelected={selectedId === p.place_id}
              onSelect={onSelect}
            />
          ))
        )}
      </div>
    </aside>
  )
}
```

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/practice-card.tsx web/components/practice-list.tsx
git commit -m "feat(web): practice card with star ratings + frosted-glass sidebar list"
```

---

## Task 14: Top bar + search bar + filter bar

**Files:**
- Create: `web/components/top-bar.tsx`
- Create: `web/components/search-bar.tsx`
- Create: `web/components/filter-bar.tsx`

- [ ] **Step 1: Create `web/components/search-bar.tsx`**

```tsx
"use client"

import { useState } from "react"
import { Search } from "lucide-react"

interface SearchBarProps {
  onSearch: (query: string) => void
  isLoading: boolean
}

export default function SearchBar({ onSearch, isLoading }: SearchBarProps) {
  const [value, setValue] = useState("")

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (value.trim()) onSearch(value.trim())
  }

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-2">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="dental clinics in Houston..."
          className="pl-9 pr-4 py-2 w-72 rounded-lg bg-white/80 border border-gray-200 text-sm
                     placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
        />
      </div>
      <button
        type="submit"
        disabled={isLoading || !value.trim()}
        className="px-4 py-2 rounded-lg bg-teal-600 text-white text-sm font-medium
                   hover:bg-teal-700 disabled:opacity-50 transition"
      >
        {isLoading ? "Scanning..." : "Scan City"}
      </button>
    </form>
  )
}
```

- [ ] **Step 2: Create `web/components/filter-bar.tsx`**

```tsx
"use client"

interface FilterBarProps {
  category: string
  onCategoryChange: (cat: string) => void
  minRating: number
  onMinRatingChange: (r: number) => void
}

const CATEGORIES = [
  { value: "", label: "All categories" },
  { value: "dental", label: "Dental" },
  { value: "chiropractic", label: "Chiropractic" },
  { value: "urgent_care", label: "Urgent Care" },
  { value: "mental_health", label: "Mental Health" },
  { value: "primary_care", label: "Primary Care" },
  { value: "specialty", label: "Specialty" },
]

export default function FilterBar({
  category,
  onCategoryChange,
  minRating,
  onMinRatingChange,
}: FilterBarProps) {
  return (
    <div className="flex items-center gap-3 px-5 py-2 border-b border-gray-200/50">
      <select
        value={category}
        onChange={(e) => onCategoryChange(e.target.value)}
        className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5
                   focus:outline-none focus:ring-2 focus:ring-teal-500/40"
      >
        {CATEGORIES.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
      <label className="flex items-center gap-2 text-sm text-gray-600">
        Min rating
        <input
          type="range"
          min={0}
          max={5}
          step={0.5}
          value={minRating}
          onChange={(e) => onMinRatingChange(Number(e.target.value))}
          className="w-24 accent-teal-600"
        />
        <span className="text-xs font-medium w-6">{minRating || "Any"}</span>
      </label>
    </div>
  )
}
```

- [ ] **Step 3: Create `web/components/top-bar.tsx`**

```tsx
"use client"

import SearchBar from "./search-bar"

interface TopBarProps {
  onSearch: (query: string) => void
  isLoading: boolean
}

export default function TopBar({ onSearch, isLoading }: TopBarProps) {
  return (
    <header className="fixed top-0 left-0 right-0 z-20 h-14 flex items-center justify-between px-6 bg-white/70 backdrop-blur-md border-b border-gray-200/50">
      <div className="flex items-center gap-2">
        <span className="font-serif text-lg font-bold text-teal-700 tracking-tight">
          Health&amp;Virtuals
        </span>
        <span className="text-xs text-gray-400 font-medium">Sales Intel</span>
      </div>
      <SearchBar onSearch={onSearch} isLoading={isLoading} />
    </header>
  )
}
```

- [ ] **Step 4: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/top-bar.tsx web/components/search-bar.tsx web/components/filter-bar.tsx
git commit -m "feat(web): top bar, search bar, category/rating filter bar"
```

---

## Task 15: Wire up the main page

**Files:**
- Replace: `web/app/page.tsx`
- Create: `web/app/actions.ts`

- [ ] **Step 1: Create `web/app/actions.ts`**

```ts
"use server"

import type { Practice } from "@/lib/types"
import { searchPractices, listPractices } from "@/lib/api"

export async function searchAction(query: string): Promise<Practice[]> {
  return searchPractices(query)
}

export async function listAction(params?: {
  city?: string
  category?: string
  min_rating?: number
}): Promise<Practice[]> {
  return listPractices(params)
}
```

- [ ] **Step 2: Replace `web/app/page.tsx` — main layout component**

```tsx
"use client"

import { useState, useMemo, useCallback } from "react"
import dynamic from "next/dynamic"
import type { Practice } from "@/lib/types"
import { mockPractices } from "@/lib/mock-data"
import TopBar from "@/components/top-bar"
import PracticeList from "@/components/practice-list"
import FilterBar from "@/components/filter-bar"
import { searchPractices } from "@/lib/api"

// Leaflet must be loaded client-side only (no SSR)
const MapView = dynamic(() => import("@/components/map-view"), { ssr: false })

export default function Page() {
  const [practices, setPractices] = useState<Practice[]>(mockPractices)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [cityLabel, setCityLabel] = useState("")
  const [category, setCategory] = useState("")
  const [minRating, setMinRating] = useState(0)

  const handleSearch = useCallback(async (query: string) => {
    setIsLoading(true)
    try {
      const results = await searchPractices(query)
      setPractices(results)
      setCityLabel(query)
      setSelectedId(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  const filtered = useMemo(() => {
    return practices.filter((p) => {
      if (category && p.category !== category) return false
      if (minRating && (p.rating ?? 0) < minRating) return false
      return true
    })
  }, [practices, category, minRating])

  return (
    <div className="h-screen w-screen overflow-hidden">
      <TopBar onSearch={handleSearch} isLoading={isLoading} />

      <main className="relative w-full h-full pt-14">
        {/* Sidebar */}
        <div className="absolute top-16 left-4 bottom-4 w-[390px] z-10 glass-panel rounded-2xl flex flex-col overflow-hidden">
          <div className="px-5 pt-5 pb-3 border-b border-gray-200/50">
            <h2 className="font-serif text-lg font-semibold text-gray-900">
              {cityLabel || "All practices"}
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">
              {filtered.length} practice{filtered.length !== 1 ? "s" : ""}
            </p>
          </div>
          <FilterBar
            category={category}
            onCategoryChange={setCategory}
            minRating={minRating}
            onMinRatingChange={setMinRating}
          />
          <div className="flex-1 overflow-y-auto sidebar-scroll p-3 space-y-2">
            {filtered.length === 0 ? (
              <p className="text-center text-gray-400 py-10 text-sm">
                No practices found. Try a different search.
              </p>
            ) : (
              filtered.map((p) => (
                <PracticeCard
                  key={p.place_id}
                  practice={p}
                  isSelected={selectedId === p.place_id}
                  onSelect={setSelectedId}
                />
              ))
            )}
          </div>
        </div>

        {/* Map */}
        <MapView
          practices={filtered}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      </main>
    </div>
  )
}

// Re-export for inline use — avoids circular import
import PracticeCard from "@/components/practice-card"
```

Note: The `PracticeList` component from Task 13 can be used instead of inlining the sidebar here. However, inlining keeps the state management clearer for this layout. If during implementation you prefer the abstraction, import `PracticeList` and pass `filtered` + `selectedId` + `setSelectedId` + `cityLabel` as props.

- [ ] **Step 3: Verify full app renders**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npm run dev`

Expected:
1. Page loads with cream background and top bar showing "Apex&Virtuals Sales Intel".
2. Left sidebar shows 20 mock practices in frosted-glass cards.
3. Map displays with teal teardrop pins at practice locations.
4. Clicking a pin highlights the corresponding card in the sidebar.
5. Clicking a card pans the map to the pin.
6. Search bar accepts input; "Scan City" triggers mock search.
7. Category dropdown and rating slider filter the visible list.

Ctrl+C to stop.

- [ ] **Step 4: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/app/page.tsx web/app/actions.ts
git commit -m "feat(web): wire up map + sidebar + search + filters on main page"
```

---

## Task 16: Leaflet CSS + Next.js config fixes

**Files:**
- Modify: `web/next.config.mjs` (if needed for transpile)

- [ ] **Step 1: Ensure Leaflet CSS is imported**

Verify `web/components/map-view.tsx` line 5 has:
```ts
import "leaflet/dist/leaflet.css"
```

If Leaflet CSS doesn't render correctly via import (common in Next.js), add to `web/app/globals.css`:
```css
@import "leaflet/dist/leaflet.css";
```

- [ ] **Step 2: Fix Leaflet default icon issue (common in Next.js/webpack)**

If map pins show broken image icons instead of the custom SVG, the `DivIcon` approach in Task 12 avoids this entirely since it uses inline SVG, not image files. Verify pins render as teal teardrops.

- [ ] **Step 3: Verify map tiles load**

Open the app in browser. Map should show OpenStreetMap tiles (roads, place names). If tiles are gray/missing, check browser console for CORS or network errors.

- [ ] **Step 4: Commit (if any fixes were needed)**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/
git commit -m "fix(web): leaflet CSS + map rendering fixes"
```

---

## Task 17: Final verification and push

**Files:**
- None (verification only)

- [ ] **Step 1: Typecheck**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 2: Lint**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npm run lint`
Expected: no errors. Warnings acceptable but prefer to fix.

- [ ] **Step 3: Production build**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npm run build`
Expected: succeeds. Output lists `/` as a dynamic route.

- [ ] **Step 4: Backend smoke test**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import asyncio
from src.places import search_places
from src.storage import query_practices
r = asyncio.run(search_places('dental houston'))
print(f'Places search: {len(r)} results')
print(f'Storage query: {len(query_practices())} results')
print('Backend OK')
"
```

Expected: `Places search: N results`, `Storage query: 0 results` (no Supabase configured), `Backend OK`.

- [ ] **Step 5: Visual check**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npm run dev`

Verify in browser:
- Cream background, Fraunces serif headings, Plus Jakarta Sans body text.
- Top bar with "Apex&Virtuals" logo and search input.
- Frosted-glass sidebar with practice cards (star ratings in amber, teal action buttons).
- Leaflet map with teal teardrop pins showing ratings.
- Pin click highlights card. Card click pans map.
- Category filter and rating slider work.
- Search returns filtered mock results.

Ctrl+C to stop.

- [ ] **Step 6: Final commit (if lint/type fixes were needed)**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git status
# If anything is dirty:
git add -A web/ src/ api/
git commit -m "chore: post-build fixes"
```

- [ ] **Step 7: Push**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git push
```

Expected: push succeeds; branch `main` updated on GitHub.

---

## Self-review notes

- **Spec coverage:** Every section of the design spec maps to at least one task. Mock fallback → Tasks 4–5 (backend) + Task 11 (frontend). Google Places API → Task 5. Supabase schema → Task 2. Supabase CRUD → Task 6. FastAPI endpoints → Task 7. Ivory visual direction → Task 10 (fonts, colors, glass-panel). Teardrop pins → Task 12. Sidebar with cards → Task 13. Search + filter → Task 14. Full layout → Task 15. Map pin ↔ card interaction → Tasks 12+15.
- **Placeholders:** Mock data (Task 4) must be generated with 50 real entries — marked with generation rules, not "add 19 more" hand-waving. Task 11 client mock (20 entries) is a subset that must be actually populated during implementation.
- **Type consistency:** `Practice` model defined in Python (`src/models.py`, Task 3) and TypeScript (`web/lib/types.ts`, Task 11) with matching fields. API responses use `model_dump()` which produces the same shape.
- **Zero-config path:** App works with no `.env` file at all. Backend returns mock data (no Google key), storage no-ops (no Supabase key), frontend falls back to client mock (no `NEXT_PUBLIC_API_URL`).
- **File sizes:** All component files under 120 lines. Largest expected file is `mock_practices.json` (~300 lines) and `mock-data.ts` (~200 lines).
