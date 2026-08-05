# Job-posting lead cron

Two background stages, `collect` → `qualify`, wired in `vercel.json`. Both are
idempotent, both claim a bounded slice, and neither assumes the other ran.

## Schedule

| Stage | Path | Schedule | Why |
|---|---|---|---|
| Collect | `/api/cron/leads/collect` | `0 * * * *` | Hourly. Each run claims `LEAD_COLLECT_BATCH` (40) targets; the Florida matrix is 14 terms × 31 locations = 434 targets, so a full sweep takes ~11 hours. |
| Qualify | `/api/cron/leads/qualify` | `20 * * * *` | Twenty minutes behind collect, so the postings it just wrote are there to claim. Runs at `LEAD_QUALIFY_BATCH` (60) postings, batched 20 per model call. |

Board rotation is independent of this schedule and derives from wall clock:
Indeed runs every firing, LinkedIn every third, per the `weight` values in
`config/leads/filters.json`. LinkedIn answers in ~22s against Indeed's ~1.5s,
so running both every time would spend the invocation waiting.

## Auth

Set `LEAD_CRON_SECRET`. **With it unset both routes return 503** — a stage that
spends model credits should not be reachable by omission.

Two headers are accepted:

- `X-Cron-Secret: <secret>` — matches the Clay webhook pattern; use this from
  `curl` or an external scheduler.
- `Authorization: Bearer <secret>` — what Vercel's own scheduler sends, and the
  only thing it can be configured to send.

Vercel cron issues a **GET**, so both stages are registered on GET as well as
the POST the design doc specifies.

## Running one by hand

```sh
curl -X POST -H "X-Cron-Secret: $LEAD_CRON_SECRET" \
  "https://<host>/api/cron/leads/collect?company_id=<uuid>&limit=5"

curl -X POST -H "X-Cron-Secret: $LEAD_CRON_SECRET" \
  "https://<host>/api/cron/leads/qualify?company_id=<uuid>&limit=20"
```

Both take `company_id` to scope to one tenant and `limit` to shrink the batch —
useful for a smoke test that doesn't burn a full sweep's worth of credits.

## Before the first run

A tenant only appears to the cron once it has search targets. Seed them:

```sh
curl -X POST --cookie "<session>" "https://<host>/api/admin/leads/seed-targets"
```

Re-run it after editing `config/leads/*.json`. It only inserts what is missing —
`enabled` and `last_run_at` belong to the tenant, so an added term never resets
the rotation or re-enables a city an operator switched off.

## What to alert on

`collect` returns `alert` and logs `[leads.collect.zero_rows]` at ERROR when
**every** target in a run returned zero rows.

That is the Indeed failure mode, and it is silence rather than an error: JobSpy
reaches Indeed through an undocumented mobile-app API using a key embedded in
the library, and when that key is rotated upstream every query returns an empty
result set without raising. Pin the library (`python-jobspy==1.1.82`) and watch
that line. `/signals/analytics` shows the same signal as "Zero-row targets".

A partial zero-row run is normal — plenty of `(term, city)` pairs genuinely have
no postings in a 7-day window.
