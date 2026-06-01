# Email Outreach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-practice email outreach from the Call Prep page — GPT-generated draft (cached), send via Microsoft Graph as a shared mailbox, on-demand reply polling, status auto-advance, every message attributed to the sender.

**Architecture:** Microsoft Graph API for send + mailbox read (v1 is single-mailbox `MS_SENDER_EMAIL`, with refresh-token OAuth). `email_messages` table captures every outbound/inbound message. Frontend Call Prep page gains a tabbed right column (Notes | Email | Activity-stub). All email endpoints return 503 if MS vars aren't configured, so the app still runs without Azure AD setup.

**Tech Stack:** FastAPI, httpx, pytest, OpenAI Python SDK, Next.js 14 App Router, TypeScript, Tailwind. Microsoft Graph v1.0, OAuth2 refresh-token flow.

**Reference spec:** [docs/specs/2026-04-22-email-outreach-design.md](../../specs/2026-04-22-email-outreach-design.md)

**Depends on:** The auth + attribution plan (already implemented). Uses `get_current_user`, `require_admin`, `last_touched_by` stamping, `profiles` table.

---

## File Structure

```
hv-sales-intel/
├── supabase/schema.sql           (modify) — email fields + email_messages table
├── src/
│   ├── settings.py               (modify) — MS_* + EMAIL_REPLY_LOOKBACK_DAYS
│   ├── ms_auth.py                (create) — MS access token cache + refresh
│   ├── email_send.py             (create) — Graph sendMail + retrieve Message-ID
│   ├── email_gen.py              (create) — GPT draft generator + mock
│   ├── email_poll.py             (create) — Graph message list + threading
│   ├── storage.py                (modify) — email-related helpers
│   └── analyzer.py               (modify) — clear email_draft on re-analysis
├── api/index.py                  (modify) — 7 new email endpoints + PATCH accepts email
├── scripts/ms_auth_bootstrap.py  (create) — one-time OAuth consent flow
├── tests/
│   ├── test_ms_auth.py           (create)
│   ├── test_email_send.py        (create)
│   ├── test_email_gen.py         (create)
│   ├── test_email_poll.py        (create)
│   ├── test_storage_email.py     (create) — email helper functions
│   └── test_api_email.py         (create) — endpoint auth + 503 branches
├── web/
│   ├── lib/
│   │   ├── types.ts              (modify) — EmailMessage, EmailDraft, Practice fields
│   │   ├── api.ts                (modify) — 7 email helpers + updatePracticeEmail
│   │   └── mock-data.ts          (modify) — email-related nulls on mock rows
│   ├── components/
│   │   ├── actions-panel.tsx     (create) — tabs shell (Notes | Email | Activity)
│   │   ├── email-panel.tsx       (create) — Email tab root
│   │   ├── email-recipient.tsx   (create) — inline-edit practice.email
│   │   ├── email-composer.tsx    (create) — subject/body + Regen/Save/Send
│   │   └── email-thread.tsx      (create) — message list, expandable
│   └── app/practice/[place_id]/page.tsx  (modify) — use ActionsPanel
├── .env.example                  (modify) — document MS_* + EMAIL_REPLY_LOOKBACK_DAYS
```

---

## Task 1: DB migration — email fields + email_messages table

**Files:**
- Modify: `supabase/schema.sql`

- [ ] **Step 1: Append email migration to `supabase/schema.sql`**

Append this block to the end of the file:

```sql
-- Email outreach

alter table practices add column if not exists email text;
alter table practices add column if not exists email_draft text;
alter table practices add column if not exists email_draft_updated_at timestamptz;

create table if not exists email_messages (
  id bigserial primary key,
  practice_id bigint not null references practices(id) on delete cascade,
  user_id uuid references profiles(id),
  direction text not null check (direction in ('out', 'in')),
  subject text,
  body text,
  message_id text,
  in_reply_to text,
  sent_at timestamptz default now(),
  error text
);

create index if not exists idx_email_messages_practice
  on email_messages (practice_id, sent_at desc);
create index if not exists idx_email_messages_message_id
  on email_messages (message_id);
```

- [ ] **Step 2: Apply migration to Supabase**

Paste the new block into the Supabase SQL editor and run. Verify:
- `practices` has 3 new columns (`email`, `email_draft`, `email_draft_updated_at`).
- `email_messages` table exists with all 10 columns and 2 indexes.

- [ ] **Step 3: Commit**

```bash
git add supabase/schema.sql
git commit -m "feat(schema): add email columns on practices + email_messages table"
```

---

## Task 2: Settings + env vars + bootstrap script

**Files:**
- Modify: `src/settings.py`
- Modify: `.env.example`
- Create: `scripts/ms_auth_bootstrap.py`

- [ ] **Step 1: Extend `src/settings.py`**

Replace the Settings class body in `src/settings.py` (keep imports and trailing `settings = Settings()`):

```python
class Settings(BaseSettings):
    google_maps_api_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""                    # anon key (legacy name preserved)
    supabase_service_role_key: str = ""       # admin client for auth verification
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Bootstrap admin (seeded on startup if profiles has zero admins)
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # Microsoft Graph (email outreach)
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_refresh_token: str = ""
    ms_sender_email: str = ""
    email_reply_lookback_days: int = 30

    class Config:
        env_file = ".env"
        extra = "ignore"
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
MS_TENANT_ID=
MS_CLIENT_ID=
MS_CLIENT_SECRET=
MS_REFRESH_TOKEN=
MS_SENDER_EMAIL=
EMAIL_REPLY_LOOKBACK_DAYS=30
NEXT_PUBLIC_RINGCENTRAL_WEB_APP_URL=https://app.ringcentral.com
```

- [ ] **Step 3: Create `scripts/ms_auth_bootstrap.py`**

```python
"""One-time Microsoft Graph OAuth consent flow.

Exchanges an authorization code for a refresh token. Run once per
environment; copy the printed refresh token into MS_REFRESH_TOKEN in .env.

Usage:
    MS_TENANT_ID=... MS_CLIENT_ID=... MS_CLIENT_SECRET=... \\
        python scripts/ms_auth_bootstrap.py
"""
import os
import sys
import webbrowser
from urllib.parse import urlencode

import httpx


SCOPES = "Mail.Send Mail.Read offline_access"
REDIRECT_URI = "http://localhost:8910/callback"


def main() -> None:
    tenant_id = os.environ.get("MS_TENANT_ID")
    client_id = os.environ.get("MS_CLIENT_ID")
    client_secret = os.environ.get("MS_CLIENT_SECRET")
    if not (tenant_id and client_id and client_secret):
        print("MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET required.", file=sys.stderr)
        sys.exit(1)

    authorize = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?"
        + urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "response_mode": "query",
            "scope": SCOPES,
        })
    )

    print(f"\n1) Register a local redirect at {REDIRECT_URI} in Azure AD.")
    print("2) Opening browser for consent. Sign in as the shared sender account.")
    print(f"\nAuthorize URL:\n{authorize}\n")
    webbrowser.open(authorize)

    code = input("After consent, paste the `code` query param from the redirect URL: ").strip()

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    resp = httpx.post(token_url, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "scope": SCOPES,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        print(f"No refresh_token in response: {data}", file=sys.stderr)
        sys.exit(1)

    print("\n=== SUCCESS ===")
    print("Copy this into .env as MS_REFRESH_TOKEN:\n")
    print(refresh_token)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Smoke check**

Run: `python -c "from src.settings import settings; print(settings.ms_sender_email, settings.email_reply_lookback_days)"`
Expected: prints empty string and 30 (defaults).

- [ ] **Step 5: Commit**

```bash
git add src/settings.py .env.example scripts/ms_auth_bootstrap.py
git commit -m "feat(settings): add Microsoft Graph env vars + OAuth bootstrap script"
```

---

## Task 3: `src/ms_auth.py` — access token cache + refresh

**Files:**
- Create: `src/ms_auth.py`
- Create: `tests/test_ms_auth.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ms_auth.py`:

```python
import time
from unittest.mock import AsyncMock, patch

import pytest

from src import ms_auth


@pytest.fixture(autouse=True)
def reset_cache():
    ms_auth._cached_token = None
    ms_auth._cached_expires_at = 0.0
    yield
    ms_auth._cached_token = None
    ms_auth._cached_expires_at = 0.0


@pytest.mark.asyncio
async def test_fetches_token_when_cache_empty():
    fake_post = AsyncMock()
    fake_post.return_value.json = lambda: {"access_token": "tok", "expires_in": 3600}
    fake_post.return_value.raise_for_status = lambda: None

    with patch("src.ms_auth.settings") as s:
        s.ms_tenant_id = "t"
        s.ms_client_id = "c"
        s.ms_client_secret = "s"
        s.ms_refresh_token = "r"
        with patch("src.ms_auth.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__.return_value.post = fake_post
            token = await ms_auth.get_access_token()

    assert token == "tok"
    assert ms_auth._cached_token == "tok"
    assert ms_auth._cached_expires_at > time.time()


@pytest.mark.asyncio
async def test_reuses_cached_token():
    ms_auth._cached_token = "cached"
    ms_auth._cached_expires_at = time.time() + 1000
    token = await ms_auth.get_access_token()
    assert token == "cached"


@pytest.mark.asyncio
async def test_raises_when_not_configured():
    with patch("src.ms_auth.settings") as s:
        s.ms_tenant_id = ""
        s.ms_client_id = ""
        s.ms_client_secret = ""
        s.ms_refresh_token = ""
        with pytest.raises(RuntimeError, match="not configured"):
            await ms_auth.get_access_token()
```

- [ ] **Step 2: Verify tests fail**

Run: `python -m pytest tests/test_ms_auth.py -v`
Expected: ModuleNotFoundError for `src.ms_auth`.

- [ ] **Step 3: Create `src/ms_auth.py`**

```python
import asyncio
import time

import httpx

from src.settings import settings


_cached_token: str | None = None
_cached_expires_at: float = 0.0
_lock = asyncio.Lock()


async def get_access_token() -> str:
    """Return a fresh Microsoft Graph access token. Cached across calls.

    Exchanges the refresh token when no valid token is cached or when the
    cached token expires in < 60 seconds.
    """
    global _cached_token, _cached_expires_at

    if not (
        settings.ms_tenant_id
        and settings.ms_client_id
        and settings.ms_client_secret
        and settings.ms_refresh_token
    ):
        raise RuntimeError("Microsoft Graph not configured")

    if _cached_token and time.time() < _cached_expires_at - 60:
        return _cached_token

    async with _lock:
        if _cached_token and time.time() < _cached_expires_at - 60:
            return _cached_token

        url = f"https://login.microsoftonline.com/{settings.ms_tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": settings.ms_client_id,
            "client_secret": settings.ms_client_secret,
            "refresh_token": settings.ms_refresh_token,
            "grant_type": "refresh_token",
            "scope": "Mail.Send Mail.Read offline_access",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, data=data)
            resp.raise_for_status()
        payload = resp.json()

        _cached_token = payload["access_token"]
        _cached_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return _cached_token
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_ms_auth.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ms_auth.py tests/test_ms_auth.py
git commit -m "feat(email): add Microsoft Graph token cache + refresh helper"
```

---

## Task 4: `src/email_send.py` — Graph sendMail

**Files:**
- Create: `src/email_send.py`
- Create: `tests/test_email_send.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_email_send.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src import email_send


@pytest.mark.asyncio
async def test_send_email_posts_to_graph_and_retrieves_message_id():
    send_resp = AsyncMock()
    send_resp.status_code = 202
    send_resp.raise_for_status = lambda: None

    sent_items_resp = AsyncMock()
    sent_items_resp.status_code = 200
    sent_items_resp.raise_for_status = lambda: None
    sent_items_resp.json = lambda: {
        "value": [{
            "internetMessageId": "<msg-123@host>",
            "subject": "Hello",
            "sentDateTime": "2026-04-22T10:00:00Z",
            "toRecipients": [{"emailAddress": {"address": "to@example.com"}}],
        }]
    }

    client = AsyncMock()
    client.post = AsyncMock(return_value=send_resp)
    client.get = AsyncMock(return_value=sent_items_resp)

    with patch("src.email_send.get_access_token", return_value="tok"):
        with patch("src.email_send.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__.return_value = client
            result = await email_send.send_email("to@example.com", "Hello", "Body text")

    assert result["message_id"] == "<msg-123@host>"
    assert "sent_at" in result

    call = client.post.call_args
    assert "sendMail" in call.args[0]
    payload = call.kwargs["json"]
    assert payload["message"]["subject"] == "Hello"
    assert payload["message"]["toRecipients"][0]["emailAddress"]["address"] == "to@example.com"
    assert payload["message"]["body"]["content"] == "Body text"


@pytest.mark.asyncio
async def test_send_email_raises_on_graph_error():
    def raise_http():
        import httpx
        raise httpx.HTTPStatusError("401", request=None, response=None)

    resp = AsyncMock()
    resp.raise_for_status = raise_http

    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)

    with patch("src.email_send.get_access_token", return_value="tok"):
        with patch("src.email_send.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__.return_value = client
            with pytest.raises(Exception):
                await email_send.send_email("to@example.com", "s", "b")
```

- [ ] **Step 2: Verify fails**

Run: `python -m pytest tests/test_email_send.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Create `src/email_send.py`**

```python
from datetime import datetime, timezone

import httpx

from src.ms_auth import get_access_token

GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
GRAPH_SENT_ITEMS_URL = (
    "https://graph.microsoft.com/v1.0/me/mailFolders/sentitems/messages"
    "?$top=5&$orderby=sentDateTime desc"
    "&$select=internetMessageId,subject,sentDateTime,toRecipients"
)


async def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email via Microsoft Graph sendMail.

    v1 sends from MS_SENDER_EMAIL (the mailbox associated with the refresh
    token). No per-user `from` or `reply-to` override — keeps replies routed
    to the shared mailbox the poll reads.

    Returns { message_id, sent_at }. Raises on failure.
    """
    token = await get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        send_resp = await client.post(GRAPH_SEND_URL, headers=headers, json=payload)
        send_resp.raise_for_status()

        # sendMail returns 202 Accepted with no body. Fetch the sent items
        # to find the newly sent message's internetMessageId.
        sent_resp = await client.get(GRAPH_SENT_ITEMS_URL, headers=headers)
        sent_resp.raise_for_status()

    items = sent_resp.json().get("value", [])
    match = _match_sent_message(items, to=to, subject=subject)

    return {
        "message_id": match.get("internetMessageId") if match else None,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


def _match_sent_message(items: list[dict], to: str, subject: str) -> dict | None:
    """Find the first sent item matching the recipient + subject."""
    for item in items:
        if item.get("subject") != subject:
            continue
        recipients = [
            r.get("emailAddress", {}).get("address", "").lower()
            for r in item.get("toRecipients", [])
        ]
        if to.lower() in recipients:
            return item
    return items[0] if items else None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_email_send.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/email_send.py tests/test_email_send.py
git commit -m "feat(email): add Graph sendMail wrapper with sent-items message-id lookup"
```

---

## Task 5: `src/email_gen.py` — GPT draft + mock fallback

**Files:**
- Create: `src/email_gen.py`
- Create: `tests/test_email_gen.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_email_gen.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import email_gen


@pytest.mark.asyncio
async def test_generate_email_draft_mock_when_no_openai_key():
    with patch("src.email_gen.settings") as s:
        s.openai_api_key = ""
        result = await email_gen.generate_email_draft(
            name="Bright Smiles Dental",
            category="dental",
            summary=None,
            pain_points=None,
            sales_angles=None,
        )
    assert "subject" in result
    assert "body" in result
    assert "Bright Smiles Dental" in result["body"] or "Bright Smiles Dental" in result["subject"]


@pytest.mark.asyncio
async def test_generate_email_draft_with_gpt():
    fake_content = json.dumps({"subject": "Staffing support for your practice", "body": "Hi there..."})
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=fake_content))]

    create_mock = AsyncMock(return_value=response)
    client = MagicMock()
    client.chat.completions.create = create_mock

    with patch("src.email_gen.settings") as s:
        s.openai_api_key = "sk-test"
        s.openai_model = "gpt-4o"
        with patch("src.email_gen.AsyncOpenAI", return_value=client):
            result = await email_gen.generate_email_draft(
                name="Test Clinic",
                category="dental",
                summary="Summary",
                pain_points='["pain 1"]',
                sales_angles='["angle 1"]',
            )

    assert result == {"subject": "Staffing support for your practice", "body": "Hi there..."}


@pytest.mark.asyncio
async def test_generate_email_draft_falls_back_on_gpt_error():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=Exception("boom"))

    with patch("src.email_gen.settings") as s:
        s.openai_api_key = "sk-test"
        s.openai_model = "gpt-4o"
        with patch("src.email_gen.AsyncOpenAI", return_value=client):
            result = await email_gen.generate_email_draft(
                name="Fallback Clinic", category="dental",
                summary=None, pain_points=None, sales_angles=None,
            )
    assert "subject" in result
    assert "body" in result
```

- [ ] **Step 2: Verify fails**

Run: `python -m pytest tests/test_email_gen.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Create `src/email_gen.py`**

```python
import json

from openai import AsyncOpenAI

from src.settings import settings

SYSTEM_PROMPT = """You are a cold outreach email writer for Apex & Virtuals, a healthcare staffing and talent acquisition company.

Given information about a healthcare practice (name, category, analysis summary, pain points, sales angles), write a short personalized cold email (80-140 words) to the practice from a Apex & Virtuals rep.

Reference ONE specific pain point and ONE specific sales angle from the analysis. End with a clear ask: a 15-minute call.

Return ONLY valid JSON with this exact structure, no other text:
{
  "subject": "a concise subject line (under 70 chars)",
  "body": "the email body as plain text with paragraph breaks as \\n\\n"
}

Tone: warm, direct, not pushy. First person ("I", "we at Apex & Virtuals")."""


async def generate_email_draft(
    name: str,
    category: str | None,
    summary: str | None,
    pain_points: str | None,
    sales_angles: str | None,
) -> dict:
    """Return {subject, body}. Uses GPT if OPENAI_API_KEY set, mock otherwise."""
    if not settings.openai_api_key:
        return _mock_draft(name, category)

    user_prompt = f"""Write a cold outreach email for this practice:

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
        if "subject" in result and "body" in result:
            return {"subject": result["subject"], "body": result["body"]}
    except Exception:
        pass

    return _mock_draft(name, category)


def _mock_draft(name: str, category: str | None) -> dict:
    cat = (category or "healthcare").replace("_", " ")
    return {
        "subject": f"Staffing support for {name}",
        "body": (
            f"Hi there,\n\n"
            f"I'm reaching out from Apex & Virtuals — we specialize in staffing "
            f"for {cat} practices. I noticed {name} could benefit from front-desk "
            f"or admin support, and wanted to introduce myself.\n\n"
            f"We place pre-vetted healthcare staff (front desk, medical assistants, "
            f"admin VAs) within 48 hours. Most clients see scheduling delays drop "
            f"meaningfully in the first month.\n\n"
            f"Would a 15-minute call this week work to explore whether we'd be "
            f"a fit for your practice?\n\n"
            f"Best,\n"
            f"[Your Name]\n"
            f"Apex & Virtuals"
        ),
    }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_email_gen.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/email_gen.py tests/test_email_gen.py
git commit -m "feat(email): add GPT-powered email draft generator with mock fallback"
```

---

## Task 6: `src/email_poll.py` — Graph reply polling

**Files:**
- Create: `src/email_poll.py`
- Create: `tests/test_email_poll.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_email_poll.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src import email_poll


FAKE_GRAPH_RESPONSE = {
    "value": [
        {
            "internetMessageId": "<reply-1@host>",
            "subject": "Re: Staffing",
            "from": {"emailAddress": {"address": "dr@practice.com"}},
            "toRecipients": [{"emailAddress": {"address": "sales@hv.com"}}],
            "sentDateTime": "2026-04-22T10:00:00Z",
            "receivedDateTime": "2026-04-22T10:00:01Z",
            "body": {"contentType": "text", "content": "Yes, interested."},
            "internetMessageHeaders": [
                {"name": "In-Reply-To", "value": "<outbound-1@hv.com>"},
            ],
        },
        {
            # Not threaded — matches by envelope sender
            "internetMessageId": "<reply-2@host>",
            "subject": "Question",
            "from": {"emailAddress": {"address": "dr@practice.com"}},
            "toRecipients": [{"emailAddress": {"address": "sales@hv.com"}}],
            "sentDateTime": "2026-04-22T11:00:00Z",
            "receivedDateTime": "2026-04-22T11:00:01Z",
            "body": {"contentType": "text", "content": "One more question."},
            "internetMessageHeaders": [],
        },
    ]
}


@pytest.mark.asyncio
async def test_poll_replies_threads_by_in_reply_to_and_by_envelope():
    get_resp = AsyncMock()
    get_resp.raise_for_status = lambda: None
    get_resp.json = lambda: FAKE_GRAPH_RESPONSE

    client = AsyncMock()
    client.get = AsyncMock(return_value=get_resp)

    with patch("src.email_poll.get_access_token", return_value="tok"):
        with patch("src.email_poll.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__.return_value = client
            results = await email_poll.poll_replies(
                practice_email="dr@practice.com",
                outbound_message_ids=["<outbound-1@hv.com>"],
                since_iso="2026-04-22T00:00:00Z",
            )

    assert len(results) == 2
    assert results[0]["message_id"] == "<reply-1@host>"
    assert results[0]["in_reply_to"] == "<outbound-1@hv.com>"
    assert results[1]["message_id"] == "<reply-2@host>"
    # Envelope-match fallback
    assert results[1]["in_reply_to"] is None


@pytest.mark.asyncio
async def test_poll_replies_returns_empty_when_no_new_messages():
    get_resp = AsyncMock()
    get_resp.raise_for_status = lambda: None
    get_resp.json = lambda: {"value": []}
    client = AsyncMock()
    client.get = AsyncMock(return_value=get_resp)

    with patch("src.email_poll.get_access_token", return_value="tok"):
        with patch("src.email_poll.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__.return_value = client
            results = await email_poll.poll_replies(
                practice_email="dr@practice.com",
                outbound_message_ids=[],
                since_iso="2026-04-22T00:00:00Z",
            )
    assert results == []
```

- [ ] **Step 2: Verify fails**

Run: `python -m pytest tests/test_email_poll.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Create `src/email_poll.py`**

```python
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from src.ms_auth import get_access_token


GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
SELECT_FIELDS = (
    "id,subject,body,from,toRecipients,sentDateTime,"
    "receivedDateTime,internetMessageId,internetMessageHeaders"
)


async def poll_replies(
    practice_email: str,
    outbound_message_ids: list[str],
    since_iso: str,
) -> list[dict]:
    """Fetch inbound messages from `practice_email` since `since_iso`.

    Returns a list of dicts ready to insert into `email_messages`:
    { message_id, in_reply_to, subject, body, sent_at }.

    Threading: if any outbound message_id appears in the message's
    In-Reply-To or References header, link it. Otherwise envelope-match
    on sender address.
    """
    token = await get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Graph OData filter
    addr = practice_email.replace("'", "''")
    filter_expr = (
        f"receivedDateTime ge {since_iso} "
        f"and from/emailAddress/address eq '{addr}'"
    )
    params = {
        "$filter": filter_expr,
        "$select": SELECT_FIELDS,
        "$orderby": "receivedDateTime desc",
        "$top": "50",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(GRAPH_MESSAGES_URL, headers=headers, params=params)
        resp.raise_for_status()

    outbound_set = set(outbound_message_ids)
    results: list[dict] = []
    for msg in resp.json().get("value", []):
        in_reply_to = _extract_threading_parent(msg, outbound_set)
        body_text = _extract_plain_body(msg.get("body", {}))
        results.append({
            "message_id": msg.get("internetMessageId"),
            "in_reply_to": in_reply_to,
            "subject": msg.get("subject"),
            "body": body_text,
            "sent_at": msg.get("receivedDateTime") or msg.get("sentDateTime"),
        })
    return results


def _extract_threading_parent(msg: dict, outbound_set: set[str]) -> str | None:
    """Return the outbound message_id this reply threads to, if any."""
    for header in msg.get("internetMessageHeaders", []) or []:
        name = (header.get("name") or "").lower()
        value = header.get("value") or ""
        if name in ("in-reply-to", "references"):
            # References header may contain multiple space-separated ids
            for candidate in value.split():
                candidate = candidate.strip()
                if candidate in outbound_set:
                    return candidate
    return None


def _extract_plain_body(body: dict) -> str:
    content = body.get("content") or ""
    content_type = (body.get("contentType") or "").lower()
    if content_type == "html":
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    return content
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_email_poll.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/email_poll.py tests/test_email_poll.py
git commit -m "feat(email): add Graph reply polling with threading + envelope fallback"
```

---

## Task 7: Storage email helpers

**Files:**
- Modify: `src/storage.py`
- Create: `tests/test_storage_email.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_storage_email.py`:

```python
from unittest.mock import MagicMock, patch

from src.storage import (
    insert_email_message,
    list_email_messages,
    list_outbound_message_ids,
)


def _make_client_with(insert_data=None, select_data=None):
    client = MagicMock()
    table = MagicMock()
    table.insert.return_value = table
    table.select.return_value = table
    table.eq.return_value = table
    table.order.return_value = table
    table.execute.return_value = MagicMock(data=insert_data if insert_data is not None else select_data)
    client.table.return_value = table
    return client, table


def test_insert_email_message_happy_path():
    client, table = _make_client_with(insert_data=[{"id": 1, "practice_id": 5}])
    with patch("src.storage._get_client", return_value=client):
        result = insert_email_message(
            practice_id=5,
            user_id="user-uuid",
            direction="out",
            subject="Hello",
            body="...",
            message_id="<m@h>",
            in_reply_to=None,
            error=None,
        )
    assert result == {"id": 1, "practice_id": 5}
    insert_arg = table.insert.call_args.args[0]
    assert insert_arg["practice_id"] == 5
    assert insert_arg["direction"] == "out"
    assert insert_arg["message_id"] == "<m@h>"


def test_list_email_messages_returns_rows():
    rows = [{"id": 1, "direction": "out"}, {"id": 2, "direction": "in"}]
    client, _ = _make_client_with(select_data=rows)
    with patch("src.storage._get_client", return_value=client):
        result = list_email_messages(5)
    assert result == rows


def test_list_outbound_message_ids():
    rows = [{"message_id": "<a>"}, {"message_id": "<b>"}, {"message_id": None}]
    client, _ = _make_client_with(select_data=rows)
    with patch("src.storage._get_client", return_value=client):
        result = list_outbound_message_ids(5)
    assert result == ["<a>", "<b>"]
```

- [ ] **Step 2: Verify fails**

Run: `python -m pytest tests/test_storage_email.py -v`
Expected: ImportError on missing functions.

- [ ] **Step 3: Append three helpers to `src/storage.py`**

Add these functions at the end of `src/storage.py`:

```python
def insert_email_message(
    practice_id: int,
    user_id: str | None,
    direction: str,
    subject: str | None,
    body: str | None,
    message_id: str | None,
    in_reply_to: str | None,
    error: str | None,
) -> dict | None:
    """Insert a row into email_messages. Returns the inserted row."""
    client = _get_client()
    if not client:
        return None
    row = {
        "practice_id": practice_id,
        "user_id": user_id,
        "direction": direction,
        "subject": subject,
        "body": body,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "error": error,
    }
    result = client.table("email_messages").insert(row).execute()
    return result.data[0] if result.data else None


def list_email_messages(practice_id: int) -> list[dict]:
    """List email messages for a practice, oldest first."""
    client = _get_client()
    if not client:
        return []
    result = (
        client.table("email_messages").select("*")
        .eq("practice_id", practice_id)
        .order("sent_at")
        .execute()
    )
    return result.data or []


def list_outbound_message_ids(practice_id: int) -> list[str]:
    """Return all outbound message_ids for a practice (used by poll threading)."""
    client = _get_client()
    if not client:
        return []
    result = (
        client.table("email_messages").select("message_id")
        .eq("practice_id", practice_id)
        .eq("direction", "out")
        .execute()
    )
    return [r["message_id"] for r in (result.data or []) if r.get("message_id")]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_storage_email.py -v`
Expected: 3 PASS.

Also run full suite: `python -m pytest -q`. Expect everything still green.

- [ ] **Step 5: Commit**

```bash
git add src/storage.py tests/test_storage_email.py
git commit -m "feat(storage): add email_messages insert + list + outbound-ids helpers"
```

---

## Task 8: Analyzer invalidation — clear email_draft on re-analysis

**Files:**
- Modify: `src/analyzer.py`

- [ ] **Step 1: Review current analyzer output shape**

In `src/analyzer.py`, `analyze_practice` returns a dict with `summary`, `pain_points`, `sales_angles`, `lead_score`, `urgency_score`, `hiring_signal_score`, `call_script`. Add `email_draft` and `email_draft_updated_at` to the return.

Open `src/analyzer.py` and find the `return {` block at the end of `analyze_practice`.

Replace:

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

With:

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
    }
```

Also update `_mock_analysis` at the bottom of the same file. Find its return dict and add the two fields the same way:

```python
    return {
        "summary": f"{name} shows signs of staffing challenges typical of {cat.replace('_', ' ')} practices. Review analysis and website signals suggest opportunities for Apex & Virtuals staffing services.",
        "pain_points": json.dumps(selected_pains),
        "sales_angles": json.dumps(selected_angles),
        "lead_score": lead,
        "urgency_score": urgency,
        "hiring_signal_score": hiring,
        "call_script": None,
        "email_draft": None,
        "email_draft_updated_at": None,
    }
```

- [ ] **Step 2: Verify suite still passes**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/analyzer.py
git commit -m "feat(analyzer): clear email_draft on re-analysis (same pattern as call_script)"
```

---

## Task 9: API — email draft endpoints (GET, POST, PATCH)

**Files:**
- Modify: `api/index.py`
- Create: `tests/test_api_email.py`

- [ ] **Step 1: Write failing tests for auth-required on draft endpoints**

Create `tests/test_api_email.py`:

```python
from fastapi.testclient import TestClient

from api.index import app

client = TestClient(app)


def test_email_draft_get_requires_auth():
    resp = client.get("/api/practices/some_id/email/draft")
    assert resp.status_code == 401


def test_email_draft_post_requires_auth():
    resp = client.post("/api/practices/some_id/email/draft")
    assert resp.status_code == 401


def test_email_draft_patch_requires_auth():
    resp = client.patch("/api/practices/some_id/email/draft", json={"subject": "x"})
    assert resp.status_code == 401


def test_email_send_requires_auth():
    resp = client.post("/api/practices/some_id/email/send")
    assert resp.status_code == 401


def test_email_messages_requires_auth():
    resp = client.get("/api/practices/some_id/email/messages")
    assert resp.status_code == 401


def test_email_poll_requires_auth():
    resp = client.post("/api/practices/some_id/email/poll")
    assert resp.status_code == 401


def test_email_mark_replied_requires_auth():
    resp = client.post("/api/practices/some_id/email/mark-replied")
    assert resp.status_code == 401
```

- [ ] **Step 2: Verify tests fail**

Run: `python -m pytest tests/test_api_email.py -v`
Expected: all 7 FAIL with 404 (endpoints don't exist yet).

- [ ] **Step 3: Add endpoints to `api/index.py`**

Open `api/index.py`. At the top of the existing import block, extend imports from `src.storage`:

Find:
```python
from src.storage import (
    get_practice,
    query_practices,
    update_practice_analysis,
    update_practice_fields,
    upsert_practices,
)
```

Replace with:
```python
from src.storage import (
    get_practice,
    insert_email_message,
    list_email_messages,
    list_outbound_message_ids,
    query_practices,
    update_practice_analysis,
    update_practice_fields,
    upsert_practices,
)
```

Add imports for the email modules (put below the existing `from src.*` block):
```python
from datetime import datetime, timedelta, timezone

from src.email_gen import generate_email_draft
from src.email_poll import poll_replies
from src.email_send import send_email
from src.settings import settings as app_settings
```

Then add this block BEFORE the existing `def _strip_joined(row: dict) -> dict:` helper:

```python
# ======================= Email outreach endpoints =======================

def _email_configured() -> bool:
    return bool(
        app_settings.ms_tenant_id
        and app_settings.ms_client_id
        and app_settings.ms_client_secret
        and app_settings.ms_refresh_token
        and app_settings.ms_sender_email
    )


class EmailDraftPatch(BaseModel):
    subject: str | None = None
    body: str | None = None


def _parse_draft(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


@app.get("/api/practices/{place_id}/email/draft")
async def get_email_draft_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    cached = _parse_draft(practice.get("email_draft"))
    if cached:
        return cached

    draft = await generate_email_draft(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
    )
    update_practice_fields(
        place_id,
        {
            "email_draft": json.dumps(draft),
            "email_draft_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        touched_by=user["id"],
    )
    return draft


@app.post("/api/practices/{place_id}/email/draft")
async def regenerate_email_draft_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    draft = await generate_email_draft(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
    )
    update_practice_fields(
        place_id,
        {
            "email_draft": json.dumps(draft),
            "email_draft_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        touched_by=user["id"],
    )
    return draft


@app.patch("/api/practices/{place_id}/email/draft")
def patch_email_draft_endpoint(
    place_id: str,
    body: EmailDraftPatch,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    current = _parse_draft(practice.get("email_draft")) or {"subject": "", "body": ""}
    if body.subject is not None:
        current["subject"] = body.subject
    if body.body is not None:
        current["body"] = body.body

    update_practice_fields(
        place_id,
        {
            "email_draft": json.dumps(current),
            "email_draft_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        touched_by=user["id"],
    )
    return current
```

- [ ] **Step 4: Run the 3 draft-related tests**

Run: `python -m pytest tests/test_api_email.py::test_email_draft_get_requires_auth tests/test_api_email.py::test_email_draft_post_requires_auth tests/test_api_email.py::test_email_draft_patch_requires_auth -v`
Expected: 3 PASS (401s).

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_email.py
git commit -m "feat(api): email draft endpoints (GET/POST regen/PATCH)"
```

---

## Task 10: API — email send + messages + poll + mark-replied

**Files:**
- Modify: `api/index.py`

- [ ] **Step 1: Append the remaining email endpoints to `api/index.py`**

Place this block right after the `patch_email_draft_endpoint` you added in Task 9:

```python
@app.post("/api/practices/{place_id}/email/send")
async def send_email_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    if not _email_configured():
        raise HTTPException(503, "Email not configured")

    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    email_to = practice.get("email")
    if not email_to:
        raise HTTPException(400, "Email address required")

    draft = _parse_draft(practice.get("email_draft"))
    if not draft or not draft.get("subject") or not draft.get("body"):
        raise HTTPException(400, "No draft to send")

    try:
        result = await send_email(email_to, draft["subject"], draft["body"])
    except Exception as e:
        insert_email_message(
            practice_id=practice["id"],
            user_id=user["id"],
            direction="out",
            subject=draft["subject"],
            body=draft["body"],
            message_id=None,
            in_reply_to=None,
            error=str(e),
        )
        raise HTTPException(500, f"Send failed: {e}") from e

    row = insert_email_message(
        practice_id=practice["id"],
        user_id=user["id"],
        direction="out",
        subject=draft["subject"],
        body=draft["body"],
        message_id=result.get("message_id"),
        in_reply_to=None,
        error=None,
    )

    # Auto-advance status CONTACTED if currently earlier
    current_status = practice.get("status", "NEW")
    fields: dict = {}
    if _should_auto_advance(current_status, "CONTACTED"):
        fields["status"] = "CONTACTED"
    if fields:
        update_practice_fields(place_id, fields, touched_by=user["id"])
    else:
        update_practice_fields(place_id, {}, touched_by=user["id"])

    return row


@app.get("/api/practices/{place_id}/email/messages")
def list_email_messages_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")
    return {"messages": list_email_messages(practice["id"])}


@app.post("/api/practices/{place_id}/email/poll")
async def poll_email_replies_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    if not _email_configured():
        raise HTTPException(503, "Email not configured")

    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    email_addr = practice.get("email")
    if not email_addr:
        raise HTTPException(400, "Practice has no email address")

    outbound = list_outbound_message_ids(practice["id"])
    since = (
        datetime.now(timezone.utc)
        - timedelta(days=app_settings.email_reply_lookback_days)
    ).isoformat()

    new_rows: list[dict] = []
    replies = await poll_replies(
        practice_email=email_addr,
        outbound_message_ids=outbound,
        since_iso=since,
    )

    # Dedup against anything already stored
    existing = list_email_messages(practice["id"])
    existing_ids = {m.get("message_id") for m in existing if m.get("message_id")}

    for reply in replies:
        if reply["message_id"] in existing_ids:
            continue
        inserted = insert_email_message(
            practice_id=practice["id"],
            user_id=None,
            direction="in",
            subject=reply.get("subject"),
            body=reply.get("body"),
            message_id=reply.get("message_id"),
            in_reply_to=reply.get("in_reply_to"),
            error=None,
        )
        if inserted:
            new_rows.append(inserted)

    # Auto-advance to FOLLOW UP if any new inbound arrived
    if new_rows:
        current_status = practice.get("status", "NEW")
        fields: dict = {}
        if _should_auto_advance(current_status, "FOLLOW UP"):
            fields["status"] = "FOLLOW UP"
        if fields:
            update_practice_fields(place_id, fields, touched_by=user["id"])

    return {
        "new_messages": new_rows,
        "total": len(list_email_messages(practice["id"])),
    }


@app.post("/api/practices/{place_id}/email/mark-replied")
def mark_email_replied_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    row = insert_email_message(
        practice_id=practice["id"],
        user_id=None,
        direction="in",
        subject=None,
        body=f"[manually marked as replied by {user.get('name') or user['email']}]",
        message_id=None,
        in_reply_to=None,
        error=None,
    )

    current_status = practice.get("status", "NEW")
    fields: dict = {}
    if _should_auto_advance(current_status, "FOLLOW UP"):
        fields["status"] = "FOLLOW UP"
    if fields:
        update_practice_fields(place_id, fields, touched_by=user["id"])

    return row
```

- [ ] **Step 2: Run the remaining email API tests**

Run: `python -m pytest tests/test_api_email.py -v`
Expected: all 7 PASS (all return 401 without auth).

Also run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add api/index.py
git commit -m "feat(api): email send + messages + poll + mark-replied endpoints"
```

---

## Task 11: Extend PATCH /api/practices to accept `email`

**Files:**
- Modify: `api/index.py`

- [ ] **Step 1: Update `PatchPracticeRequest`**

In `api/index.py`, find:
```python
class PatchPracticeRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
```

Replace with:
```python
class PatchPracticeRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
    email: str | None = None
```

Find the `patch_practice` handler (right after that model). Replace its body with:

```python
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
    if body.email is not None:
        fields["email"] = body.email
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = update_practice_fields(place_id, fields, touched_by=user["id"])
    if not updated:
        raise HTTPException(status_code=404, detail="Practice not found")
    return updated
```

- [ ] **Step 2: Run test suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add api/index.py
git commit -m "feat(api): PATCH /api/practices accepts email field"
```

---

## Task 12: Extend Practice model + frontend types

**Files:**
- Modify: `src/models.py`
- Modify: `web/lib/types.ts`
- Modify: `web/lib/mock-data.ts`

- [ ] **Step 1: Update `src/models.py`**

Open `src/models.py`. Add three fields inside the `Practice` class, next to the other CRM fields:

```python
    # Phase 3 (CRM)
    status: str = "NEW"
    notes: str | None = None

    # Email outreach
    email: str | None = None
    email_draft: str | None = None
    email_draft_updated_at: str | None = None

    # Attribution
    last_touched_by: str | None = None
    last_touched_by_name: str | None = None
    last_touched_at: str | None = None
```

- [ ] **Step 2: Update `web/lib/types.ts`**

Add the three fields to the `Practice` interface and append two new types below it:

Find the `Practice` interface and add these fields (anywhere in the body):

```ts
  email: string | null
  email_draft: string | null
  email_draft_updated_at: string | null
```

Then append at the end of the file:

```ts
export interface EmailMessage {
  id: number
  practice_id: number
  user_id: string | null
  direction: "out" | "in"
  subject: string | null
  body: string | null
  message_id: string | null
  in_reply_to: string | null
  sent_at: string
  error: string | null
}

export interface EmailDraft {
  subject: string
  body: string
}
```

- [ ] **Step 3: Update `web/lib/mock-data.ts`**

Each mock practice entry needs the 3 new fields set to null. Open `web/lib/mock-data.ts`. For every entry in `mockPractices`, add these lines alongside the other nulls (e.g. next to `last_touched_by: null`):

```ts
    email: null,
    email_draft: null,
    email_draft_updated_at: null,
```

- [ ] **Step 4: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/models.py web/lib/types.ts web/lib/mock-data.ts
git commit -m "feat(types): add email fields to Practice + EmailMessage/EmailDraft types"
```

---

## Task 13: `web/lib/api.ts` — email helpers

**Files:**
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Append email helpers at the end of `web/lib/api.ts`**

Add these functions (and their imports at top of file if missing `EmailDraft` and `EmailMessage`). First update the top import:

Find:
```ts
import type { Practice, Script } from "./types"
```

Replace with:
```ts
import type { EmailDraft, EmailMessage, Practice, Script } from "./types"
```

Then append at the end of the file:

```ts
export async function getEmailDraft(placeId: string): Promise<EmailDraft> {
  try {
    return await apiFetch<EmailDraft>(`/api/practices/${placeId}/email/draft`)
  } catch {
    return { subject: "", body: "" }
  }
}

export async function regenerateEmailDraft(placeId: string): Promise<EmailDraft> {
  try {
    return await apiFetch<EmailDraft>(`/api/practices/${placeId}/email/draft`, {
      method: "POST",
    })
  } catch {
    return { subject: "", body: "" }
  }
}

export async function saveEmailDraft(
  placeId: string,
  draft: Partial<EmailDraft>,
): Promise<EmailDraft> {
  try {
    return await apiFetch<EmailDraft>(`/api/practices/${placeId}/email/draft`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    })
  } catch {
    return { subject: draft.subject ?? "", body: draft.body ?? "" }
  }
}

export async function sendEmail(placeId: string): Promise<EmailMessage> {
  return await apiFetch<EmailMessage>(`/api/practices/${placeId}/email/send`, {
    method: "POST",
  })
}

export async function getEmailMessages(placeId: string): Promise<EmailMessage[]> {
  try {
    const data = await apiFetch<{ messages: EmailMessage[] }>(
      `/api/practices/${placeId}/email/messages`,
    )
    return data.messages
  } catch {
    return []
  }
}

export async function pollEmailReplies(
  placeId: string,
): Promise<{ new_messages: EmailMessage[]; total: number }> {
  return await apiFetch<{ new_messages: EmailMessage[]; total: number }>(
    `/api/practices/${placeId}/email/poll`,
    { method: "POST" },
  )
}

export async function markEmailReplied(placeId: string): Promise<EmailMessage> {
  return await apiFetch<EmailMessage>(
    `/api/practices/${placeId}/email/mark-replied`,
    { method: "POST" },
  )
}

export async function updatePracticeEmail(
  placeId: string,
  email: string,
): Promise<Practice> {
  return await apiFetch<Practice>(`/api/practices/${placeId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  })
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/lib/api.ts
git commit -m "feat(web): email API helpers (draft, send, messages, poll, mark-replied)"
```

---

## Task 14: ActionsPanel — tabbed right column

**Files:**
- Create: `web/components/actions-panel.tsx`

- [ ] **Step 1: Create the file**

Write `web/components/actions-panel.tsx`:

```tsx
"use client"

import { useState, ReactNode } from "react"
import { cn } from "@/lib/utils"

interface Tab {
  id: string
  label: string
  disabled?: boolean
  badge?: number
}

interface ActionsPanelProps {
  tabs: Tab[]
  renderTab: (id: string) => ReactNode
  defaultTab?: string
}

export default function ActionsPanel({ tabs, renderTab, defaultTab }: ActionsPanelProps) {
  const [active, setActive] = useState(defaultTab ?? tabs[0].id)

  return (
    <div className="flex flex-col h-full">
      <div className="flex border-b border-gray-200/60">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => !tab.disabled && setActive(tab.id)}
            disabled={tab.disabled}
            className={cn(
              "flex-1 text-sm font-medium py-2 border-b-2 transition",
              active === tab.id
                ? "border-teal-600 text-teal-700"
                : "border-transparent text-gray-500 hover:text-gray-700",
              tab.disabled && "opacity-40 cursor-not-allowed"
            )}
          >
            {tab.label}
            {tab.badge && tab.badge > 0 ? (
              <span className="ml-1.5 inline-flex items-center justify-center min-w-[16px] h-4 px-1 text-[10px] font-bold text-white bg-rose-500 rounded-full">
                {tab.badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto mt-4">{renderTab(active)}</div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/components/actions-panel.tsx
git commit -m "feat(web): generic tabbed ActionsPanel shell"
```

---

## Task 15: EmailRecipient — inline-edit practice.email

**Files:**
- Create: `web/components/email-recipient.tsx`

- [ ] **Step 1: Create the file**

Write `web/components/email-recipient.tsx`:

```tsx
"use client"

import { useState } from "react"
import { Edit2, Check, X } from "lucide-react"
import { updatePracticeEmail } from "@/lib/api"

interface EmailRecipientProps {
  placeId: string
  email: string | null
  onChange: (email: string) => void
}

export default function EmailRecipient({ placeId, email, onChange }: EmailRecipientProps) {
  const [editing, setEditing] = useState(!email)
  const [draft, setDraft] = useState(email ?? "")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    if (!draft.trim()) return
    setSaving(true)
    setError(null)
    try {
      const updated = await updatePracticeEmail(placeId, draft.trim())
      onChange(updated.email ?? draft.trim())
      setEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <input
          type="email"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="contact@practice.com"
          autoFocus
          className="flex-1 text-sm rounded-lg border border-gray-200 bg-white/80 px-2 py-1"
        />
        <button
          onClick={save}
          disabled={saving || !draft.trim()}
          className="p-1.5 rounded-lg text-teal-700 hover:bg-teal-50 disabled:opacity-50"
          title="Save"
        >
          <Check className="w-4 h-4" />
        </button>
        {email && (
          <button
            onClick={() => {
              setDraft(email)
              setEditing(false)
              setError(null)
            }}
            className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100"
            title="Cancel"
          >
            <X className="w-4 h-4" />
          </button>
        )}
        {error && <span className="text-xs text-rose-600">{error}</span>}
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-gray-500">To:</span>
      <span className="text-gray-900">{email}</span>
      <button
        onClick={() => setEditing(true)}
        className="p-1 rounded text-gray-400 hover:text-gray-700"
        title="Edit email"
      >
        <Edit2 className="w-3 h-3" />
      </button>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/components/email-recipient.tsx
git commit -m "feat(web): EmailRecipient component with inline edit"
```

---

## Task 16: EmailComposer — subject/body + Regenerate/Save/Send

**Files:**
- Create: `web/components/email-composer.tsx`

- [ ] **Step 1: Create the file**

Write `web/components/email-composer.tsx`:

```tsx
"use client"

import { useState, useEffect } from "react"
import { RefreshCw, Save, Send, Loader2, AlertTriangle } from "lucide-react"
import type { EmailDraft } from "@/lib/types"

interface EmailComposerProps {
  draft: EmailDraft
  canSend: boolean                       // disabled if no recipient or no draft
  recipient: string | null
  onSave: (draft: EmailDraft) => Promise<void>
  onRegenerate: () => Promise<void>
  onSend: () => Promise<void>
  isRegenerating: boolean
}

export default function EmailComposer({
  draft,
  canSend,
  recipient,
  onSave,
  onRegenerate,
  onSend,
  isRegenerating,
}: EmailComposerProps) {
  const [subject, setSubject] = useState(draft.subject)
  const [body, setBody] = useState(draft.body)
  const [saving, setSaving] = useState(false)
  const [sending, setSending] = useState(false)
  const [confirm, setConfirm] = useState(false)

  useEffect(() => {
    setSubject(draft.subject)
    setBody(draft.body)
  }, [draft.subject, draft.body])

  async function handleSave() {
    setSaving(true)
    try {
      await onSave({ subject, body })
    } finally {
      setSaving(false)
    }
  }

  async function handleSendConfirmed() {
    setSending(true)
    try {
      // Persist any unsaved edits first
      if (subject !== draft.subject || body !== draft.body) {
        await onSave({ subject, body })
      }
      await onSend()
      setConfirm(false)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="space-y-3">
      <input
        type="text"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        onBlur={handleSave}
        placeholder="Subject"
        className="w-full text-sm font-medium rounded-lg border border-gray-200 bg-white/80 px-3 py-2"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        onBlur={handleSave}
        placeholder="Email body..."
        className="w-full h-64 text-sm p-3 rounded-lg border border-gray-200 bg-white/80 resize-none"
      />
      {confirm ? (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-amber-50 border border-amber-200">
          <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
          <span className="text-xs text-amber-900 flex-1">Send to {recipient}?</span>
          <button
            onClick={() => setConfirm(false)}
            disabled={sending}
            className="text-xs px-3 py-1 rounded-lg text-gray-700 hover:bg-gray-100"
          >
            Cancel
          </button>
          <button
            onClick={handleSendConfirmed}
            disabled={sending}
            className="text-xs px-3 py-1 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 inline-flex items-center gap-1"
          >
            {sending && <Loader2 className="w-3 h-3 animate-spin" />}
            {sending ? "Sending..." : "Yes, send"}
          </button>
        </div>
      ) : (
        <div className="flex gap-2">
          <button
            onClick={onRegenerate}
            disabled={isRegenerating}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {isRegenerating ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <RefreshCw className="w-3 h-3" />
            )}
            Regenerate
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            Save draft
          </button>
          <button
            onClick={() => setConfirm(true)}
            disabled={!canSend}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 ml-auto"
          >
            <Send className="w-3 h-3" />
            Send
          </button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/components/email-composer.tsx
git commit -m "feat(web): EmailComposer — subject/body + Regenerate/Save/Send + inline confirm"
```

---

## Task 17: EmailThread — message list, expandable

**Files:**
- Create: `web/components/email-thread.tsx`

- [ ] **Step 1: Create the file**

Write `web/components/email-thread.tsx`:

```tsx
"use client"

import { useState } from "react"
import { ArrowLeft, ArrowRight, AlertCircle, ChevronDown, ChevronUp, RefreshCw, Loader2 } from "lucide-react"
import type { EmailMessage } from "@/lib/types"
import { timeAgo } from "@/lib/utils"
import { cn } from "@/lib/utils"

interface EmailThreadProps {
  messages: EmailMessage[]
  onPoll: () => Promise<void>
  onMarkReplied: () => Promise<void>
  isPolling: boolean
}

export default function EmailThread({
  messages,
  onPoll,
  onMarkReplied,
  isPolling,
}: EmailThreadProps) {
  const [expandedId, setExpandedId] = useState<number | null>(null)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Thread
        </h4>
        <div className="flex gap-1.5">
          <button
            onClick={onPoll}
            disabled={isPolling}
            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {isPolling ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            Check replies
          </button>
          <button
            onClick={onMarkReplied}
            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50"
          >
            Mark replied
          </button>
        </div>
      </div>

      {messages.length === 0 ? (
        <p className="text-xs text-gray-400">No messages yet.</p>
      ) : (
        <ul className="space-y-1">
          {messages.map((m) => {
            const isExpanded = expandedId === m.id
            const Icon = m.direction === "out" ? ArrowRight : ArrowLeft
            const color = m.error ? "text-rose-500" : m.direction === "out" ? "text-teal-600" : "text-gray-500"
            return (
              <li
                key={m.id}
                className={cn(
                  "rounded-lg border border-gray-200/60 bg-white/60",
                  m.error && "border-rose-200 bg-rose-50/60"
                )}
              >
                <button
                  onClick={() => setExpandedId(isExpanded ? null : m.id)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left"
                >
                  {m.error ? (
                    <AlertCircle className="w-3.5 h-3.5 text-rose-500 shrink-0" />
                  ) : (
                    <Icon className={cn("w-3.5 h-3.5 shrink-0", color)} />
                  )}
                  <span className="text-xs font-medium text-gray-700 truncate flex-1">
                    {m.subject || (m.error ? "Send failed" : "(no subject)")}
                  </span>
                  <span className="text-[11px] text-gray-400 shrink-0">
                    {timeAgo(m.sent_at)}
                  </span>
                  {isExpanded ? (
                    <ChevronUp className="w-3 h-3 text-gray-400" />
                  ) : (
                    <ChevronDown className="w-3 h-3 text-gray-400" />
                  )}
                </button>
                {isExpanded && (
                  <div className="px-3 pb-3 text-xs text-gray-700 whitespace-pre-line">
                    {m.error ? (
                      <span className="text-rose-700">{m.error}</span>
                    ) : (
                      m.body || "(no body)"
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/components/email-thread.tsx
git commit -m "feat(web): EmailThread — collapsible message list + poll/mark-replied controls"
```

---

## Task 18: EmailPanel — root of Email tab

**Files:**
- Create: `web/components/email-panel.tsx`

- [ ] **Step 1: Create the file**

Write `web/components/email-panel.tsx`:

```tsx
"use client"

import { useState, useEffect, useCallback } from "react"
import type { EmailDraft, EmailMessage, Practice } from "@/lib/types"
import {
  getEmailDraft,
  regenerateEmailDraft,
  saveEmailDraft,
  sendEmail,
  getEmailMessages,
  pollEmailReplies,
  markEmailReplied,
} from "@/lib/api"
import EmailRecipient from "./email-recipient"
import EmailComposer from "./email-composer"
import EmailThread from "./email-thread"

interface EmailPanelProps {
  practice: Practice
  onPracticeUpdate: (next: Partial<Practice>) => void
}

export default function EmailPanel({ practice, onPracticeUpdate }: EmailPanelProps) {
  const [draft, setDraft] = useState<EmailDraft>({ subject: "", body: "" })
  const [messages, setMessages] = useState<EmailMessage[]>([])
  const [isRegenerating, setIsRegenerating] = useState(false)
  const [isPolling, setIsPolling] = useState(false)

  const loadDraft = useCallback(async () => {
    const d = await getEmailDraft(practice.place_id)
    setDraft(d)
  }, [practice.place_id])

  const loadMessages = useCallback(async () => {
    const m = await getEmailMessages(practice.place_id)
    setMessages(m)
  }, [practice.place_id])

  useEffect(() => {
    if (!practice.email) return
    loadDraft()
    loadMessages()
  }, [practice.email, loadDraft, loadMessages])

  async function handleSave(next: EmailDraft) {
    const saved = await saveEmailDraft(practice.place_id, next)
    setDraft(saved)
  }

  async function handleRegenerate() {
    setIsRegenerating(true)
    try {
      const fresh = await regenerateEmailDraft(practice.place_id)
      setDraft(fresh)
    } finally {
      setIsRegenerating(false)
    }
  }

  async function handleSend() {
    await sendEmail(practice.place_id)
    await loadMessages()
    onPracticeUpdate({ status: "CONTACTED" })
    setDraft({ subject: "", body: "" })
  }

  async function handlePoll() {
    setIsPolling(true)
    try {
      const result = await pollEmailReplies(practice.place_id)
      if (result.new_messages.length > 0) {
        onPracticeUpdate({ status: "FOLLOW UP" })
      }
      await loadMessages()
    } finally {
      setIsPolling(false)
    }
  }

  async function handleMarkReplied() {
    await markEmailReplied(practice.place_id)
    onPracticeUpdate({ status: "FOLLOW UP" })
    await loadMessages()
  }

  return (
    <div className="space-y-4">
      <EmailRecipient
        placeId={practice.place_id}
        email={practice.email}
        onChange={(email) => onPracticeUpdate({ email })}
      />

      {practice.email ? (
        <>
          <EmailComposer
            draft={draft}
            canSend={Boolean(practice.email) && Boolean(draft.subject) && Boolean(draft.body)}
            recipient={practice.email}
            onSave={handleSave}
            onRegenerate={handleRegenerate}
            onSend={handleSend}
            isRegenerating={isRegenerating}
          />
          <EmailThread
            messages={messages}
            onPoll={handlePoll}
            onMarkReplied={handleMarkReplied}
            isPolling={isPolling}
          />
        </>
      ) : (
        <p className="text-xs text-gray-500">Add an email address to compose and send.</p>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/components/email-panel.tsx
git commit -m "feat(web): EmailPanel composes Recipient + Composer + Thread"
```

---

## Task 19: Integrate ActionsPanel into Call Prep page

**Files:**
- Modify: `web/app/practice/[place_id]/page.tsx`

- [ ] **Step 1: Replace `<NotesPanel>` usage with `<ActionsPanel>`**

Open `web/app/practice/[place_id]/page.tsx`. Change the imports at the top:

Find:
```tsx
import NotesPanel from "@/components/notes-panel"
```

Replace with:
```tsx
import NotesPanel from "@/components/notes-panel"
import ActionsPanel from "@/components/actions-panel"
import EmailPanel from "@/components/email-panel"
```

Find the right-column aside:
```tsx
        {/* Right: Notes & Actions */}
        <aside className="w-[320px] shrink-0 overflow-y-auto p-5 border-l border-gray-200/50">
          <NotesPanel
            notes={practice.notes ?? ""}
            onSave={handleSaveNotes}
          />
        </aside>
```

Replace with:
```tsx
        {/* Right: Tabbed actions panel */}
        <aside className="w-[340px] shrink-0 overflow-y-auto p-5 border-l border-gray-200/50">
          <ActionsPanel
            defaultTab="notes"
            tabs={[
              { id: "notes", label: "Notes" },
              { id: "email", label: "Email" },
              { id: "activity", label: "Activity", disabled: true },
            ]}
            renderTab={(id) => {
              if (id === "notes") {
                return (
                  <NotesPanel
                    notes={practice.notes ?? ""}
                    onSave={handleSaveNotes}
                  />
                )
              }
              if (id === "email") {
                return (
                  <EmailPanel
                    practice={practice}
                    onPracticeUpdate={(next) =>
                      setPractice((prev) => (prev ? { ...prev, ...next } : prev))
                    }
                  />
                )
              }
              return (
                <p className="text-xs text-gray-400">
                  Activity history — coming soon.
                </p>
              )
            }}
          />
        </aside>
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/app/practice/[place_id]/page.tsx
git commit -m "feat(web): Call Prep page uses ActionsPanel (Notes | Email | Activity)"
```

---

## Task 20: E2E smoke test

**Files:** none (manual verification + backend reload)

- [ ] **Step 1: Apply DB migration if not already done**

In Supabase SQL editor, ensure the Task 1 SQL block has been applied. Check that `practices` has `email`, `email_draft`, `email_draft_updated_at` columns and `email_messages` table exists.

- [ ] **Step 2: Configure MS Graph env vars (skip if not ready)**

This step is OPTIONAL — the email tab works degraded without it (buttons return 503 banner). For a real send test, complete Azure AD app registration (Mail.Send + Mail.Read + offline_access delegated permissions, admin consent) and run:

```bash
python scripts/ms_auth_bootstrap.py
```

Paste the printed refresh token into `.env` as `MS_REFRESH_TOKEN`. Fill in `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_SENDER_EMAIL`.

- [ ] **Step 3: Restart backend and run full suite**

```bash
python -m pytest -q
```
Expected: all green (backend + email tests).

Then:
```bash
uvicorn api.index:app --reload --port 8000
```

- [ ] **Step 4: Walk through the flow**

- Log in, go to the map, search and analyze a practice.
- Open Call Prep for that practice.
- Right column now has three tabs: **Notes | Email | Activity (disabled)**.
- Click **Email** tab.
- No email on the practice yet → "Add an email address..." placeholder with an input. Type `test@example.com`, save.
- Composer appears with an auto-generated subject + body.
- Edit, click **Save draft** → saved confirmation.
- Click **Regenerate** → subject/body replaced with fresh GPT output.
- Click **Send** → confirmation bar → **Yes, send**.
  - If MS vars configured: message sends, thread shows an outbound row, status auto-advances to `CONTACTED`.
  - If MS vars not configured: toast/error with 503; thread still shows nothing.
- Reply from the target address to the shared mailbox.
- Click **Check replies** → inbound row appears in thread; status auto-advances to `FOLLOW UP`.
- Click **Mark replied** on a different practice (optional) → synthetic inbound row appears, status advances.

- [ ] **Step 5: Verify DB state**

```sql
select id, practice_id, direction, subject, message_id, sent_at
from email_messages
order by sent_at desc
limit 10;
```
Rows correspond to what you sent/received.

- [ ] **Step 6: Run tests one more time**

Run: `python -m pytest -v && cd web && npx tsc --noEmit`
Expected: all pass, no type errors.

---

## Done criteria

- `practices` has `email`, `email_draft`, `email_draft_updated_at` columns; `email_messages` table exists.
- All 7 email endpoints (`GET/POST/PATCH /email/draft`, `POST /email/send`, `GET /email/messages`, `POST /email/poll`, `POST /email/mark-replied`) return 401 without auth, 503 when MS unconfigured (for `/send` and `/poll`).
- Call Prep page right column is tabbed: Notes | Email | Activity (disabled placeholder).
- Email tab supports: edit recipient inline, auto-generate draft on open, regenerate, edit + save, send with confirm, poll replies, mark replied.
- Sending auto-advances status to `CONTACTED`; reply (via poll or mark-replied) advances to `FOLLOW UP`; never regresses.
- Every outbound message carries `user_id`; inbound has `user_id=null`.
- Re-analyzing a practice clears `email_draft`.
- `python -m pytest -v` passes; `npx tsc --noEmit` passes in `web/`.
