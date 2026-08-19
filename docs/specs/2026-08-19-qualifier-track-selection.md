# Qualifier track selection — DB-derived tracks + selection rule

> **SUPERSEDED (2026-08-19) by `2026-08-19-deterministic-track-resolver.md`.**
> The prompt-reword hypothesis was tested and REJECTED: on 16 known-bad chiro
> leads the candidate prompt gave 13/16 = the current prompt (no gain, one
> regression), and a realistic-batch A/B put the analyzer at 42% on specialty
> tracks — a temperature-1 *variance* problem no wording fixes. The track is now
> resolved deterministically in code; the qualifier prompt is left UNCHANGED.
> Kept for the record only.

**Status:** REJECTED / superseded
**Date:** 2026-08-19
**Problem:** the qualifier picks the wrong *track* two ways —
1. **Assisted Living is unassignable.** `build_prompt` / `parse_verdict` take the
   allowed track set from `lead_config.service_lines()` (roles.json, 6 tracks).
   `Virtual Assisted Living Coordinator` lives only in the DB `search_terms`
   (added by `scripts/add_assisted_living_track.py`), so the model is never
   offered it and `parse_verdict` rejects it → 100% miss (falls back to hint).
2. **Chiro (and any specialty) leaks to the generic track.** The prompt gives
   *no* rule for choosing a track — one line, `EXACTLY one of: {lines}` — and
   `Virtual Medical Assistant` is listed first + is generic, so the model
   defaults to it. Measured: of 74 kept postings whose text says "chiropractic",
   12 (16%) were filed as Medical Assistant.

Only the qualifier is in scope. It runs on a **raw posting, before practice
linking** (`scripts/run_leads.py`: qualify at ~L334, `link_postings` at ~L362),
so there is **no practice / category** to lean on — the track must come from the
posting text alone. That is enough: the specialty is usually in the title or
employer name (the chiro misses above literally say "chiropractic").

The tuned keep/discard TESTs and `work_mode` rules are **not touched** — the
docstring warns rewording brings back measured regressions, and none of that
logic lives in the (currently empty) track-selection path.

---

## Change 1 — a tenant-tracks helper (new)

`src/lead_targets.py`:

```python
def tenant_tracks(company_id: str) -> tuple[str, ...]:
    """Distinct service lines this tenant actually searches, from `search_terms`.

    The qualifier's allowed track set. DB-derived so a track added to
    `search_terms` (e.g. the DB-only Assisted Living track) is assignable
    without a roles.json edit. Falls back to the checked-in config when the DB
    is unreachable so a qualify run never loses its track list.
    """
    from src.storage import _get_client

    client = _get_client()
    if not client or not company_id:
        return lead_config.service_lines()
    try:
        rows = (
            client.table("search_terms")
            .select("service_line, enabled")
            .eq("company_id", company_id)
            .order("service_line", desc=False)
            .execute()
        ).data or []
    except Exception as e:  # noqa: BLE001
        log.warning("[leads.tenant_tracks.error] %s: %s", type(e).__name__, str(e)[:200])
        return lead_config.service_lines()
    tracks = [r["service_line"] for r in rows
              if r.get("enabled") and (r.get("service_line") or "").strip()]
    # dedupe, keep first-seen order; fall back if the tenant has no rows yet
    seen: list[str] = []
    for t in tracks:
        if t not in seen:
            seen.append(t)
    return tuple(seen) or lead_config.service_lines()
```

## Change 2 — `build_prompt` takes the track set + gains a selection rule

`src/lead_qualifier.py::build_prompt`:

```diff
-def build_prompt(postings: list[dict]) -> str:
-    tracks = lead_config.service_lines()
+def build_prompt(postings: list[dict], tracks: tuple[str, ...] | None = None) -> str:
+    tracks = tracks or lead_config.service_lines()
     rows = "\n".join(_posting_row(i + 1, p) for i, p in enumerate(postings))
     lines = ", ".join(f'"{t}"' for t in tracks)
     tracks_bullets = "\n".join("  - " + t for t in tracks)
```

Add a **CHOOSING THE TRACK** block right after the `{tracks_bullets}` list
(between the tracks list and `For EACH posting below, decide KEEP or DISCARD`):

```
CHOOSING THE TRACK (only for a KEPT posting):
Pick the ONE track that fits what THIS posting is for. Judge ONLY from the job title, employer name, and snippet — do not assume a specialty that is not stated.
- If the posting names a specialty we have a track for, use that specialty's track:
    chiropractic / chiropractor            -> Virtual Chiropractic Assistant
    dental / dentist / orthodontic / oral surgery -> Virtual Dental Assistant
    home health / home care / hospice      -> Virtual Home Health Operations Coordinator
    assisted living / senior living / memory care / skilled nursing -> Virtual Assisted Living Coordinator
    medical scribe / clinical documentation -> Virtual Medical Scribe
    scheduling / patient scheduler as the CORE duty -> Virtual Medical Scheduler
- Virtual Medical Assistant is the GENERIC fallback: use it ONLY for general medical / front-office admin roles where the posting names none of the specialties above.
- When a specialty above is clearly named, prefer its track over the generic one. Use only the tracks listed for this run.
```

And expand the output-field line (do **not** touch the other fields):

```diff
-- "service_line": for KEEP, EXACTLY one of: {lines}. For discard, null.
+- "service_line": for KEEP, EXACTLY one of: {lines}, chosen per "CHOOSING THE TRACK" above. For discard, null.
```

## Change 3 — `parse_verdict` validates against the same set

`src/lead_qualifier.py::parse_verdict` (so a valid DB track like Assisted Living
is not rejected as "invalid" and forced back to the hint):

```diff
-def parse_verdict(raw: dict, posting: dict, model: str | None) -> dict | None:
+def parse_verdict(raw: dict, posting: dict, model: str | None,
+                  tracks: tuple[str, ...] | None = None) -> dict | None:
+    allowed = tracks or lead_config.service_lines()
     ...
     service_line = raw.get("service_line")
-    if service_line not in lead_config.service_lines():
+    if service_line not in allowed:
         service_line = None
```

## Change 4 — `qualify_batch` derives the set once and threads it

`src/lead_qualifier.py::qualify_batch`:

```diff
     client = _client()
     model = settings.qualifier_model
     effort: str | None = settings.qualifier_reasoning_effort
-    prompt = build_prompt(postings)
+    tracks = lead_targets.tenant_tracks(company_id) if company_id else lead_config.service_lines()
+    prompt = build_prompt(postings, tracks)
     ...
-        verdict = parse_verdict(raw, posting, getattr(response, "model", model))
+        verdict = parse_verdict(raw, posting, getattr(response, "model", model), tracks)
```

Add `lead_targets` to the module import (`from src import job_boards, lead_config,
lead_store, lead_targets`). No circular import — `lead_targets` does not import
`lead_qualifier`.

---

## Ship gate

Run `scripts/eval_track_prompt.py --run` first (current vs candidate prompt on a
posting-text-labeled sample). Ship only if specialty-track accuracy rises
(expect Assisted Living 0 → most, chiro's ~16% text-miss shrinking) **with no
regression** on dental / home-health. The keep/discard rate is unaffected — this
touches only the `service_line` field — but the eval prints it too as a canary.
