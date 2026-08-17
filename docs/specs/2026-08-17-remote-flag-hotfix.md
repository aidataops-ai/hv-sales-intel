# Remote-flag hotfix — collector false positives, qualifier blind spot (2026-08-17)

**Status:** **implemented 2026-08-17** (F1–F3 in `src/job_boards.py` /
`src/lead_qualifier.py` + tests; F4 backfill run: 15 kept leads →
`work_mode='onsite'`, 67 posting flags → `false`, snapshot taken first).
Landed on `main` and cherry-picked to `staging`. Still open: the ~1-week
shadow-log review below, and filing the upstream JobSpy issue.

## Why

Case study: signal 57 (`company_job_leads.id=57`), "Medical Front Office
Receptionist" at Respiratory Critical Care and Sleep Medicine Associates,
Jacksonville FL (indeed `jk=06e883132e9a8d46`). We label it `work_mode=remote`;
the live posting says **"Work Remotely: No"** and **"Work Location: In person"**
(verified against the still-rendering expired page on 2026-08-17).

The causal chain, each link verified:

1. **Collector (root cause).** JobSpy 1.1.82's Indeed scraper decides
   `is_remote` by naive substring search — `"remote" in description.lower()`
   (`jobspy/indeed/util.py:52-68`). The template heading "Work Remotely"
   contains "remote", so a posting that explicitly answers **No** arrives with
   `is_remote=True` → `job_postings.board_remote_flag=true`
   (`src/job_boards.py:200`). The stored description itself is correct — the
   truth was captured, then the wrong flag derived from it.
2. **Qualifier (accomplice by design).** The prompt treats the flag as ground
   truth — *"if remote_flag=true … answer 'remote'"*
   (`src/lead_qualifier.py:106`) — and the model reads only the **first 280
   chars** of the description (`qualifier_excerpt_chars`,
   `config/leads/filters.json`). Indeed puts the work-location template at the
   **end** of the description, so the contradicting evidence is structurally
   invisible. The model then rationalized the flag: the stored `reason` invents
   "remote front-office duties".
3. **Downstream.** `work_mode` also steers the outreach draft — the onsite +
   salary path pitches against the local wage (`src/lead_qualifier.py:112`) —
   so mislabels weaken drafts, not just filters.

## Blast radius (measured against our stored data — no re-scraping)

- 29,806 postings; **1,078** flagged remote (with description).
- **67** of those are provably false: explicit "Work Remotely: No" /
  "Work Location: In person" template, or negated phrasing ("not a remote
  position", "no remote work").
- **34** leads carry a contradicted `work_mode=remote`; **15 are kept signals**
  (lead ids 57, 617, 4479, 4612, 4710, 5447, 8242, 9066, 17752, 21869, 21993,
  23227, 24169, 24437, 25441). The 19 discarded ones are dead rows — out of
  scope.
- **TalentDB is clean**: the one real push (`leadset-100.json`, 2026-08-11/12)
  contains none of the 15; verified against the file, the import CSV, and
  `talentdb_exported_at`.
- Worst-case sweep of all 1,078 flags through the candidate patch flips 77% —
  the pollution is far wider than the provable 67, but only the 67 are
  *provable* retroactively, so only they (and the 15 kept leads) get a data
  fix. 9,376 stored descriptions sit at the 2,000-char cap, so some historical
  evidence is unrecoverable without re-fetching; accepted.
- Upstream check (2026-08-17): 1.1.82 is the latest PyPI release, upstream
  `main` has the identical function, no issue or PR covers this. There is no
  fix to upgrade to.

## LinkedIn sanity check (2026-08-17)

LinkedIn's scraper has the **same naive keyword bug**
(`jobspy/linkedin/util.py:88-96`: substring match over
title+description+location, no structured evidence at all) — but our exposure
is structurally different because **we never fetch LinkedIn descriptions**
(`linkedin_fetch_description` defaults off; all 19,078 LinkedIn rows have
`description=null`). Consequences:

- The LinkedIn flag derives from **title + location only**, which is far less
  noisy: 245/19,078 flagged (1.3%) vs Indeed's 10%. "Remote" in a title is
  usually affirmative.
- Of the 6 kept LinkedIn `work_mode=remote` leads, 4 say Remote in the title
  (correct). **2 anomalies** (lead 1051 "Front Desk Receptionist/Scheduler",
  Winter Park FL; lead 1053 "Billing Specialist", Clearwater FL) carry the
  flag with no remote evidence in any stored field — likely a "(Remote)"
  suffix in LinkedIn's location display at scrape time that our
  `split_location` didn't retain. Not provable retroactively; flag for manual
  review in the UI rather than SQL-flipping.
- The "Work Remotely: No" template is an Indeed description artifact —
  **zero provable LinkedIn false flags, so no LinkedIn backfill**. F2's
  template extraction is likewise Indeed-only in practice (it operates on
  descriptions, which LinkedIn rows don't have).
- Known, separate limitation worth recording: with descriptions unfetched,
  the qualifier judges every LinkedIn posting on title/company/location/salary
  alone — `snippet=""`. Out of scope for this hotfix.

## Ground basis

`scratchpad/test_monkeypatch.py` (session scratchpad; regexes and function to
be lifted verbatim into F1) ran three cases, all green:

- **Real posting** (id 1137, from the DB): upstream `is_job_remote` → `True`
  (bug reproduced); patched → `False`; patched with an adversarial "Remote"
  attribute injected → still `False` (explicit template wins).
- **11 synthetic controls**: location "Remote", Remote attribute, "fully
  remote", "work from home", template-Yes, "Remote Scheduler" title all stay
  `True`; RPM-as-service, template-No, negated phrasing go `False`. Testing
  first caught two real defects in the draft patch (template-separator width —
  the markdown stores `**\n* ` between "Work Remotely" and "No"; and missing
  title evidence, which had cratered worst-case recall to 22%).
- **DB sweep**: all 67 provable false flags flip; title/location-evidenced
  remote postings retained at **108/109 (99%)** even with attributes
  unavailable (they are available live, so real recall is higher).

## The fix

### F1 — monkeypatch `is_job_remote` (primary, collector, both boards)

In `src/job_boards.py`, at module level next to the jobspy import, replace the
scrapers' classifiers with tested functions. **Two patch targets, both
imported by name at their call sites** (patching the `util` modules would miss
them):

- `jobspy.indeed.is_job_remote` (`jobspy/indeed/__init__.py:8`) — signature
  `(job: dict, description: str)`; the fully tested function below.
- `jobspy.linkedin.is_job_remote` (`jobspy/linkedin/__init__.py:17`) —
  signature `(title, description, location)`; a sibling built from the same
  regexes: title evidence (RPM-excluded) + location evidence + affirmative
  description phrasing + onsite-marker override. Descriptions are currently
  always `None` for LinkedIn, so today this only tightens the title match;
  it becomes load-bearing the day `linkedin_fetch_description` is enabled.

The patched logic:

- Employer's explicit template answer beats everything: onsite markers
  (`work remotely …no`, `work location …in person`, `no/not a remote`, with
  `[\W_]{0,10}` separators for the markdown mangling) → `False`.
- Affirmative evidence: Indeed `attributes` labels, location, **title**
  (excluding "Remote Patient Monitoring …" titles — a service name, not a work
  mode), and affirmative description phrasing ("fully remote", "100% remote",
  "remote position/role/job/opportunity/work/only", "work from home", "wfh",
  "telecommut…", template-Yes).
- Bare "remote" substrings in the description no longer count. This is the
  upstream bug.

Pin `python-jobspy==1.1.82` and add a guard test asserting the patch target
exists, so an upgrade cannot silently un-patch.

### F2 — `normalise_row` fallback + evidence preservation (collector)

In `normalise_row()` (`src/job_boards.py:155`), operating on the **full
pre-truncation** description (in scope before the `description_max_chars`
slice):

- Belt-and-braces: if an explicit onsite marker matches, force
  `board_remote_flag=false` even if F1 didn't load.
- Evidence preservation: extract the `Work Remotely: …` / `Work Location: …`
  template lines and ensure they survive the 2,000-char truncation (append
  within budget when the cap would drop them), so the qualifier and any future
  re-qualification can always see them.

### F3 — qualifier stops trusting the flag (prompt + snippet)

`src/lead_qualifier.py`:

- Prompt (`:106`): demote `remote_flag` from mandated evidence to a
  board-derived hint with known keyword false positives; explicit posting text
  overrides it. **Must keep the ADR-08 pinned strings** `"from EVIDENCE, not
  assumption"` and `'Only answer "onsite" when there is no remote or hybrid
  signal'` (`tests/test_lead_qualifier.py:47-52,68-72`) — the fix must not
  reintroduce the default-onsite regression.
- Snippet (`_posting_row`, `:55-62`): keep the 280-char head, and append the
  extracted work-location template line from F2 so the decisive evidence is in
  the model's window. This also lets the qualifier resolve the 7
  "Work Location: Hybrid remote" postings to `hybrid`, which a boolean flag
  cannot express.

### F4 — data backfill (SQL only, zero Indeed traffic, no LLM re-runs)

1. Snapshot the affected ids and current values (reversibility).
2. `UPDATE company_job_leads SET work_mode='onsite' WHERE id IN (…the 15…)`.
3. Optional insurance: `UPDATE job_postings SET board_remote_flag=false` for
   the 67 provable postings — matters only if the shared universe is ever
   re-qualified (e.g. a second tenant); costs nothing.

Discarded leads, re-qualification, and re-scraping are explicitly out of
scope. No calls to Indeed anywhere in this plan.

## Branch strategy — main and staging

Verified with `git diff main staging`: **every patch point is byte-identical
on both branches.** Staging's pipeline changes do not overlap the fix —
`search_jobs` gained timing stats (`elapsed_s`), the qualifier gained an
OpenAI client cache (`:226+`), the GitHub workflow split into `leads.yml` /
`leads-indeed.yml` / `leads-linkedin.yml`. All entry points on both branches
(`scripts/run_leads.py`, `api/index.py`, and all three split workflows) route
through `src/job_boards.py`, so F1/F2 land everywhere with no per-workflow
work.

Order:

1. Branch `hotfix/remote-flag` off `main`; implement F1–F3 + tests; PR → main.
2. Merge `main` → `staging` (expected clean; the diff shows no overlapping
   hunks). Run staging's test suite — it has drifted (~900 lines in
   `api/index.py`) — do not assume main-green implies staging-green.
3. Run F4 once against the shared DB (branch-independent).

## Tests

- `tests/test_job_boards.py`: patch-target-exists guards (both
  `jobspy.indeed` and `jobspy.linkedin`); the ground-truth cases from
  `test_monkeypatch.py`, including the real signal-57 description as a
  fixture; LinkedIn-signature cases (remote title kept, RPM title not,
  `description=None` safe); `normalise_row` override + template-line
  preservation.
- `tests/test_lead_qualifier.py`: ADR-08 pinned strings still present; new
  assertions that the prompt names the flag as fallible and the snippet
  carries the extracted template line.

## Rollout verification (shadow log)

For ~1 week of normal scrape runs, log upstream-vs-patched disagreement per
job (log only; the patched value is what's stored; zero extra Indeed
traffic). Acceptance: no genuinely-remote posting (human spot-check of a
disagreement sample) lost its flag. Then drop the shadow log.

## Upstream

File the issue against `speedyapply/JobSpy` with the evidence (67 provable
false positives / 1,078 flags; substring-match root cause), offer the patched
function as a PR. The local monkeypatch is deletable if it ever merges.

## Risks

- **Patch fragility on upgrade** — guarded by the pin + target-exists test.
- **Recall loss on quiet remote postings** (evidence only in attributes we
  can't see retroactively) — 99% worst-case retention measured; live runs see
  attributes; shadow log is the backstop.
- **ADR-08 regression** (everything-onsite) — pinned prompt strings kept, test
  asserts them.
- **Staging drift** — no overlapping hunks today, but staging moves fast;
  re-diff at merge time.

## Appendix — the ground-tested patch function (verbatim)

Exactly what passed all three test cases on 2026-08-17; lift as-is into F1.

```python
_ONSITE_MARKERS = re.compile(
    r"work\s+remotely[\W_]{0,10}no\b"            # Indeed template: **Work Remotely** / No
    r"|work\s+location[\W_]{0,10}in[\s-]?person"  # Work Location: In person
    r"|\bno\s+remote\b|\bnot\s+(?:a\s+)?remote\b",
    re.I,
)

_POSITIVE_REMOTE = re.compile(
    r"\b(?:fully|100%)\s*remote\b"
    r"|\bremote\s+(?:position|role|job|opportunity|work|only)\b"
    r"|\bwork\s+from\s+home\b|\bwfh\b|\btelecommut"
    r"|work\s+remotely[\W_]{0,10}yes\b",
    re.I,
)

# "Remote Patient Monitoring Coordinator" is a service name, not a work mode.
_TITLE_REMOTE = re.compile(
    r"\bremote\b(?!\s+patient\s+monitoring)|\bwork\s+from\s+home\b|\bwfh\b", re.I
)

_REMOTE_KEYWORDS = ("remote", "work from home", "wfh")


def patched_is_job_remote(job: dict, description: str) -> bool:
    """Attributes, location, and title are primary evidence; the employer's
    explicit template answer ("Work Remotely: No" / "Work Location: In
    person") overrides everything; bare 'remote' substrings in the
    description no longer count — only affirmative remote phrasing does."""
    desc = description or ""
    if _ONSITE_MARKERS.search(desc):
        return False
    in_attributes = any(
        kw in (attr.get("label") or "").lower()
        for attr in (job.get("attributes") or [])
        for kw in _REMOTE_KEYWORDS
    )
    loc = ((job.get("location") or {}).get("formatted") or {}).get("long") or ""
    in_location = any(kw in loc.lower() for kw in _REMOTE_KEYWORDS)
    in_title = bool(_TITLE_REMOTE.search(job.get("title") or ""))
    in_description = bool(_POSITIVE_REMOTE.search(desc))
    return in_attributes or in_location or in_title or in_description
```

Applied as `jobspy.indeed.is_job_remote = patched_is_job_remote` (the name
bound in `jobspy.indeed`, not in `jobspy.indeed.util`).
