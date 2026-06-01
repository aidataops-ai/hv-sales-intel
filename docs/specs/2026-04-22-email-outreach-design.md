# Email Outreach — Design

**Date:** 2026-04-22
**Status:** Approved
**Depends on:** [2026-04-22-auth-user-attribution-design.md](2026-04-22-auth-user-attribution-design.md) — this spec assumes `profiles`, `last_touched_*`, and the `get_current_user` dependency already exist.

## Goal

Let a rep, from the Call Prep page, send a personalized outreach email to a practice using a GPT-generated draft (reviewed and editable), receive replies back via on-demand poll, and have sending/receiving auto-advance the CRM status — with every message attributed to the sender.

## Non-goals

- Email **sourcing** — email arrives via Clay (future iteration) or manual entry on the Call Prep page. This spec adds a nullable `email` column but does not scrape.
- No bulk send. Per-practice only.
- No scheduled reply polling. On-demand button + manual mark.
- No template library — one GPT-generated draft per practice, regenerable. Static templates can come later.
- No attachments.
- No follow-up sequencing / drip campaigns.
- No tracking pixels / open tracking.
- No unsubscribe UI (1:1 outreach, not list-based).

## Data model

One new table + three columns on `practices`:

```sql
-- Email address + cached draft on the practice itself.
alter table practices add column if not exists email text;
alter table practices add column if not exists email_draft text;  -- JSON { subject, body }
alter table practices add column if not exists email_draft_updated_at timestamptz;

-- Every outbound send + every inbound reply.
create table if not exists email_messages (
  id bigserial primary key,
  practice_id bigint not null references practices(id) on delete cascade,
  user_id uuid references profiles(id),            -- sender (null for inbound)
  direction text not null check (direction in ('out', 'in')),
  subject text,
  body text,
  message_id text,                                  -- internetMessageId from Graph
  in_reply_to text,                                 -- In-Reply-To / References for inbound
  sent_at timestamptz default now(),                -- when we sent (out) or received (in)
  error text                                        -- populated on send failure; null otherwise
);

create index if not exists idx_email_messages_practice on email_messages (practice_id, sent_at desc);
create index if not exists idx_email_messages_message_id on email_messages (message_id);
```

### Why these choices

- **`email` on practices** — Clay or manual entry populates it. Nullable; existing rows don't break.
- **`email_draft` cached as JSON** — same pattern as `call_script`. Saves GPT calls. Invalidated (set to null) when `analyze_practice` re-runs, so fresh analysis → fresh draft.
- **`email_messages` append-only** — every send (including failures, with `error` populated) and every inbound reply. No updates. Serves as the audit trail until feature A lands.
- **`message_id` + `in_reply_to`** — Graph returns `internetMessageId` for every message; same value appears in peers' `In-Reply-To`. This is the primary threading key.
- **`user_id` nullable** — outbound always has one (sender); inbound is null (practice replied, no internal user).
- **`direction` enum via check** — two values only.

### Type additions

Backend `Practice` + frontend `lib/types.ts`:

```ts
email: string | null
email_draft: string | null             // JSON "{ subject, body }"
email_draft_updated_at: string | null
```

New frontend types:

```ts
export interface EmailMessage {
  id: number
  practice_id: number
  user_id: string | null
  user_name?: string | null             // joined from profiles
  direction: "out" | "in"
  subject: string | null
  body: string | null
  message_id: string | null
  in_reply_to: string | null
  sent_at: string                       // ISO
  error: string | null
}

export interface EmailDraft { subject: string; body: string }
```

## Backend

### Provider: Microsoft Graph (M365 business)

M365 business disables basic SMTP/IMAP by default. The supported path is Microsoft Graph API + OAuth2.

**Azure AD setup (one-time, manual):**
1. Register a confidential client app in Azure AD.
2. Grant **delegated** permissions: `Mail.Send`, `Mail.Read`, `offline_access`.
3. Admin consents for the company tenant.
4. Run `scripts/ms_auth_bootstrap.py` once → opens the authorize URL, exchanges the auth code for `access_token` + `refresh_token`, prints the refresh token to paste into `.env`.

### New module: `src/email_send.py`

```python
async def send_email(
    to: str,
    subject: str,
    body: str,
) -> dict:
    """POST https://graph.microsoft.com/v1.0/me/sendMail.

    Returns { message_id, sent_at }.
    Raises on failure (caller inserts an error row).

    v1: always sends from MS_SENDER_EMAIL (no per-user `from` or `reply-to`
    override). This keeps replies routed to the shared mailbox the poll reads.
    Per-user sending (each rep from their own mailbox) is a future iteration;
    would require per-user OAuth tokens.
    """
```

- Graph `sendMail` doesn't return the sent message directly. After send, call `GET /me/mailFolders/sentitems/messages?$top=1&$orderby=sentDateTime desc` to retrieve the sent message's `internetMessageId`. Match on `toRecipients` + `subject` to confirm.
- Token refresh handled by a small `src/ms_auth.py` helper that caches an access token + auto-refreshes using `MS_REFRESH_TOKEN` when expired.

### New module: `src/email_gen.py`

```python
async def generate_email_draft(
    name: str,
    category: str | None,
    summary: str | None,
    pain_points: str | None,   # JSON string
    sales_angles: str | None,  # JSON string
) -> dict:
    """Return {subject, body}. GPT if OPENAI_API_KEY set, mock otherwise."""
```

- System prompt: "Write a short cold outreach email (80–140 words) from a Apex & Virtuals rep. Reference one specific pain point and one sales angle from the analysis. End with a clear ask (15-min call)."
- JSON output: `{subject, body}`.
- Mock fallback: category-appropriate canned subject + body, same shape as `scriptgen._mock_script`.

### New module: `src/email_poll.py`

```python
async def poll_replies(practice_id: int, since: datetime | None = None) -> list[dict]:
    """GET https://graph.microsoft.com/v1.0/me/messages
       ?$filter=receivedDateTime ge <since> and from/emailAddress/address eq '<practice.email>'
       &$select=id,subject,body,from,toRecipients,sentDateTime,receivedDateTime,
                internetMessageId,internetMessageHeaders
       &$orderby=receivedDateTime desc
       &$top=50
    """
```

**Threading logic:**
1. Fetch all outbound `message_id`s for this practice from `email_messages`.
2. For each Graph hit, parse `internetMessageHeaders` for `In-Reply-To` and `References`. If any match a stored outbound `message_id`, this is a threaded reply.
3. Fallback: if no header match, match by `from.emailAddress.address == practice.email` (envelope match).
4. Dedup by `internetMessageId` — skip if already stored.
5. Insert new rows with `direction='in'`, `user_id=null`, `message_id=internetMessageId`, `in_reply_to=<matched outbound id>`, body in plain text (strip HTML via BeautifulSoup if needed).
6. Return list of newly inserted rows.

### New module: `src/ms_auth.py`

Access-token cache + refresh helper. Module-level async lock so concurrent callers share one refresh.

```python
async def get_access_token() -> str:
    """Return a fresh access token, refreshing via MS_REFRESH_TOKEN if expired."""
```

POSTs to `https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token` with `grant_type=refresh_token`, `refresh_token=MS_REFRESH_TOKEN`, `client_id=MS_CLIENT_ID`, `client_secret=MS_CLIENT_SECRET`, `scope=Mail.Send Mail.Read offline_access`.

### New endpoints in `api/index.py`

All require `get_current_user`. Mutating endpoints stamp `last_touched_by = user["id"]` via the storage helpers (added in the auth spec).

```
GET    /api/practices/{place_id}/email/draft        → { subject, body }
POST   /api/practices/{place_id}/email/draft        → regenerate
PATCH  /api/practices/{place_id}/email/draft        → { subject?, body? }
POST   /api/practices/{place_id}/email/send         → send current draft
GET    /api/practices/{place_id}/email/messages     → EmailMessage[]
POST   /api/practices/{place_id}/email/poll         → trigger reply poll
POST   /api/practices/{place_id}/email/mark-replied → synthetic inbound + advance status
```

### Send flow

```
POST /api/practices/{place_id}/email/send
  1. Fetch practice. If practice.email is null → 400 "Email address required".
  2. Fetch practice.email_draft. If null → 400 "No draft".
  3. send_email(practice.email, draft.subject, draft.body)
       → { message_id, sent_at }
     (v1 sends from MS_SENDER_EMAIL with no reply-to override, so replies land
      in the shared mailbox the poll reads. Per-user sending is out of scope.)
  4. Insert email_messages: direction='out', user_id=user.id, subject, body,
     message_id, sent_at, error=null.
  5. If status ordered < 'CONTACTED': update practices.status = 'CONTACTED'.
  6. Stamp last_touched_by = user.id, last_touched_at = now().
  7. Return the inserted email_messages row.
  On send error: insert row with error populated, status unchanged, return 500.
```

### Reply flow

```
POST /api/practices/{place_id}/email/poll
  1. since = last inbound message's sent_at for this practice, or now - 30 days.
  2. poll_replies(practice_id, since=since)
  3. For each result: insert email_messages with direction='in', user_id=null.
  4. If any new inbound AND status ordered < 'FOLLOW UP':
       update practices.status = 'FOLLOW UP'.
  5. Stamp last_touched_by = user.id (the rep who clicked poll), last_touched_at = now().
  6. Return { new_messages: [...], total: <count of all messages for this practice> }.
```

`POST /email/mark-replied` creates a synthetic inbound row (`body="[manually marked as replied by <user.name>]"`, `message_id=null`) and advances status identically. Escape hatch for when the rep sees the reply in Outlook before HV Intel polls.

### New env vars

```
MS_TENANT_ID=
MS_CLIENT_ID=
MS_CLIENT_SECRET=
MS_REFRESH_TOKEN=        # from one-time bootstrap
MS_SENDER_EMAIL=         # e.g. sales@apexvirtuals.com
EMAIL_REPLY_LOOKBACK_DAYS=30
```

Any missing MS var → email endpoints return 503 "Email not configured". App otherwise runs normally.

## Frontend

### Tabbed right column

Replace `<NotesPanel>` inside `web/app/practice/[place_id]/page.tsx` with `<ActionsPanel>`:

```
web/components/actions-panel.tsx     NEW — tabs shell
├─ Notes    → <NotesPanel />       (existing, moved)
├─ Email    → <EmailPanel />       NEW
└─ Activity → placeholder "Coming soon" (disabled — future feature A)
```

Tab bar: underline style, teal accent. Email tab shows a small unread-count badge when inbound messages are newer than the last tab open (client-side local-state, not persisted).

### Email panel

```
┌─ EmailPanel ────────────────────────────┐
│ To: dr.smith@practice.com  [edit]       │
│                                         │
│ ── Composer ──                          │
│ [Subject: _______________________]      │
│ [Body                                 ] │
│ [                                     ] │
│ [Regenerate] [Save draft] [Send ▶]      │
│                                         │
│ ── Thread ──                            │
│ ▸ You → them · 2h ago · Re: ...         │
│ ▸ them → you · 10m ago · Re: ...        │
│                                         │
│ [Check for replies] [Mark as replied]   │
└─────────────────────────────────────────┘
```

### Components

```
web/components/
├── actions-panel.tsx         Tabs shell (Notes | Email | Activity)
├── email-panel.tsx           Root of the Email tab
├── email-recipient.tsx       Displays + inline-edits practice.email (PATCH /practices)
├── email-composer.tsx        Subject/body textareas, Regenerate + Save + Send
└── email-thread.tsx          EmailMessage list, collapsed/expanded per row
```

### Empty-state branches

| Condition | Composer | Thread |
| --- | --- | --- |
| No `email` on practice | "Add email to send" + inline input | "No messages yet" |
| Email present, no draft | Auto-calls `GET /email/draft` on tab open (generates + caches) | "No messages yet" |
| Draft ready, no sends | Show draft + Send button | "No messages yet" |
| Messages exist | Show draft | Collapsed thread, newest at bottom; click to expand |

### Draft lifecycle

1. Tab open → `GET /email/draft`. Server returns cached or generates.
2. Rep edits → `onBlur` (debounced) or "Save draft" button → `PATCH /email/draft`. Saved indicator flashes briefly.
3. Rep clicks Regenerate → `POST /email/draft`. Loading spinner, then replaces content.
4. Rep clicks Send → inline confirmation bar `⚠ Send to <email>?  [Cancel] [Yes, send]` → `POST /email/send`. Button → "Sending..." → on success: toast, refresh thread, clear composer to a fresh empty state.

### `lib/api.ts` additions

```ts
getEmailDraft(placeId): Promise<EmailDraft>
regenerateEmailDraft(placeId): Promise<EmailDraft>
saveEmailDraft(placeId, draft: Partial<EmailDraft>): Promise<EmailDraft>
sendEmail(placeId): Promise<EmailMessage>
getEmailMessages(placeId): Promise<EmailMessage[]>
pollEmailReplies(placeId): Promise<{ new_messages: EmailMessage[], total: number }>
markEmailReplied(placeId): Promise<EmailMessage>
updatePracticeEmail(placeId, email: string): Promise<Practice>   // PATCH /practices/{id}
```

Mock fallback pattern matches existing `api.ts` — unreachable backend returns plausible mock data so the UI is debuggable without a backend.

## Error handling

| Case | Backend | Frontend |
| --- | --- | --- |
| Practice has no `email` when Send clicked | 400 "Email address required" | Send button disabled when email null; inline nudge |
| MS Graph env vars missing | 503 "Email not configured" | Config banner in Email tab |
| Graph token expired | Auto-refresh via refresh token; retry once. If refresh fails → 500 "Email auth expired" | Error toast |
| Graph 429 rate-limit | Read `Retry-After`, return 503 with `retry_after_seconds` | Toast: "Rate limited, try again in Ns" |
| Graph `/sendMail` fails | Insert error row, return 500 | Failed send shown in thread with red badge + Retry |
| Poll finds 0 new | `{new_messages: [], total: <existing>}` | "No new replies" toast |
| Poll finds reply without matching `In-Reply-To` | Store if `from` matches `practice.email` | Shown with "matched by sender" indicator |
| Duplicate `internetMessageId` on poll | Silent skip (idempotent) | No effect |
| Mark-as-replied when status already ≥ FOLLOW UP | Insert synthetic row; do NOT regress status | Normal behavior |
| Draft regen GPT error | Return 500 with mock fallback body | Composer shows mock; rep edits freely |
| Send-to-self | No guard (rep's choice) | No warning |
| Concurrent draft edits | Last write wins; `email_draft_updated_at` lets frontend warn if remote is newer than loaded | Refresh button if stale |

## Testing

### Backend unit tests
- `email_gen.generate_email_draft` — happy path (GPT), failure → mock fallback, shape validation.
- `email_send.send_email` — mocked Graph HTTP client: request shape (auth header, body), success, 429, 401, 500.
- `email_poll.poll_replies` — mocked Graph list: dedup on `internetMessageId`, threading via `In-Reply-To`, envelope-sender fallback, plain-text extraction from HTML body.
- `ms_auth.get_access_token` — fresh, expired, refresh failure.
- Status-transition logic: send auto-advances to CONTACTED iff current < CONTACTED; reply → FOLLOW UP iff current < FOLLOW UP; never regresses.
- Attribution: every mutating endpoint writes `last_touched_by`.

### Integration test
One end-to-end script, env-gated: login as seeded rep → add test email on a practice → generate draft → send to a test Outlook inbox → poll → assert reply lands → assert status advanced.

### Frontend
Manual smoke: draft gen, edit, send, poll, mark-replied, all empty states, no-provider config banner. No Playwright harness exists yet.

## Rollout (local-first)

1. Apply migration.
2. Register Azure AD app, configure scopes, run `scripts/ms_auth_bootstrap.py` once.
3. Add MS + OpenAI env vars to `.env`.
4. Verify end-to-end on a test Outlook account.
5. Commit. No production gate.

## Feature flags / graceful degradation

- Missing `MS_*` vars → Email tab hidden with "Email not configured" notice.
- Missing `OPENAI_API_KEY` → drafts use mock generator; send still works.
- Missing Supabase → mutating email endpoints fail (they require attribution). Consistent with the rest of the app's no-Supabase mode.

## Decisions log

- **Microsoft Graph API** over SMTP/IMAP — M365 business disables basic auth by default. The Q4 `send_email()` abstraction kept the swap free.
- **Per-practice rep-initiated send** (Q2 a) — every send gets human review; deliberate sales tool, not a cold-blast engine.
- **Cached GPT draft** (Q3 c) — same pattern as `call_script`. One GPT call per analysis cycle.
- **Invalidate draft on re-analyze** — mirrors call_script behavior.
- **On-demand poll + manual mark** (Q5 c) — no background worker per project constraint; manual mark handles the Outlook-noticed-first case.
- **Auto-advance status** (Q6 a) — send → CONTACTED, reply → FOLLOW UP; never regresses. Consistent with existing phase auto-transitions.
- **Separate `email_messages` table** (Q7 b) — threading always turns "we'll add it later" into a painful rewrite.
- **Tabbed right column** (Q8 b) — no layout regression; natural home for future Activity tab.
- **Append-only log** — failed sends keep a row; audit trail is trustworthy.
- **No bulk send, no tracking pixels, no drip sequencing** — out of scope; each carries real risk (misfires, consent, deliverability).
