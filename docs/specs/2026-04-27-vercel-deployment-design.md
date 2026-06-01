# Vercel Deployment + Observability — Design Spec

**Date:** 2026-04-27
**Status:** Implemented and shipped

## Goal

Deploy the existing monorepo (Next.js frontend in `web/`, FastAPI backend in `api/`) as a single Vercel project so the team can use the live URL instead of localhost-via-ngrok. Add structured logging that Vercel's log capture can ingest, plus an admin-only env-var sanity check endpoint so we can verify configuration without leaking secrets.

## Scope

### In scope
- Single Vercel project, framework auto-detected as Next.js, builds `web/` and serves `api/index.py` as a Python serverless function.
- URL shape: `https://<project>.vercel.app` serves both UI (`/`) and API (`/api/*`) — same-origin, no CORS issues.
- Frontend works in three modes:
  - **Production** (Vercel) — `NEXT_PUBLIC_API_URL` is empty, fetch calls go to relative paths (`/api/...`), Vercel's rewrite routes them to the Python function.
  - **Local dev with backend** — `NEXT_PUBLIC_API_URL=http://localhost:8000`, fetches go cross-origin (CORS regex allows `localhost:*`).
  - **Local dev without backend** — `NEXT_PUBLIC_API_URL` empty, `apiFetch` throws `NO_API`, callers fall back to mock data.
- Structured logging at INFO level under `hvsi.*` namespaces, written to stdout so Vercel's runtime captures them.
- Per-request log lines in `salesforce.py` and `call_log.py` covering create/update payload size, response body (truncated), success/failure flags.
- `GET /api/debug/env` admin-only endpoint: returns booleans + first-few-chars previews of every env var the function actually reads. Lets us verify Vercel env-var configuration without leaking values.

### Out of scope
- Custom domain (defer until UAT signoff).
- Edge runtime / streaming endpoints (everything is the default Node-on-Vercel + Python serverless function).
- Database migrations from the deploy pipeline (Supabase migrations stay manual via SQL editor).
- Log forwarding to a third-party log service (Vercel's built-in log viewer is enough for v1).
- Crash reporting (Sentry / similar) — defer until incident volume justifies it.
- Rate-limiting on the API.
- A staging environment — preview deployments per branch are sufficient.

## Architecture

```
Browser ──► https://<project>.vercel.app/...
              │
              ├── /  → Vercel-served Next.js (web/)
              │
              └── /api/*  → vercel.json rewrite
                              ▼
                          /api/index.py (Python serverless function)
                              │
                              ▼
                          FastAPI app (mounted at /api/* internally — see "API path note")
```

### `vercel.json`
```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": "nextjs",
  "buildCommand": "cd web && npm install --include=dev && npm run build",
  "outputDirectory": "web/.next",
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/index.py" }
  ]
}
```

### Root `package.json`
A minimal manifest at the repo root so Vercel auto-detects the project as Next.js. Only declares the `next` dependency Vercel needs at the root level; the actual app deps live in `web/package.json`. Build script just delegates into `web/`.

### `.vercelignore`
Excludes `tests/`, `docs/`, `scripts/`, `__pycache__/`, `.env*`, `.git/` from the deploy bundle — keeps the Python function lean and prevents test/dev files from shipping.

### API path note
FastAPI routes are declared as `/api/practices/...` (with the `/api` prefix), and the Vercel rewrite forwards `/api/(.*)` → `/api/index.py`. Vercel runs `index.py` and FastAPI sees the original `/api/...` path, so the routes match. Local uvicorn works the same way (`uvicorn api.index:app` exposes the same paths).

## Frontend behavior

### `web/lib/api.ts`
```typescript
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""
const IS_PROD = process.env.NODE_ENV === "production"

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // Empty API_URL in production = same-origin (Vercel rewrite handles routing).
  // Empty API_URL in dev = no backend; caller falls back to mocks.
  if (!API_URL && !IS_PROD) throw new Error("NO_API")
  ...
}
```

### `web/app/practice/[place_id]/page.tsx`
Mirrors the same logic for the bespoke `fetch` it does for the practice detail load — so the Call Prep page works identically in dev and production.

## Observability

### Logging setup (top of `api/index.py`)
```python
import logging, sys
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))
logging.getLogger("hvsi").handlers = [_log_handler]
logging.getLogger("hvsi").setLevel(logging.INFO)
logging.getLogger("hvsi").propagate = False
```

All app modules use `logging.getLogger("hvsi.<module>")`. Vercel ingests stdout into its log viewer. Tags used:

| Logger              | Tag prefixes                                                       | When                                                           |
| ------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------- |
| `hvsi.api`          | `[api.call_log]`, `[api.patch_practice]`, `[api.call_log.404]`     | Endpoint entry + exit + early-return cases.                    |
| `hvsi.call_log`     | `[call_log.start]`, `[call_log.fetched]`, `[call_log.sync.attempt]`, `[call_log.sync.result]`, `[call_log.sync.error]`, `[call_log.done]` | Each step of the call-log orchestrator. |
| `hvsi.salesforce`   | `[sf.create.request]`, `[sf.create.response]`, `[sf.update.*]`, `[sf.update_desc.*]`, `[sf.sync.*]`, `[sf.create.network_error]` | Every Apex request and response. Response body truncated to 500 chars. |

Endpoint URL is logged with host + path only (`_redacted_endpoint` strips the scheme prefix) so logs are useful but not as leaky.

API key is logged only as `first6...last3` chars in the `/api/debug/env` response, never in normal request logs. Request bodies don't include the key (it's a header).

### `GET /api/debug/env` (admin-only)
```python
@app.get("/api/debug/env")
async def debug_env(user: dict = Depends(require_admin)):
    return {
        "supabase_url_set": bool(app_settings.supabase_url),
        "supabase_service_role_set": bool(app_settings.supabase_service_role_key),
        "openai_api_key_set": bool(app_settings.openai_api_key),
        "sf_apex_url_set": bool(app_settings.sf_apex_url),
        "sf_apex_url_host": (app_settings.sf_apex_url.split("/")[2]
                             if app_settings.sf_apex_url else None),
        "sf_api_key_set": bool(app_settings.sf_api_key),
        "sf_api_key_first6": (app_settings.sf_api_key[:6] + "..."
                              if app_settings.sf_api_key else None),
        "clay_inbound_secret_set": bool(app_settings.clay_inbound_secret),
        "google_maps_set": bool(app_settings.google_maps_api_key),
        "bootstrap_admin_email": app_settings.bootstrap_admin_email or None,
    }
```

Returns booleans + non-secret previews. Useful when:
- Verifying Vercel env vars after deploy.
- Confirming a production incident isn't a missing-key issue before opening logs.
- Onboarding: a new admin checks if their Supabase + SF + Clay creds got pasted in correctly.

403s for non-admins, never returns secret values.

## Env vars on Vercel

Configured via the Vercel project settings UI for the **Production** environment (preview deployments inherit unless overridden):

| Var                              | Source                                                  | Notes                                                          |
| -------------------------------- | ------------------------------------------------------- | -------------------------------------------------------------- |
| `SUPABASE_URL`                   | Supabase project settings                               |                                                                |
| `SUPABASE_KEY`                   | Supabase anon key                                       | Used by frontend via Next.js public env (`NEXT_PUBLIC_SUPABASE_*`). |
| `SUPABASE_SERVICE_ROLE_KEY`      | Supabase service-role key                               | Backend only, never exposed.                                   |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI dashboard                                        | `OPENAI_MODEL=gpt-4o-mini` default.                            |
| `BOOTSTRAP_ADMIN_EMAIL/PASSWORD` | Operator-defined                                        | Only seeds an admin if `profiles` has zero admins.             |
| `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_REFRESH_TOKEN`, `MS_SENDER_EMAIL` | M365 Graph app reg              | Email outreach.                                                |
| `SF_APEX_URL`, `SF_API_KEY`      | Apex SF admin                                 | See [Salesforce spec](2026-04-23-salesforce-integration-design.md). |
| `CLAY_TABLE_WEBHOOK_URL`, `CLAY_TABLE_API_KEY`, `CLAY_INBOUND_SECRET` | Clay workspace             | See [Clay spec](2026-04-24-clay-enrichment-design.md).          |
| `GOOGLE_MAPS_API_KEY`            | Google Cloud project                                    | Places API enabled.                                            |
| `NEXT_PUBLIC_API_URL`            | Set to **empty string** in production                   | Empty = same-origin. Override in preview/dev only when pointing the deployed UI at a non-Vercel API. |

Frontend public vars (must be prefixed `NEXT_PUBLIC_`):
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `NEXT_PUBLIC_RINGCENTRAL_WEB_APP_URL` (defaults to `https://app.ringcentral.com`)
- `NEXT_PUBLIC_API_URL` (empty in prod)

## Build pipeline

1. Vercel detects monorepo via root `package.json` + `vercel.json`.
2. `buildCommand: cd web && npm install --include=dev && npm run build` → builds Next.js into `web/.next`.
3. Vercel discovers `api/index.py` as a Python serverless function. Dependencies pulled from `pyproject.toml` (or `requirements.txt` if Vercel needs it). FastAPI + httpx + supabase-py + pydantic + openai + beautifulsoup4 are the heavy hitters.
4. The rewrite in `vercel.json` connects `/api/*` requests to the Python function.
5. Static + dynamic Next.js routes serve the rest.

## Local development

Three commands, three terminals (pattern unchanged from pre-Vercel days):

```bash
# Backend
uvicorn api.index:app --reload --port 8000

# Frontend
cd web && npm run dev         # listens on :3000

# Tunnel (only when Clay/SF need to reach a real backend)
ngrok http 8000
```

`web/.env.local`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

In CI / preview deploys, `NEXT_PUBLIC_API_URL` is empty (same-origin) so the same UI works against the deployed backend.

## Deployment workflow

- Push to `main` → Vercel auto-deploys to production.
- Push to feature branch → Vercel creates a preview URL (good for UAT before merging).
- `git revert` → Vercel auto-deploys the reverted state.
- Env var changes via Vercel UI → click "Redeploy" on the most recent prod deployment to pick up the new values (Vercel doesn't auto-redeploy on env-only changes).

## Operational checklist

After every prod deploy:
1. Hit `https://<project>.vercel.app` — homepage loads, login works.
2. Sign in as bootstrap admin → load any practice → confirm UI renders without console errors.
3. `GET /api/debug/env` (in browser, signed in as admin) → verify all `*_set` flags are `true`.
4. (If SF or Clay env vars changed) trigger one Call Log + one Enrich → check `vercel logs` for the `[sf.sync.*]` / `[call_log.sync.*]` / Clay webhook trace.
5. Check the latest Vercel deployment's Functions tab for any 500s in the past hour.

## Decision log

1. **Single Vercel project, not two.** Originally considered hosting `web/` on Vercel and `api/` on Fly.io. Same-origin via Vercel rewrites is simpler (no CORS, no two URLs to coordinate, one set of env vars).

2. **Same-origin in production, cross-origin in dev.** Cleaner than maintaining two API URL conventions or proxying through Next.js's API routes. The 4-line `apiFetch` change (check `IS_PROD`) covers it.

3. **No log aggregation service yet.** Vercel's built-in viewer + `vercel logs --follow` is enough at this scale. Add Logtail / Datadog only when we have multiple operators or need cross-deploy retention.

4. **Debug endpoint is admin-only.** Tempting to expose unauthenticated for ops convenience, but env-var presence is itself sensitive intel (e.g., "they don't have SF_API_KEY set yet" tips off attackers). `require_admin` keeps it safe.

5. **API key previews in `debug/env` are first-6 chars only.** Enough to disambiguate "is this the right key?" without enabling reconstruction.

## Success criteria

- Production URL serves UI + API.
- Admin can `GET /api/debug/env` and see correct presence flags.
- Calls + enrichments fired from the production UI hit the SF + Clay endpoints with no manual ngrok step.
- Vercel logs show structured `hvsi.*` traces for every SF and Clay call, with response bodies (truncated) for debugging.
- Local dev still works with `npm run dev` + `uvicorn api.index:app --reload --port 8000`.
- Mock-mode UI still works without any env vars set (frontend-only `npm run dev`).
