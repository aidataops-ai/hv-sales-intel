# Phase 2: AI Business Analysis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AI-powered practice analysis (website crawl + Google reviews + GPT-4o) so Apex & Virtuals' sales team sees hiring signals, pain points, sales angles, and lead scores — on-demand per card or in bulk — with mock fallback for zero-config operation.

**Architecture:** Three new Python modules (`crawler.py`, `reviews.py`, `analyzer.py`) orchestrate data collection and LLM analysis. One new FastAPI endpoint (`POST /api/practices/{place_id}/analyze`) exposes it. Frontend gets an "Analyze" button per card, "Score All" in the top bar, score badges, inline analysis expansion, and color-coded map pins.

**Tech Stack:** Python (httpx, beautifulsoup4, openai), FastAPI, GPT-4o, Next.js 14, React 18, TypeScript, Tailwind CSS, Leaflet.

**Reference spec:** [docs/specs/2026-04-16-phase2-ai-analysis-design.md](../../specs/2026-04-16-phase2-ai-analysis-design.md)

---

## File Structure

```
src/
├── settings.py          (modify) Add openai_api_key
├── models.py            (no change) Phase 2 fields already exist
├── crawler.py           (create) Website crawler — httpx + BeautifulSoup, max 10 pages
├── reviews.py           (create) Google Places reviews fetcher
├── analyzer.py          (create) Orchestrator: crawl → reviews → GPT-4o → scores + mock fallback
├── storage.py           (no change) upsert already handles Phase 2 fields

api/
└── index.py             (modify) Add POST /api/practices/{place_id}/analyze

web/
├── lib/
│   ├── types.ts         (modify) Add Phase 2 fields to Practice interface
│   ├── api.ts           (modify) Add analyzePractice() + mock fallback
│   └── mock-data.ts     (no change)
├── components/
│   ├── practice-card.tsx (modify) Add Analyze button, score badge, inline analysis section
│   ├── score-bar.tsx    (create) Reusable horizontal score bar component
│   ├── top-bar.tsx      (modify) Add "Score All" button with progress
│   └── map-view.tsx     (modify) Pin color based on lead_score
└── app/
    └── page.tsx         (modify) Sort by lead_score, bulk analyze handler, update practice in state
```

**Responsibility boundaries:**
- `src/crawler.py` — ONLY module that fetches website HTML. Returns plain text.
- `src/reviews.py` — ONLY module that calls Google Places for reviews. Returns review text list.
- `src/analyzer.py` — Orchestrates crawl + reviews + LLM call. ONLY module that calls OpenAI. Returns analysis dict.
- `web/lib/api.ts` — ONLY module that calls `fetch`. Mock fallback for analyze lives here.

---

## Task 1: Add `openai` + `beautifulsoup4` to Python deps

**Files:**
- Modify: `requirements.txt`
- Modify: `src/settings.py`
- Modify: `.env.example`

- [ ] **Step 1: Update `requirements.txt`**

Add these two lines to the end of `requirements.txt`:

```
openai>=1.30,<2
beautifulsoup4>=4.12,<5
```

- [ ] **Step 2: Install new deps**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
pip install -r requirements.txt
```

Expected: `openai` and `beautifulsoup4` install successfully alongside existing deps.

- [ ] **Step 3: Add `openai_api_key` to settings**

In `src/settings.py`, add `openai_api_key` field to the `Settings` class:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_maps_api_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""
    openai_api_key: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
```

- [ ] **Step 4: Update `.env.example`**

```
GOOGLE_MAPS_API_KEY=
SUPABASE_URL=
SUPABASE_KEY=
OPENAI_API_KEY=
```

- [ ] **Step 5: Verify**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "from src.settings import settings; print('OK:', settings.openai_api_key == '')"
```

Expected: `OK: True`

- [ ] **Step 6: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add requirements.txt src/settings.py .env.example
git commit -m "feat: add openai + beautifulsoup4 deps, OPENAI_API_KEY setting"
```

---

## Task 2: Website crawler (`src/crawler.py`)

**Files:**
- Create: `src/crawler.py`

- [ ] **Step 1: Create `src/crawler.py`**

```python
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

# Pages to prioritize (crawled first)
PRIORITY_PATTERNS = re.compile(
    r"(career|job|hiring|team|about|staff|service|contact)", re.IGNORECASE
)

MAX_PAGES = 10
TIMEOUT = 10


async def crawl_website(url: str) -> str:
    """Crawl a website starting from the given URL. Returns combined text from up to 10 pages."""
    if not url:
        return ""

    visited: set[str] = set()
    texts: list[str] = []
    base_domain = urlparse(url).netloc

    # Discover links from homepage first, then prioritize career/about pages
    to_visit = [url]
    discovered: list[str] = []

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": "HVSalesIntel/1.0"}
    ) as client:
        while (to_visit or discovered) and len(visited) < MAX_PAGES:
            # Pick next URL: prioritize to_visit queue, then discovered
            if to_visit:
                current = to_visit.pop(0)
            else:
                current = discovered.pop(0)

            normalized = _normalize_url(current)
            if normalized in visited:
                continue
            visited.add(normalized)

            try:
                resp = await client.get(current)
                if resp.status_code != 200:
                    continue
                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type:
                    continue
            except (httpx.HTTPError, Exception):
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract text
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            if text:
                # Limit per-page text to avoid blowing up context
                texts.append(text[:5000])

            # Discover internal links (only from first few pages)
            if len(visited) <= 3:
                for a in soup.find_all("a", href=True):
                    href = urljoin(current, a["href"])
                    parsed = urlparse(href)
                    if parsed.netloc != base_domain:
                        continue
                    if parsed.scheme not in ("http", "https"):
                        continue
                    norm = _normalize_url(href)
                    if norm in visited:
                        continue
                    # Priority pages go to the front
                    if PRIORITY_PATTERNS.search(href):
                        to_visit.append(href)
                    else:
                        discovered.append(href)

    return "\n\n---\n\n".join(texts)


def _normalize_url(url: str) -> str:
    """Strip fragments and trailing slashes for dedup."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"
```

- [ ] **Step 2: Verify crawler runs**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import asyncio
from src.crawler import crawl_website
text = asyncio.run(crawl_website('https://example.com'))
print(f'Crawled {len(text)} chars')
print(text[:200])
"
```

Expected: prints some text from example.com (a simple page). Non-zero char count.

- [ ] **Step 3: Verify empty URL returns empty string**

Run:
```bash
python -c "
import asyncio
from src.crawler import crawl_website
print('Empty:', repr(asyncio.run(crawl_website(''))))
"
```

Expected: `Empty: ''`

- [ ] **Step 4: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/crawler.py
git commit -m "feat: website crawler (httpx + BeautifulSoup, max 10 pages)"
```

---

## Task 3: Google Places reviews fetcher (`src/reviews.py`)

**Files:**
- Create: `src/reviews.py`

- [ ] **Step 1: Create `src/reviews.py`**

```python
import httpx

from src.settings import settings

REVIEWS_FIELD_MASK = "reviews.text,reviews.rating,reviews.originalText"


async def fetch_reviews(place_id: str) -> list[dict]:
    """Fetch up to 5 reviews for a place via Google Places API.

    Returns list of dicts with 'text' and 'rating' keys.
    Returns [] if no API key or place_id starts with 'mock_'.
    """
    if not settings.google_maps_api_key:
        return []
    if place_id.startswith("mock_"):
        return []

    url = f"https://places.googleapis.com/v1/places/{place_id}"
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": REVIEWS_FIELD_MASK,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
    except (httpx.HTTPError, Exception):
        return []

    data = resp.json()
    reviews = []
    for r in data.get("reviews", []):
        text = r.get("originalText", {}).get("text") or r.get("text", {}).get("text", "")
        rating = r.get("rating")
        if text:
            reviews.append({"text": text, "rating": rating})
    return reviews


def format_reviews_for_prompt(reviews: list[dict]) -> str:
    """Format reviews into a string for the LLM prompt."""
    if not reviews:
        return "No Google reviews available."
    lines = []
    for i, r in enumerate(reviews, 1):
        stars = f"{r['rating']}/5" if r.get("rating") else "no rating"
        lines.append(f"Review {i} ({stars}): {r['text']}")
    return "\n".join(lines)
```

- [ ] **Step 2: Verify mock place_id returns empty**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import asyncio
from src.reviews import fetch_reviews, format_reviews_for_prompt
reviews = asyncio.run(fetch_reviews('mock_dental_houston_001'))
print('Mock reviews:', reviews)
print('Formatted:', format_reviews_for_prompt(reviews))
"
```

Expected: `Mock reviews: []` and `Formatted: No Google reviews available.`

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/reviews.py
git commit -m "feat: Google Places reviews fetcher"
```

---

## Task 4: AI analyzer with mock fallback (`src/analyzer.py`)

**Files:**
- Create: `src/analyzer.py`

- [ ] **Step 1: Create `src/analyzer.py`**

```python
import json
import random

from openai import AsyncOpenAI

from src.crawler import crawl_website
from src.reviews import fetch_reviews, format_reviews_for_prompt
from src.settings import settings

SYSTEM_PROMPT = """You are a healthcare sales intelligence analyst for Apex & Virtuals, a healthcare staffing and talent acquisition company.

Your job is to analyze healthcare practices and identify:
1. Staffing-related pain points (understaffed, high turnover, hiring difficulties)
2. Hiring signals (job postings, "we're hiring" pages, open positions for front desk, medical assistants, clinical staff, admin/VA roles)
3. Sales angles for pitching Apex & Virtuals' staffing services

Focus specifically on roles Apex & Virtuals can fill: front desk staff, medical assistants, clinical staff, administrative assistants, virtual assistants.

Scoring (0-100 each):
- lead_score: Overall composite. Weight hiring signals 50%, urgency 30%, practice size/growth 20%.
- urgency_score: How urgently they need staffing help NOW (negative reviews about wait times, staff shortages, understaffed signals).
- hiring_signal_score: Direct evidence of hiring for roles Apex fills (job postings, careers page, open positions).

Return ONLY valid JSON with this exact structure, no other text:
{
  "summary": "1-2 sentence overview relevant to staffing needs",
  "pain_points": ["point 1", "point 2"],
  "sales_angles": ["angle 1", "angle 2"],
  "lead_score": 0,
  "urgency_score": 0,
  "hiring_signal_score": 0
}

Provide 2-4 pain points and 2-3 sales angles. All scores must be integers 0-100."""


async def analyze_practice(
    place_id: str,
    name: str,
    website: str | None,
    category: str | None,
) -> dict:
    """Analyze a practice. Uses GPT-4o if API key is set, otherwise returns mock data."""
    if not settings.openai_api_key:
        return _mock_analysis(name, category)

    # Collect data
    website_text = await crawl_website(website or "")
    reviews = await fetch_reviews(place_id)
    reviews_text = format_reviews_for_prompt(reviews)

    # Build user prompt
    user_prompt = f"""Analyze this healthcare practice for staffing needs:

Practice: {name}
Category: {category or 'Unknown'}

=== WEBSITE CONTENT ===
{website_text[:15000] if website_text else 'No website available.'}

=== GOOGLE REVIEWS ===
{reviews_text}
"""

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
    except Exception:
        return _mock_analysis(name, category)

    # Ensure all required fields exist with correct types
    return {
        "summary": result.get("summary", ""),
        "pain_points": json.dumps(result.get("pain_points", [])),
        "sales_angles": json.dumps(result.get("sales_angles", [])),
        "lead_score": _clamp(result.get("lead_score", 0)),
        "urgency_score": _clamp(result.get("urgency_score", 0)),
        "hiring_signal_score": _clamp(result.get("hiring_signal_score", 0)),
    }


def _clamp(value: int) -> int:
    """Clamp score to 0-100."""
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


MOCK_PAIN_POINTS = {
    "dental": [
        "Multiple reviews mention long wait times for appointments",
        "Website shows 3 open front desk positions unfilled for 2+ months",
        "Patient complaints about phone responsiveness and scheduling delays",
        "Small team handling high patient volume with no admin support",
    ],
    "chiropractic": [
        "Reviews cite difficulty reaching office by phone",
        "No online scheduling available — all booking is phone-based",
        "Single receptionist managing a multi-provider practice",
        "Patients report long hold times and missed callbacks",
    ],
    "urgent_care": [
        "Frequent reviews about excessive wait times (2+ hours)",
        "Website careers page lists multiple MA and front desk openings",
        "Staff turnover evident from reviews mentioning 'new staff every visit'",
        "Understaffed night and weekend shifts based on patient feedback",
    ],
    "mental_health": [
        "Weeks-long wait for new patient appointments",
        "Reviews mention difficulty with billing and insurance follow-up",
        "No dedicated admin staff — providers handling scheduling themselves",
        "Patient intake process described as slow and disorganized",
    ],
    "primary_care": [
        "Reviews frequently mention long wait times in lobby",
        "Website shows hiring for medical assistants and front desk",
        "Patients report difficulty getting referral paperwork processed",
        "Phone system overwhelmed — multiple reviews about busy signals",
    ],
    "specialty": [
        "Complex referral and prior-auth process causing patient frustration",
        "Reviews mention staff seeming overwhelmed and rushed",
        "Limited appointment availability suggesting capacity constraints",
        "Administrative delays in test results and follow-up communication",
    ],
}

MOCK_SALES_ANGLES = {
    "dental": [
        "Pitch virtual front desk staff to handle scheduling overflow",
        "Propose trained dental admin VAs for insurance verification",
        "Offer temp-to-perm medical receptionists to fill open positions",
    ],
    "chiropractic": [
        "Propose virtual receptionist to handle call volume and scheduling",
        "Pitch admin VA for patient intake and insurance processing",
        "Offer bilingual front desk staff for diverse patient base",
    ],
    "urgent_care": [
        "Pitch staffing packages for night/weekend coverage gaps",
        "Propose trained medical assistants for triage support",
        "Offer front desk temp staffing to reduce patient wait times",
    ],
    "mental_health": [
        "Pitch dedicated intake coordinator to reduce new patient wait",
        "Propose billing specialist VA for insurance and claims management",
        "Offer virtual admin assistant so providers can focus on patients",
    ],
    "primary_care": [
        "Pitch medical assistants to support providers and reduce burnout",
        "Propose virtual front desk staff for phone and scheduling overflow",
        "Offer admin VAs for referral processing and follow-up coordination",
    ],
    "specialty": [
        "Pitch prior-authorization specialist to streamline referral process",
        "Propose admin staff for test result follow-up and patient communication",
        "Offer medical assistants trained in specialty clinic workflows",
    ],
}


def _mock_analysis(name: str, category: str | None) -> dict:
    """Return realistic mock analysis data."""
    cat = category or "primary_care"
    pain_points = MOCK_PAIN_POINTS.get(cat, MOCK_PAIN_POINTS["primary_care"])
    sales_angles = MOCK_SALES_ANGLES.get(cat, MOCK_SALES_ANGLES["primary_care"])

    # Pick 2-3 random items from each list
    selected_pains = random.sample(pain_points, min(3, len(pain_points)))
    selected_angles = random.sample(sales_angles, min(2, len(sales_angles)))

    hiring = random.randint(25, 95)
    urgency = random.randint(20, 80)
    lead = int(hiring * 0.5 + urgency * 0.3 + random.randint(5, 20) * 0.2)
    lead = max(0, min(100, lead))

    return {
        "summary": f"{name} shows signs of staffing challenges typical of {cat.replace('_', ' ')} practices. Review analysis and website signals suggest opportunities for Apex & Virtuals staffing services.",
        "pain_points": json.dumps(selected_pains),
        "sales_angles": json.dumps(selected_angles),
        "lead_score": lead,
        "urgency_score": urgency,
        "hiring_signal_score": hiring,
    }
```

- [ ] **Step 2: Verify mock analysis**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import asyncio, json
from src.analyzer import analyze_practice
result = asyncio.run(analyze_practice('mock_dental_houston_001', 'Bright Smile Dental', None, 'dental'))
print('Summary:', result['summary'][:80])
print('Lead score:', result['lead_score'])
print('Pain points:', json.loads(result['pain_points']))
print('Sales angles:', json.loads(result['sales_angles']))
"
```

Expected: prints a summary, lead_score (30-90 range), 2-3 pain points, 2 sales angles — all dental-specific.

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/analyzer.py
git commit -m "feat: AI analyzer — GPT-4o analysis with category-specific mock fallback"
```

---

## Task 5: Add analyze endpoint to FastAPI

**Files:**
- Modify: `api/index.py`

- [ ] **Step 1: Add the analyze endpoint**

Add these imports to the top of `api/index.py`:

```python
from src.analyzer import analyze_practice
```

Add this new endpoint after the existing `get_single` endpoint:

```python
class AnalyzeRequest(BaseModel):
    force: bool = False


@app.post("/api/practices/{place_id}/analyze")
async def analyze(place_id: str, body: AnalyzeRequest | None = None):
    """Analyze a practice: crawl website, fetch reviews, run GPT-4o."""
    force = body.force if body else False

    # Try to get existing practice from Supabase (or mock data)
    existing = get_practice(place_id)

    # If already analyzed and not forcing, return cached
    if existing and existing.get("lead_score") is not None and not force:
        return existing

    # Get practice info for the analyzer
    if existing:
        name = existing["name"]
        website = existing.get("website")
        category = existing.get("category")
    else:
        # Practice not in DB — need at least a name
        name = place_id
        website = None
        category = None

    # Run analysis
    analysis = await analyze_practice(place_id, name, website, category)

    # Upsert the analysis fields into Supabase
    from src.storage import update_practice_analysis
    updated = update_practice_analysis(place_id, analysis)

    if updated:
        return updated

    # If Supabase not configured, merge analysis into existing data or return standalone
    if existing:
        return {**existing, **analysis}
    return {"place_id": place_id, "name": name, **analysis}
```

- [ ] **Step 2: Add `update_practice_analysis` to storage.py**

Add this function to the end of `src/storage.py`:

```python
def update_practice_analysis(place_id: str, analysis: dict) -> dict | None:
    """Update Phase 2 analysis fields for a practice. Returns updated row or None."""
    client = _get_client()
    if not client:
        return None
    result = (
        client.table("practices")
        .update(analysis)
        .eq("place_id", place_id)
        .execute()
    )
    return result.data[0] if result.data else None
```

- [ ] **Step 3: Smoke-test the analyze endpoint**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
uvicorn api.index:app --port 8001 &
sleep 3
curl -s -X POST http://localhost:8001/api/practices/mock_dental_houston_001/analyze | python -c "
import sys, json
d = json.load(sys.stdin)
print('Name:', d.get('name', d.get('place_id')))
print('Lead score:', d.get('lead_score'))
print('Summary:', d.get('summary', '')[:80])
"
```

Expected: prints name, a lead_score (30-90), and a dental-related summary. Kill the server after.

- [ ] **Step 4: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add api/index.py src/storage.py
git commit -m "feat: POST /api/practices/{place_id}/analyze endpoint"
```

---

## Task 6: Update Practice TypeScript type with Phase 2 fields

**Files:**
- Modify: `web/lib/types.ts`

- [ ] **Step 1: Add Phase 2 fields to the Practice interface**

Replace the entire content of `web/lib/types.ts`:

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

  // Phase 2 (AI analysis)
  summary: string | null
  pain_points: string | null  // JSON string of string[]
  sales_angles: string | null // JSON string of string[]
  lead_score: number | null
  urgency_score: number | null
  hiring_signal_score: number | null
}

/** Parse a JSON string array field, returning [] on failure. */
export function parseJsonArray(value: string | null): string[] {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/lib/types.ts
git commit -m "feat(web): add Phase 2 fields to Practice type + parseJsonArray helper"
```

---

## Task 7: Add `analyzePractice` to API client with mock fallback

**Files:**
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Add the analyzePractice function**

Add this function to the end of `web/lib/api.ts`:

```ts
export async function analyzePractice(
  placeId: string,
  force?: boolean
): Promise<Practice> {
  try {
    return await apiFetch<Practice>(`/api/practices/${placeId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: force ?? false }),
    })
  } catch {
    return mockAnalysis(placeId)
  }
}

function mockAnalysis(placeId: string): Practice {
  const practice = mockPractices.find((p) => p.place_id === placeId) ?? mockPractices[0]
  const hiring = Math.floor(Math.random() * 70) + 25
  const urgency = Math.floor(Math.random() * 60) + 20
  const lead = Math.floor(hiring * 0.5 + urgency * 0.3 + Math.random() * 20)

  const painPoints = [
    "Reviews mention long wait times and difficulty reaching the office",
    "Website shows open positions unfilled for several weeks",
    "Patients report staff seeming overwhelmed during visits",
  ]
  const salesAngles = [
    "Pitch trained front desk staff to handle scheduling overflow",
    "Propose medical assistant staffing to reduce provider burnout",
  ]

  return {
    ...practice,
    summary: `${practice.name} shows staffing challenges typical of ${(practice.category ?? "healthcare").replace("_", " ")} practices. Opportunities exist for Apex & Virtuals staffing solutions.`,
    pain_points: JSON.stringify(painPoints),
    sales_angles: JSON.stringify(salesAngles),
    lead_score: Math.min(100, lead),
    urgency_score: urgency,
    hiring_signal_score: hiring,
  }
}
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/lib/api.ts
git commit -m "feat(web): analyzePractice API function with mock fallback"
```

---

## Task 8: Score bar component

**Files:**
- Create: `web/components/score-bar.tsx`

- [ ] **Step 1: Create `web/components/score-bar.tsx`**

```tsx
import { cn } from "@/lib/utils"

interface ScoreBarProps {
  label: string
  value: number
  max?: number
}

export default function ScoreBar({ label, value, max = 100 }: ScoreBarProps) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  const color =
    value >= 75 ? "bg-rose-500" : value >= 50 ? "bg-amber-400" : "bg-teal-500"

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 w-14 shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all", color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-medium text-gray-700 w-7 text-right">{value}</span>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/score-bar.tsx
git commit -m "feat(web): reusable ScoreBar component"
```

---

## Task 9: Update practice card with Analyze button + inline analysis

**Files:**
- Modify: `web/components/practice-card.tsx`

- [ ] **Step 1: Replace `web/components/practice-card.tsx`**

```tsx
"use client"

import { Phone, Globe, Star, Brain, Loader2 } from "lucide-react"
import type { Practice } from "@/lib/types"
import { parseJsonArray } from "@/lib/types"
import { cn } from "@/lib/utils"
import ScoreBar from "./score-bar"

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

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 75
      ? "bg-rose-100 text-rose-700"
      : score >= 50
        ? "bg-amber-100 text-amber-700"
        : "bg-teal-100 text-teal-700"
  return (
    <span className={cn("text-xs font-bold px-1.5 py-0.5 rounded-full", color)}>
      {score}
    </span>
  )
}

interface PracticeCardProps {
  practice: Practice
  isSelected: boolean
  onSelect: (placeId: string) => void
  onAnalyze: (placeId: string) => void
  isAnalyzing: boolean
}

export default function PracticeCard({
  practice,
  isSelected,
  onSelect,
  onAnalyze,
  isAnalyzing,
}: PracticeCardProps) {
  const isScored = practice.lead_score != null
  const painPoints = parseJsonArray(practice.pain_points ?? null)
  const salesAngles = parseJsonArray(practice.sales_angles ?? null)

  return (
    <div
      onClick={() => onSelect(practice.place_id)}
      className={cn(
        "w-full text-left p-4 rounded-xl transition-all cursor-pointer",
        "hover:bg-ivory-200/60",
        isSelected ? "bg-teal-50 ring-1 ring-teal-600/30" : "bg-white/60"
      )}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-serif font-semibold text-gray-900 text-base leading-tight">
          {practice.name}
        </h3>
        {isScored && <ScoreBadge score={practice.lead_score!} />}
      </div>
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

      {/* Action buttons */}
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
        <button
          onClick={(e) => {
            e.stopPropagation()
            onAnalyze(practice.place_id)
          }}
          disabled={isAnalyzing}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-teal-600 text-teal-700 hover:bg-teal-50 disabled:opacity-50 transition"
        >
          {isAnalyzing ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Brain className="w-3 h-3" />
          )}
          {isAnalyzing ? "Analyzing..." : isScored ? "Re-analyze" : "Analyze"}
        </button>
      </div>

      {/* Inline analysis results */}
      {isScored && (
        <div className="mt-3 pt-3 border-t border-gray-200/50 space-y-3">
          {/* Summary */}
          {practice.summary && (
            <p className="text-xs text-gray-600 leading-relaxed">{practice.summary}</p>
          )}

          {/* Pain points */}
          {painPoints.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-700 mb-1">Pain Points</h4>
              <ul className="space-y-0.5">
                {painPoints.map((p, i) => (
                  <li key={i} className="text-xs text-gray-500 flex gap-1.5">
                    <span className="text-rose-400 shrink-0">•</span>
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Sales angles */}
          {salesAngles.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-700 mb-1">Sales Angles</h4>
              <ul className="space-y-0.5">
                {salesAngles.map((a, i) => (
                  <li key={i} className="text-xs text-gray-500 flex gap-1.5">
                    <span className="text-teal-500 shrink-0">→</span>
                    {a}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Score bars */}
          <div className="space-y-1.5">
            <ScoreBar label="Lead" value={practice.lead_score!} />
            <ScoreBar label="Urgency" value={practice.urgency_score!} />
            <ScoreBar label="Hiring" value={practice.hiring_signal_score!} />
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/practice-card.tsx
git commit -m "feat(web): practice card with Analyze button, score badge, inline analysis"
```

---

## Task 10: Update top bar with "Score All" button

**Files:**
- Modify: `web/components/top-bar.tsx`

- [ ] **Step 1: Replace `web/components/top-bar.tsx`**

```tsx
"use client"

import { Brain } from "lucide-react"
import SearchBar from "./search-bar"

interface TopBarProps {
  onSearch: (query: string) => void
  isLoading: boolean
  onScoreAll: () => void
  scoreProgress: string | null
}

export default function TopBar({ onSearch, isLoading, onScoreAll, scoreProgress }: TopBarProps) {
  return (
    <header className="fixed top-0 left-0 right-0 z-20 h-14 flex items-center justify-between px-6 bg-white/70 backdrop-blur-md border-b border-gray-200/50">
      <div className="flex items-center gap-2">
        <span className="font-serif text-lg font-bold text-teal-700 tracking-tight">
          Health&amp;Virtuals
        </span>
        <span className="text-xs text-gray-400 font-medium">Sales Intel</span>
      </div>
      <div className="flex items-center gap-3">
        <SearchBar onSearch={onSearch} isLoading={isLoading} />
        <button
          onClick={onScoreAll}
          disabled={!!scoreProgress}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-teal-600 text-teal-700 text-sm font-medium hover:bg-teal-50 disabled:opacity-50 transition"
        >
          <Brain className="w-4 h-4" />
          {scoreProgress ?? "Score All"}
        </button>
      </div>
    </header>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/top-bar.tsx
git commit -m "feat(web): Score All button in top bar with progress display"
```

---

## Task 11: Update map pins with lead_score colors

**Files:**
- Modify: `web/components/map-view.tsx`

- [ ] **Step 1: Update `createPinIcon` to accept lead_score**

Replace the `createPinIcon` function in `web/components/map-view.tsx`:

```tsx
function createPinIcon(rating: number | null, leadScore: number | null): L.DivIcon {
  const label = rating ? rating.toFixed(1) : "\u2014"
  let fill = "#0d9488" // teal (default / unscored / 0-49)
  if (leadScore != null && leadScore >= 75) {
    fill = "#e11d48" // rose (hot lead)
  } else if (leadScore != null && leadScore >= 50) {
    fill = "#f59e0b" // amber
  }
  return L.divIcon({
    className: "",
    iconSize: [32, 42],
    iconAnchor: [16, 42],
    popupAnchor: [0, -42],
    html: `
      <svg width="32" height="42" viewBox="0 0 32 42" xmlns="http://www.w3.org/2000/svg">
        <path d="M16 0C7.16 0 0 7.16 0 16c0 12 16 26 16 26s16-14 16-26C32 7.16 24.84 0 16 0z"
              fill="${fill}" stroke="#fff" stroke-width="1.5"/>
        <text x="16" y="18" text-anchor="middle" fill="white"
              font-family="system-ui" font-size="10" font-weight="600">${label}</text>
      </svg>
    `,
  })
}
```

- [ ] **Step 2: Update the Marker to pass lead_score**

In the same file, update the `Marker` component's `icon` prop from:

```tsx
icon={createPinIcon(p.rating)}
```

to:

```tsx
icon={createPinIcon(p.rating, p.lead_score ?? null)}
```

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/map-view.tsx
git commit -m "feat(web): map pin colors — teal/amber/rose based on lead_score"
```

---

## Task 12: Wire up main page — analyze, score all, sorting

**Files:**
- Modify: `web/app/page.tsx`

- [ ] **Step 1: Replace `web/app/page.tsx`**

```tsx
"use client"

import { useState, useMemo, useCallback } from "react"
import dynamic from "next/dynamic"
import type { Practice } from "@/lib/types"
import { mockPractices } from "@/lib/mock-data"
import TopBar from "@/components/top-bar"
import PracticeCard from "@/components/practice-card"
import FilterBar from "@/components/filter-bar"
import { searchPractices, analyzePractice } from "@/lib/api"

const MapView = dynamic(() => import("@/components/map-view"), { ssr: false })

export default function Page() {
  const [practices, setPractices] = useState<Practice[]>(mockPractices)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [cityLabel, setCityLabel] = useState("")
  const [category, setCategory] = useState("")
  const [minRating, setMinRating] = useState(0)
  const [analyzingIds, setAnalyzingIds] = useState<Set<string>>(new Set())
  const [scoreProgress, setScoreProgress] = useState<string | null>(null)

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

  const handleAnalyze = useCallback(async (placeId: string) => {
    setAnalyzingIds((prev) => new Set(prev).add(placeId))
    try {
      const updated = await analyzePractice(placeId)
      setPractices((prev) =>
        prev.map((p) => (p.place_id === placeId ? { ...p, ...updated } : p))
      )
    } finally {
      setAnalyzingIds((prev) => {
        const next = new Set(prev)
        next.delete(placeId)
        return next
      })
    }
  }, [])

  const handleScoreAll = useCallback(async () => {
    const unscored = practices.filter((p) => p.lead_score == null)
    if (unscored.length === 0) return

    for (let i = 0; i < unscored.length; i++) {
      setScoreProgress(`Scoring ${i + 1}/${unscored.length}...`)
      const placeId = unscored[i].place_id
      setAnalyzingIds((prev) => new Set(prev).add(placeId))
      try {
        const updated = await analyzePractice(placeId)
        setPractices((prev) =>
          prev.map((p) => (p.place_id === placeId ? { ...p, ...updated } : p))
        )
      } finally {
        setAnalyzingIds((prev) => {
          const next = new Set(prev)
          next.delete(placeId)
          return next
        })
      }
    }
    setScoreProgress(null)
  }, [practices])

  const filtered = useMemo(() => {
    const list = practices.filter((p) => {
      if (category && p.category !== category) return false
      if (minRating && (p.rating ?? 0) < minRating) return false
      return true
    })
    // Sort: scored practices first (by lead_score desc), then unscored
    return list.sort((a, b) => {
      const aScore = a.lead_score ?? -1
      const bScore = b.lead_score ?? -1
      return bScore - aScore
    })
  }, [practices, category, minRating])

  return (
    <div className="h-screen w-screen overflow-hidden">
      <TopBar
        onSearch={handleSearch}
        isLoading={isLoading}
        onScoreAll={handleScoreAll}
        scoreProgress={scoreProgress}
      />

      <main className="relative w-full h-full pt-14">
        {/* Sidebar */}
        <div className="absolute top-2 left-4 bottom-4 w-[390px] z-10 glass-panel rounded-2xl flex flex-col overflow-hidden">
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
                  onAnalyze={handleAnalyze}
                  isAnalyzing={analyzingIds.has(p.place_id)}
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
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/app/page.tsx
git commit -m "feat(web): wire up analyze, score all, lead_score sorting"
```

---

## Task 13: Final verification and push

**Files:**
- None (verification only)

- [ ] **Step 1: Typecheck**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 2: Lint**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npm run lint`
Expected: no errors.

- [ ] **Step 3: Production build**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npm run build`
Expected: succeeds.

- [ ] **Step 4: Backend smoke test**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import asyncio, json
from src.analyzer import analyze_practice
from src.crawler import crawl_website
# Test crawler
text = asyncio.run(crawl_website(''))
print(f'Empty crawl: {repr(text)}')
# Test analyzer mock
result = asyncio.run(analyze_practice('mock_dental_houston_001', 'Bright Smile Dental', None, 'dental'))
print(f'Mock analysis lead_score: {result[\"lead_score\"]}')
print(f'Pain points: {json.loads(result[\"pain_points\"])[0][:50]}...')
print('Backend OK')
"
```

Expected: empty crawl returns `''`, mock analysis returns valid scores and pain points.

- [ ] **Step 5: Visual check**

Run: `cd "c:/Users/Moiz Ahmed/hv-sales-intel/web" && npx next dev --port 3005`

Verify in browser at http://localhost:3005:
1. Practice cards show an "Analyze" button (teal outline, brain icon).
2. Clicking "Analyze" shows spinner, then card expands with: summary, pain points (rose bullets), sales angles (teal arrows), three score bars.
3. Lead score badge appears next to practice name (colored pill).
4. "Score All" button in top bar. Clicking it shows "Scoring 1/20..." progress, analyzes each card sequentially.
5. After scoring, cards re-sort with highest lead_score at top.
6. Map pins change color: teal (0-49), amber (50-74), rose (75+).
7. Clicking "Re-analyze" on a scored card re-runs analysis.

Ctrl+C to stop.

- [ ] **Step 6: Final commit (if fixes were needed)**

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

Expected: push succeeds.

---

## Self-review notes

- **Spec coverage:** Data collection pipeline (crawl + reviews + GPT) → Tasks 2, 3, 4. Scoring dimensions → Task 4 (SYSTEM_PROMPT). API endpoint → Task 5. Mock fallback → Tasks 4, 7. UI: Analyze button → Task 9. Score badge → Task 9. Inline analysis → Task 9. Score All → Tasks 10, 12. Map pin colors → Task 11. Sidebar sorting → Task 12. Practice type update → Task 6.
- **Placeholders:** None. All code blocks are complete. Mock data includes category-specific pain points and sales angles for all 6 categories.
- **Type consistency:** `Practice` interface (Task 6) adds `summary`, `pain_points`, `sales_angles`, `lead_score`, `urgency_score`, `hiring_signal_score` — matching Python model and Supabase schema exactly. `parseJsonArray` (Task 6) used in `practice-card.tsx` (Task 9). `analyzePractice` (Task 7) returns `Practice`. `PracticeCardProps` (Task 9) adds `onAnalyze` and `isAnalyzing`. `TopBarProps` (Task 10) adds `onScoreAll` and `scoreProgress`. `createPinIcon` (Task 11) signature changes to `(rating, leadScore)` — called correctly in the same file.
- **File sizes:** All files under 150 lines except `analyzer.py` (~180 lines, includes mock data for 6 categories — unavoidable) and `practice-card.tsx` (~170 lines with inline analysis section).
