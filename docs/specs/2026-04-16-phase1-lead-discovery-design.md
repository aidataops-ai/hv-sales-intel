# Phase 1: Lead Discovery + Map UI — Design

**Date:** 2026-04-16
**Status:** Approved

## Goal

Give Apex & Virtuals' sales team a map-based tool to search healthcare practices by city/specialty, see them as pins on an interactive map, browse details in a sidebar, and store everything in Supabase. Works immediately with mock data; swap in Google Places API when ready.

## Non-goals (deferred to later phases)

- AI business analysis, pain points, sales angles (Phase 2)
- Cold call script generator, CRM status tracking (Phase 3)
- Scheduled scraping, review sentiment, competitor insight (Phase 4)
- Schema columns for these features exist but are nullable/unpopulated.

## Visual direction: Ivory

Warm cream background (#faf8f4). Fraunces serif for headlines, Plus Jakarta Sans for body. Teal (#0d9488) as primary accent. White frosted-glass sidebar with subtle shadows. Teardrop map pins with scores inside. Star ratings in amber. Premium healthcare aesthetic — professional enough to demo to a client.

## Architecture

```
hv-sales-intel/
├── api/index.py                 FastAPI (Vercel-compatible)
├── src/
│   ├── settings.py              Env vars
│   ├── models.py                Practice model
│   ├── places.py                Google Places client + mock fallback
│   └── storage.py               Supabase CRUD
├── supabase/schema.sql          DB schema
├── web/                         Next.js 14 App Router
│   ├── app/
│   │   ├── layout.tsx           Root layout, Fraunces + Jakarta fonts
│   │   ├── page.tsx             Map + sidebar layout (server component)
│   │   └── actions.ts           Server actions
│   ├── components/
│   │   ├── map-view.tsx         Leaflet map with teardrop pins
│   │   ├── practice-card.tsx    Sidebar card (rating, phone, actions)
│   │   ├── practice-list.tsx    Scrollable sidebar list
│   │   ├── search-bar.tsx       Keyword + location search
│   │   └── filter-bar.tsx       Category + rating filters
│   └── lib/
│       ├── api.ts               Client fetch functions
│       ├── types.ts             Practice type
│       └── mock-data.ts         50 realistic practices
├── requirements.txt
└── .env.example
```

## Schema: `practices` table

```sql
create table practices (
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
  -- Phase 2 (AI)
  summary text,
  pain_points text,
  sales_angles text,
  recommended_service text,
  lead_score int,
  urgency_score int,
  hiring_signal_score int,
  -- Phase 3 (CRM)
  status text default 'NEW',
  notes text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index idx_practices_place_id on practices (place_id);
create index idx_practices_category on practices (category);
create index idx_practices_city on practices (city);
create index idx_practices_score on practices (lead_score desc nulls last);
```

## API endpoints

```
GET  /api/health
GET  /api/practices?city=&category=&min_rating=&limit=
POST /api/practices/search   { query: "dental clinics in Houston" }
GET  /api/practices/{place_id}
```

`POST /api/practices/search`:
- If `GOOGLE_MAPS_API_KEY` is set → calls Google Places Text Search API, upserts results.
- If not set → filters `mock_practices.json` by keyword + city, upserts into Supabase if connected, otherwise returns directly.

## Mock data

50 healthcare practices across TX, FL, CA, NY, OH:
- 15 dental, 10 chiropractic, 10 urgent care, 5 psychiatry/mental health, 5 primary care, 5 specialty.
- Real city coordinates, ratings 3.2–4.9, review counts 12–800, realistic names and addresses.
- Place IDs prefixed `mock_` to distinguish from real Google Place IDs.

## Google Places API integration (when key is set)

Uses the Places API (New) — `searchText` endpoint:
```
POST https://places.googleapis.com/v1/places:searchText
Headers: X-Goog-Api-Key, X-Goog-FieldMask
Body: { "textQuery": "dental clinics in Houston", "maxResultCount": 20 }
```

Field mask requests: `places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,places.nationalPhoneNumber,places.websiteUri,places.types,places.regularOpeningHours`

Maps response fields to our `Practice` model. Upserts by `place_id`.

## UI layout

Full-viewport. Top bar with logo + search. Map takes remaining space. Sidebar floats over the map (left side, 390px).

```
┌─────────────────────────────────────────────────────────┐
│  Apex&Virtuals    [🔍 Search...]           [+Scan City]│
├──────────────────┬──────────────────────────────────────┤
│  SIDEBAR (390px) │            LEAFLET MAP               │
│  glass panel     │                                      │
│                  │        📍  📍     📍                  │
│  Houston, TX     │              📍         📍            │
│  47 practices    │    📍                                │
│                  │          📍    📍       📍            │
│  [filters]       │                                      │
│                  │      📍          📍                   │
│  ┌─ Card ──────┐ │               📍                     │
│  │ Bright Smile│ │                                      │
│  │ ★★★★★ 4.7  │ │         📍                           │
│  │ 📞 Call     │ │                                      │
│  └─────────────┘ │                                      │
│  ┌─ Card ──────┐ │                                      │
│  │ ...         │ │                                      │
│  └─────────────┘ │                                      │
└──────────────────┴──────────────────────────────────────┘
```

- Click pin → sidebar scrolls to card + highlights.
- Click card → map pans to pin.
- Pins: teardrop shape with rating inside. Teal for normal, rose (#e11d48) for hot (Phase 2 — for now all pins are teal).
- Sidebar: white frosted glass, Fraunces headings, teal action buttons.

## Map: Leaflet + OpenStreetMap

`react-leaflet` with a light OSM tile. No API key. Custom marker icons rendered as SVG teardrops.

## Env vars

```
GOOGLE_MAPS_API_KEY=     # empty = mock mode
SUPABASE_URL=
SUPABASE_KEY=
```

## Decisions log

- **Leaflet over Google Maps JS** — free, no billing, swap later.
- **Wide schema** — Phase 2/3/4 columns exist but null. Avoid migrations between phases.
- **Mock fallback in `places.py`** — same pattern as real estate project. UI works without any API key.
- **Ivory visual** — chosen from 3 options. Fraunces serif + teal + cream. Premium healthcare feel.
- **Server components for data fetch** — same pattern as Lead Desk.
- **No auth** — same as Lead Desk. Add later if needed.
