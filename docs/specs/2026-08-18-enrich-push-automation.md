# Enrich → Push automation — twice-daily lead delivery (2026-08-18)

**Status:** implemented. Adds `scripts/run_enrich_push.py` and
`.github/workflows/leads-enrich.yml`. No schema change. The live webhook mapping
(`src/talentdb.py`) was aligned the same day — see *Dependencies* below.

## Why

The scheduled pipeline (`leads-indeed.yml` / `leads-linkedin.yml`) ends at
**qualify → practice-link**. Everything after that — AI analyze, the call-script
playbook, the email draft, Clay enrichment, and the Talent-DB push — was run by
hand (`scripts/run_leadset_batched.py` + `scripts/talentdb_export.py`). This
document specifies the automation that closes the loop: **every new qualified
lead is enriched and delivered to Talent-DB without a human in the loop**, on a
twice-daily cadence.

## The one constraint that shapes the design: Clay is asynchronous

`src/clay.py::trigger_enrichment` does **not** return the enriched contact. It
POSTs the practice to Clay and marks `practices.enrichment_status = "pending"`;
the `owner_email` / `owner_name` / … land **later** via the inbound webhook
`POST /api/webhooks/clay` (`api/index.py`), which flips the status to
`enriched` / `failed`.

So enrichment and push **cannot** happen in the same run — a same-run push would
send the lead before Clay called back, i.e. without the enriched contact. The
fix is to **decouple** them across runs and let wall-clock time cover Clay's
round-trip. Two runs a day, ~12h apart, is exactly enough.

## Design — two phases per run, push first

Each run of `scripts/run_enrich_push.py` does, in order:

**Phase A — PUSH the ready ones.** Every lead that is
`decision = keep` **AND** not yet exported (`talentdb_exported_at IS NULL`)
**AND** whose practice is fully enriched (`call_script` **and** `email_draft`
present) **AND** whose Clay enrichment has *resolved* is written to a leadset and
sent through `scripts/talentdb_export.py` (which additionally skips billing-held
and already-exported rows, and stamps `talentdb_exported_at` on success).

*Clay "resolved"* = `enrichment_status != 'pending'` (i.e. `enriched`, `failed`,
`skipped`, or null when Clay is not configured), **or** a stuck-pending fallback:
`pending` with `email_draft_updated_at` older than `--stuck-pending-hours`
(default 6h), so a lost Clay callback can never strand a lead forever.

**Phase B — ENRICH the new keeps.** Every lead that is `decision = keep`, not yet
exported, has an employer, and is **not** yet enriched (no practice, or a
practice missing `call_script`/`email_draft`) is written to a leadset and run
through `scripts/run_leadset_batched.py`: Places → `analyze_practice` →
`generate_script` ∥ `generate_email_draft` → `trigger_enrichment` (Clay). These
become Phase-A-eligible **at the next run**, once Clay has called back.

**Why push-first:** the pushes are already-ready work from a prior run; running
them before the long, paid Phase B means a Phase-B timeout or cap-halt never
delays a delivery that was ready to go.

```
run N       Phase A: push leads enriched in run N-1 (Clay now done)
            Phase B: enrich new keeps  ──▶ trigger Clay ──┐
                                                          │ ~12h, Clay calls back
run N+1     Phase A: push those leads  ◀───────────────────┘
            Phase B: enrich the next batch of new keeps
```

**Latency:** a lead is delivered ~12–24h after it qualifies (enriched at the
next run, pushed at the one after). That is the price of guaranteeing
Clay-complete data, and it is what "two runs cover freshness" buys.

## Selection (both phases share one base query)

Base universe, paginated (`_PAGE = 1000`, PostgREST 1000-row cap):
`job_postings !inner company_job_leads` where `lead.company_id = tenant`,
`lead.decision = 'keep'`, `lead.talentdb_exported_at IS NULL`,
`employer_name NOT NULL`, `service_line_hint NOT NULL` (any real track — **not**
a hardcoded track list, so a newly-added track like *Virtual Assisted Living
Coordinator* is included automatically).

For each posting, its linked `practices` row (if any) supplies
`call_script`, `email_draft`, `enrichment_status`, `email_draft_updated_at`.
Partition:

- **fully enriched + Clay resolved** → Phase A (push now)
- **fully enriched + Clay still pending (not stuck)** → *wait* (neither phase; pushed next run)
- **not enriched** (no practice, or missing script/email) → Phase B (enrich now)

## Safety

- **Places spend cap is untouched.** Phase B calls `run_leadset_batched.py`,
  whose producer is the single serial Places caller and enforces
  `--max-places-calls` / `--max-usd` before every call (the control from the
  2026-08-07 uncapped-Places incident). Workflow defaults: 250 calls / $25 per
  run — well above steady-state, bounded well below a runaway. A large initial
  backlog therefore drains over several runs rather than in one uncapped burst.
- **Dry-run by default.** `run_enrich_push.py` sends nothing and enriches nothing
  unless `--yes` is passed (mirroring both underlying runners). The workflow
  passes `--yes`.
- **Billing-held leads** are still excluded by `talentdb_export._is_billing`.
- **Idempotent.** Re-running is safe: Phase A skips already-exported rows, Phase B
  skips already-analyzed practices. A crash mid-run strands nothing — the next
  run re-selects from live state.

## Known v1 tradeoff

`run_leadset_batched`'s producer makes **one Places call per lead even when the
posting is already practice-linked** (it refreshes the practice via a fresh
Places search). For repeat-employer keeps already linked by the qualify-stage
matcher, that Places call is redundant spend. It is bounded by the cap and the
practice refresh is harmless. **Future optimization:** skip the Places call when
`practice_id` is already set and only the AI/Clay half is missing.

## Cadence

`.github/workflows/leads-enrich.yml`, cron `30 5,17 * * *` — **05:30 and 17:30
UTC** (≈ 10:30 / 22:30 PKT). Offset from the `:17` (Indeed) and `:41` (LinkedIn)
sweeps and off GitHub's congested top-of-hour. Own concurrency group
(`leads-enrich`, `cancel-in-progress: false`) so a long run queues the next tick
rather than overlapping itself. `workflow_dispatch` exposes the caps and a
`dry-run` toggle for manual runs.

## Dependencies

- **`src/talentdb.py` mapping fixes (same day):** hint-first track resolution,
  the new `Industry` field, and the `_scrub_email` placeholder drop. The push in
  Phase A uses this mapping, so the automated deliveries carry the corrected
  track + Industry and never send a `"Not Found"` email. See the git history for
  `src/talentdb.py` on 2026-08-18.

## Rollout

1. Deploy the `src/talentdb.py` mapping fixes (already merged separately).
2. Land this workflow on the **default branch** — GitHub only runs `schedule`
   from the default branch (same rule as the sweeps).
3. Watch the **first** run: it drains the standing enrich backlog up to the cap,
   so expect several cap-limited runs before steady-state. Confirm Phase A stays
   at 0 pushes until a full run-to-run cycle has let Clay call back.
4. Steady-state: each run enriches a day-half of new keeps and pushes the prior
   half. Monitor `talentdb_exported_at` counts and the Clay `pending` backlog.
