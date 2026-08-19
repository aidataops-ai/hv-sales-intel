# Deterministic track resolver + webhook mapping flip

**Status:** plan (approved direction; implement measure-first, no prod writes until validated)
**Date:** 2026-08-19
**Supersedes:** the prompt-reword idea in `2026-08-19-qualifier-track-selection.md` (rejected).

## Problem

The lead **track** (which H&V product a lead belongs to) is wrong for specialty
practices, and TalentDB — where the sales process starts — receives the wrong one.
Two independent causes, measured against prod (company
`8ab8db4a-e652-4077-b684-05ab1ccc2ea2`):

- **The qualifier's track is non-deterministic.** It runs at temperature 1; the
  same posting yields `Medical Assistant` one run, `Chiropractic` the next. On 14
  genuinely-chiro leads currently mislabeled, a realistic-batch re-run recovered
  only **6/14 (42%)** and once *discarded* a valid lead. A prompt reword changed
  nothing.
- **The webhook sends the search hint as the track.** `talentdb.py:208` and
  `talentdb_export.py:209` map the track from `service_line_hint` (*how we found
  it*), so a chiropractic practice found by a dental keyword ships as "Dental".
  ~101 of 1,210 exported leads went under a wrong specialty track.

## Principle

**The track is a deterministic function of the posting — the LLM is not in the
track path at all.** Assigning a specialty track is a lookup (the word is in the
title/employer). When there is no such signal, fall back to the search-term hint
(also deterministic). Both branches are deterministic; the qualifier's noisy
`service_line` guess is dropped. The qualifier keeps every other job (keep/discard,
employer type, work mode, provider count, draft) and its **prompt is unchanged** —
we simply stop reading its track field.

**No practice data is used.** Practice/category is available only after linking,
which is *after* the analyzer — so it plays no part here. (Considered and dropped:
a post-link category reconcile. We do not respect practice category.)

## The resolver (the "calculator")

New module `src/track_resolver.py`, promoted from the validated prototype
`scripts/proto_specialty_override.py` (99% precision vs practice category on the
rows it fires, 0 conflicts, fires on 32% of keeps). Pure, no I/O, no model:

- `from_posting(posting) -> str | None` — scans **title + employer only** (never
  the description, where "dental insurance"-type false positives live); fires only
  on an **unambiguous single-specialty** match. Covers chiro / dental /
  assisted-living / home-health.
- `resolve(posting) -> str | None` — the track:
  **`from_posting(posting) or posting["service_line_hint"]`.**
  (Returns None only for a non-kept posting, where a track is never set.)

### Coverage — who decides the track

| Case | Track source |
|---|---|
| Title/employer names chiro / dental / assisted-living / home-health | **calculator** (deterministic) |
| Title *and* employer generic (no specialty word) | **`service_line_hint`** (search-term track) |
| — | qualifier's `service_line` is **not used** |

So a generically-titled lead inherits the track of the **search term that found
it** — e.g. a bare "Front Desk" found by `chiropractic receptionist` → Chiro; found
by `scheduling coordinator` → Scheduler. That is the best deterministic guess when
the posting itself says nothing; specialty-named postings are pinned exactly.

## Changes, in dependency order

**1. `src/track_resolver.py` + unit tests.** Lift the rules from the prototype;
   test the known cases (5827 "…Front Desk Chiropractic" → Chiro; a generic "Front
   Desk Receptionist" with a dental hint → falls back to the hint; the
   "dental insurance" and "Alpha Home Health and…" edge cases do *not* mis-fire).

**2. Qualify-time: resolve the track deterministically.** In
   `lead_qualifier.parse_verdict` (~L219, currently
   `service_line = <model verdict> or hint`), replace with, for a KEPT lead:
   `service_line = track_resolver.from_posting(posting) or posting.get("service_line_hint")`.
   The model's `service_line` is no longer read (the now-dead validation at
   L191-193 can be removed). Discards still get `null`. **No prompt change.**

**3. One-time backfill of `company_job_leads.service_line`.** Staged script: run
   `resolve()` over every kept lead, **back up old values to JSON**, and UPDATE
   where it differs. Fully deterministic — no model calls. Subsumes and retires the
   hand-written `track-fix-20260818.sql`.

**4. Webhook mapping flip.** Only after 2–3 make `service_line` trustworthy:
   - `src/talentdb.py:208`: `track = ld.get("service_line") or pg.get("service_line_hint")`.
   - `scripts/talentdb_export.py:209`: same (it has a **duplicate** `build_fields`).
   - `scripts/backfill_talentdb.py:144`: pass the lead (`find_lead_by_posting`) —
     today it calls `import_lead(practice, posting)` with no lead, so `ld={}` and
     the flip would silently fall back to the hint on that path.
   - Rewrite the now-inverted comments at both mapping sites.
   - Flipping `track` corrects both `interested_tracks` (Tracks UUID) and `Industry`
     (both derive from it). Attribution isn't lost — `search_term` already ships as
     its own field.
   - (Optional cleanup: dedupe the two `build_fields` copies so they can't drift.)

**5. Re-send corrected leads to TalentDB.** After the flip + backfill, re-send the
   affected exported leads via `talentdb_export --resend` (receiver upserts on
   `source_practice_id`, so tracks + Industry correct in place). Scope to the leads
   whose track changed in step 3.

## Validation (data backing at each step)

- Resolver precision: re-run `scripts/proto_specialty_override.py` after building
  the module (expect ~99% agreement with category on the rows it fires — an
  independent check the rule never reads).
- Justification of record: `scripts/diff_analyzer_vs_override.py` (42% analyzer vs
  deterministic) — why the LLM is out of the track path.
- Post-backfill query: for kept leads, `service_line` should equal `resolve()` for
  all of them (it's now defined that way); spot-check specialty leads land right.
- After re-send: chiro lead 5827 shows Chiro + correct Industry in TalentDB; the
  dashboard's "keyword performance by track" is no longer tautological.

## Limits (accepted)

- **Generic-name specialty leads fall to the hint, not the truth.** A
  chiropractic office named "Peak Wellness" with a "Front Desk" title has no
  specialty signal in the posting; it inherits its search term's track, which may
  be wrong if that term was a generic/overlapping keyword. This is accepted — it's
  deterministic and no worse than today, and the truth isn't in the posting.
- **Home Health** relies entirely on title/employer wording (no other signal in
  scope).

## Open decisions

1. ~~Scan the description?~~ **TESTED AND REJECTED 2026-08-19.** On the post-resume
   batch, description scanning fired 25 description-only labels and **24 were false**
   (~96%): "Benefits: • Dental insurance", "clinics or dental practices", "dental
   practice management software" → Dental at nephrology / dermatology / mental-health
   practices. The resolver reads **title + employer only**. Precision on linked
   leads held at 99%; the clean re-audit found 3/456 posting-text specialty mislabels
   on new leads, all correct, 0 false positives.
2. Keep asking the model for `service_line` in the prompt even though we ignore it?
   Default: **yes** — leaving the prompt untouched avoids regression risk; the
   unused field is harmless.
3. **Backfill scope (step 3).** On 456 new leads, `resolve()` would relabel 84: only
   **3 are specialty fixes** (from_posting), the other **~81 are generic postings
   swapping model-track → search hint** (the design's fallback, deterministic but not
   a provable correctness win). Decide whether the backfill applies full `resolve()`
   (consistent with new-lead behavior, larger blast radius) or only the specialty
   fixes (conservative). Same question scaled to the full table.
