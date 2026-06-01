# Auth + User Attribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the whole HV Sales Intel app behind Supabase Auth; admins create rep accounts via an in-app admin UI; every mutating action stamps `last_touched_by` + `last_touched_at` on the affected practice.

**Architecture:** Supabase Auth issues JWTs in httpOnly cookies via `@supabase/ssr`. FastAPI verifies the JWT on every `/api/*` request using the Supabase admin (service-role) client, resolves it to a `profiles` row, and passes the user into route handlers as a FastAPI dependency. Storage helpers gain an optional `touched_by` arg that writes attribution columns. Next.js middleware redirects unauth'd requests to `/login`; a React context hydrates current-user state from `GET /api/me`.

**Tech Stack:** FastAPI, supabase-py, pytest, pytest-asyncio, Next.js 14 App Router, @supabase/supabase-js, @supabase/ssr, TypeScript, Tailwind.

**Reference spec:** [docs/specs/2026-04-22-auth-user-attribution-design.md](../../specs/2026-04-22-auth-user-attribution-design.md)

---

## File Structure

```
hv-sales-intel/
├── supabase/schema.sql              (modify) — add profiles, trigger, attribution columns
├── src/
│   ├── settings.py                  (modify) — new env vars
│   ├── auth.py                      (create) — token reader, get_current_user, require_admin
│   ├── storage.py                   (modify) — touched_by arg, join profiles on reads
│   └── models.py                    (modify) — attribution fields on Practice
├── api/
│   └── index.py                     (modify) — protect routes, /api/me, /api/admin/users/*, bootstrap admin
├── scripts/
│   └── bootstrap_admin.py           (create) — seed initial admin manually if startup missed it
├── tests/
│   ├── __init__.py                  (create)
│   ├── conftest.py                  (create) — fixtures: mocked Supabase client, sample profile rows
│   ├── test_auth.py                 (create) — get_current_user + require_admin
│   ├── test_storage.py              (create) — touched_by attribution stamping
│   └── test_api_auth.py             (create) — route-level 401 / 403 / stamping
├── pyproject.toml                   (create) — pytest config + test deps pin
├── requirements.txt                 (modify) — add pytest, pytest-asyncio, httpx for TestClient
├── web/
│   ├── package.json                 (modify) — add @supabase/supabase-js, @supabase/ssr
│   ├── .env.example                 (modify) — document NEXT_PUBLIC_SUPABASE_* vars
│   ├── middleware.ts                (create) — unauth'd redirect
│   ├── lib/
│   │   ├── supabase-client.ts       (create) — createBrowserClient()
│   │   ├── supabase-server.ts       (create) — createServerClient()
│   │   ├── auth.ts                  (create) — AuthProvider + useAuth hook
│   │   ├── types.ts                 (modify) — attribution fields + User type
│   │   ├── api.ts                   (modify) — credentials: "include" + 401 → /login
│   │   └── utils.ts                 (modify) — timeAgo helper
│   ├── app/
│   │   ├── layout.tsx               (modify) — wrap in AuthProvider
│   │   ├── login/page.tsx           (create)
│   │   ├── admin/users/page.tsx     (create)
│   │   └── practice/[place_id]/page.tsx  (modify) — render last-touched in header
│   └── components/
│       ├── user-menu.tsx            (create) — avatar + name + sign-out
│       ├── top-bar.tsx              (modify) — render UserMenu
│       └── practice-card.tsx        (modify) — render last-touched line
├── .env.example                     (modify) — SUPABASE_SERVICE_ROLE_KEY, BOOTSTRAP_ADMIN_*
```

---

## Task 1: Add pytest infrastructure

**Files:**
- Create: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add test deps to `requirements.txt`**

Append to `requirements.txt`:

```
pytest>=8.2,<9
pytest-asyncio>=0.23,<1
```

- [ ] **Step 2: Create `pyproject.toml`**

Write `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 3: Create empty `tests/__init__.py`**

Write `tests/__init__.py`:

```python
```

- [ ] **Step 4: Create `tests/conftest.py` with a sanity fixture**

Write `tests/conftest.py`:

```python
import pytest


@pytest.fixture
def sample_rep_profile() -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "rep@example.com",
        "name": "Test Rep",
        "role": "rep",
        "created_at": "2026-04-22T00:00:00Z",
    }


@pytest.fixture
def sample_admin_profile() -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000002",
        "email": "admin@example.com",
        "name": "Test Admin",
        "role": "admin",
        "created_at": "2026-04-22T00:00:00Z",
    }
```

- [ ] **Step 5: Install deps and verify pytest runs**

Run: `pip install -r requirements.txt && pytest -q`
Expected: `no tests ran in 0.01s` (or similar) — exit code 0.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/__init__.py tests/conftest.py requirements.txt
git commit -m "chore(test): set up pytest infrastructure"
```

---

## Task 2: Migrate DB schema — profiles, trigger, attribution columns

**Files:**
- Modify: `supabase/schema.sql`

- [ ] **Step 1: Append profiles table + trigger + attribution columns to `supabase/schema.sql`**

Append to the end of `supabase/schema.sql`:

```sql
-- Auth + user attribution

create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  name text,
  role text not null default 'rep' check (role in ('admin', 'rep')),
  created_at timestamptz default now()
);

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, email, name, role)
  values (new.id, new.email, new.raw_user_meta_data->>'name', 'rep')
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

alter table practices add column if not exists last_touched_by uuid references profiles(id);
alter table practices add column if not exists last_touched_at timestamptz;

create index if not exists idx_profiles_role on profiles (role);
```

- [ ] **Step 2: Apply the migration**

In the Supabase dashboard SQL editor, paste and run the new section. Verify:
- `profiles` table exists with one row per `auth.users` entry (empty is fine).
- `practices` has `last_touched_by` (uuid, nullable) and `last_touched_at` (timestamptz, nullable).
- Trigger `on_auth_user_created` exists on `auth.users`.

- [ ] **Step 3: Commit**

```bash
git add supabase/schema.sql
git commit -m "feat(schema): add profiles table, trigger, and practice attribution columns"
```

---

## Task 3: Extend `src/settings.py` with new env vars

**Files:**
- Modify: `src/settings.py`
- Modify: `.env.example`

- [ ] **Step 1: Update `src/settings.py`**

Replace the existing Settings class in `src/settings.py`:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_maps_api_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""                    # anon key (legacy name preserved)
    supabase_service_role_key: str = ""       # NEW — admin client for auth verification
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Bootstrap admin (seeded on startup if profiles has zero admins)
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
```

- [ ] **Step 2: Update `.env.example`**

Overwrite `.env.example`:

```
GOOGLE_MAPS_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_SERVICE_ROLE_KEY=
BOOTSTRAP_ADMIN_EMAIL=
BOOTSTRAP_ADMIN_PASSWORD=
NEXT_PUBLIC_RINGCENTRAL_WEB_APP_URL=https://app.ringcentral.com
```

- [ ] **Step 3: Verify settings imports cleanly**

Run: `python -c "from src.settings import settings; print(settings.model_dump_json(indent=2))"`
Expected: JSON output showing all fields with their defaults; no ImportError.

- [ ] **Step 4: Commit**

```bash
git add src/settings.py .env.example
git commit -m "feat(settings): add Supabase service-role key and bootstrap admin env vars"
```

---

## Task 4: Create `src/auth.py` — cookie reader + get_admin_client

**Files:**
- Create: `src/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write failing test for `_read_supabase_token`**

Create `tests/test_auth.py`:

```python
from unittest.mock import MagicMock

from src.auth import _read_supabase_token


def _mock_request(cookies: dict):
    req = MagicMock()
    req.cookies = cookies
    return req


def test_read_token_returns_none_when_no_cookies():
    assert _read_supabase_token(_mock_request({})) is None


def test_read_token_reads_single_auth_cookie():
    token_payload = '{"access_token":"abc.def.ghi"}'
    req = _mock_request({"sb-proj-auth-token": token_payload})
    assert _read_supabase_token(req) == "abc.def.ghi"


def test_read_token_reassembles_chunked_cookies():
    # @supabase/ssr sometimes splits the cookie into .0 and .1
    part0 = '{"access_token":"abc.de'
    part1 = 'f.ghi","refresh_token":"r"}'
    req = _mock_request({
        "sb-proj-auth-token.0": part0,
        "sb-proj-auth-token.1": part1,
    })
    assert _read_supabase_token(req) == "abc.def.ghi"


def test_read_token_returns_none_on_malformed_cookie():
    req = _mock_request({"sb-proj-auth-token": "not json"})
    assert _read_supabase_token(req) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.auth'`.

- [ ] **Step 3: Create `src/auth.py` with the minimal implementation**

Write `src/auth.py`:

```python
import json
from typing import Any

from fastapi import Depends, HTTPException, Request
from supabase import create_client

from src.settings import settings

_admin_client: Any = None


def get_admin_client():
    """Return a Supabase client authenticated with the service-role key.

    Lazily instantiated so imports don't fail when Supabase isn't configured.
    Callers should check `settings.supabase_service_role_key` before using.
    """
    global _admin_client
    if _admin_client is None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError("Supabase service-role client not configured")
        _admin_client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
    return _admin_client


def _read_supabase_token(request: Request) -> str | None:
    """Reassemble the access token from @supabase/ssr cookies.

    The cookie is named `sb-<project-ref>-auth-token`, sometimes chunked
    into `.0` / `.1` for large payloads. Value is a JSON blob containing
    an `access_token` field.
    """
    # Collect all sb-*-auth-token* cookies
    auth_cookies = {
        name: value
        for name, value in request.cookies.items()
        if name.startswith("sb-") and "auth-token" in name
    }
    if not auth_cookies:
        return None

    # Group chunked cookies (name.0, name.1, ...) by base name
    bases: dict[str, dict[int, str]] = {}
    singles: dict[str, str] = {}
    for name, value in auth_cookies.items():
        if "." in name and name.rsplit(".", 1)[-1].isdigit():
            base, idx = name.rsplit(".", 1)
            bases.setdefault(base, {})[int(idx)] = value
        else:
            singles[name] = value

    # Try assembled chunks first, then singles
    candidates: list[str] = []
    for base, parts in bases.items():
        candidates.append("".join(parts[i] for i in sorted(parts)))
    candidates.extend(singles.values())

    for raw in candidates:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        token = decoded.get("access_token")
        if token:
            return token
    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_auth.py -v`
Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/auth.py tests/test_auth.py
git commit -m "feat(auth): add cookie-reader helper and admin-client factory"
```

---

## Task 5: Implement `get_current_user` + `require_admin`

**Files:**
- Modify: `src/auth.py`
- Modify: `tests/test_auth.py`

- [ ] **Step 1: Add failing tests for `get_current_user` and `require_admin`**

Append to `tests/test_auth.py`:

```python
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.auth import get_current_user, require_admin


@pytest.mark.asyncio
async def test_get_current_user_401_when_no_token():
    req = MagicMock()
    req.cookies = {}
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_returns_profile(sample_rep_profile):
    token = "abc.def.ghi"
    req = MagicMock()
    req.cookies = {"sb-proj-auth-token": f'{{"access_token":"{token}"}}'}

    auth_user = MagicMock()
    auth_user.id = sample_rep_profile["id"]

    client = MagicMock()
    client.auth.get_user.return_value = MagicMock(user=auth_user)
    table = MagicMock()
    table.select.return_value = table
    table.eq.return_value = table
    table.single.return_value = table
    table.execute.return_value = MagicMock(data=sample_rep_profile)
    client.table.return_value = table

    with patch("src.auth.get_admin_client", return_value=client):
        result = await get_current_user(req)
    assert result == sample_rep_profile


@pytest.mark.asyncio
async def test_get_current_user_401_on_invalid_token():
    req = MagicMock()
    req.cookies = {"sb-proj-auth-token": '{"access_token":"bad"}'}
    client = MagicMock()
    client.auth.get_user.side_effect = Exception("invalid")
    with patch("src.auth.get_admin_client", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_403_when_profile_missing():
    req = MagicMock()
    req.cookies = {"sb-proj-auth-token": '{"access_token":"abc"}'}
    auth_user = MagicMock()
    auth_user.id = "missing"
    client = MagicMock()
    client.auth.get_user.return_value = MagicMock(user=auth_user)
    table = MagicMock()
    table.select.return_value = table
    table.eq.return_value = table
    table.single.return_value = table
    table.execute.return_value = MagicMock(data=None)
    client.table.return_value = table
    with patch("src.auth.get_admin_client", return_value=client):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(req)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_passes_for_admin(sample_admin_profile):
    result = await require_admin(sample_admin_profile)
    assert result == sample_admin_profile


@pytest.mark.asyncio
async def test_require_admin_403_for_rep(sample_rep_profile):
    with pytest.raises(HTTPException) as exc:
        await require_admin(sample_rep_profile)
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_auth.py -v`
Expected: 6 new tests FAIL with `ImportError: cannot import name 'get_current_user'`.

- [ ] **Step 3: Implement `get_current_user` and `require_admin`**

Append to `src/auth.py`:

```python
async def get_current_user(request: Request) -> dict:
    """Resolve JWT → profiles row. 401 if missing/invalid, 403 if no profile."""
    token = _read_supabase_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    client = get_admin_client()
    try:
        user_resp = client.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    auth_user = user_resp.user
    result = (
        client.table("profiles").select("*")
        .eq("id", auth_user.id).single().execute()
    )
    if not result.data:
        raise HTTPException(status_code=403, detail="No profile for this user")
    return result.data


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Raise 403 if the current user isn't an admin."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/auth.py tests/test_auth.py
git commit -m "feat(auth): add get_current_user and require_admin dependencies"
```

---

## Task 6: Add attribution to storage helpers

**Files:**
- Modify: `src/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write failing tests for `touched_by` behavior**

Create `tests/test_storage.py`:

```python
from unittest.mock import MagicMock, patch

from src.storage import update_practice_fields


def _mock_supabase_update_returning(row):
    client = MagicMock()
    table = MagicMock()
    table.update.return_value = table
    table.eq.return_value = table
    table.execute.return_value = MagicMock(data=[row])
    client.table.return_value = table
    return client, table


def test_update_practice_fields_stamps_touched_by():
    client, table = _mock_supabase_update_returning({"place_id": "p1"})
    with patch("src.storage._get_client", return_value=client):
        update_practice_fields("p1", {"status": "CONTACTED"}, touched_by="user-1")
    call_args = table.update.call_args.args[0]
    assert call_args["status"] == "CONTACTED"
    assert call_args["last_touched_by"] == "user-1"
    assert "last_touched_at" in call_args


def test_update_practice_fields_no_stamp_when_touched_by_none():
    client, table = _mock_supabase_update_returning({"place_id": "p1"})
    with patch("src.storage._get_client", return_value=client):
        update_practice_fields("p1", {"status": "CONTACTED"})
    call_args = table.update.call_args.args[0]
    assert "last_touched_by" not in call_args
    assert "last_touched_at" not in call_args
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL with `TypeError: update_practice_fields() got an unexpected keyword argument 'touched_by'`.

- [ ] **Step 3: Modify `src/storage.py` to add the `touched_by` arg**

Replace the contents of `src/storage.py`:

```python
from datetime import datetime, timezone

from supabase import create_client

from src.models import Practice
from src.settings import settings

PROFILE_JOIN_SELECT = "*, last_touched_by_profile:profiles!last_touched_by(name)"


def _get_client():
    """Return Supabase client or None if unconfigured."""
    if settings.supabase_url and settings.supabase_key:
        return create_client(settings.supabase_url, settings.supabase_key)
    return None


def _with_attribution(fields: dict, touched_by: str | None) -> dict:
    if not touched_by:
        return fields
    return {
        **fields,
        "last_touched_by": touched_by,
        "last_touched_at": datetime.now(timezone.utc).isoformat(),
    }


def _flatten_attribution(row: dict) -> dict:
    """Flatten the joined profile into last_touched_by_name."""
    if not row:
        return row
    joined = row.pop("last_touched_by_profile", None)
    row["last_touched_by_name"] = joined.get("name") if joined else None
    return row


def upsert_practices(
    practices: list[Practice],
    touched_by: str | None = None,
) -> int:
    """Upsert practices. Returns count. Stamps attribution when touched_by set."""
    client = _get_client()
    if not client or not practices:
        return 0
    rows = []
    for p in practices:
        row = p.model_dump()
        rows.append(_with_attribution(row, touched_by))
    result = client.table("practices").upsert(rows, on_conflict="place_id").execute()
    return len(result.data) if result.data else 0


def query_practices(
    city: str | None = None,
    category: str | None = None,
    min_rating: float | None = None,
    limit: int = 50,
) -> list[dict]:
    """List practices with profile join. Returns [] if unconfigured."""
    client = _get_client()
    if not client:
        return []
    q = client.table("practices").select(PROFILE_JOIN_SELECT)
    if city:
        q = q.ilike("city", f"%{city}%")
    if category:
        q = q.eq("category", category)
    if min_rating:
        q = q.gte("rating", min_rating)
    q = q.order("rating", desc=True).limit(limit)
    result = q.execute()
    return [_flatten_attribution(r) for r in (result.data or [])]


def get_practice(place_id: str) -> dict | None:
    """Get single practice with profile join."""
    client = _get_client()
    if not client:
        return None
    result = (
        client.table("practices").select(PROFILE_JOIN_SELECT)
        .eq("place_id", place_id).single().execute()
    )
    return _flatten_attribution(result.data) if result.data else None


def update_practice_analysis(
    place_id: str,
    analysis: dict,
    touched_by: str | None = None,
) -> dict | None:
    """Update Phase 2 analysis fields. Stamps attribution when touched_by set."""
    client = _get_client()
    if not client:
        return None
    result = (
        client.table("practices")
        .update(_with_attribution(analysis, touched_by))
        .eq("place_id", place_id)
        .execute()
    )
    return result.data[0] if result.data else None


def update_practice_fields(
    place_id: str,
    fields: dict,
    touched_by: str | None = None,
) -> dict | None:
    """Update arbitrary fields. Stamps attribution when touched_by set."""
    client = _get_client()
    if not client:
        return None
    result = (
        client.table("practices")
        .update(_with_attribution(fields, touched_by))
        .eq("place_id", place_id)
        .execute()
    )
    return result.data[0] if result.data else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/storage.py tests/test_storage.py
git commit -m "feat(storage): stamp last_touched_by on mutations + join profile on reads"
```

---

## Task 7: Extend `Practice` model with attribution fields

**Files:**
- Modify: `src/models.py`

- [ ] **Step 1: Update `src/models.py`**

Replace `src/models.py`:

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

    # Attribution
    last_touched_by: str | None = None
    last_touched_by_name: str | None = None
    last_touched_at: str | None = None
```

- [ ] **Step 2: Verify imports still work**

Run: `python -c "from src.models import Practice; print(Practice(place_id='x', name='y').model_dump())"`
Expected: JSON-compatible dict including all three attribution fields (all None).

- [ ] **Step 3: Commit**

```bash
git add src/models.py
git commit -m "feat(models): add attribution fields to Practice"
```

---

## Task 8: Protect existing `/api/practices/*` routes + stamp on mutations

**Files:**
- Modify: `api/index.py`
- Create: `tests/test_api_auth.py`

- [ ] **Step 1: Write failing test: unauth'd request returns 401**

Create `tests/test_api_auth.py`:

```python
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.index import app

client = TestClient(app)


def test_health_is_public():
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_list_practices_requires_auth():
    resp = client.get("/api/practices")
    assert resp.status_code == 401


def test_get_practice_requires_auth():
    resp = client.get("/api/practices/some_id")
    assert resp.status_code == 401


def test_patch_practice_requires_auth():
    resp = client.patch("/api/practices/some_id", json={"status": "CONTACTED"})
    assert resp.status_code == 401


def test_me_requires_auth():
    resp = client.get("/api/me")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail for the right reason**

Run: `pytest tests/test_api_auth.py -v`
Expected: `test_health_is_public` PASSES. Other tests FAIL — some with 200 (no auth enforced yet) and the `/api/me` test with 404 (endpoint doesn't exist yet).

- [ ] **Step 3: Rewrite `api/index.py` to protect routes + add `/api/me`**

Replace `api/index.py`:

```python
import json

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.analyzer import analyze_practice
from src.auth import get_current_user
from src.models import Practice
from src.places import get_place, search_places
from src.scriptgen import generate_script
from src.storage import (
    get_practice,
    query_practices,
    update_practice_analysis,
    update_practice_fields,
    upsert_practices,
)

app = FastAPI(title="HV Sales Intel", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATUS_ORDER = [
    "NEW", "RESEARCHED", "SCRIPT READY", "CONTACTED",
    "FOLLOW UP", "MEETING SET", "PROPOSAL", "CLOSED WON", "CLOSED LOST",
]


def _should_auto_advance(current: str, target: str) -> bool:
    try:
        return STATUS_ORDER.index(target) > STATUS_ORDER.index(current)
    except ValueError:
        return False


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    return user


@app.get("/api/practices")
def list_practices(
    city: str | None = Query(None),
    category: str | None = Query(None),
    min_rating: float | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    rows = query_practices(city=city, category=category, min_rating=min_rating, limit=limit)
    return {"practices": rows, "count": len(rows)}


class SearchRequest(BaseModel):
    query: str
    refresh: bool = False


@app.post("/api/practices/search")
async def search(body: SearchRequest, user: dict = Depends(get_current_user)):
    practices = await search_places(body.query)
    upserted = upsert_practices(practices, touched_by=user["id"])
    return {
        "practices": [p.model_dump() for p in practices],
        "count": len(practices),
        "upserted": upserted,
    }


@app.get("/api/practices/{place_id}")
def get_single(place_id: str, user: dict = Depends(get_current_user)):
    row = get_practice(place_id)
    if not row:
        raise HTTPException(status_code=404, detail="Practice not found")
    return row


class AnalyzeRequest(BaseModel):
    force: bool = False
    rescan: bool = False


@app.post("/api/practices/{place_id}/analyze")
async def analyze(
    place_id: str,
    body: AnalyzeRequest | None = None,
    user: dict = Depends(get_current_user),
):
    force = body.force if body else False
    rescan = body.rescan if body else False

    existing = get_practice(place_id)
    if existing and existing.get("lead_score") is not None and not force and not rescan:
        return existing

    current_record = existing
    if existing and rescan:
        refreshed = await get_place(place_id, fallback=Practice(**_strip_joined(existing)))
        if refreshed:
            upsert_practices([refreshed], touched_by=user["id"])
            current_record = get_practice(place_id) or refreshed.model_dump()

    if current_record:
        name = current_record["name"]
        website = current_record.get("website")
        category = current_record.get("category")
        city = current_record.get("city")
        state = current_record.get("state")
    else:
        name = place_id
        website = None
        category = None
        city = None
        state = None

    analysis = await analyze_practice(place_id, name, website, category, city=city, state=state)

    if current_record:
        current_status = current_record.get("status", "NEW")
        if _should_auto_advance(current_status, "RESEARCHED"):
            analysis["status"] = "RESEARCHED"

    updated = update_practice_analysis(place_id, analysis, touched_by=user["id"])
    if updated:
        return updated

    if current_record:
        return {**current_record, **analysis}
    return {"place_id": place_id, "name": name, **analysis}


@app.post("/api/practices/{place_id}/rescan")
async def rescan_practice(place_id: str, user: dict = Depends(get_current_user)):
    existing = get_practice(place_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Practice not found")

    refreshed = await get_place(place_id, fallback=Practice(**_strip_joined(existing)))
    if not refreshed:
        return existing

    upsert_practices([refreshed], touched_by=user["id"])
    return get_practice(place_id) or refreshed.model_dump()


@app.get("/api/practices/{place_id}/script")
async def get_script(place_id: str, user: dict = Depends(get_current_user)):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(status_code=404, detail="Practice not found")

    if practice.get("call_script"):
        return json.loads(practice["call_script"])

    script = await generate_script(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
    )

    update_practice_fields(place_id, {"call_script": json.dumps(script)}, touched_by=user["id"])

    current_status = practice.get("status", "NEW")
    if _should_auto_advance(current_status, "SCRIPT READY"):
        update_practice_fields(place_id, {"status": "SCRIPT READY"}, touched_by=user["id"])

    return script


@app.post("/api/practices/{place_id}/script")
async def regenerate_script_endpoint(place_id: str, user: dict = Depends(get_current_user)):
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

    update_practice_fields(place_id, {"call_script": json.dumps(script)}, touched_by=user["id"])
    return script


class PatchPracticeRequest(BaseModel):
    status: str | None = None
    notes: str | None = None


@app.patch("/api/practices/{place_id}")
def patch_practice(
    place_id: str,
    body: PatchPracticeRequest,
    user: dict = Depends(get_current_user),
):
    fields: dict = {}
    if body.status is not None:
        if body.status not in STATUS_ORDER:
            raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
        fields["status"] = body.status
    if body.notes is not None:
        fields["notes"] = body.notes
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = update_practice_fields(place_id, fields, touched_by=user["id"])
    if not updated:
        raise HTTPException(status_code=404, detail="Practice not found")
    return updated


def _strip_joined(row: dict) -> dict:
    """Drop keys the Practice model doesn't know about (attribution + joins)."""
    allowed = set(Practice.model_fields.keys())
    return {k: v for k, v in row.items() if k in allowed}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_auth.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Verify existing tests still pass**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/index.py tests/test_api_auth.py
git commit -m "feat(api): require auth on /api/practices/*; add /api/me; stamp attribution"
```

---

## Task 9: Add admin user CRUD endpoints

**Files:**
- Modify: `api/index.py`
- Modify: `tests/test_api_auth.py`

- [ ] **Step 1: Write failing tests for admin route protection**

Append to `tests/test_api_auth.py`:

```python
def test_admin_users_list_requires_auth():
    resp = client.get("/api/admin/users")
    assert resp.status_code == 401


def test_admin_users_create_requires_auth():
    resp = client.post(
        "/api/admin/users",
        json={"email": "x@y.com", "name": "X", "password": "p"},
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_auth.py -v`
Expected: the two new tests FAIL with 404.

- [ ] **Step 3: Add admin endpoints to `api/index.py`**

Append to `api/index.py` (above `_strip_joined`):

```python
from src.auth import get_admin_client, require_admin


class CreateUserRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "rep"


@app.get("/api/admin/users")
def list_users(admin: dict = Depends(require_admin)):
    """List all profiles with per-user touched-practice count."""
    client = get_admin_client()
    profiles_res = client.table("profiles").select("*").execute()
    counts_res = client.table("practices").select("last_touched_by").execute()
    counts: dict[str, int] = {}
    for row in counts_res.data or []:
        uid = row.get("last_touched_by")
        if uid:
            counts[uid] = counts.get(uid, 0) + 1
    users = []
    for p in profiles_res.data or []:
        users.append({**p, "practices_touched": counts.get(p["id"], 0)})
    return {"users": users}


@app.post("/api/admin/users")
def create_user(body: CreateUserRequest, admin: dict = Depends(require_admin)):
    if body.role not in ("admin", "rep"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'rep'")
    client = get_admin_client()
    try:
        created = client.auth.admin.create_user({
            "email": body.email,
            "password": body.password,
            "email_confirm": True,
            "user_metadata": {"name": body.name},
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    user_id = created.user.id
    # Trigger inserted a profile with role='rep'. If we want admin, update it.
    if body.role == "admin":
        client.table("profiles").update({"role": "admin"}).eq("id", user_id).execute()
    profile = client.table("profiles").select("*").eq("id", user_id).single().execute()
    return profile.data


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: str, admin: dict = Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete self")
    client = get_admin_client()
    try:
        client.auth.admin.delete_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


class ResetPasswordRequest(BaseModel):
    new_password: str


@app.post("/api/admin/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    admin: dict = Depends(require_admin),
):
    client = get_admin_client()
    try:
        client.auth.admin.update_user_by_id(user_id, {"password": body.new_password})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_auth.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_auth.py
git commit -m "feat(api): admin user CRUD endpoints (list/create/delete/reset-password)"
```

---

## Task 10: Bootstrap admin on startup

**Files:**
- Modify: `api/index.py`
- Create: `scripts/bootstrap_admin.py`

- [ ] **Step 1: Add startup hook to `api/index.py`**

Add near the top of `api/index.py`, after `app = FastAPI(...)` and before `app.add_middleware(...)`:

```python
@app.on_event("startup")
async def bootstrap_admin_on_startup():
    """If no admin exists and BOOTSTRAP_ADMIN_* env vars are set, seed one."""
    from src.settings import settings
    if not (settings.supabase_url and settings.supabase_service_role_key):
        return
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    try:
        client = get_admin_client()
        existing = client.table("profiles").select("id").eq("role", "admin").execute()
        if existing.data:
            return
        created = client.auth.admin.create_user({
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
            "email_confirm": True,
            "user_metadata": {"name": "Bootstrap Admin"},
        })
        client.table("profiles").update({"role": "admin"}).eq("id", created.user.id).execute()
        print(f"[bootstrap] Seeded admin: {settings.bootstrap_admin_email}")
    except Exception as e:
        print(f"[bootstrap] Skipped ({e!r})")
```

- [ ] **Step 2: Create standalone bootstrap script**

Create `scripts/bootstrap_admin.py`:

```python
"""Manually seed the initial admin. Run when the startup hook didn't catch it.

Usage:
    BOOTSTRAP_ADMIN_EMAIL=... BOOTSTRAP_ADMIN_PASSWORD=... python scripts/bootstrap_admin.py
"""
import asyncio

from api.index import bootstrap_admin_on_startup


if __name__ == "__main__":
    asyncio.run(bootstrap_admin_on_startup())
```

- [ ] **Step 3: Smoke-test the startup hook manually**

Set `BOOTSTRAP_ADMIN_EMAIL` + `BOOTSTRAP_ADMIN_PASSWORD` in `.env`, start the server:

Run: `uvicorn api.index:app --reload --port 8000`
Expected: Log line `[bootstrap] Seeded admin: <email>` on first start, silent on subsequent starts. Verify in Supabase: one row in `profiles` with `role='admin'`.

- [ ] **Step 4: Commit**

```bash
git add api/index.py scripts/bootstrap_admin.py
git commit -m "feat(auth): bootstrap initial admin on startup if none exists"
```

---

## Task 11: Install frontend Supabase deps + env example

**Files:**
- Modify: `web/package.json`
- Modify: `web/.env.example`

- [ ] **Step 1: Install Supabase packages**

Run: `cd web && npm install @supabase/supabase-js @supabase/ssr`
Expected: `package.json` updates, no errors.

- [ ] **Step 2: Update `web/.env.example`**

Overwrite `web/.env.example`:

```
NEXT_PUBLIC_API_URL=
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_RINGCENTRAL_WEB_APP_URL=https://app.ringcentral.com
```

- [ ] **Step 3: Create `web/.env.local` for development (not committed)**

If `web/.env.local` doesn't exist yet, create it with the values from Supabase dashboard:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=<project URL>
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
```

- [ ] **Step 4: Commit**

```bash
git add web/package.json web/package-lock.json web/.env.example
git commit -m "chore(web): install @supabase/supabase-js + @supabase/ssr"
```

---

## Task 12: Create Supabase client helpers

**Files:**
- Create: `web/lib/supabase-client.ts`
- Create: `web/lib/supabase-server.ts`

- [ ] **Step 1: Create `web/lib/supabase-client.ts`**

Write `web/lib/supabase-client.ts`:

```ts
import { createBrowserClient } from "@supabase/ssr"

export function getSupabaseBrowserClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  )
}
```

- [ ] **Step 2: Create `web/lib/supabase-server.ts`**

Write `web/lib/supabase-server.ts`:

```ts
import { createServerClient } from "@supabase/ssr"
import { cookies } from "next/headers"

export function getSupabaseServerClient() {
  const cookieStore = cookies()
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        get(name: string) {
          return cookieStore.get(name)?.value
        },
        set(name: string, value: string, options) {
          cookieStore.set({ name, value, ...options })
        },
        remove(name: string, options) {
          cookieStore.set({ name, value: "", ...options })
        },
      },
    },
  )
}
```

- [ ] **Step 3: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/lib/supabase-client.ts web/lib/supabase-server.ts
git commit -m "feat(web): add Supabase browser and server client helpers"
```

---

## Task 13: Next.js middleware — protect all non-public routes

**Files:**
- Create: `web/middleware.ts`

- [ ] **Step 1: Create `web/middleware.ts`**

Write `web/middleware.ts`:

```ts
import { createServerClient } from "@supabase/ssr"
import { NextResponse, type NextRequest } from "next/server"

const PUBLIC_PATHS = ["/login"]

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  // Skip middleware for public paths and Next.js internals.
  if (
    PUBLIC_PATHS.some((p) => pathname.startsWith(p)) ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname === "/favicon.ico"
  ) {
    return NextResponse.next()
  }

  const response = NextResponse.next()

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        get: (name) => request.cookies.get(name)?.value,
        set: (name, value, options) => {
          response.cookies.set({ name, value, ...options })
        },
        remove: (name, options) => {
          response.cookies.set({ name, value: "", ...options })
        },
      },
    },
  )

  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    const loginUrl = new URL("/login", request.url)
    loginUrl.searchParams.set("redirect", pathname)
    return NextResponse.redirect(loginUrl)
  }

  return response
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/middleware.ts
git commit -m "feat(web): middleware redirects unauthenticated requests to /login"
```

---

## Task 14: Login page

**Files:**
- Create: `web/app/login/page.tsx`

- [ ] **Step 1: Create `web/app/login/page.tsx`**

Write `web/app/login/page.tsx`:

```tsx
"use client"

import { useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { getSupabaseBrowserClient } from "@/lib/supabase-client"

export default function LoginPage() {
  const router = useRouter()
  const search = useSearchParams()
  const redirect = search.get("redirect") || "/"

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    const supabase = getSupabaseBrowserClient()
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) {
      setError(error.message)
      setLoading(false)
      return
    }
    router.push(redirect)
    router.refresh()
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-cream">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 bg-white/80 p-8 rounded-2xl shadow-lg backdrop-blur"
      >
        <h1 className="font-serif text-2xl font-bold text-teal-700">Sign in</h1>
        <p className="text-sm text-gray-500">Health &amp; Virtuals Sales Intel</p>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            className="w-full text-sm rounded-lg border border-gray-200 px-3 py-2
                       focus:outline-none focus:ring-2 focus:ring-teal-500/40"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
            className="w-full text-sm rounded-lg border border-gray-200 px-3 py-2
                       focus:outline-none focus:ring-2 focus:ring-teal-500/40"
          />
        </div>

        {error && <p className="text-sm text-rose-600">{error}</p>}

        <button
          type="submit"
          disabled={loading}
          className="w-full text-sm px-4 py-2 rounded-lg bg-teal-600 text-white
                     hover:bg-teal-700 disabled:opacity-50 transition"
        >
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  )
}
```

- [ ] **Step 2: Smoke test**

Run: `cd web && npm run dev`
Visit `http://localhost:3000/login`
Expected: form renders; submitting wrong credentials shows "Invalid login credentials"; submitting correct bootstrap admin creds redirects to `/`.

- [ ] **Step 3: Commit**

```bash
git add web/app/login/page.tsx
git commit -m "feat(web): login page with Supabase email/password auth"
```

---

## Task 15: Auth context provider + useAuth hook

**Files:**
- Create: `web/lib/auth.ts`
- Modify: `web/lib/types.ts`
- Modify: `web/app/layout.tsx`

- [ ] **Step 1: Add `User` type to `web/lib/types.ts`**

Append to `web/lib/types.ts`:

```ts
export interface User {
  id: string
  email: string
  name: string | null
  role: "admin" | "rep"
  created_at?: string
}
```

- [ ] **Step 2: Create `web/lib/auth.ts`**

Write `web/lib/auth.ts`:

```tsx
"use client"

import { createContext, useContext, useEffect, useState, ReactNode } from "react"
import { useRouter } from "next/navigation"
import { getSupabaseBrowserClient } from "./supabase-client"
import type { User } from "./types"

interface AuthContextValue {
  user: User | null
  loading: boolean
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  signOut: async () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const router = useRouter()

  useEffect(() => {
    let cancelled = false
    async function hydrate() {
      try {
        const API_URL = process.env.NEXT_PUBLIC_API_URL || ""
        if (!API_URL) return
        const res = await fetch(`${API_URL}/api/me`, { credentials: "include" })
        if (res.ok && !cancelled) {
          setUser(await res.json())
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    hydrate()
    return () => {
      cancelled = true
    }
  }, [])

  async function signOut() {
    const supabase = getSupabaseBrowserClient()
    await supabase.auth.signOut()
    setUser(null)
    router.push("/login")
    router.refresh()
  }

  return (
    <AuthContext.Provider value={{ user, loading, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
```

- [ ] **Step 3: Wrap root layout with `AuthProvider`**

Replace the contents of `web/app/layout.tsx`:

```tsx
import type { Metadata } from "next"
import { Fraunces, Plus_Jakarta_Sans } from "next/font/google"
import "./globals.css"
import { AuthProvider } from "@/lib/auth"

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
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  )
}
```

- [ ] **Step 4: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add web/lib/auth.ts web/lib/types.ts web/app/layout.tsx
git commit -m "feat(web): AuthProvider + useAuth hook wired into root layout"
```

---

## Task 16: Update `web/lib/api.ts` — credentials + 401 redirect

**Files:**
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Replace the `apiFetch` helper in `web/lib/api.ts`**

Find the `apiFetch` function in `web/lib/api.ts`. Replace it with:

```ts
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_URL) throw new Error("NO_API")
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
  })
  if (res.status === 401 && typeof window !== "undefined") {
    const redirect = encodeURIComponent(window.location.pathname)
    window.location.href = `/login?redirect=${redirect}`
    throw new Error("API 401")
  }
  if (!res.ok) throw new Error(`API ${res.status}`)
  return res.json()
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/lib/api.ts
git commit -m "feat(web): apiFetch sends credentials + redirects to /login on 401"
```

---

## Task 17: UserMenu component + TopBar integration

**Files:**
- Create: `web/components/user-menu.tsx`
- Modify: `web/components/top-bar.tsx`

- [ ] **Step 1: Create `web/components/user-menu.tsx`**

Write `web/components/user-menu.tsx`:

```tsx
"use client"

import Link from "next/link"
import { LogOut, UserCog } from "lucide-react"
import { useAuth } from "@/lib/auth"

export default function UserMenu() {
  const { user, loading, signOut } = useAuth()

  if (loading || !user) return null

  return (
    <div className="flex items-center gap-2">
      {user.role === "admin" && (
        <Link
          href="/admin/users"
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg
                     border border-gray-300 text-gray-700 hover:bg-gray-50 transition"
        >
          <UserCog className="w-3.5 h-3.5" /> Users
        </Link>
      )}
      <div className="flex items-center gap-2 text-sm">
        <div className="w-7 h-7 rounded-full bg-teal-600 text-white grid place-items-center text-xs font-semibold">
          {(user.name?.[0] ?? user.email[0]).toUpperCase()}
        </div>
        <span className="text-gray-700 max-w-[120px] truncate">
          {user.name ?? user.email}
        </span>
      </div>
      <button
        onClick={signOut}
        title="Sign out"
        className="p-1.5 rounded-lg text-gray-500 hover:text-gray-900 hover:bg-gray-100 transition"
      >
        <LogOut className="w-4 h-4" />
      </button>
    </div>
  )
}
```

- [ ] **Step 2: Render UserMenu in `web/components/top-bar.tsx`**

Read `web/components/top-bar.tsx`. Add the import at the top:

```tsx
import UserMenu from "./user-menu"
```

Find the outermost right-side flex container (the one holding `SearchBar`, `Rescan`, `Score All` buttons) and add `<UserMenu />` as the last child:

```tsx
<div className="flex items-center gap-3">
  <SearchBar onSearch={onSearch} isLoading={isLoading} currentQuery={currentQuery} />
  {/* existing Rescan button */}
  {/* existing Score All button */}
  <UserMenu />
</div>
```

- [ ] **Step 3: Smoke test**

Run: `cd web && npm run dev`
Login as admin → top bar shows "Users" link + avatar + signout icon. Login as rep → same minus "Users" link. Click signout → redirected to `/login`.

- [ ] **Step 4: Commit**

```bash
git add web/components/user-menu.tsx web/components/top-bar.tsx
git commit -m "feat(web): UserMenu in TopBar with admin link + signout"
```

---

## Task 18: Admin Users page

**Files:**
- Create: `web/app/admin/users/page.tsx`

- [ ] **Step 1: Create the page**

Write `web/app/admin/users/page.tsx`:

```tsx
"use client"

import { useState, useEffect, useCallback } from "react"
import Link from "next/link"
import { ArrowLeft, Trash2, Loader2 } from "lucide-react"
import { useAuth } from "@/lib/auth"

interface AdminUser {
  id: string
  email: string
  name: string | null
  role: "admin" | "rep"
  created_at: string
  practices_touched: number
}

export default function AdminUsersPage() {
  const { user, loading: authLoading } = useAuth()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "rep" })

  const API_URL = process.env.NEXT_PUBLIC_API_URL || ""

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/admin/users`, { credentials: "include" })
      if (!res.ok) throw new Error(`${res.status}`)
      const data = await res.json()
      setUsers(data.users)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [API_URL])

  useEffect(() => {
    if (user?.role === "admin") load()
  }, [user, load])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setCreating(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/admin/users`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail ?? `HTTP ${res.status}`)
      }
      setForm({ email: "", name: "", password: "", role: "rep" })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this user?")) return
    const res = await fetch(`${API_URL}/api/admin/users/${id}`, {
      method: "DELETE",
      credentials: "include",
    })
    if (res.ok) load()
    else setError(`HTTP ${res.status}`)
  }

  if (authLoading) return <div className="p-10 text-gray-500">Loading...</div>

  if (user?.role !== "admin") {
    return (
      <div className="min-h-screen bg-cream p-10">
        <p className="text-rose-600 font-medium">Admin only</p>
        <Link href="/" className="text-sm text-teal-700 underline">Back to map</Link>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-cream">
      <header className="sticky top-0 z-20 h-14 flex items-center justify-between px-6 bg-white/70 backdrop-blur-md border-b border-gray-200/50">
        <Link href="/" className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900">
          <ArrowLeft className="w-4 h-4" /> Back to Map
        </Link>
        <span className="font-serif text-lg font-bold text-teal-700">Users</span>
        <span />
      </header>

      <main className="max-w-4xl mx-auto p-8 space-y-8">
        <section>
          <h2 className="font-serif text-xl font-bold mb-4">Create rep</h2>
          <form onSubmit={handleCreate} className="grid grid-cols-2 gap-3 bg-white/80 p-4 rounded-xl">
            <input
              placeholder="Email"
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              required
              className="text-sm rounded-lg border border-gray-200 px-3 py-2"
            />
            <input
              placeholder="Name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              className="text-sm rounded-lg border border-gray-200 px-3 py-2"
            />
            <input
              placeholder="Initial password"
              type="text"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
              minLength={8}
              className="text-sm rounded-lg border border-gray-200 px-3 py-2"
            />
            <select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
              className="text-sm rounded-lg border border-gray-200 px-3 py-2"
            >
              <option value="rep">Rep</option>
              <option value="admin">Admin</option>
            </select>
            <button
              type="submit"
              disabled={creating}
              className="col-span-2 text-sm px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition inline-flex items-center justify-center gap-2"
            >
              {creating && <Loader2 className="w-4 h-4 animate-spin" />}
              {creating ? "Creating..." : "Create user"}
            </button>
          </form>
          {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
        </section>

        <section>
          <h2 className="font-serif text-xl font-bold mb-4">All users</h2>
          {loading ? (
            <p className="text-gray-500">Loading...</p>
          ) : (
            <table className="w-full bg-white/80 rounded-xl text-sm">
              <thead>
                <tr className="text-left text-gray-500 text-xs uppercase tracking-wide">
                  <th className="p-3">Email</th>
                  <th className="p-3">Name</th>
                  <th className="p-3">Role</th>
                  <th className="p-3">Touched</th>
                  <th className="p-3">Created</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-t border-gray-200/50">
                    <td className="p-3">{u.email}</td>
                    <td className="p-3">{u.name ?? "—"}</td>
                    <td className="p-3 capitalize">{u.role}</td>
                    <td className="p-3">{u.practices_touched}</td>
                    <td className="p-3 text-gray-500">{u.created_at.slice(0, 10)}</td>
                    <td className="p-3 text-right">
                      {u.id !== user.id && (
                        <button
                          onClick={() => handleDelete(u.id)}
                          className="text-rose-600 hover:text-rose-800"
                          title="Delete user"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </main>
    </div>
  )
}
```

- [ ] **Step 2: Smoke test**

Run: `cd web && npm run dev`
Login as admin → visit `/admin/users` → see table + create-form. Create a rep → rep appears. Delete rep → gone. Login as rep → same URL → "Admin only" placeholder.

- [ ] **Step 3: Commit**

```bash
git add web/app/admin/users/page.tsx
git commit -m "feat(web): admin users page (list + create + delete)"
```

---

## Task 19: timeAgo helper + attribution fields on Practice type

**Files:**
- Modify: `web/lib/utils.ts`
- Modify: `web/lib/types.ts`

- [ ] **Step 1: Add `timeAgo` to `web/lib/utils.ts`**

Read `web/lib/utils.ts`. Append:

```ts
export function timeAgo(iso: string | null): string {
  if (!iso) return ""
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  if (hrs < 48) return "yesterday"
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" })
}
```

- [ ] **Step 2: Add attribution fields to `Practice` in `web/lib/types.ts`**

Find the `Practice` interface. Add these three fields inside it:

```ts
last_touched_by: string | null
last_touched_by_name: string | null
last_touched_at: string | null
```

- [ ] **Step 3: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/lib/utils.ts web/lib/types.ts
git commit -m "feat(web): timeAgo helper + attribution fields on Practice type"
```

---

## Task 20: Render last-touched on practice card + call prep header

**Files:**
- Modify: `web/components/practice-card.tsx`
- Modify: `web/app/practice/[place_id]/page.tsx`

- [ ] **Step 1: Add the line on practice card**

Read `web/components/practice-card.tsx`. Add the `timeAgo` import:

```tsx
import { timeAgo } from "@/lib/utils"
```

Find the block that renders the status/score row (search for `<StatusBadge` and `ScoreBadge`). Immediately below that block — but still inside the card's top-level `<div>` and before the address `<p>` — add:

```tsx
{practice.last_touched_by_name && practice.last_touched_at && (
  <p className="text-[11px] text-gray-400 mt-1">
    Last touched by {practice.last_touched_by_name} · {timeAgo(practice.last_touched_at)}
  </p>
)}
```

- [ ] **Step 2: Add the line in the Call Prep header**

Read `web/app/practice/[place_id]/page.tsx`. Add import:

```tsx
import { timeAgo } from "@/lib/utils"
```

Find the header's status section (the div that contains `<select>` and `<StatusBadge />`). Replace it with a wrapper that includes the attribution line:

```tsx
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
  {practice.last_touched_by_name && practice.last_touched_at && (
    <span className="text-xs text-gray-400">
      by {practice.last_touched_by_name} · {timeAgo(practice.last_touched_at)}
    </span>
  )}
</div>
```

- [ ] **Step 3: Smoke test**

Run: `cd web && npm run dev`
Login as rep → search for a practice → click Analyze → card should now show "Last touched by <name> · just now". Open Call Prep → header shows "by <name> · just now" next to status badge.

- [ ] **Step 4: Commit**

```bash
git add web/components/practice-card.tsx web/app/practice/[place_id]/page.tsx
git commit -m "feat(web): render last-touched attribution on card + call prep header"
```

---

## Task 21: End-to-end smoke test

**Files:** none (manual verification)

- [ ] **Step 1: Apply schema migration if not already done**

Supabase dashboard → SQL editor → paste everything from `supabase/schema.sql` → Run. Verify tables + trigger + columns.

- [ ] **Step 2: Configure env vars**

In `.env` at repo root:
```
SUPABASE_URL=...
SUPABASE_KEY=<anon>
SUPABASE_SERVICE_ROLE_KEY=<service_role>
BOOTSTRAP_ADMIN_EMAIL=admin@apexvirtuals.com
BOOTSTRAP_ADMIN_PASSWORD=<temp-password>
OPENAI_API_KEY=...
```

In `web/.env.local`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
```

- [ ] **Step 2: Start both servers**

Terminal 1: `uvicorn api.index:app --reload --port 8000`
Expected log: `[bootstrap] Seeded admin: admin@apexvirtuals.com`

Terminal 2: `cd web && npm run dev`

- [ ] **Step 3: Walk through the flow**

- Visit `http://localhost:3000` → redirected to `/login?redirect=%2F`.
- Enter bootstrap admin creds → redirected to `/`.
- TopBar shows avatar + "Users" link + signout.
- Click "Users" → admin users page loads.
- Create a rep (email, name, password, role=rep).
- Sign out, log in as rep → map loads, no "Users" link visible.
- Search for a practice, click Analyze on a card → card shows "Last touched by <rep name> · just now".
- Open Call Prep → header shows "by <rep name> · just now".
- Change status → attribution line updates.
- Sign out → redirected to `/login`.
- Visit `/admin/users` as rep (directly) → "Admin only" placeholder renders.

- [ ] **Step 4: Verify DB state**

In Supabase SQL editor:
```sql
select p.name, u.name as touched_by, p.last_touched_at
from practices p
left join profiles u on u.id = p.last_touched_by
where p.last_touched_by is not null
order by p.last_touched_at desc
limit 10;
```
Expected: rows for every practice you touched, with correct rep name and recent timestamp.

- [ ] **Step 5: Run all tests one more time**

Run: `pytest -v && cd web && npx tsc --noEmit`
Expected: all pass, no type errors.

- [ ] **Step 6: Final commit**

No code changes at this task — just verification. If everything passes, this feature is done.

---

## Done criteria

- Every `/api/practices/*` and `/api/admin/*` route returns 401 without a valid session.
- Login works via the `/login` page; sign-out clears the session.
- `last_touched_by` + `last_touched_at` stamped on all mutating actions (search upsert, analyze, rescan, script gen/regen, PATCH status/notes).
- Card + Call Prep header show "Last touched by ..." when attribution is present.
- Admin can list + create + delete + reset password for users via `/admin/users`.
- Non-admin hitting any admin route (backend or frontend) sees a 403 or "Admin only" placeholder.
- Bootstrap admin seeded on first startup when env vars are set.
- `pytest -v` passes all backend tests; `npx tsc --noEmit` passes in `web/`.
