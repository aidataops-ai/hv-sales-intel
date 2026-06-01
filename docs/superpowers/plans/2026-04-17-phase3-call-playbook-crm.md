# Phase 3: Cold Call Playbook + CRM Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated Call Prep page with GPT-generated cold call playbooks (5 sections), call notes, and a CRM pipeline (NEW → CLOSED WON/LOST) with auto-status transitions, status badges, and filtering.

**Architecture:** New `scriptgen.py` module generates playbooks via GPT-4o-mini with mock fallback. New PATCH endpoint updates status/notes. New Next.js route `/practice/[place_id]` renders the three-column Call Prep page. Status badges and filters added to existing sidebar. Analyzer modified to clear cached scripts and auto-set status on re-analysis.

**Tech Stack:** Python (OpenAI), FastAPI, Next.js 14, React 18, TypeScript, Tailwind CSS.

**Reference spec:** [docs/specs/2026-04-17-phase3-call-playbook-crm-design.md](../../specs/2026-04-17-phase3-call-playbook-crm-design.md)

---

## File Structure

```
src/
├── scriptgen.py          (create) GPT playbook generator + mock fallback
├── analyzer.py           (modify) Clear call_script on re-analysis, auto-set status RESEARCHED
├── storage.py            (modify) Add update_practice_fields, get_script, set_script

api/
└── index.py              (modify) Add GET/POST script endpoints, PATCH practice endpoint

supabase/
└── schema.sql            (modify) Add call_script column

web/
├── app/
│   ├── page.tsx                    (modify) Add status filter, pass navigate handler
│   └── practice/
│       └── [place_id]/
│           └── page.tsx            (create) Call Prep page — three columns
├── components/
│   ├── practice-card.tsx           (modify) Add Call Prep button, status badge, name as link
│   ├── status-badge.tsx            (create) Colored status pill component
│   ├── script-view.tsx             (create) Playbook renderer (5 sections)
│   ├── notes-panel.tsx             (create) Notepad + save + activity history
│   ├── practice-info.tsx           (create) Left column — practice details + analysis
│   ├── filter-bar.tsx              (modify) Add status filter dropdown
│   └── score-bar.tsx               (no change)
├── lib/
│   ├── types.ts                    (modify) Add ScriptSection type, call_script field
│   └── api.ts                      (modify) Add getScript, regenerateScript, updatePractice
```

**Responsibility boundaries:**
- `src/scriptgen.py` — ONLY module that generates playbook content (GPT or mock). Returns `{ sections: [...] }`.
- `web/components/script-view.tsx` — ONLY component that renders playbook sections. Takes sections array as prop.
- `web/components/notes-panel.tsx` — ONLY component that handles notes save + activity history. Calls `updatePractice` API.
- `web/components/practice-info.tsx` — Read-only left column. Extracted from practice-card to avoid duplication.

---

## Task 1: Add `call_script` column to schema

**Files:**
- Modify: `supabase/schema.sql`

- [ ] **Step 1: Add column to schema file**

In `supabase/schema.sql`, add after the `hiring_signal_score` line:

```sql
  -- Phase 3 (Call Playbook)
  call_script text,
```

- [ ] **Step 2: Run migration on Supabase**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
from src.settings import settings
from supabase import create_client
client = create_client(settings.supabase_url, settings.supabase_key)
# Add column if it doesn't exist — Supabase handles this via RPC or dashboard
# For now, use raw SQL via the Supabase dashboard SQL editor:
# ALTER TABLE practices ADD COLUMN IF NOT EXISTS call_script text;
print('Run in Supabase SQL editor: ALTER TABLE practices ADD COLUMN IF NOT EXISTS call_script text;')
"
```

If you have Supabase CLI, run: `supabase db push`. Otherwise, run the ALTER TABLE in the Supabase dashboard SQL editor.

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add supabase/schema.sql
git commit -m "feat: add call_script column to practices schema"
```

---

## Task 2: Script generator with mock fallback (`src/scriptgen.py`)

**Files:**
- Create: `src/scriptgen.py`

- [ ] **Step 1: Create `src/scriptgen.py`**

```python
import json

from openai import AsyncOpenAI

from src.settings import settings

SYSTEM_PROMPT = """You are a cold call script writer for Apex & Virtuals, a healthcare staffing and talent acquisition company.

Given information about a healthcare practice (name, category, analysis summary, pain points, sales angles), generate a structured cold call playbook.

Return ONLY valid JSON with this exact structure:
{
  "sections": [
    {
      "title": "Opening",
      "icon": "phone",
      "content": "The opening script text..."
    },
    {
      "title": "Discovery Questions",
      "icon": "search",
      "content": "3-4 numbered questions..."
    },
    {
      "title": "Pitch",
      "icon": "target",
      "content": "The tailored pitch..."
    },
    {
      "title": "Objection Handling",
      "icon": "shield",
      "content": "3-4 objections with rebuttals, formatted as 'Objection: ... Response: ...'"
    },
    {
      "title": "Closing",
      "icon": "check",
      "content": "The closing script with next steps..."
    }
  ]
}

Guidelines:
- Opening: Reference the practice by name, mention something specific about them (category, size, detail from analysis)
- Discovery Questions: Ask about staffing challenges, hiring timeline, current workflow pain points
- Pitch: Directly address their specific pain points. Mention Apex & Virtuals by name. Focus on staffing solutions they need.
- Objection Handling: Include "We already have a recruiter", "We can't afford it", "We're not hiring right now", and one specific to their situation
- Closing: Suggest a 15-minute meeting, offer a free staffing assessment, provide follow-up framing

Keep each section 3-6 sentences. Be conversational, not robotic. Use the rep's perspective ("I", "we at Apex & Virtuals")."""


async def generate_script(
    name: str,
    category: str | None,
    summary: str | None,
    pain_points: str | None,
    sales_angles: str | None,
) -> dict:
    """Generate a cold call playbook. Uses GPT if API key set, otherwise mock."""
    if not settings.openai_api_key:
        return _mock_script(name, category)

    user_prompt = f"""Generate a cold call playbook for this practice:

Practice: {name}
Category: {category or 'Healthcare'}
Analysis Summary: {summary or 'No analysis available'}
Pain Points: {pain_points or '[]'}
Sales Angles: {sales_angles or '[]'}
"""

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        if "sections" in result and len(result["sections"]) == 5:
            return result
    except Exception:
        pass

    return _mock_script(name, category)


MOCK_SCRIPTS = {
    "dental": {
        "sections": [
            {
                "title": "Opening",
                "icon": "phone",
                "content": "Hi, this is [Your Name] calling from Apex & Virtuals. I'm reaching out because we specialize in staffing solutions for dental practices, and I noticed {name} may benefit from some of our services. Do you have a quick moment?"
            },
            {
                "title": "Discovery Questions",
                "icon": "search",
                "content": "1. How are you currently handling front desk coverage when staff call out or during peak hours?\n2. Are you finding it challenging to recruit and retain qualified dental assistants in this market?\n3. How much time does your office manager spend on scheduling and administrative tasks versus patient coordination?\n4. If you could add one more person to your team tomorrow, what role would make the biggest impact?"
            },
            {
                "title": "Pitch",
                "icon": "target",
                "content": "At Apex & Virtuals, we provide pre-vetted front desk staff, dental assistants, and administrative support specifically for dental practices like {name}. We handle recruiting, screening, and onboarding so you can focus on patient care. Our placements typically reduce scheduling delays by 40% and free up 10+ hours per week of admin time for your existing team."
            },
            {
                "title": "Objection Handling",
                "icon": "shield",
                "content": "Objection: \"We already have a recruiter.\"\nResponse: That's great — we actually complement existing recruiters. We focus specifically on healthcare staffing with candidates who are pre-trained in dental office workflows, so there's no overlap.\n\nObjection: \"We can't afford it right now.\"\nResponse: I understand budget is always a consideration. Many of our dental clients actually save money because our temp-to-perm model eliminates costly bad hires and reduces overtime costs.\n\nObjection: \"We're not hiring right now.\"\nResponse: That's perfectly fine. Many practices work with us proactively so when a position does open up, they have qualified candidates ready to go within 48 hours instead of spending weeks searching.\n\nObjection: \"We've had bad experiences with staffing agencies.\"\nResponse: I hear that a lot, and it's exactly why we exist. We're not a general staffing agency — we only place healthcare professionals, and every candidate goes through a dental-specific skills assessment."
            },
            {
                "title": "Closing",
                "icon": "check",
                "content": "I'd love to set up a quick 15-minute call to learn more about your practice and share how we've helped similar dental offices in your area. We also offer a free staffing assessment where we review your current team structure and identify areas where we could add value. Would Tuesday or Wednesday work better for a brief chat?"
            }
        ]
    },
    "chiropractic": {
        "sections": [
            {
                "title": "Opening",
                "icon": "phone",
                "content": "Hi, this is [Your Name] from Apex & Virtuals. We work with chiropractic practices to solve staffing challenges — from front desk coverage to clinical support. I came across {name} and thought we might be able to help. Do you have a moment?"
            },
            {
                "title": "Discovery Questions",
                "icon": "search",
                "content": "1. How are patient calls and scheduling handled when your front desk staff is unavailable?\n2. Are your practitioners currently handling any administrative tasks that take away from patient care?\n3. What's your biggest challenge right now when it comes to staffing or team capacity?\n4. Have you considered virtual assistant support for insurance verification and patient intake?"
            },
            {
                "title": "Pitch",
                "icon": "target",
                "content": "Apex & Virtuals provides trained front desk staff and virtual assistants who specialize in chiropractic practice workflows. We can handle patient scheduling, insurance verification, and intake processing so your providers can focus entirely on patient care. Our clients typically see a 30% reduction in patient wait times and significantly improved phone answer rates."
            },
            {
                "title": "Objection Handling",
                "icon": "shield",
                "content": "Objection: \"We already have a recruiter.\"\nResponse: We complement recruiters by providing healthcare-specific candidates who understand chiropractic workflows from day one — no general staffing learning curve.\n\nObjection: \"We can't afford it.\"\nResponse: Our virtual assistant services actually start at a fraction of the cost of a full-time hire, and many clients see ROI within the first month through improved patient retention.\n\nObjection: \"We're not hiring.\"\nResponse: Totally understand. Many practices use us as a backup — so when someone calls out or you hit a busy season, you have trained staff ready to step in immediately.\n\nObjection: \"Our practice is too small for staffing services.\"\nResponse: Actually, smaller practices benefit the most. A single trained VA can handle scheduling, insurance, and follow-ups — work that currently falls on your providers."
            },
            {
                "title": "Closing",
                "icon": "check",
                "content": "Could we schedule a quick 15-minute call this week? I'd like to learn more about {name} and share some specific examples of how we've helped chiropractic practices your size. We also offer a complimentary staffing assessment. Does Thursday afternoon work?"
            }
        ]
    },
}


def _mock_script(name: str, category: str | None) -> dict:
    """Return a category-appropriate mock playbook."""
    cat = category or "dental"
    template = MOCK_SCRIPTS.get(cat, MOCK_SCRIPTS["dental"])

    # Replace {name} placeholder in mock scripts
    sections = []
    for section in template["sections"]:
        sections.append({
            "title": section["title"],
            "icon": section["icon"],
            "content": section["content"].replace("{name}", name),
        })

    return {"sections": sections}
```

- [ ] **Step 2: Verify mock script generation**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import asyncio, json
from src.scriptgen import generate_script
result = asyncio.run(generate_script('Houston Dental Care', 'dental', None, None, None))
print(f'Sections: {len(result[\"sections\"])}')
for s in result['sections']:
    print(f'  {s[\"title\"]}: {s[\"content\"][:60]}...')
"
```

Expected: 5 sections with dental-specific content mentioning "Houston Dental Care".

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/scriptgen.py
git commit -m "feat: GPT playbook generator with category-specific mock fallback"
```

---

## Task 3: Modify analyzer to clear script + auto-set status

**Files:**
- Modify: `src/analyzer.py`

- [ ] **Step 1: Add `call_script` clearing to the analysis result**

At the end of the `analyze_practice` function (before the final `return`), add `call_script: None` to the returned dict. This ensures re-analysis clears any cached script.

In the real GPT path, after the line `return {`:
```python
    return {
        "summary": result.get("summary", ""),
        "pain_points": json.dumps(result.get("pain_points", [])),
        "sales_angles": json.dumps(result.get("sales_angles", [])),
        "lead_score": _clamp(result.get("lead_score", 0)),
        "urgency_score": _clamp(result.get("urgency_score", 0)),
        "hiring_signal_score": _clamp(result.get("hiring_signal_score", 0)),
        "call_script": None,
    }
```

In the `_mock_analysis` function, add to the returned dict:
```python
        "call_script": None,
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/analyzer.py
git commit -m "feat: clear cached call_script on re-analysis"
```

---

## Task 4: Add storage helpers for script + status/notes updates

**Files:**
- Modify: `src/storage.py`

- [ ] **Step 1: Add `update_practice_fields` function**

Add this function to the end of `src/storage.py`:

```python
def update_practice_fields(place_id: str, fields: dict) -> dict | None:
    """Update arbitrary fields on a practice. Returns updated row or None."""
    client = _get_client()
    if not client:
        return None
    result = (
        client.table("practices")
        .update(fields)
        .eq("place_id", place_id)
        .execute()
    )
    return result.data[0] if result.data else None
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add src/storage.py
git commit -m "feat: add update_practice_fields storage helper"
```

---

## Task 5: Add script + PATCH endpoints to FastAPI

**Files:**
- Modify: `api/index.py`

- [ ] **Step 1: Add imports**

Add to the imports at the top of `api/index.py`:

```python
from src.scriptgen import generate_script
from src.storage import upsert_practices, query_practices, get_practice, update_practice_analysis, update_practice_fields
```

(Replace the existing `from src.storage import ...` line to include `update_practice_fields`.)

- [ ] **Step 2: Add the script endpoints and PATCH endpoint**

Add after the existing `analyze` endpoint:

```python
# Status ordering for auto-transitions
STATUS_ORDER = [
    "NEW", "RESEARCHED", "SCRIPT READY", "CONTACTED",
    "FOLLOW UP", "MEETING SET", "PROPOSAL", "CLOSED WON", "CLOSED LOST",
]


def _should_auto_advance(current: str, target: str) -> bool:
    """Return True if target is ahead of current in the pipeline."""
    try:
        return STATUS_ORDER.index(target) > STATUS_ORDER.index(current)
    except ValueError:
        return False


@app.get("/api/practices/{place_id}/script")
async def get_script(place_id: str):
    """Get or generate the call script for a practice."""
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(status_code=404, detail="Practice not found")

    # Return cached script if it exists
    if practice.get("call_script"):
        import json
        return json.loads(practice["call_script"])

    # Generate new script
    script = await generate_script(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
    )

    # Store script
    import json
    update_practice_fields(place_id, {"call_script": json.dumps(script)})

    # Auto-advance status to SCRIPT READY
    current_status = practice.get("status", "NEW")
    if _should_auto_advance(current_status, "SCRIPT READY"):
        update_practice_fields(place_id, {"status": "SCRIPT READY"})

    return script


@app.post("/api/practices/{place_id}/script")
async def regenerate_script(place_id: str):
    """Force regenerate the call script."""
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(status_code=404, detail="Practice not found")

    script = await generate_script(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
    )

    import json
    update_practice_fields(place_id, {"call_script": json.dumps(script)})

    return script


class PatchPracticeRequest(BaseModel):
    status: str | None = None
    notes: str | None = None


@app.patch("/api/practices/{place_id}")
def patch_practice(place_id: str, body: PatchPracticeRequest):
    """Update status and/or notes for a practice."""
    fields: dict = {}
    if body.status is not None:
        if body.status not in STATUS_ORDER:
            raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
        fields["status"] = body.status
    if body.notes is not None:
        fields["notes"] = body.notes
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = update_practice_fields(place_id, fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Practice not found")
    return updated
```

- [ ] **Step 3: Also update the analyze endpoint to auto-set RESEARCHED status**

In the existing `analyze` endpoint, after `analysis = await analyze_practice(...)`, add auto-status logic. Replace the upsert block:

```python
    # Run analysis
    analysis = await analyze_practice(place_id, name, website, category)

    # Auto-advance status to RESEARCHED
    if existing:
        current_status = existing.get("status", "NEW")
        if _should_auto_advance(current_status, "RESEARCHED"):
            analysis["status"] = "RESEARCHED"

    # Upsert the analysis fields into Supabase
    updated = update_practice_analysis(place_id, analysis)
```

- [ ] **Step 4: Move `import json` to the top of the file**

Add `import json` to the imports at the top of `api/index.py` so the script endpoints don't need inline imports.

- [ ] **Step 5: Smoke test**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
uvicorn api.index:app --port 8006 &
sleep 3
# Test script generation
curl -s http://localhost:8006/api/practices/real_dental_houston_001/script | python -c "
import sys, json
d = json.load(sys.stdin)
print(f'Sections: {len(d.get(\"sections\", []))}')
for s in d['sections']:
    print(f'  {s[\"title\"]}: {s[\"content\"][:50]}...')
"
echo ""
# Test PATCH
curl -s -X PATCH http://localhost:8006/api/practices/real_dental_houston_001 \
  -H "Content-Type: application/json" \
  -d '{"status": "CONTACTED", "notes": "Left voicemail"}' | python -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status: {d.get(\"status\")}')
print(f'Notes: {d.get(\"notes\")}')
"
```

Expected: 5 script sections printed, then status=CONTACTED and notes shown.

Kill the server after testing.

- [ ] **Step 6: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add api/index.py
git commit -m "feat: script GET/POST endpoints + PATCH status/notes + auto-status transitions"
```

---

## Task 6: Update TypeScript types + API client

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Add ScriptSection type and call_script field to `web/lib/types.ts`**

Add after the `Practice` interface:

```ts
export interface ScriptSection {
  title: string
  icon: string
  content: string
}

export interface Script {
  sections: ScriptSection[]
}
```

Add `call_script` to the Practice interface (in the Phase 2 optional section):

```ts
  call_script?: string | null // JSON string of Script
```

- [ ] **Step 2: Add API functions to `web/lib/api.ts`**

Add to the end of the file:

```ts
export async function getScript(placeId: string): Promise<Script> {
  try {
    return await apiFetch<Script>(`/api/practices/${placeId}/script`)
  } catch {
    return mockScript(placeId)
  }
}

export async function regenerateScript(placeId: string): Promise<Script> {
  try {
    return await apiFetch<Script>(`/api/practices/${placeId}/script`, {
      method: "POST",
    })
  } catch {
    return mockScript(placeId)
  }
}

export async function updatePractice(
  placeId: string,
  fields: { status?: string; notes?: string }
): Promise<Practice> {
  try {
    return await apiFetch<Practice>(`/api/practices/${placeId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    })
  } catch {
    const practice = mockPractices.find((p) => p.place_id === placeId) ?? mockPractices[0]
    return { ...practice, ...fields }
  }
}

function mockScript(placeId: string): Script {
  const practice = mockPractices.find((p) => p.place_id === placeId) ?? mockPractices[0]
  const name = practice.name
  return {
    sections: [
      { title: "Opening", icon: "phone", content: `Hi, this is [Your Name] from Apex & Virtuals. I'm calling about ${name} — we specialize in healthcare staffing and I noticed your practice may benefit from our services. Do you have a quick moment?` },
      { title: "Discovery Questions", icon: "search", content: "1. How are you currently handling front desk coverage during peak hours or when staff call out?\n2. Are you finding it challenging to recruit qualified clinical staff in this market?\n3. How much admin time do your providers spend that could be handled by support staff?\n4. If you could add one team member tomorrow, what role would it be?" },
      { title: "Pitch", icon: "target", content: `At Apex & Virtuals, we provide pre-vetted healthcare staff — front desk, medical assistants, and admin support — specifically for practices like ${name}. We handle the recruiting and screening so you can focus on patients.` },
      { title: "Objection Handling", icon: "shield", content: "Objection: \"We already have a recruiter.\"\nResponse: We complement recruiters with healthcare-specific candidates ready from day one.\n\nObjection: \"We can't afford it.\"\nResponse: Our model often saves money by eliminating bad hires and reducing overtime.\n\nObjection: \"We're not hiring.\"\nResponse: Many practices use us proactively so qualified candidates are ready when a need arises." },
      { title: "Closing", icon: "check", content: "I'd love to set up a 15-minute call to learn more about your practice and share how we've helped similar offices. We also offer a free staffing assessment. Would later this week work?" },
    ],
  }
}
```

Add the import for Script type at the top of api.ts:

```ts
import type { Practice, Script } from "./types"
```

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/lib/types.ts web/lib/api.ts
git commit -m "feat(web): ScriptSection type + getScript/regenerateScript/updatePractice API functions"
```

---

## Task 7: Status badge component

**Files:**
- Create: `web/components/status-badge.tsx`

- [ ] **Step 1: Create `web/components/status-badge.tsx`**

```tsx
import { cn } from "@/lib/utils"

const STATUS_COLORS: Record<string, string> = {
  NEW: "bg-gray-100 text-gray-600",
  RESEARCHED: "bg-blue-100 text-blue-700",
  "SCRIPT READY": "bg-blue-100 text-blue-700",
  CONTACTED: "bg-amber-100 text-amber-700",
  "FOLLOW UP": "bg-amber-100 text-amber-700",
  "MEETING SET": "bg-teal-100 text-teal-700",
  PROPOSAL: "bg-teal-100 text-teal-700",
  "CLOSED WON": "bg-green-100 text-green-700",
  "CLOSED LOST": "bg-rose-100 text-rose-700",
}

export default function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? "bg-gray-100 text-gray-600"
  return (
    <span className={cn("text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide", color)}>
      {status}
    </span>
  )
}

export const ALL_STATUSES = [
  "NEW",
  "RESEARCHED",
  "SCRIPT READY",
  "CONTACTED",
  "FOLLOW UP",
  "MEETING SET",
  "PROPOSAL",
  "CLOSED WON",
  "CLOSED LOST",
]
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/status-badge.tsx
git commit -m "feat(web): StatusBadge component with pipeline color coding"
```

---

## Task 8: Script view component

**Files:**
- Create: `web/components/script-view.tsx`

- [ ] **Step 1: Create `web/components/script-view.tsx`**

```tsx
"use client"

import { Phone, Search, Target, Shield, CheckCircle, Loader2, RefreshCw } from "lucide-react"
import type { ScriptSection } from "@/lib/types"

const ICON_MAP: Record<string, React.ElementType> = {
  phone: Phone,
  search: Search,
  target: Target,
  shield: Shield,
  check: CheckCircle,
}

interface ScriptViewProps {
  sections: ScriptSection[]
  isLoading: boolean
  onRegenerate: () => void
}

export default function ScriptView({ sections, isLoading, onRegenerate }: ScriptViewProps) {
  if (isLoading && sections.length === 0) {
    return (
      <div className="flex items-center justify-center py-20 text-gray-400">
        <Loader2 className="w-6 h-6 animate-spin mr-2" />
        Generating playbook...
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {sections.map((section, i) => {
        const Icon = ICON_MAP[section.icon] ?? Phone
        return (
          <div key={i} className="space-y-2">
            <div className="flex items-center gap-2">
              <Icon className="w-4 h-4 text-teal-600" />
              <h3 className="font-serif font-semibold text-gray-900">{section.title}</h3>
            </div>
            <div className="text-sm text-gray-600 leading-relaxed whitespace-pre-line pl-6">
              {section.content}
            </div>
          </div>
        )
      })}

      <button
        onClick={onRegenerate}
        disabled={isLoading}
        className="inline-flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg border border-teal-600 text-teal-700 hover:bg-teal-50 disabled:opacity-50 transition"
      >
        {isLoading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <RefreshCw className="w-4 h-4" />
        )}
        {isLoading ? "Regenerating..." : "Regenerate Script"}
      </button>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/script-view.tsx
git commit -m "feat(web): ScriptView component — playbook renderer with 5 sections"
```

---

## Task 9: Notes panel component

**Files:**
- Create: `web/components/notes-panel.tsx`

- [ ] **Step 1: Create `web/components/notes-panel.tsx`**

```tsx
"use client"

import { useState } from "react"
import { Save, Loader2 } from "lucide-react"

interface NotesPanelProps {
  notes: string
  onSave: (notes: string) => Promise<void>
}

export default function NotesPanel({ notes: initialNotes, onSave }: NotesPanelProps) {
  const [notes, setNotes] = useState(initialNotes)
  const [isSaving, setIsSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  async function handleSave() {
    setIsSaving(true)
    setSaved(false)
    try {
      await onSave(notes)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <h3 className="font-serif font-semibold text-gray-900">Call Notes</h3>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        onBlur={handleSave}
        placeholder="Add notes from your call..."
        className="w-full h-48 text-sm p-3 rounded-lg border border-gray-200 bg-white/80
                   placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-teal-500/40
                   resize-none"
      />
      <button
        onClick={handleSave}
        disabled={isSaving}
        className="inline-flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
      >
        {isSaving ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Save className="w-4 h-4" />
        )}
        {saved ? "Saved!" : isSaving ? "Saving..." : "Save Notes"}
      </button>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/notes-panel.tsx
git commit -m "feat(web): NotesPanel component — textarea with auto-save on blur"
```

---

## Task 10: Practice info component (left column)

**Files:**
- Create: `web/components/practice-info.tsx`

- [ ] **Step 1: Create `web/components/practice-info.tsx`**

```tsx
import { Phone, Globe, Star } from "lucide-react"
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

export default function PracticeInfo({ practice }: { practice: Practice }) {
  const painPoints = parseJsonArray(practice.pain_points ?? null)
  const salesAngles = parseJsonArray(practice.sales_angles ?? null)

  return (
    <div className="space-y-4">
      <div>
        <h2 className="font-serif text-xl font-bold text-gray-900">{practice.name}</h2>
        <p className="text-sm text-gray-500 mt-1">{practice.address}</p>
      </div>

      <div className="flex items-center gap-3">
        <StarRating rating={practice.rating} />
        {practice.review_count > 0 && (
          <span className="text-xs text-gray-400">({practice.review_count})</span>
        )}
      </div>

      {practice.category && (
        <span className="inline-block text-xs px-2 py-0.5 rounded-full bg-teal-50 text-teal-700 font-medium capitalize">
          {practice.category.replace("_", " ")}
        </span>
      )}

      <div className="flex gap-2">
        {practice.phone && (
          <a
            href={`tel:${practice.phone}`}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 transition"
          >
            <Phone className="w-3 h-3" /> {practice.phone}
          </a>
        )}
        {practice.website && (
          <a
            href={practice.website}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-gray-100 text-gray-700 hover:bg-gray-200 transition"
          >
            <Globe className="w-3 h-3" /> Website
          </a>
        )}
      </div>

      {/* Analysis section */}
      {practice.lead_score != null && (
        <div className="pt-3 border-t border-gray-200/50 space-y-3">
          {practice.summary && (
            <p className="text-xs text-gray-600 leading-relaxed">{practice.summary}</p>
          )}

          {painPoints.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-700 mb-1">Pain Points</h4>
              <ul className="space-y-0.5">
                {painPoints.map((p, i) => (
                  <li key={i} className="text-xs text-gray-500 flex gap-1.5">
                    <span className="text-rose-400 shrink-0">&bull;</span>
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {salesAngles.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-700 mb-1">Sales Angles</h4>
              <ul className="space-y-0.5">
                {salesAngles.map((a, i) => (
                  <li key={i} className="text-xs text-gray-500 flex gap-1.5">
                    <span className="text-teal-500 shrink-0">&rarr;</span>
                    {a}
                  </li>
                ))}
              </ul>
            </div>
          )}

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
git add web/components/practice-info.tsx
git commit -m "feat(web): PracticeInfo component — left column for Call Prep page"
```

---

## Task 11: Call Prep page (`/practice/[place_id]`)

**Files:**
- Create: `web/app/practice/[place_id]/page.tsx`

- [ ] **Step 1: Create the directory**

Run:
```bash
mkdir -p "c:/Users/Moiz Ahmed/hv-sales-intel/web/app/practice/[place_id]"
```

- [ ] **Step 2: Create `web/app/practice/[place_id]/page.tsx`**

```tsx
"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import { ArrowLeft } from "lucide-react"
import type { Practice, ScriptSection } from "@/lib/types"
import { getScript, regenerateScript, updatePractice } from "@/lib/api"
import { mockPractices } from "@/lib/mock-data"
import PracticeInfo from "@/components/practice-info"
import ScriptView from "@/components/script-view"
import NotesPanel from "@/components/notes-panel"
import StatusBadge, { ALL_STATUSES } from "@/components/status-badge"

export default function CallPrepPage() {
  const params = useParams()
  const router = useRouter()
  const placeId = params.place_id as string

  const [practice, setPractice] = useState<Practice | null>(null)
  const [sections, setSections] = useState<ScriptSection[]>([])
  const [isLoadingScript, setIsLoadingScript] = useState(true)

  // Load practice data
  useEffect(() => {
    async function load() {
      try {
        const API_URL = process.env.NEXT_PUBLIC_API_URL || ""
        if (API_URL) {
          const res = await fetch(`${API_URL}/api/practices/${placeId}`)
          if (res.ok) {
            setPractice(await res.json())
          }
        }
      } catch {
        // Fallback to mock
      }

      if (!practice) {
        const mock = mockPractices.find((p) => p.place_id === placeId) ?? mockPractices[0]
        setPractice(mock)
      }
    }
    load()
  }, [placeId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load script
  useEffect(() => {
    async function loadScript() {
      setIsLoadingScript(true)
      try {
        const script = await getScript(placeId)
        setSections(script.sections)
      } finally {
        setIsLoadingScript(false)
      }
    }
    loadScript()
  }, [placeId])

  const handleRegenerate = useCallback(async () => {
    setIsLoadingScript(true)
    try {
      const script = await regenerateScript(placeId)
      setSections(script.sections)
    } finally {
      setIsLoadingScript(false)
    }
  }, [placeId])

  const handleStatusChange = useCallback(async (newStatus: string) => {
    const updated = await updatePractice(placeId, { status: newStatus })
    setPractice((prev) => (prev ? { ...prev, ...updated } : prev))
  }, [placeId])

  const handleSaveNotes = useCallback(async (notes: string) => {
    const updated = await updatePractice(placeId, { notes })
    setPractice((prev) => (prev ? { ...prev, ...updated } : prev))
  }, [placeId])

  if (!practice) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-cream">
      {/* Header */}
      <header className="sticky top-0 z-20 h-14 flex items-center justify-between px-6 bg-white/70 backdrop-blur-md border-b border-gray-200/50">
        <div className="flex items-center gap-4">
          <button
            onClick={() => router.push("/")}
            className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 transition"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Map
          </button>
          <span className="font-serif text-lg font-bold text-teal-700 tracking-tight">
            Health&amp;Virtuals
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-500">Status:</span>
          <select
            value={practice.status}
            onChange={(e) => handleStatusChange(e.target.value)}
            className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5
                       focus:outline-none focus:ring-2 focus:ring-teal-500/40"
          >
            {ALL_STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <StatusBadge status={practice.status} />
        </div>
      </header>

      {/* Three-column layout */}
      <div className="flex h-[calc(100vh-3.5rem)]">
        {/* Left: Practice Info */}
        <aside className="w-[280px] shrink-0 overflow-y-auto p-5 border-r border-gray-200/50">
          <PracticeInfo practice={practice} />
        </aside>

        {/* Center: Call Playbook */}
        <main className="flex-1 overflow-y-auto p-6">
          <h2 className="font-serif text-xl font-bold text-gray-900 mb-6">Call Playbook</h2>
          <ScriptView
            sections={sections}
            isLoading={isLoadingScript}
            onRegenerate={handleRegenerate}
          />
        </main>

        {/* Right: Notes & Actions */}
        <aside className="w-[320px] shrink-0 overflow-y-auto p-5 border-l border-gray-200/50">
          <NotesPanel
            notes={practice.notes ?? ""}
            onSave={handleSaveNotes}
          />
        </aside>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/app/practice/
git commit -m "feat(web): Call Prep page — three-column layout with script, notes, practice info"
```

---

## Task 12: Update practice card with Call Prep button + status badge + name link

**Files:**
- Modify: `web/components/practice-card.tsx`

- [ ] **Step 1: Add imports and update props**

Add to the imports at the top:

```tsx
import Link from "next/link"
import { Phone, Globe, Star, Brain, Loader2, FileText } from "lucide-react"
import StatusBadge from "./status-badge"
```

(Add `Link`, `FileText`, and `StatusBadge` to existing imports. Keep all existing imports.)

- [ ] **Step 2: Make the name a link and add status badge + Call Prep button**

Replace the header row section (the `<div className="flex items-start justify-between gap-2">` block) with:

```tsx
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <Link
          href={`/practice/${practice.place_id}`}
          onClick={(e) => e.stopPropagation()}
          className="font-serif font-semibold text-gray-900 text-base leading-tight hover:text-teal-700 transition"
        >
          {practice.name}
        </Link>
        <div className="flex items-center gap-1.5 shrink-0">
          <StatusBadge status={practice.status} />
          {isScored && <ScoreBadge score={practice.lead_score!} />}
        </div>
      </div>
```

Add the "Call Prep" button in the action buttons div, after the Analyze button:

```tsx
        <Link
          href={`/practice/${practice.place_id}`}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 transition"
        >
          <FileText className="w-3 h-3" /> Call Prep
        </Link>
```

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/practice-card.tsx
git commit -m "feat(web): practice card — Call Prep button, status badge, name as link"
```

---

## Task 13: Add status filter to filter bar

**Files:**
- Modify: `web/components/filter-bar.tsx`

- [ ] **Step 1: Update FilterBarProps and add status dropdown**

Replace the entire `web/components/filter-bar.tsx`:

```tsx
"use client"

import { ALL_STATUSES } from "./status-badge"

interface FilterBarProps {
  category: string
  onCategoryChange: (cat: string) => void
  minRating: number
  onMinRatingChange: (r: number) => void
  status: string
  onStatusChange: (status: string) => void
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

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "ACTIVE", label: "Active (excl. Closed Lost)" },
  ...ALL_STATUSES.map((s) => ({ value: s, label: s })),
]

export default function FilterBar({
  category,
  onCategoryChange,
  minRating,
  onMinRatingChange,
  status,
  onStatusChange,
}: FilterBarProps) {
  return (
    <div className="flex items-center gap-3 px-5 py-2 border-b border-gray-200/50 flex-wrap">
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
      <select
        value={status}
        onChange={(e) => onStatusChange(e.target.value)}
        className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5
                   focus:outline-none focus:ring-2 focus:ring-teal-500/40"
      >
        {STATUS_OPTIONS.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
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

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/components/filter-bar.tsx
git commit -m "feat(web): add status filter to filter bar"
```

---

## Task 14: Update main page with status filter

**Files:**
- Modify: `web/app/page.tsx`

- [ ] **Step 1: Add status state and filter logic**

Add state variable after `minRating`:

```tsx
  const [statusFilter, setStatusFilter] = useState("ACTIVE")
```

Update the `filtered` useMemo to include status filtering:

```tsx
  const filtered = useMemo(() => {
    const list = practices.filter((p) => {
      if (category && p.category !== category) return false
      if (minRating && (p.rating ?? 0) < minRating) return false
      if (statusFilter === "ACTIVE" && p.status === "CLOSED LOST") return false
      if (statusFilter && statusFilter !== "ACTIVE" && p.status !== statusFilter) return false
      return true
    })
    return list.sort((a, b) => {
      const aScore = a.lead_score ?? -1
      const bScore = b.lead_score ?? -1
      return bScore - aScore
    })
  }, [practices, category, minRating, statusFilter])
```

Update the `FilterBar` component to pass status props:

```tsx
          <FilterBar
            category={category}
            onCategoryChange={setCategory}
            minRating={minRating}
            onMinRatingChange={setMinRating}
            status={statusFilter}
            onStatusChange={setStatusFilter}
          />
```

- [ ] **Step 2: Commit**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git add web/app/page.tsx
git commit -m "feat(web): status filter on main page (default: Active, excludes Closed Lost)"
```

---

## Task 15: Final verification and push

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
Expected: succeeds. Output should list `/` and `/practice/[place_id]` routes.

- [ ] **Step 4: Backend smoke test**

Run:
```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
source .venv/Scripts/activate
python -c "
import asyncio, json
from src.scriptgen import generate_script
result = asyncio.run(generate_script('Test Practice', 'dental', 'Summary', '[]', '[]'))
print(f'Script sections: {len(result[\"sections\"])}')
print('Backend OK')
"
```

Expected: 5 sections, Backend OK.

- [ ] **Step 5: Visual check**

Start backend and frontend, then verify in browser:
1. Practice cards show status badge (gray "NEW" by default) and "Call Prep" button.
2. Clicking practice name or "Call Prep" navigates to `/practice/[place_id]`.
3. Call Prep page shows three columns: practice info (left), playbook (center), notes (right).
4. Playbook has 5 sections with icons.
5. "Regenerate Script" button works.
6. Notes textarea saves on blur and on "Save Notes" click.
7. Status dropdown in header changes practice status.
8. Status filter in sidebar works (default "Active" hides CLOSED LOST).
9. "← Back to Map" returns to main page.

- [ ] **Step 6: Final commit (if fixes needed)**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git status
# If dirty:
git add -A web/ src/ api/
git commit -m "chore: post-build fixes"
```

- [ ] **Step 7: Push**

```bash
cd "c:/Users/Moiz Ahmed/hv-sales-intel"
git push
```

---

## Self-review notes

- **Spec coverage:** Call script generation → Tasks 2, 5. Caching + auto-regenerate → Tasks 3, 5. CRM statuses → Tasks 5, 7. Auto-transitions (RESEARCHED, SCRIPT READY) → Tasks 3, 5. Status badge → Tasks 7, 12. Status filter → Tasks 13, 14. Call Prep page three-column layout → Task 11. Practice info left column → Task 10. Script view center column → Task 8. Notes panel right column → Task 9. Card navigation (name link + Call Prep button) → Task 12. PATCH endpoint → Task 5. Mock fallback → Tasks 2, 6.
- **Placeholders:** None. All code blocks complete. Mock scripts cover dental and chiropractic with `{name}` replacement. Frontend mock in api.ts covers all 5 sections.
- **Type consistency:** `ScriptSection` type (Task 6) has `title`, `icon`, `content` — matches `scriptgen.py` output (Task 2), `script-view.tsx` props (Task 8), and API response shape (Task 5). `ALL_STATUSES` array exported from `status-badge.tsx` (Task 7) used in `filter-bar.tsx` (Task 13) and Call Prep page status dropdown (Task 11). `updatePractice` (Task 6) accepts `{ status?, notes? }` — matches PATCH endpoint (Task 5). `Practice.notes` field already exists in the type as `string` — the notes panel reads it correctly.
- **File sizes:** All new components under 100 lines. `scriptgen.py` is ~170 lines (includes mock data for 2 categories). Call Prep page is ~130 lines.
