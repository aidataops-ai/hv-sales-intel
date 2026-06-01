# Leads Workspace + Personalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist sidebar state across navigation, add a local search bar + multi-tag/enriched/owner filters, attach an admin-driven assignment workflow, and personalize the analyzer + call-script outputs with website-extracted doctor name/phone.

**Architecture:** New `tags text[]` column accumulates milestone tags via a single `add_tags` storage helper called from each milestone endpoint. New `assigned_to` columns + admin-only PATCH path drive ownership. `crawl_website` returns a structured dict `{text, doctor_name, doctor_phone}` extracted by heuristics with optional GPT fallback; results live in two new columns. `generate_script` receives richer structured context (doctor, owner, city, review excerpts) and the system prompt is rewritten to require their use. Frontend uses a `useUrlState` hook + sessionStorage snapshot so that returning to `/` from a practice page restores the exact prior state synchronously.

**Tech Stack:** FastAPI, Supabase (PostgreSQL with `text[]` + GIN index), httpx + BeautifulSoup, OpenAI (gpt-4o-mini for fallback extraction), Next.js 14 App Router, React, Tailwind.

**Spec:** [docs/specs/2026-04-27-leads-workspace-personalization-design.md](../../specs/2026-04-27-leads-workspace-personalization-design.md)

---

## File Structure

**Backend — create:**
- `tests/test_storage_tags.py` — `add_tags` helper coverage
- `tests/test_crawler_doctor.py` — doctor name + phone extraction
- `tests/test_scriptgen_personalized.py` — prompt construction with new context
- `tests/test_api_practices_assignment.py` — admin-only `assigned_to` PATCH
- `tests/test_api_practices_tags.py` — auto-tagging on analyze/script/call/email/status-change

**Backend — modify:**
- `supabase/schema.sql` — add `tags`, `assigned_to`, `assigned_at`, `assigned_by`, `website_doctor_name`, `website_doctor_phone` + indexes + backfill SQL
- `src/storage.py` — add `add_tags`, extend `update_practice_analysis` and `update_practice_fields` with tag side-effects, extend `_with_attribution` callers in PATCH path with assignment
- `src/crawler.py` — `crawl_website` returns dict; new `_extract_doctor_name`, `_extract_doctor_phone` helpers
- `src/analyzer.py` — call new `crawl_website` shape, pass doctor fields through return dict
- `src/scriptgen.py` — extended signature + new system prompt + new mock fallback
- `api/index.py`:
  - `analyze` endpoint: pass through new fields; tag `RESEARCHED`
  - `get_script` / `regenerate_script_endpoint`: build context dict; tag `SCRIPT_READY`
  - `enrich` endpoint: tag `ENRICHED` on success
  - `call/log` endpoint: tag `CONTACTED`
  - `email/send` endpoint: tag `CONTACTED`
  - `email/poll` endpoint: tag `REPLIED` on inbound match
  - `update_practice` (PATCH): accept `assigned_to`, gate to admin; tag on status change
  - `list_practices` returns the new fields (already does via `select *`)

**Frontend — create:**
- `web/lib/use-url-state.ts` — URL query param hook
- `web/lib/use-session-snapshot.ts` — sessionStorage snapshot hook
- `web/components/tags-filter.tsx` — multi-select chip dropdown
- `web/components/owner-filter.tsx` — user dropdown
- `web/components/assign-dropdown.tsx` — admin-only assignment select on practice page
- `web/lib/tags.ts` — tag constant set + label helpers
- `tests/web/state-persistence.test.tsx`
- `tests/web/filter-logic.test.tsx`

**Frontend — modify:**
- `web/lib/types.ts` — extend `Practice` with `tags`, `assigned_to`, `assigned_at`, `assigned_by`, `website_doctor_name`, `website_doctor_phone`
- `web/lib/api.ts` — add `listUsers()` (for owner filter dropdown), update `updatePractice` to accept `assigned_to`
- `web/components/filter-bar.tsx` — rebuild with search input + new filters; remove single-status dropdown
- `web/components/practice-card.tsx` — surface doctor name + direct line
- `web/components/practice-info.tsx` — surface doctor name + direct line
- `web/app/page.tsx` — switch to URL-driven state + sessionStorage; new filter logic
- `web/app/practice/[place_id]/page.tsx` — render `AssignDropdown` for admins in header

---

### Task 1: Schema migration — new columns + indexes

**Files:**
- Modify: `supabase/schema.sql`

- [ ] **Step 1: Add the schema changes**

Append to `supabase/schema.sql` (at the end):

```sql
-- ======================= Leads workspace + personalization =======================

-- Multi-tag visibility (orthogonal to status)
alter table practices add column if not exists tags text[] not null default '{}';
create index if not exists idx_practices_tags on practices using gin (tags);

-- Assignment workflow
alter table practices add column if not exists assigned_to uuid references profiles(id);
alter table practices add column if not exists assigned_at timestamptz;
alter table practices add column if not exists assigned_by uuid references profiles(id);
create index if not exists idx_practices_assigned_to on practices (assigned_to);

-- Website-extracted doctor info (separate from Google Places `phone`)
alter table practices add column if not exists website_doctor_name text;
alter table practices add column if not exists website_doctor_phone text;

-- Backfill tags from existing state (idempotent — only writes empty tags)
update practices set tags = (
  select array_agg(distinct t) from unnest(array[
    case when lead_score is not null then 'RESEARCHED' end,
    case when call_script is not null then 'SCRIPT_READY' end,
    case when enrichment_status = 'enriched' then 'ENRICHED' end,
    case when call_count > 0 then 'CONTACTED' end,
    case when status = 'MEETING SET' then 'MEETING_SET' end,
    case when status = 'CLOSED WON' then 'CLOSED_WON' end,
    case when status = 'CLOSED LOST' then 'CLOSED_LOST' end
  ]) t where t is not null
) where tags = '{}'::text[];
```

- [ ] **Step 2: Run in Supabase SQL editor**

Paste the block into the SQL editor and run. Verify with:

```sql
select column_name, data_type from information_schema.columns
  where table_name = 'practices' and column_name in (
    'tags', 'assigned_to', 'assigned_at', 'assigned_by',
    'website_doctor_name', 'website_doctor_phone'
  );
-- Expect 6 rows.

select place_id, status, tags from practices where tags <> '{}'::text[] limit 5;
-- Expect tags populated according to status / lead_score / call_count.
```

- [ ] **Step 3: Commit**

```bash
git add supabase/schema.sql
git commit -m "feat(schema): tags, assignment, website-doctor columns + backfill"
```

---

### Task 2: `add_tags` storage helper

**Files:**
- Modify: `src/storage.py`
- Create: `tests/test_storage_tags.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_storage_tags.py`:

```python
from unittest.mock import MagicMock, patch

from src.storage import add_tags


def _fake_client_with_existing_tags(existing: list[str]):
    """Build a Supabase client mock returning a row with the given tags."""
    client = MagicMock()
    select_chain = MagicMock()
    select_chain.execute.return_value.data = {"tags": existing}
    client.table.return_value.select.return_value.eq.return_value.maybe_single.return_value = select_chain

    update_chain = MagicMock()
    update_chain.execute.return_value.data = [{"tags": existing + ["NEW"]}]
    client.table.return_value.update.return_value.eq.return_value = update_chain
    return client


def test_add_tags_appends_when_absent():
    fake = _fake_client_with_existing_tags(["RESEARCHED"])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", ["SCRIPT_READY"])
    update_args = fake.table.return_value.update.call_args.args[0]
    assert sorted(update_args["tags"]) == ["RESEARCHED", "SCRIPT_READY"]


def test_add_tags_dedupes_existing():
    fake = _fake_client_with_existing_tags(["RESEARCHED", "SCRIPT_READY"])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", ["RESEARCHED"])
    update_args = fake.table.return_value.update.call_args.args[0]
    assert sorted(update_args["tags"]) == ["RESEARCHED", "SCRIPT_READY"]


def test_add_tags_handles_empty_existing():
    fake = _fake_client_with_existing_tags([])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", ["RESEARCHED", "ENRICHED"])
    update_args = fake.table.return_value.update.call_args.args[0]
    assert sorted(update_args["tags"]) == ["ENRICHED", "RESEARCHED"]


def test_add_tags_noop_when_no_new_tags():
    fake = _fake_client_with_existing_tags(["RESEARCHED"])
    with patch("src.storage._get_client", return_value=fake):
        add_tags("place-1", [])
    fake.table.return_value.update.assert_not_called()


def test_add_tags_skips_when_client_unconfigured():
    with patch("src.storage._get_client", return_value=None):
        add_tags("place-1", ["RESEARCHED"])  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_storage_tags.py -v
```
Expected: ImportError on `add_tags`.

- [ ] **Step 3: Add `add_tags` to `src/storage.py`**

Append to `src/storage.py`:

```python
def add_tags(place_id: str, new_tags: list[str]) -> None:
    """Append tags to a practice's tags array, deduped. No-op if list empty.

    Reads current tags, computes union, writes back. Two roundtrips is fine
    for our write rate; postgres array_append + ON CONFLICT was rejected
    because Supabase's PostgREST client does not expose array_cat directly.
    """
    if not new_tags:
        return
    client = _get_client()
    if not client:
        return
    try:
        result = (
            client.table("practices").select("tags")
            .eq("place_id", place_id).maybe_single().execute()
        )
    except Exception:
        return
    existing = (result.data or {}).get("tags") or []
    merged = sorted(set(existing) | set(new_tags))
    if sorted(existing) == merged:
        return  # nothing new
    client.table("practices").update({"tags": merged}).eq("place_id", place_id).execute()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_storage_tags.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/storage.py tests/test_storage_tags.py
git commit -m "feat(storage): add_tags helper for dedup-append tag updates"
```

---

### Task 3: Auto-tag on analyze (RESEARCHED)

**Files:**
- Modify: `api/index.py` (the `analyze` handler)
- Create: `tests/test_api_practices_tags.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_api_practices_tags.py`:

```python
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import get_current_user


def _override_user(profile: dict):
    app.dependency_overrides[get_current_user] = lambda: profile


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_analyze_appends_researched_tag(sample_admin_profile):
    _override_user(sample_admin_profile)

    existing = {"place_id": "p1", "name": "X", "status": "NEW", "tags": []}
    analysis = {
        "summary": "s", "pain_points": "[]", "sales_angles": "[]",
        "lead_score": 50, "urgency_score": 50, "hiring_signal_score": 50,
        "call_script": None, "email_draft": None, "email_draft_updated_at": None,
        "website_doctor_name": None, "website_doctor_phone": None,
    }

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.analyze_practice", new=MagicMock(return_value=analysis)) as analyze_mock, \
         patch("api.index.update_practice_analysis", return_value={**existing, **analysis}), \
         patch("api.index.add_tags") as add_tags_mock:
        # analyze_practice is async — use an awaitable stub
        async def _aresult(*args, **kwargs):
            return analysis
        analyze_mock.side_effect = None
        with patch("api.index.analyze_practice", new=_aresult):
            client = TestClient(app)
            resp = client.post("/api/practices/p1/analyze", json={"force": True})

    assert resp.status_code == 200
    add_tags_mock.assert_any_call("p1", ["RESEARCHED"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_api_practices_tags.py::test_analyze_appends_researched_tag -v
```
Expected: AttributeError on `api.index.add_tags` import (not yet wired).

- [ ] **Step 3: Wire add_tags into the analyze endpoint**

In `api/index.py`, add `add_tags` to the imports from `src.storage`:

```python
from src.storage import (
    # ... existing imports ...
    add_tags,
)
```

Then in the `analyze` handler, after `update_practice_analysis(...)` returns and before the response is returned:

```python
    updated = update_practice_analysis(place_id, analysis, touched_by=user["id"])
    add_tags(place_id, ["RESEARCHED"])  # NEW
    if updated:
        return updated
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_api_practices_tags.py::test_analyze_appends_researched_tag -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_practices_tags.py
git commit -m "feat(api): tag RESEARCHED on analyze success"
```

---

### Task 4: Auto-tag on script generation (SCRIPT_READY)

**Files:**
- Modify: `api/index.py` (`get_script` handler)
- Modify: `tests/test_api_practices_tags.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api_practices_tags.py`:

```python
def test_get_script_appends_script_ready_tag(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {
        "place_id": "p1", "name": "X", "status": "RESEARCHED", "tags": ["RESEARCHED"],
        "category": "dental", "summary": "s", "pain_points": "[]", "sales_angles": "[]",
        "city": "Boise", "state": "ID", "rating": 4.5, "review_count": 30,
        "website_doctor_name": None, "owner_name": None, "owner_title": None,
        "call_script": None,
    }
    script = {"sections": [{"title": "Opening", "icon": "phone", "content": "..."}] * 5}

    async def _agen(*args, **kwargs):
        return script

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.fetch_reviews", new=MagicMock()), \
         patch("api.index.generate_script", new=_agen), \
         patch("api.index.update_practice_fields"), \
         patch("api.index.add_tags") as add_tags_mock:
        client = TestClient(app)
        resp = client.get("/api/practices/p1/script")

    assert resp.status_code == 200
    add_tags_mock.assert_any_call("p1", ["SCRIPT_READY"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_api_practices_tags.py::test_get_script_appends_script_ready_tag -v
```
Expected: FAIL — `add_tags` not called for SCRIPT_READY.

- [ ] **Step 3: Wire add_tags + handle the case where the script is regenerated too**

In `api/index.py`, in `get_script`, after `update_practice_fields(place_id, {"call_script": ...}, ...)`:

```python
    update_practice_fields(place_id, {"call_script": json.dumps(script)}, touched_by=user["id"])
    add_tags(place_id, ["SCRIPT_READY"])  # NEW

    current_status = practice.get("status", "NEW")
    if _should_auto_advance(current_status, "SCRIPT READY"):
        update_practice_fields(place_id, {"status": "SCRIPT READY"}, touched_by=user["id"])
```

Also in `regenerate_script_endpoint` (POST `/api/practices/{place_id}/script`), after `update_practice_fields` for the new script:

```python
    add_tags(place_id, ["SCRIPT_READY"])
```

- [ ] **Step 4: Run tests to verify it passes**

```bash
pytest tests/test_api_practices_tags.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_practices_tags.py
git commit -m "feat(api): tag SCRIPT_READY on script generation"
```

---

### Task 5: Auto-tag on Clay enrichment (ENRICHED)

**Files:**
- Modify: `api/index.py` (`enrich` handler)
- Modify: `tests/test_api_practices_tags.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api_practices_tags.py`:

```python
def test_enrich_appends_enriched_tag_on_success(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {"place_id": "p1", "name": "X", "tags": []}
    enriched = {**existing, "owner_name": "Dr. Y", "enrichment_status": "enriched"}

    async def _arun_enrich(*args, **kwargs):
        return enriched

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.run_enrichment", new=_arun_enrich), \
         patch("api.index.add_tags") as add_tags_mock:
        client = TestClient(app)
        resp = client.post("/api/practices/p1/enrich")

    assert resp.status_code == 200
    add_tags_mock.assert_any_call("p1", ["ENRICHED"])


def test_enrich_does_not_tag_on_failure(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {"place_id": "p1", "name": "X", "tags": []}
    failed = {**existing, "enrichment_status": "failed"}

    async def _arun_enrich(*args, **kwargs):
        return failed

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.run_enrichment", new=_arun_enrich), \
         patch("api.index.add_tags") as add_tags_mock:
        client = TestClient(app)
        resp = client.post("/api/practices/p1/enrich")

    assert resp.status_code == 200
    add_tags_mock.assert_not_called()
```

- [ ] **Step 2: Run tests — expect both to fail**

```bash
pytest tests/test_api_practices_tags.py -v -k enrich
```
Expected: 2 failures.

- [ ] **Step 3: Wire add_tags into the enrich handler**

In `api/index.py`, find the `enrich` endpoint. After `run_enrichment` returns, before the response:

```python
    result = await run_enrichment(...)
    if result and result.get("enrichment_status") == "enriched":
        add_tags(place_id, ["ENRICHED"])
    return result
```

(Adjust to match the actual variable names in your `enrich` handler — the principle is: only tag when `enrichment_status == "enriched"`.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_practices_tags.py -v -k enrich
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_practices_tags.py
git commit -m "feat(api): tag ENRICHED on Clay success only"
```

---

### Task 6: Auto-tag on call log + email send (CONTACTED)

**Files:**
- Modify: `api/index.py` (`call/log` and `email/send` handlers)
- Modify: `tests/test_api_practices_tags.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api_practices_tags.py`:

```python
def test_call_log_appends_contacted_tag(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {
        "place_id": "p1", "id": 1, "name": "X", "tags": [],
        "call_count": 0, "call_notes": None, "status": "RESEARCHED",
    }

    async def _alog(*args, **kwargs):
        return {**existing, "call_count": 1, "call_notes": "logged"}

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.handle_call_log", new=_alog), \
         patch("api.index.add_tags") as add_tags_mock:
        client = TestClient(app)
        resp = client.post(
            "/api/practices/p1/call/log",
            json={"notes": "rang and chatted"},
        )

    assert resp.status_code == 200
    add_tags_mock.assert_any_call("p1", ["CONTACTED"])


def test_email_send_appends_contacted_tag(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {"place_id": "p1", "id": 1, "name": "X", "tags": [], "email": "x@y.com"}

    async def _asend(*args, **kwargs):
        return {"ok": True, "message_id": "m1"}

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.send_email", new=_asend), \
         patch("api.index.insert_email_message"), \
         patch("api.index.add_tags") as add_tags_mock:
        client = TestClient(app)
        resp = client.post(
            "/api/practices/p1/email/send",
            json={"subject": "Hi", "body": "Hello"},
        )

    assert resp.status_code == 200
    add_tags_mock.assert_any_call("p1", ["CONTACTED"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_practices_tags.py -v -k "contacted"
```
Expected: 2 failures.

- [ ] **Step 3: Wire add_tags into call/log + email/send**

In `api/index.py`'s call/log endpoint, after the call is successfully logged:

```python
    add_tags(place_id, ["CONTACTED"])
```

In the email/send endpoint, after the message row is inserted with no `error`:

```python
    add_tags(place_id, ["CONTACTED"])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_practices_tags.py -v -k "contacted"
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_practices_tags.py
git commit -m "feat(api): tag CONTACTED on first call log or email send"
```

---

### Task 7: Auto-tag on inbound reply + status changes

**Files:**
- Modify: `api/index.py` (email/poll handler + PATCH `/api/practices/{id}` handler)
- Modify: `tests/test_api_practices_tags.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api_practices_tags.py`:

```python
def test_email_poll_appends_replied_tag_on_inbound(sample_admin_profile):
    _override_user(sample_admin_profile)

    async def _apoll(*args, **kwargs):
        return {"matched": 1, "saved": [{"direction": "in"}]}

    with patch("api.index.poll_replies", new=_apoll), \
         patch("api.index.get_practice", return_value={"place_id": "p1", "id": 1, "tags": []}), \
         patch("api.index.add_tags") as add_tags_mock:
        client = TestClient(app)
        resp = client.post("/api/practices/p1/email/poll")

    assert resp.status_code == 200
    add_tags_mock.assert_any_call("p1", ["REPLIED"])


@pytest.mark.parametrize("status,expected_tag", [
    ("MEETING SET", "MEETING_SET"),
    ("CLOSED WON", "CLOSED_WON"),
    ("CLOSED LOST", "CLOSED_LOST"),
])
def test_patch_practice_tags_on_status_change(sample_admin_profile, status, expected_tag):
    _override_user(sample_admin_profile)
    existing = {"place_id": "p1", "id": 1, "name": "X", "tags": [], "status": "CONTACTED"}

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.update_practice_fields", return_value={**existing, "status": status}), \
         patch("api.index.add_tags") as add_tags_mock:
        client = TestClient(app)
        resp = client.patch("/api/practices/p1", json={"status": status})

    assert resp.status_code == 200
    add_tags_mock.assert_any_call("p1", [expected_tag])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_practices_tags.py -v -k "replied or status_change"
```
Expected: 4 failures.

- [ ] **Step 3: Wire add_tags into email/poll + PATCH practice**

In `api/index.py`'s email/poll handler, after polling and saving inbound messages:

```python
    if any(saved.get("direction") == "in" for saved in (poll_result.get("saved") or [])):
        add_tags(place_id, ["REPLIED"])
```

In the PATCH `/api/practices/{place_id}` handler, after `update_practice_fields(...)`:

```python
    STATUS_TAG_MAP = {
        "MEETING SET": "MEETING_SET",
        "CLOSED WON": "CLOSED_WON",
        "CLOSED LOST": "CLOSED_LOST",
    }
    if body.status and body.status in STATUS_TAG_MAP:
        add_tags(place_id, [STATUS_TAG_MAP[body.status]])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_practices_tags.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_practices_tags.py
git commit -m "feat(api): tag REPLIED on inbound + tag pipeline closing statuses"
```

---

### Task 8: Doctor extraction in `crawl_website`

**Files:**
- Modify: `src/crawler.py`
- Modify: `src/analyzer.py` (use new shape)
- Create: `tests/test_crawler_doctor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_crawler_doctor.py`:

```python
import pytest

from src.crawler import _extract_doctor_name, _extract_doctor_phone


HOMEPAGE_WITH_DOCTOR = """
<html><body>
<h1>Smile Dental</h1>
<p>Dr. Sarah Smith, DDS leads our team. Call her direct line at (555) 123-4567.</p>
<footer>Front desk: (555) 999-0000</footer>
</body></html>
"""

ABOUT_PAGE = """
<html><body>
<h2>Meet Our Lead Doctor</h2>
<p>Dr. Sarah J. Smith has been practicing for 15 years.</p>
</body></html>
"""

NO_DOCTOR_PAGE = """
<html><body><h1>Smile Dental</h1><p>We treat everyone.</p></body></html>
"""


def test_extract_doctor_name_finds_dr_prefix():
    assert _extract_doctor_name(HOMEPAGE_WITH_DOCTOR) == "Dr. Sarah Smith"


def test_extract_doctor_name_finds_credential_suffix():
    text = "<h1>Sarah Smith, MD</h1>"
    assert _extract_doctor_name(text) == "Dr. Sarah Smith"


def test_extract_doctor_name_returns_none_when_absent():
    assert _extract_doctor_name(NO_DOCTOR_PAGE) is None


def test_extract_doctor_name_picks_most_frequent():
    text = """
    <p>Dr. Sarah Smith</p>
    <p>Dr. John Doe</p>
    <p>Dr. Sarah Smith</p>
    <p>Dr. Sarah Smith</p>
    """
    assert _extract_doctor_name(text) == "Dr. Sarah Smith"


def test_extract_doctor_phone_near_doctor_name():
    text = "Dr. Sarah Smith, DDS — direct (555) 123-4567"
    phone = _extract_doctor_phone(text, doctor_name="Dr. Sarah Smith", front_desk_phone=None)
    assert phone == "(555) 123-4567"


def test_extract_doctor_phone_skips_front_desk_match():
    text = "Dr. Sarah Smith, DDS — call (555) 999-0000"
    phone = _extract_doctor_phone(text, doctor_name="Dr. Sarah Smith", front_desk_phone="555-999-0000")
    assert phone is None


def test_extract_doctor_phone_returns_none_when_no_phone():
    text = "Dr. Sarah Smith, DDS leads our team."
    phone = _extract_doctor_phone(text, doctor_name="Dr. Sarah Smith", front_desk_phone=None)
    assert phone is None


def test_extract_doctor_phone_invalid_digits():
    text = "Dr. Sarah Smith — (555) 12-3"  # too short
    phone = _extract_doctor_phone(text, doctor_name="Dr. Sarah Smith", front_desk_phone=None)
    assert phone is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_crawler_doctor.py -v
```
Expected: ImportError on `_extract_doctor_name`.

- [ ] **Step 3: Implement helpers and update `crawl_website`**

Replace contents of `src/crawler.py`:

```python
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

PRIORITY_PATTERNS = re.compile(
    r"(career|job|hiring|team|about|staff|service|contact|provider|doctor|meet)",
    re.IGNORECASE,
)

DOCTOR_NAME_DR_PREFIX = re.compile(
    r"(?:Dr\.?|Doctor)\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)"
)
DOCTOR_NAME_CRED_SUFFIX = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s*,?\s*(MD|DDS|DO|DPM|DC|FNP|PA-C)\b"
)
PHONE_PATTERN = re.compile(
    r"(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})"
)

MAX_PAGES = 10
TIMEOUT = 10


async def crawl_website(url: str) -> dict:
    """Crawl a website and extract bulk text + best-guess doctor name and phone.

    Returns:
        {
          "text": <combined page text>,
          "doctor_name": <"Dr. Firstname Lastname"> or None,
          "doctor_phone": <"(555) 123-4567"> or None,
        }
    """
    if not url:
        return {"text": "", "doctor_name": None, "doctor_phone": None}

    visited: set[str] = set()
    texts: list[str] = []
    raw_html_chunks: list[str] = []
    base_domain = urlparse(url).netloc

    to_visit = [url]
    discovered: list[str] = []

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=TIMEOUT,
        headers={"User-Agent": "HVSalesIntel/1.0"},
    ) as client:
        while (to_visit or discovered) and len(visited) < MAX_PAGES:
            current = to_visit.pop(0) if to_visit else discovered.pop(0)
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
            raw_html_chunks.append(resp.text)
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            if text:
                texts.append(text[:5000])

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
                    if PRIORITY_PATTERNS.search(href):
                        to_visit.append(href)
                    else:
                        discovered.append(href)

    combined_text = "\n\n---\n\n".join(texts)
    combined_html = "\n".join(raw_html_chunks)
    doctor_name = _extract_doctor_name(combined_html)
    doctor_phone = _extract_doctor_phone(
        combined_html,
        doctor_name=doctor_name,
        front_desk_phone=None,  # caller wires the practice's Google phone in
    )
    return {
        "text": combined_text,
        "doctor_name": doctor_name,
        "doctor_phone": doctor_phone,
    }


def _extract_doctor_name(html_or_text: str) -> str | None:
    """Find the most frequent Dr.-prefix or credential-suffix name in the text."""
    if not html_or_text:
        return None
    counts: Counter[str] = Counter()
    for match in DOCTOR_NAME_DR_PREFIX.finditer(html_or_text):
        counts[f"Dr. {match.group(1)}"] += 1
    for match in DOCTOR_NAME_CRED_SUFFIX.finditer(html_or_text):
        counts[f"Dr. {match.group(1)}"] += 1
    if not counts:
        return None
    most_common, _ = counts.most_common(1)[0]
    return most_common


def _extract_doctor_phone(
    html_or_text: str,
    doctor_name: str | None,
    front_desk_phone: str | None,
) -> str | None:
    """Find a phone near the doctor name; skip if it equals the front desk phone."""
    if not html_or_text:
        return None
    front_desk_digits = re.sub(r"\D", "", front_desk_phone or "")

    def _digit_match(phone: str) -> bool:
        digits = re.sub(r"\D", "", phone)
        if len(digits) not in (10, 11):
            return False
        if front_desk_digits and digits.endswith(front_desk_digits[-10:]):
            return False
        return True

    if doctor_name:
        # Search for a phone in a 200-char window around each occurrence of the name.
        bare_name = doctor_name.replace("Dr. ", "")
        for needle in (doctor_name, bare_name):
            for match in re.finditer(re.escape(needle), html_or_text):
                start = max(0, match.start() - 200)
                end = min(len(html_or_text), match.end() + 200)
                window = html_or_text[start:end]
                phone_match = PHONE_PATTERN.search(window)
                if phone_match and _digit_match(phone_match.group(1)):
                    return phone_match.group(1).strip()

    # Fallback: scan for "Direct" / "Personal" / "Cell" labelled phones anywhere.
    label_pattern = re.compile(
        r"(?:direct|personal|cell|mobile|doctor's)[^.\n]{0,40}?"
        + PHONE_PATTERN.pattern,
        re.IGNORECASE,
    )
    label_match = label_pattern.search(html_or_text)
    if label_match and _digit_match(label_match.group(1)):
        return label_match.group(1).strip()
    return None


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_crawler_doctor.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Update `analyzer.py` to use new dict shape**

In `src/analyzer.py`, change the `crawl_website` call:

```python
    # Before:
    # website_text = await crawl_website(website or "")
    # After:
    crawl_result = await crawl_website(website or "")
    website_text = crawl_result["text"]
    website_doctor_name = crawl_result["doctor_name"]
    website_doctor_phone = crawl_result["doctor_phone"]
```

In the analyzer's return dict at the bottom, add the two fields:

```python
    return {
        "summary": result.get("summary", ""),
        "pain_points": json.dumps(result.get("pain_points", [])),
        "sales_angles": json.dumps(result.get("sales_angles", [])),
        "lead_score": _clamp(result.get("lead_score", 0)),
        "urgency_score": _clamp(result.get("urgency_score", 0)),
        "hiring_signal_score": _clamp(result.get("hiring_signal_score", 0)),
        "call_script": None,
        "email_draft": None,
        "email_draft_updated_at": None,
        "website_doctor_name": website_doctor_name,
        "website_doctor_phone": website_doctor_phone,
    }
```

Same in `_mock_analysis` — add `"website_doctor_name": None, "website_doctor_phone": None,` to the return.

- [ ] **Step 6: Run analyzer tests to verify nothing breaks**

```bash
pytest tests/ -v -k "analyz or crawl"
```
Expected: all green (or only previously-failing tests still failing).

- [ ] **Step 7: Commit**

```bash
git add src/crawler.py src/analyzer.py tests/test_crawler_doctor.py
git commit -m "feat(crawler): extract website doctor name + direct phone"
```

---

### Task 9: Storage writes new doctor columns; analyze endpoint passes them through

**Files:**
- Modify: `src/storage.py` (no behavior change — `update_practice_analysis` already takes a dict; just verify callers pass new keys)
- Verify: `api/index.py` analyze handler

- [ ] **Step 1: Write a sanity test**

Append to `tests/test_api_practices_tags.py`:

```python
def test_analyze_persists_website_doctor_fields(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {"place_id": "p1", "name": "X", "status": "NEW", "tags": []}
    analysis = {
        "summary": "s", "pain_points": "[]", "sales_angles": "[]",
        "lead_score": 50, "urgency_score": 50, "hiring_signal_score": 50,
        "call_script": None, "email_draft": None, "email_draft_updated_at": None,
        "website_doctor_name": "Dr. Sarah Smith",
        "website_doctor_phone": "(555) 123-4567",
    }

    async def _aresult(*args, **kwargs):
        return analysis

    captured: dict = {}
    def _fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.analyze_practice", new=_aresult), \
         patch("api.index.update_practice_analysis", side_effect=_fake_update), \
         patch("api.index.add_tags"):
        client = TestClient(app)
        resp = client.post("/api/practices/p1/analyze", json={"force": True})

    assert resp.status_code == 200
    assert captured["website_doctor_name"] == "Dr. Sarah Smith"
    assert captured["website_doctor_phone"] == "(555) 123-4567"
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_api_practices_tags.py::test_analyze_persists_website_doctor_fields -v
```
Expected: PASS already, since `update_practice_analysis(place_id, analysis)` writes whatever is in the dict — and Task 8 added the keys to the analyzer return.

If FAIL: confirm `analyze` handler passes the unmodified `analysis` dict into `update_practice_analysis` (it should already).

- [ ] **Step 3: Commit**

```bash
git add tests/test_api_practices_tags.py
git commit -m "test(api): assert analyze persists website doctor fields"
```

---

### Task 10: Personalized `generate_script` signature + prompt

**Files:**
- Modify: `src/scriptgen.py`
- Create: `tests/test_scriptgen_personalized.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_scriptgen_personalized.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scriptgen import generate_script


@pytest.mark.asyncio
async def test_generate_script_uses_doctor_name_in_prompt():
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='{"sections":[{"title":"Opening","icon":"phone","content":"x"}]}'))]
    captured_user_prompt: list[str] = []

    async def _create(**kwargs):
        captured_user_prompt.append(kwargs["messages"][1]["content"])
        return fake_response

    with patch("src.scriptgen.settings") as s:
        s.openai_api_key = "k"
        s.openai_model = "gpt-4o-mini"
        with patch("src.scriptgen.AsyncOpenAI") as cls:
            cls.return_value.chat.completions.create = AsyncMock(side_effect=_create)
            await generate_script(
                name="Smile Dental",
                category="dental",
                summary="busy practice",
                pain_points='["wait times"]',
                sales_angles='["front desk"]',
                city="Boise", state="ID", rating=4.5, review_count=30,
                website_doctor_name="Dr. Sarah Smith",
                owner_name=None, owner_title=None,
                review_excerpts=["Long wait times in lobby"],
            )

    assert "Dr. Sarah Smith" in captured_user_prompt[0]
    assert "Boise" in captured_user_prompt[0]
    assert "Long wait times in lobby" in captured_user_prompt[0]


@pytest.mark.asyncio
async def test_generate_script_falls_back_to_practice_name_when_no_doctor():
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='{"sections":[{"title":"Opening","icon":"phone","content":"x"}]}'))]
    captured_user_prompt: list[str] = []

    async def _create(**kwargs):
        captured_user_prompt.append(kwargs["messages"][1]["content"])
        return fake_response

    with patch("src.scriptgen.settings") as s:
        s.openai_api_key = "k"
        s.openai_model = "gpt-4o-mini"
        with patch("src.scriptgen.AsyncOpenAI") as cls:
            cls.return_value.chat.completions.create = AsyncMock(side_effect=_create)
            await generate_script(
                name="Smile Dental",
                category="dental",
                summary=None, pain_points=None, sales_angles=None,
                city=None, state=None, rating=None, review_count=None,
                website_doctor_name=None,
                owner_name=None, owner_title=None,
                review_excerpts=None,
            )

    assert "Smile Dental" in captured_user_prompt[0]
    # Should not crash even when most fields are None.


@pytest.mark.asyncio
async def test_generate_script_mock_uses_doctor_name():
    """Without OpenAI key, mock script substitutes doctor name into opening."""
    with patch("src.scriptgen.settings") as s:
        s.openai_api_key = None
        result = await generate_script(
            name="Smile Dental",
            category="dental",
            summary=None, pain_points=None, sales_angles=None,
            city="Boise", state="ID", rating=4.5, review_count=30,
            website_doctor_name="Dr. Sarah Smith",
            owner_name=None, owner_title=None,
            review_excerpts=None,
        )
    opening = result["sections"][0]["content"]
    assert "Dr. Sarah Smith" in opening
```

- [ ] **Step 2: Add `pytest-asyncio` if missing**

```bash
grep -E "^pytest-asyncio" requirements-dev.txt || echo "pytest-asyncio>=0.21" >> requirements-dev.txt
pip install -r requirements-dev.txt
```

In `tests/conftest.py`, ensure the asyncio mode is configured (skip if already there):

```python
# In pytest.ini or pyproject.toml — pick one:
# pytest.ini:
# [pytest]
# asyncio_mode = auto
```

If you don't want to add pytest-asyncio, mark each async test with `@pytest.mark.asyncio` and run with the existing trio/anyio plugin if installed; otherwise switch to a synchronous wrapper using `asyncio.run`.

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_scriptgen_personalized.py -v
```
Expected: TypeError on unexpected keyword argument (current signature doesn't accept the new fields).

- [ ] **Step 4: Update `src/scriptgen.py`**

Replace the file contents:

```python
import json

from openai import AsyncOpenAI

from src.settings import settings

SYSTEM_PROMPT = """You are a cold call script writer for Apex & Virtuals, a healthcare staffing and talent acquisition company.

Given information about a healthcare practice (name, category, location, lead doctor, owner, analysis summary, pain points, sales angles, review excerpts), generate a personalized cold call playbook tailored to THIS specific practice.

Return ONLY valid JSON with this exact structure:
{
  "sections": [
    {"title": "Opening", "icon": "phone", "content": "..."},
    {"title": "Discovery Questions", "icon": "search", "content": "..."},
    {"title": "Pitch", "icon": "target", "content": "..."},
    {"title": "Objection Handling", "icon": "shield", "content": "..."},
    {"title": "Closing", "icon": "check", "content": "..."}
  ]
}

Personalization requirements:
- Opening: If a lead doctor name is provided, ask for them by name ("Hi, may I speak with Dr. Smith?"). Otherwise greet the practice. Reference the city if provided.
- Discovery Questions: Reference 1-2 specific items from the provided pain_points by name (not generic). 3-4 numbered questions total.
- Pitch: If review_excerpts are provided, quote ONE excerpt verbatim with leading attribution ("One of your patient reviews mentioned, '...'") and tie it to a Apex & Virtuals staffing solution. Mention Apex & Virtuals by name.
- Objection Handling: Cover "We already have a recruiter", "We can't afford it", "We're not hiring right now", and one objection specific to this category.
- Closing: Reference the city when present ("we've placed staff at multiple [city]-area clinics"). Suggest a 15-minute meeting and a free staffing assessment.

Keep each section 3-6 sentences. Be conversational, not robotic. Use the rep's perspective ("I", "we at Apex & Virtuals")."""


async def generate_script(
    name: str,
    category: str | None,
    summary: str | None,
    pain_points: str | None,
    sales_angles: str | None,
    *,
    city: str | None = None,
    state: str | None = None,
    rating: float | None = None,
    review_count: int | None = None,
    website_doctor_name: str | None = None,
    owner_name: str | None = None,
    owner_title: str | None = None,
    review_excerpts: list[str] | None = None,
) -> dict:
    """Generate a cold call playbook personalized to the practice."""
    if not settings.openai_api_key:
        return _mock_script(
            name=name, category=category,
            website_doctor_name=website_doctor_name,
            city=city,
        )

    excerpts = review_excerpts or []
    user_prompt = f"""Generate a personalized cold call playbook for this practice:

Practice: {name}
Category: {category or 'Healthcare'}
Location: {(city + ', ' + state) if (city and state) else (city or state or 'Unknown')}
Rating: {rating or 'unknown'} ({review_count or 0} reviews)
Lead Doctor: {website_doctor_name or 'Unknown'}
Owner Contact: {owner_name or 'Unknown'} ({owner_title or 'no title'})

Analysis Summary: {summary or 'No analysis available'}
Pain Points: {pain_points or '[]'}
Sales Angles: {sales_angles or '[]'}

Verbatim Patient Review Excerpts:
{chr(10).join(f'- "{ex}"' for ex in excerpts) if excerpts else '(none available)'}
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

    return _mock_script(
        name=name, category=category,
        website_doctor_name=website_doctor_name, city=city,
    )


def _mock_script(
    name: str,
    category: str | None,
    website_doctor_name: str | None = None,
    city: str | None = None,
) -> dict:
    cat_label = (category or "healthcare").replace("_", " ")
    doctor_greeting = (
        f"Hi, may I speak with {website_doctor_name}?"
        if website_doctor_name
        else f"Hi, this is [Your Name] calling from Apex & Virtuals about {name}."
    )
    city_phrase = f" in the {city} area" if city else ""

    return {
        "sections": [
            {
                "title": "Opening",
                "icon": "phone",
                "content": f"{doctor_greeting} I'm reaching out because Apex & Virtuals helps {cat_label} practices{city_phrase} with staffing solutions. Do you have a quick moment?",
            },
            {
                "title": "Discovery Questions",
                "icon": "search",
                "content": "1. How are you currently handling front desk coverage when staff call out?\n2. Are you finding it challenging to recruit and retain qualified staff in this market?\n3. How much time does your team spend on admin tasks versus patient coordination?\n4. If you could add one more person to your team tomorrow, what role would make the biggest impact?",
            },
            {
                "title": "Pitch",
                "icon": "target",
                "content": f"At Apex & Virtuals, we provide pre-vetted front desk staff, medical assistants, and administrative support specifically for practices like {name}. We handle recruiting, screening, and onboarding so you can focus on patient care.",
            },
            {
                "title": "Objection Handling",
                "icon": "shield",
                "content": 'Objection: "We already have a recruiter."\nResponse: We complement existing recruiters with healthcare specialists.\n\nObjection: "We can\'t afford it right now."\nResponse: Many of our clients save money via temp-to-perm placements that prevent costly bad hires.\n\nObjection: "We\'re not hiring right now."\nResponse: Many practices work with us proactively so they have qualified candidates ready when a position opens.',
            },
            {
                "title": "Closing",
                "icon": "check",
                "content": f"I'd love to set up a quick 15-minute call to learn more about {name}{city_phrase} and share how we've helped similar practices. Would Tuesday or Wednesday work for a brief chat?",
            },
        ]
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_scriptgen_personalized.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/scriptgen.py tests/test_scriptgen_personalized.py
git commit -m "feat(scriptgen): personalize prompt with doctor, city, review excerpts"
```

---

### Task 11: Script endpoint builds context dict (review excerpts + doctor)

**Files:**
- Modify: `api/index.py` (`get_script` and `regenerate_script_endpoint`)
- Modify: `tests/test_api_practices_tags.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api_practices_tags.py`:

```python
def test_get_script_passes_personalization_context(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {
        "place_id": "p1", "name": "Smile Dental", "status": "RESEARCHED",
        "tags": ["RESEARCHED"], "category": "dental",
        "summary": "s", "pain_points": "[]", "sales_angles": "[]",
        "city": "Boise", "state": "ID", "rating": 4.5, "review_count": 30,
        "website_doctor_name": "Dr. Sarah Smith",
        "owner_name": None, "owner_title": None,
        "call_script": None,
    }
    captured: dict = {}

    async def _agen(**kwargs):
        captured.update(kwargs)
        return {"sections": [{"title": "Opening", "icon": "phone", "content": "..."}] * 5}

    async def _afetch(*args, **kwargs):
        return [
            {"text": "Long wait times in lobby", "rating": 2, "source": "Google", "url": None},
            {"text": "Friendly staff", "rating": 5, "source": "Google", "url": None},
        ]

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.fetch_reviews", new=_afetch), \
         patch("api.index.generate_script", new=_agen), \
         patch("api.index.update_practice_fields"), \
         patch("api.index.add_tags"):
        client = TestClient(app)
        resp = client.get("/api/practices/p1/script")

    assert resp.status_code == 200
    assert captured["website_doctor_name"] == "Dr. Sarah Smith"
    assert captured["city"] == "Boise"
    assert captured["rating"] == 4.5
    assert captured["review_excerpts"] is not None
    assert any("Long wait times" in ex for ex in captured["review_excerpts"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_api_practices_tags.py::test_get_script_passes_personalization_context -v
```
Expected: FAIL — current `get_script` doesn't pass these fields.

- [ ] **Step 3: Update `get_script` and `regenerate_script_endpoint`**

In `api/index.py`, find `get_script`. Add the import:

```python
from src.reviews import fetch_reviews
```

(if not already imported.)

Replace the body of `get_script` after `practice = get_practice(...)` and before `update_practice_fields`:

```python
    if practice.get("call_script"):
        return json.loads(practice["call_script"])

    # Build personalization context.
    reviews = await fetch_reviews(
        place_id,
        name=practice.get("name"),
        city=practice.get("city"),
        state=practice.get("state"),
        website=practice.get("website"),
    )
    review_excerpts = sorted(
        [r["text"] for r in (reviews or []) if r.get("text")],
        key=len,
    )[:3]

    script = await generate_script(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
        city=practice.get("city"),
        state=practice.get("state"),
        rating=practice.get("rating"),
        review_count=practice.get("review_count"),
        website_doctor_name=practice.get("website_doctor_name"),
        owner_name=practice.get("owner_name"),
        owner_title=practice.get("owner_title"),
        review_excerpts=review_excerpts,
    )
```

Apply the same context-building inside `regenerate_script_endpoint` so both paths personalize identically.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_practices_tags.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_practices_tags.py
git commit -m "feat(api): build personalization context for script gen"
```

---

### Task 12: PATCH `/api/practices/{id}` accepts `assigned_to` (admin-only)

**Files:**
- Modify: `api/index.py` (PATCH practice handler + `PatchPracticeRequest` model)
- Create: `tests/test_api_practices_assignment.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_api_practices_assignment.py`:

```python
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import get_current_user


def _override_user(profile: dict):
    app.dependency_overrides[get_current_user] = lambda: profile


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_patch_assigned_to_allowed_for_admin(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {"place_id": "p1", "name": "X", "tags": [], "status": "NEW"}

    captured: dict = {}
    def _fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.update_practice_fields", side_effect=_fake_update), \
         patch("api.index.add_tags"):
        client = TestClient(app)
        resp = client.patch(
            "/api/practices/p1",
            json={"assigned_to": "user-uuid-1"},
        )

    assert resp.status_code == 200
    assert captured["assigned_to"] == "user-uuid-1"
    assert "assigned_at" in captured
    assert captured["assigned_by"] == sample_admin_profile["id"]


def test_patch_assigned_to_blocked_for_sdr(sample_sdr_profile):
    _override_user(sample_sdr_profile)
    existing = {"place_id": "p1", "name": "X", "tags": [], "status": "NEW"}

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.update_practice_fields"):
        client = TestClient(app)
        resp = client.patch(
            "/api/practices/p1",
            json={"assigned_to": "user-uuid-1"},
        )

    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"].lower()


def test_patch_assigned_to_empty_string_clears_assignment(sample_admin_profile):
    _override_user(sample_admin_profile)
    existing = {"place_id": "p1", "name": "X", "tags": [], "status": "NEW",
                "assigned_to": "user-uuid-1"}

    captured: dict = {}
    def _fake_update(place_id, fields, touched_by=None):
        captured.update(fields)
        return {**existing, **fields}

    with patch("api.index.get_practice", return_value=existing), \
         patch("api.index.update_practice_fields", side_effect=_fake_update), \
         patch("api.index.add_tags"):
        client = TestClient(app)
        resp = client.patch(
            "/api/practices/p1",
            json={"assigned_to": ""},
        )

    assert resp.status_code == 200
    assert captured["assigned_to"] is None
    assert captured["assigned_at"] is None
    assert captured["assigned_by"] is None
```

If `sample_sdr_profile` doesn't exist in `conftest.py`, add it next to `sample_admin_profile`:

```python
@pytest.fixture
def sample_sdr_profile():
    return {
        "id": "sdr-id",
        "email": "sdr@healthandgroup.com",
        "name": "SDR User",
        "role": "sdr",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_practices_assignment.py -v
```
Expected: 3 failures (current PATCH doesn't accept `assigned_to`).

- [ ] **Step 3: Update `api/index.py`**

Find `PatchPracticeRequest` (or whatever model the PATCH handler uses). Add:

```python
class PatchPracticeRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
    email: str | None = None
    assigned_to: str | None = None  # NEW; "" or None to clear
```

In the PATCH handler body, before the existing update call:

```python
    update_fields: dict = {}
    if body.status is not None:
        update_fields["status"] = body.status
    if body.notes is not None:
        update_fields["notes"] = body.notes
    if body.email is not None:
        update_fields["email"] = body.email

    if body.assigned_to is not None:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin only: assignment changes")
        if body.assigned_to == "":
            update_fields["assigned_to"] = None
            update_fields["assigned_at"] = None
            update_fields["assigned_by"] = None
        else:
            update_fields["assigned_to"] = body.assigned_to
            update_fields["assigned_at"] = datetime.now(timezone.utc).isoformat()
            update_fields["assigned_by"] = user["id"]

    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = update_practice_fields(place_id, update_fields, touched_by=user["id"])
```

(Add `from datetime import datetime, timezone` if missing.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_practices_assignment.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_practices_assignment.py tests/conftest.py
git commit -m "feat(api): admin-only assigned_to PATCH on practices"
```

---

### Task 13: Frontend types + API helpers

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/api.ts`
- Create: `web/lib/tags.ts`

- [ ] **Step 1: Extend the `Practice` type**

In `web/lib/types.ts`, extend `Practice`:

```typescript
export interface Practice {
  // ... existing fields ...

  // Tags + assignment
  tags: string[]
  assigned_to: string | null
  assigned_at: string | null
  assigned_by: string | null
  assigned_to_name?: string | null  // joined display

  // Website doctor info
  website_doctor_name: string | null
  website_doctor_phone: string | null
}
```

- [ ] **Step 2: Create the tags constants file**

Create `web/lib/tags.ts`:

```typescript
export const ALL_TAGS = [
  "RESEARCHED",
  "SCRIPT_READY",
  "ENRICHED",
  "CONTACTED",
  "REPLIED",
  "MEETING_SET",
  "CLOSED_WON",
  "CLOSED_LOST",
] as const

export type Tag = (typeof ALL_TAGS)[number]

export const TAG_LABELS: Record<Tag, string> = {
  RESEARCHED: "Researched",
  SCRIPT_READY: "Script Ready",
  ENRICHED: "Enriched",
  CONTACTED: "Contacted",
  REPLIED: "Replied",
  MEETING_SET: "Meeting Set",
  CLOSED_WON: "Closed Won",
  CLOSED_LOST: "Closed Lost",
}

export const TAG_COLORS: Record<Tag, string> = {
  RESEARCHED: "bg-blue-100 text-blue-700",
  SCRIPT_READY: "bg-blue-100 text-blue-700",
  ENRICHED: "bg-purple-100 text-purple-700",
  CONTACTED: "bg-amber-100 text-amber-700",
  REPLIED: "bg-amber-100 text-amber-700",
  MEETING_SET: "bg-teal-100 text-teal-700",
  CLOSED_WON: "bg-green-100 text-green-700",
  CLOSED_LOST: "bg-rose-100 text-rose-700",
}
```

- [ ] **Step 3: Add API helpers**

In `web/lib/api.ts`, add:

```typescript
export interface AdminUserSummary {
  id: string
  email: string
  name: string | null
  role: "admin" | "sdr"
}

export async function listUsers(): Promise<AdminUserSummary[]> {
  const res = await fetch(`${API_URL}/api/admin/users`, { credentials: "include" })
  if (!res.ok) return []
  const data = await res.json()
  return data.users ?? data ?? []
}
```

If `/api/admin/users` is admin-only, also expose a permissive endpoint that returns just `id+name` for any signed-in user. For v1: only admins see the owner dropdown populated; SDRs see only themselves preselected (handled in the filter component).

In the same file, ensure `updatePractice` accepts `assigned_to`:

```typescript
export async function updatePractice(
  placeId: string,
  fields: Partial<{ status: string; notes: string; email: string; assigned_to: string }>,
): Promise<Practice> {
  const res = await fetch(`${API_URL}/api/practices/${placeId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(fields),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
```

- [ ] **Step 4: Type-check**

```bash
cd web && npx tsc --noEmit
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add web/lib/types.ts web/lib/api.ts web/lib/tags.ts
git commit -m "feat(web): types + helpers for tags, assignment, doctor info"
```

---

### Task 14: `useUrlState` + `useSessionSnapshot` hooks

**Files:**
- Create: `web/lib/use-url-state.ts`
- Create: `web/lib/use-session-snapshot.ts`

- [ ] **Step 1: Create `useUrlState`**

Create `web/lib/use-url-state.ts`:

```typescript
"use client"

import { useCallback, useMemo } from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"

export interface FilterState {
  q: string
  search: string
  cat: string
  rating: number
  tags: string[]
  enriched: "" | "yes" | "no"
  owner: string
  sel: string
}

export const EMPTY_FILTERS: FilterState = {
  q: "", search: "", cat: "", rating: 0,
  tags: [], enriched: "", owner: "", sel: "",
}

export function useUrlState(): [FilterState, (next: Partial<FilterState>) => void] {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()

  const state = useMemo<FilterState>(() => ({
    q: params.get("q") ?? "",
    search: params.get("search") ?? "",
    cat: params.get("cat") ?? "",
    rating: Number(params.get("rating") ?? 0),
    tags: (params.get("tags") ?? "").split(",").filter(Boolean),
    enriched: (params.get("enriched") as "" | "yes" | "no") ?? "",
    owner: params.get("owner") ?? "",
    sel: params.get("sel") ?? "",
  }), [params])

  const update = useCallback((next: Partial<FilterState>) => {
    const merged = { ...state, ...next }
    const sp = new URLSearchParams()
    if (merged.q) sp.set("q", merged.q)
    if (merged.search) sp.set("search", merged.search)
    if (merged.cat) sp.set("cat", merged.cat)
    if (merged.rating) sp.set("rating", String(merged.rating))
    if (merged.tags.length > 0) sp.set("tags", merged.tags.join(","))
    if (merged.enriched) sp.set("enriched", merged.enriched)
    if (merged.owner) sp.set("owner", merged.owner)
    if (merged.sel) sp.set("sel", merged.sel)
    const qs = sp.toString()
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
  }, [state, pathname, router])

  return [state, update]
}
```

- [ ] **Step 2: Create `useSessionSnapshot`**

Create `web/lib/use-session-snapshot.ts`:

```typescript
"use client"

import { useEffect, useRef } from "react"
import type { Practice } from "@/lib/types"
import type { FilterState } from "./use-url-state"

const KEY = "leads-workspace-snapshot-v1"
const TTL_MS = 30 * 60 * 1000  // 30 min

export interface Snapshot {
  practices: Practice[]
  filters: FilterState
  scrollTop: number
  savedAt: number
}

export function readSnapshot(): Snapshot | null {
  if (typeof window === "undefined") return null
  try {
    const raw = window.sessionStorage.getItem(KEY)
    if (!raw) return null
    const snap = JSON.parse(raw) as Snapshot
    if (Date.now() - snap.savedAt > TTL_MS) return null
    return snap
  } catch {
    return null
  }
}

export function writeSnapshot(snap: Omit<Snapshot, "savedAt">) {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.setItem(
      KEY,
      JSON.stringify({ ...snap, savedAt: Date.now() }),
    )
  } catch {
    // sessionStorage might be full; fail silently.
  }
}

export function clearSnapshot() {
  if (typeof window === "undefined") return
  window.sessionStorage.removeItem(KEY)
}

/** Hook that snapshots state on every change (debounced). */
export function useSessionSnapshot(
  practices: Practice[],
  filters: FilterState,
  scrollContainerRef: React.RefObject<HTMLElement>,
) {
  const timer = useRef<NodeJS.Timeout | null>(null)
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      writeSnapshot({
        practices,
        filters,
        scrollTop: scrollContainerRef.current?.scrollTop ?? 0,
      })
    }, 200)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [practices, filters, scrollContainerRef])
}
```

- [ ] **Step 3: Type-check**

```bash
cd web && npx tsc --noEmit
```
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add web/lib/use-url-state.ts web/lib/use-session-snapshot.ts
git commit -m "feat(web): URL state + session snapshot hooks"
```

---

### Task 15: Filter bar — search input, tags multi-select, enriched, owner

**Files:**
- Create: `web/components/tags-filter.tsx`
- Create: `web/components/owner-filter.tsx`
- Modify: `web/components/filter-bar.tsx`

- [ ] **Step 1: Create `TagsFilter`**

Create `web/components/tags-filter.tsx`:

```typescript
"use client"

import { useState } from "react"
import { ALL_TAGS, TAG_LABELS, type Tag } from "@/lib/tags"
import { ChevronDown, Check } from "lucide-react"
import { cn } from "@/lib/utils"

interface Props {
  selected: string[]
  onChange: (next: string[]) => void
}

export default function TagsFilter({ selected, onChange }: Props) {
  const [open, setOpen] = useState(false)

  const toggle = (tag: Tag) => {
    onChange(
      selected.includes(tag)
        ? selected.filter((t) => t !== tag)
        : [...selected, tag],
    )
  }

  const label = selected.length === 0
    ? "All tags"
    : selected.length === 1
      ? TAG_LABELS[selected[0] as Tag]
      : `${selected.length} tags`

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5 inline-flex items-center gap-1.5"
      >
        {label}
        <ChevronDown className="w-3.5 h-3.5" />
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-44 bg-white rounded-lg border border-gray-200 shadow-md">
          {ALL_TAGS.map((tag) => {
            const isSelected = selected.includes(tag)
            return (
              <button
                key={tag}
                type="button"
                onClick={() => toggle(tag)}
                className={cn(
                  "w-full text-left text-sm px-3 py-1.5 flex items-center justify-between",
                  isSelected && "bg-teal-50 text-teal-700",
                )}
              >
                {TAG_LABELS[tag]}
                {isSelected && <Check className="w-3.5 h-3.5" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Create `OwnerFilter`**

Create `web/components/owner-filter.tsx`:

```typescript
"use client"

import { useEffect, useState } from "react"
import { listUsers, type AdminUserSummary } from "@/lib/api"

interface Props {
  selected: string  // user UUID or ""
  onChange: (next: string) => void
  currentUser: { id: string; role: "admin" | "sdr" }
}

export default function OwnerFilter({ selected, onChange, currentUser }: Props) {
  const [users, setUsers] = useState<AdminUserSummary[]>([])

  useEffect(() => {
    if (currentUser.role !== "admin") return
    listUsers().then(setUsers).catch(() => setUsers([]))
  }, [currentUser.role])

  const options =
    currentUser.role === "admin"
      ? [{ id: "", name: "All owners" }, ...users.map((u) => ({ id: u.id, name: u.name ?? u.email }))]
      : [
          { id: "", name: "All" },
          { id: currentUser.id, name: "Me" },
        ]

  return (
    <select
      value={selected}
      onChange={(e) => onChange(e.target.value)}
      className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5"
    >
      {options.map((o) => (
        <option key={o.id} value={o.id}>{o.name}</option>
      ))}
    </select>
  )
}
```

- [ ] **Step 3: Rebuild `FilterBar`**

Replace `web/components/filter-bar.tsx`:

```typescript
"use client"

import { Search } from "lucide-react"
import TagsFilter from "./tags-filter"
import OwnerFilter from "./owner-filter"
import type { User } from "@/lib/types"

interface FilterBarProps {
  search: string
  onSearchChange: (s: string) => void
  category: string
  onCategoryChange: (cat: string) => void
  minRating: number
  onMinRatingChange: (r: number) => void
  tags: string[]
  onTagsChange: (tags: string[]) => void
  enriched: "" | "yes" | "no"
  onEnrichedChange: (v: "" | "yes" | "no") => void
  owner: string
  onOwnerChange: (uid: string) => void
  currentUser: User
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

export default function FilterBar(p: FilterBarProps) {
  return (
    <div className="flex flex-col gap-2 px-5 py-3 border-b border-gray-200/50">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          type="search"
          placeholder="Search name, address, doctor…"
          value={p.search}
          onChange={(e) => p.onSearchChange(e.target.value)}
          className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg border border-gray-200 bg-white/80 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
        />
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={p.category}
          onChange={(e) => p.onCategoryChange(e.target.value)}
          className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5"
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
        <TagsFilter selected={p.tags} onChange={p.onTagsChange} />
        <select
          value={p.enriched}
          onChange={(e) => p.onEnrichedChange(e.target.value as "" | "yes" | "no")}
          className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5"
        >
          <option value="">Any enrichment</option>
          <option value="yes">Enriched</option>
          <option value="no">Not enriched</option>
        </select>
        <OwnerFilter
          selected={p.owner}
          onChange={p.onOwnerChange}
          currentUser={p.currentUser}
        />
        <label className="flex items-center gap-2 text-sm text-gray-600">
          Min rating
          <input
            type="range" min={0} max={5} step={0.5}
            value={p.minRating}
            onChange={(e) => p.onMinRatingChange(Number(e.target.value))}
            className="w-20 accent-teal-600"
          />
          <span className="text-xs font-medium w-6">{p.minRating || "Any"}</span>
        </label>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Type-check**

```bash
cd web && npx tsc --noEmit
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add web/components/tags-filter.tsx web/components/owner-filter.tsx web/components/filter-bar.tsx
git commit -m "feat(web): rebuild filter bar with search + tags + enriched + owner"
```

---

### Task 16: Wire URL state + session snapshot into the map page

**Files:**
- Modify: `web/app/page.tsx`

- [ ] **Step 1: Replace `web/app/page.tsx`**

```typescript
"use client"

import { useState, useMemo, useCallback, useEffect, useRef } from "react"
import dynamic from "next/dynamic"
import type { Practice } from "@/lib/types"
import { mockPractices } from "@/lib/mock-data"
import TopBar from "@/components/top-bar"
import PracticeCard from "@/components/practice-card"
import FilterBar from "@/components/filter-bar"
import { searchPractices, analyzePractice, listPractices } from "@/lib/api"
import { useUrlState } from "@/lib/use-url-state"
import {
  readSnapshot, writeSnapshot, clearSnapshot, useSessionSnapshot,
} from "@/lib/use-session-snapshot"
import { useCurrentUser } from "@/components/user-provider"  // existing helper that exposes the signed-in user

const MapView = dynamic(() => import("@/components/map-view"), { ssr: false })

export default function Page() {
  const currentUser = useCurrentUser()
  const [filters, setFilters] = useUrlState()
  const sidebarRef = useRef<HTMLDivElement>(null)

  // Hydrate practices: snapshot first, then DB.
  const initialSnap = typeof window !== "undefined" ? readSnapshot() : null
  const [practices, setPractices] = useState<Practice[]>(
    initialSnap?.practices ?? mockPractices,
  )
  const [hydrated, setHydrated] = useState<boolean>(!!initialSnap)

  useEffect(() => {
    // Default SDR view: filter to own practices on first arrival without ?owner.
    if (
      !hydrated &&
      currentUser?.role === "sdr" &&
      !filters.owner
    ) {
      setFilters({ owner: currentUser.id })
    }
  }, [currentUser, hydrated, filters.owner, setFilters])

  useEffect(() => {
    if (hydrated) return
    let cancelled = false
    async function hydrate() {
      try {
        const dbRows = await listPractices({})
        if (!cancelled && dbRows.length > 0) {
          setPractices(dbRows)
        }
      } catch {
        /* keep mock */
      } finally {
        if (!cancelled) setHydrated(true)
      }
    }
    hydrate()
    return () => { cancelled = true }
  }, [hydrated])

  // Restore scroll once on mount if snapshot present.
  useEffect(() => {
    if (initialSnap?.scrollTop && sidebarRef.current) {
      sidebarRef.current.scrollTop = initialSnap.scrollTop
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useSessionSnapshot(practices, filters, sidebarRef)

  // ----- handlers -----
  const [isLoading, setIsLoading] = useState(false)
  const [analyzingIds, setAnalyzingIds] = useState<Set<string>>(new Set())

  const handleSearch = useCallback(async (query: string) => {
    setIsLoading(true)
    try {
      const results = await searchPractices(query)
      setPractices(results)
      setFilters({ q: query, sel: "" })
    } finally {
      setIsLoading(false)
    }
  }, [setFilters])

  const handleAnalyze = useCallback(async (placeId: string, refresh = false) => {
    setAnalyzingIds((prev) => new Set(prev).add(placeId))
    try {
      const updated = await analyzePractice(placeId, { force: refresh, rescan: refresh })
      setPractices((prev) =>
        prev.map((p) => (p.place_id === placeId ? { ...p, ...updated } : p)),
      )
    } finally {
      setAnalyzingIds((prev) => {
        const next = new Set(prev)
        next.delete(placeId)
        return next
      })
    }
  }, [])

  const handleRefresh = useCallback(async () => {
    clearSnapshot()
    setIsLoading(true)
    try {
      const dbRows = await listPractices({})
      setPractices(dbRows)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // ----- filtering -----
  const filtered = useMemo(() => {
    const needle = filters.search.toLowerCase()
    const list = practices.filter((p) => {
      if (needle) {
        const hay = [
          p.name, p.address, p.city, p.owner_name, p.website_doctor_name,
        ].filter(Boolean).join(" ").toLowerCase()
        if (!hay.includes(needle)) return false
      }
      if (filters.cat && p.category !== filters.cat) return false
      if (filters.rating && (p.rating ?? 0) < filters.rating) return false
      if (filters.tags.length > 0 && !filters.tags.some((t) => p.tags?.includes(t))) {
        return false
      }
      if (filters.enriched === "yes" && p.enrichment_status !== "enriched") return false
      if (filters.enriched === "no" && p.enrichment_status === "enriched") return false
      if (filters.owner && p.assigned_to !== filters.owner && p.last_touched_by !== filters.owner) {
        return false
      }
      return true
    })
    return list.sort((a, b) => (b.lead_score ?? -1) - (a.lead_score ?? -1))
  }, [practices, filters])

  return (
    <div className="h-screen w-screen overflow-hidden">
      <TopBar
        onSearch={handleSearch}
        isLoading={isLoading}
        onRefresh={handleRefresh}
        currentQuery={filters.q}
      />
      <main className="relative w-full h-full pt-14">
        <div className="absolute top-2 left-4 bottom-4 w-[420px] z-10 glass-panel rounded-2xl flex flex-col overflow-hidden">
          <div className="px-5 pt-5 pb-3 border-b border-gray-200/50">
            <h2 className="font-serif text-lg font-semibold text-gray-900">
              {filters.q || "All practices"}
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">
              {filtered.length} practice{filtered.length !== 1 ? "s" : ""}
            </p>
          </div>
          <FilterBar
            search={filters.search}
            onSearchChange={(s) => setFilters({ search: s })}
            category={filters.cat}
            onCategoryChange={(c) => setFilters({ cat: c })}
            minRating={filters.rating}
            onMinRatingChange={(r) => setFilters({ rating: r })}
            tags={filters.tags}
            onTagsChange={(t) => setFilters({ tags: t })}
            enriched={filters.enriched}
            onEnrichedChange={(v) => setFilters({ enriched: v })}
            owner={filters.owner}
            onOwnerChange={(o) => setFilters({ owner: o })}
            currentUser={currentUser}
          />
          <div ref={sidebarRef} className="flex-1 overflow-y-auto sidebar-scroll p-3 space-y-2">
            {filtered.length === 0 ? (
              <p className="text-center text-gray-400 py-10 text-sm">
                No practices match these filters.
              </p>
            ) : (
              filtered.map((p) => (
                <PracticeCard
                  key={p.place_id}
                  practice={p}
                  isSelected={filters.sel === p.place_id}
                  onSelect={(id) => setFilters({ sel: id ?? "" })}
                  onAnalyze={handleAnalyze}
                  isAnalyzing={analyzingIds.has(p.place_id)}
                  onCallLogged={(response) => {
                    setPractices((prev) =>
                      prev.map((x) =>
                        x.place_id === response.practice.place_id
                          ? { ...x, ...response.practice } : x,
                      ),
                    )
                  }}
                  onEnrichmentUpdate={(next) => {
                    setPractices((prev) =>
                      prev.map((x) => (x.place_id === next.place_id ? { ...x, ...next } : x)),
                    )
                  }}
                />
              ))
            )}
          </div>
        </div>
        <MapView
          practices={filtered}
          selectedId={filters.sel || null}
          onSelect={(id) => setFilters({ sel: id ?? "" })}
        />
      </main>
    </div>
  )
}
```

If `useCurrentUser` doesn't exist, create it minimally in `web/components/user-provider.tsx`:

```typescript
"use client"
import { useEffect, useState } from "react"
import type { User } from "@/lib/types"

export function useCurrentUser(): User {
  const [user, setUser] = useState<User>({
    id: "", email: "", name: null, role: "sdr",
  })
  useEffect(() => {
    fetch("/api/me", { credentials: "include" }).then((r) => r.json()).then(setUser).catch(() => {})
  }, [])
  return user
}
```

(If the codebase already has a user provider context, use that instead.)

- [ ] **Step 2: Type-check + run dev server**

```bash
cd web && npx tsc --noEmit
npm run dev
```
Visit http://localhost:3000 — verify list loads, filters work, navigating to a practice page and back keeps state.

- [ ] **Step 3: Commit**

```bash
git add web/app/page.tsx web/components/user-provider.tsx
git commit -m "feat(web): URL-driven filters + sessionStorage snapshot on map page"
```

---

### Task 17: Practice card surfaces website doctor info

**Files:**
- Modify: `web/components/practice-card.tsx`

- [ ] **Step 1: Add doctor row to the card**

In `web/components/practice-card.tsx`, in the JSX, after the address/phone rows (or wherever phone is currently rendered), add:

```typescript
{practice.website_doctor_name && (
  <div className="flex items-center gap-1.5 text-xs text-gray-600">
    <span className="font-medium">{practice.website_doctor_name}</span>
    {practice.website_doctor_phone && (
      <a
        href={`tel:${practice.website_doctor_phone.replace(/\D/g, "")}`}
        onClick={(e) => e.stopPropagation()}
        className="text-teal-700 hover:underline"
      >
        {practice.website_doctor_phone}
      </a>
    )}
    <span className="ml-1 text-[10px] uppercase tracking-wide bg-purple-100 text-purple-700 rounded px-1.5 py-0.5">
      direct
    </span>
  </div>
)}
```

- [ ] **Step 2: Visual check**

```bash
cd web && npm run dev
```
Open a practice with `website_doctor_name` populated. Verify the line renders with the `[direct]` chip and a clickable phone link distinct from the front-desk phone.

- [ ] **Step 3: Commit**

```bash
git add web/components/practice-card.tsx
git commit -m "feat(web): surface website doctor name + direct line on card"
```

---

### Task 18: Practice info panel surfaces doctor info

**Files:**
- Modify: `web/components/practice-info.tsx`

- [ ] **Step 1: Add direct line + doctor rows**

In `web/components/practice-info.tsx`, find where `phone` is rendered. Relabel it "Front desk" and add the new rows underneath:

```typescript
{practice.phone && (
  <div className="flex justify-between text-sm">
    <span className="text-gray-500">Front desk</span>
    <a href={`tel:${practice.phone.replace(/\D/g, "")}`} className="text-gray-900 hover:underline">
      {practice.phone}
    </a>
  </div>
)}
{practice.website_doctor_phone && (
  <div className="flex justify-between text-sm">
    <span className="text-gray-500">Direct line</span>
    <a href={`tel:${practice.website_doctor_phone.replace(/\D/g, "")}`} className="text-teal-700 hover:underline">
      {practice.website_doctor_phone}
    </a>
  </div>
)}
{practice.website_doctor_name && (
  <div className="flex justify-between text-sm">
    <span className="text-gray-500">Doctor</span>
    <span className="text-gray-900">{practice.website_doctor_name}</span>
  </div>
)}
```

- [ ] **Step 2: Commit**

```bash
git add web/components/practice-info.tsx
git commit -m "feat(web): surface doctor + direct line on practice info panel"
```

---

### Task 19: Admin assignment dropdown on practice page header

**Files:**
- Create: `web/components/assign-dropdown.tsx`
- Modify: `web/app/practice/[place_id]/page.tsx`

- [ ] **Step 1: Create `AssignDropdown`**

Create `web/components/assign-dropdown.tsx`:

```typescript
"use client"

import { useEffect, useState } from "react"
import { listUsers, updatePractice, type AdminUserSummary } from "@/lib/api"
import type { Practice } from "@/lib/types"

interface Props {
  practice: Practice
  onChange: (next: Partial<Practice>) => void
}

export default function AssignDropdown({ practice, onChange }: Props) {
  const [users, setUsers] = useState<AdminUserSummary[]>([])

  useEffect(() => {
    listUsers().then(setUsers).catch(() => setUsers([]))
  }, [])

  async function handleChange(value: string) {
    const updated = await updatePractice(practice.place_id, { assigned_to: value })
    onChange(updated)
  }

  return (
    <select
      value={practice.assigned_to ?? ""}
      onChange={(e) => handleChange(e.target.value)}
      className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5"
    >
      <option value="">Unassigned</option>
      {users.map((u) => (
        <option key={u.id} value={u.id}>{u.name ?? u.email}</option>
      ))}
    </select>
  )
}
```

- [ ] **Step 2: Render it on the practice page header for admins**

In `web/app/practice/[place_id]/page.tsx`, in the header next to the status select, add:

```typescript
import { useCurrentUser } from "@/components/user-provider"
import AssignDropdown from "@/components/assign-dropdown"

// ... inside the component:
const currentUser = useCurrentUser()

// ... in the header JSX, after the status badge:
{currentUser?.role === "admin" && (
  <>
    <span className="text-sm text-gray-500">Owner:</span>
    <AssignDropdown
      practice={practice}
      onChange={(next) => setPractice((prev) => (prev ? { ...prev, ...next } : prev))}
    />
  </>
)}
```

- [ ] **Step 3: Commit**

```bash
git add web/components/assign-dropdown.tsx web/app/practice/[place_id]/page.tsx
git commit -m "feat(web): admin-only owner assignment dropdown on practice page"
```

---

### Task 20: E2E smoke test

**Files:**
- None (manual verification)

- [ ] **Step 1: Backend smoke**

```bash
pytest -v
```
Expected: full suite green, including the new tag/assignment/personalization tests.

- [ ] **Step 2: Frontend smoke**

Run `npm run dev`. Then walk through this checklist in a browser:

1. **State persistence** — Search "Dental Boise" → list loads. Open a card → click "Generate script" → click Back to Map. List, filters, selection, scroll position all identical to before.
2. **Local search** — Type a few letters in the new search input → list filters live. Reload tab → filter persists in URL.
3. **Tags multi-select** — Select two tags. List shows union. Verify a record with one tag (e.g., RESEARCHED) appears even when SCRIPT_READY is also selected.
4. **Enriched filter** — Switch tri-state to "Enriched" → only Clay-enriched practices show. To "Not enriched" → only un-enriched.
5. **Owner filter (admin)** — Set Owner to a specific SDR's name → only practices where `assigned_to` OR `last_touched_by` matches show.
6. **Owner filter (SDR)** — Sign in as SDR → owner filter defaults to "Me". Clear it → all visible.
7. **Assignment** — As admin, on a practice page, set Owner. Reload sidebar → that practice now matches the SDR filter.
8. **Doctor extraction** — Pick a practice with a known doctor on its website. Click Analyze. Verify `website_doctor_name` and `website_doctor_phone` populate on the card and the practice info panel. Phone is distinct from the front desk number.
9. **Personalized script** — Click into that practice → Generate script. Verify Opening references the doctor by name, Pitch quotes one of the practice's reviews verbatim, Closing references the city.
10. **Refresh button** — Click Refresh in the toolbar → list reloads from `/api/practices` (no Google-spend), sessionStorage cleared.

- [ ] **Step 2: Commit smoke results (no code changes — informational)**

If you took notes or fixture rows, commit any new test fixtures created during smoke:

```bash
git add tests/fixtures
git commit -m "test: smoke fixtures for leads workspace"
```

(Skip if nothing changed.)

---

## Self-review

**Spec coverage:**

| Spec section | Implemented in task |
|---|---|
| State persistence (URL + sessionStorage) | 14, 16 |
| Local search bar | 15 (FilterBar input), 16 (filter logic) |
| Multi-tag visibility | 1 (column), 2 (helper), 3-7 (auto-tagging hooks), 15 (UI), 16 (logic) |
| Enriched tri-state filter | 15, 16 |
| Owner filter | 15, 16 |
| Assignment workflow + admin gate | 12 (backend), 19 (frontend) |
| SDR default owner = me | 16 |
| `crawl_website` doctor extraction | 8 |
| Doctor columns + storage | 1 (schema), 8 (analyzer wires return) |
| Personalized `generate_script` | 10 |
| Script endpoint context building | 11 |
| Practice card / info doctor surfacing | 17, 18 |
| Backfill migration | 1 |
| Tests + smoke | each task + 20 |

No spec gaps.

**Placeholder scan:** No "TBD" / "implement later" / "appropriate error handling" found. Every code block is self-contained.

**Type consistency:**
- Tag constants `RESEARCHED / SCRIPT_READY / ENRICHED / CONTACTED / REPLIED / MEETING_SET / CLOSED_WON / CLOSED_LOST` consistent across schema, helpers, prompts, and frontend.
- `add_tags(place_id, list[str])` signature consistent across tests and call sites.
- `crawl_website` returns `dict` with keys `text / doctor_name / doctor_phone` consistent across analyzer + tests.
- `generate_script` keyword arg names consistent: `website_doctor_name / city / state / rating / review_count / owner_name / owner_title / review_excerpts`.
- `Practice.tags: string[]` matches backend `tags text[]`.
- `assigned_to / assigned_at / assigned_by` consistent across schema, PATCH, and UI.
- `useUrlState` returns `[FilterState, (next: Partial<FilterState>) => void]` and is called with that shape in `page.tsx`.

No inconsistencies found.
