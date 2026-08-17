"""Job-board collection — the only module that knows `python-jobspy` exists.

JobSpy reaches Indeed through its undocumented mobile-app API and LinkedIn
through the public guest endpoint. Both are markup/API surfaces we do not
control, so every board-shaped assumption is isolated here: the id extraction,
the pandas quirks, the column names. Everything downstream sees rows shaped
like the `job_postings` table.

Two behaviours are load-bearing and easy to lose in a refactor:

* **No employer filtering happens here** beyond the config prefilter. Deciding
  whether an employer is a small independent practice is the qualifier's job
  and it needs the full candidate set to make that call.
* **`external_id` is per-source.** Indeed's is the 16-hex `jk` query param;
  LinkedIn's is the trailing numeric id in `/jobs/view/...`. Different
  namespaces — hence `unique (source, external_id)` rather than a global one.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timezone

from src import lead_config

log = logging.getLogger("hvsi.leads.boards")

# US state codes, used to pull a 2-letter state out of a board location string.
_STATE_RE = re.compile(r"\b([A-Z]{2})\b")
_INDEED_ID = re.compile(r"[?&]jk=([0-9a-f]+)")
_LINKEDIN_ID = re.compile(r"/jobs/view/(?:.*-)?(\d+)")

# Legal suffixes and punctuation that differ between how two boards render the
# same employer ("Blanding Dental Associates" vs "Blanding Dental Assoc., LLC").
_SUFFIX = re.compile(
    r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|pa|p\.a|pllc|"
    r"llp|ltd|co|company|pc|p\.c)\b\.?",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^a-z0-9\s]")
_SPACE = re.compile(r"\s+")

# JobSpy 1.1.82 decides `is_remote` by substring-matching "remote" anywhere in
# the description, so Indeed's own "Work Remotely: No" template flags a posting
# remote (67 provable false positives as of 2026-08-17; signal 57 was the case
# study — docs/specs/2026-08-17-remote-flag-hotfix.md). Corrected in three
# layers below: `_patch_jobspy_remote_flags` swaps both boards' classifiers at
# scrape time, `normalise_row` re-checks the full pre-truncation description,
# and `_capped_description` keeps the decisive template lines — which Indeed
# renders at the very end, past the storage cap for a third of postings —
# inside what gets stored.
#
# The separators allow up to 10 non-word chars because the HTML→markdown
# conversion renders the template heading as "**Work Remotely**\n* No".
_ONSITE_MARKERS = re.compile(
    r"work\s+remotely[\W_]{0,10}no\b"
    r"|work\s+location[\W_]{0,10}in[\s-]?person"
    r"|\bno\s+remote\b|\bnot\s+(?:a\s+)?remote\b",
    re.IGNORECASE,
)
_POSITIVE_REMOTE = re.compile(
    r"\b(?:fully|100%)\s*remote\b"
    r"|\bremote\s+(?:position|role|job|opportunity|work|only)\b"
    r"|\bwork\s+from\s+home\b|\bwfh\b|\btelecommut"
    r"|work\s+remotely[\W_]{0,10}yes\b",
    re.IGNORECASE,
)
# "Remote Patient Monitoring Coordinator" names a service line, not a work mode.
_TITLE_REMOTE = re.compile(
    r"\bremote\b(?!\s+patient\s+monitoring)|\bwork\s+from\s+home\b|\bwfh\b",
    re.IGNORECASE,
)
_REMOTE_KEYWORDS = ("remote", "work from home", "wfh")

_WORK_REMOTELY = re.compile(r"work\s+remotely[\W_]{0,10}(yes|no)\b", re.IGNORECASE)
_WORK_LOCATION = re.compile(r"work\s+location[\W_]{0,10}([^\n]{1,60})", re.IGNORECASE)


def extract_work_arrangement(description: str | None) -> str | None:
    """Pull Indeed's work-arrangement template answers out of a description.

    "Work Remotely: No" / "Work Location: In person / Hybrid remote in …" are
    the employer's explicit answers from the posting form — the decisive
    work-mode evidence — and they sit exactly where both the storage cap and
    the qualifier's head excerpt cut. Returned canonicalised ("Work Remotely:
    No | Work Location: In person") for re-appending wherever they'd be lost.
    """
    if not description:
        return None
    parts = []
    m = _WORK_REMOTELY.search(description)
    if m:
        parts.append(f"Work Remotely: {m.group(1).capitalize()}")
    m = _WORK_LOCATION.search(description)
    if m:
        value = m.group(1).strip().strip("*").strip()
        if value:
            parts.append(f"Work Location: {value}")
    return " | ".join(parts) or None


def _patched_indeed_is_remote(job: dict, description: str) -> bool:
    """Strict replacement for `jobspy.indeed.is_job_remote`.

    The employer's explicit template answer beats everything; after that,
    Indeed's structured attributes, the location, and the title are the
    affirmative evidence. Bare "remote" substrings in the description no
    longer count — that substring match is the upstream bug.
    """
    desc = description or ""
    if _ONSITE_MARKERS.search(desc):
        return False
    in_attributes = any(
        keyword in (attr.get("label") or "").lower()
        for attr in (job.get("attributes") or [])
        for keyword in _REMOTE_KEYWORDS
    )
    location = ((job.get("location") or {}).get("formatted") or {}).get("long") or ""
    in_location = any(keyword in location.lower() for keyword in _REMOTE_KEYWORDS)
    in_title = bool(_TITLE_REMOTE.search(job.get("title") or ""))
    return in_attributes or in_location or in_title or bool(_POSITIVE_REMOTE.search(desc))


def _patched_linkedin_is_remote(title, description, location) -> bool:
    """Strict replacement for `jobspy.linkedin.is_job_remote` (its signature).

    LinkedIn descriptions are only present when `linkedin_fetch_description`
    is on (it is off today), so in practice this tightens the title/location
    match; the description clauses matter the day fetching is enabled.
    """
    desc = description or ""
    if _ONSITE_MARKERS.search(desc):
        return False
    loc = location.display_location() if hasattr(location, "display_location") else str(location or "")
    in_location = any(keyword in loc.lower() for keyword in _REMOTE_KEYWORDS)
    in_title = bool(_TITLE_REMOTE.search(str(title or "")))
    return in_location or in_title or bool(_POSITIVE_REMOTE.search(desc))


def _patch_jobspy_remote_flags() -> None:
    """Swap both boards' `is_job_remote` for the strict versions, idempotently.

    The patch targets are the names bound in `jobspy.indeed` and
    `jobspy.linkedin` — the call sites import the function by name, so
    patching the `util` modules they came from would miss them. Each wrapper
    also runs the original and logs a shadow line on disagreement, so the
    rollout can be reviewed from normal run logs (zero extra board traffic).
    """
    import jobspy.indeed
    import jobspy.linkedin

    if getattr(jobspy.indeed.is_job_remote, "_hvsi_patched", False):
        return

    orig_indeed = jobspy.indeed.is_job_remote
    orig_linkedin = jobspy.linkedin.is_job_remote

    def indeed_wrapper(job, description):
        verdict = _patched_indeed_is_remote(job, description)
        try:
            upstream = orig_indeed(job, description)
        except Exception:
            upstream = verdict
        if upstream != verdict:
            log.info("[leads.remote_flag.shadow] source=indeed old=%s new=%s title=%r",
                     upstream, verdict, (job.get("title") or "")[:80])
        return verdict

    def linkedin_wrapper(title, description, location):
        verdict = _patched_linkedin_is_remote(title, description, location)
        try:
            upstream = orig_linkedin(title, description, location)
        except Exception:
            upstream = verdict
        if upstream != verdict:
            log.info("[leads.remote_flag.shadow] source=linkedin old=%s new=%s title=%r",
                     upstream, verdict, str(title)[:80])
        return verdict

    indeed_wrapper._hvsi_patched = True
    linkedin_wrapper._hvsi_patched = True
    jobspy.indeed.is_job_remote = indeed_wrapper
    jobspy.linkedin.is_job_remote = linkedin_wrapper


def _capped_description(description: str, max_chars: int) -> str | None:
    """Truncate to the storage cap without losing the work-arrangement lines."""
    if not description:
        return None
    if len(description) <= max_chars:
        return description
    head = description[:max_chars]
    arrangement = extract_work_arrangement(description)
    if not arrangement or extract_work_arrangement(head) == arrangement:
        return head
    suffix = f"\n{arrangement}"
    return description[: max_chars - len(suffix)].rstrip() + suffix


def normalise_employer(name: str | None) -> str | None:
    """Fold an employer name to a comparable key.

    Stored alongside the raw name so a future practices-linking step (deferred
    out of v1) has something to match on without re-deriving the rule.
    """
    if not name:
        return None
    s = _SUFFIX.sub(" ", name.lower())
    s = _PUNCT.sub(" ", s)
    return _SPACE.sub(" ", s).strip() or None


def _clean(value) -> str:
    """Board cells arrive as pandas objects; the string 'nan' is a real hazard.

    `str(float('nan'))` is `'nan'`, and an employer literally named "nan" would
    otherwise sail through every downstream check as a valid name.
    """
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in ("nan", "none", "nat") else text


def _number(value) -> float | None:
    """pandas leaves a missing numeric as `float('nan')`, not None.

    `nan != nan` is the only reliable check — a truthiness test passes it
    through and a `None` comparison misses it.
    """
    if value is None or value != value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def external_id(source: str, url: str) -> str | None:
    """Stable per-source posting id, extracted from the posting URL.

    Falls back to the full URL so a row is never silently dropped for want of
    an id — a URL is still stable enough to dedupe on.
    """
    if source == "indeed":
        match = _INDEED_ID.search(url or "")
        if match:
            return match.group(1)
    if source == "linkedin":
        match = _LINKEDIN_ID.search(url or "")
        if match:
            return match.group(1)
    return (url or "").strip() or None


def split_location(raw: str | None) -> tuple[str | None, str | None]:
    """`'Orange Park, FL, US'` -> `('Orange Park', 'FL')`.

    The city is the stable leading segment; the two boards format the rest
    differently, and LinkedIn sometimes omits the country entirely.
    """
    text = _clean(raw)
    if not text:
        return None, None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    city = parts[0] if parts else None
    state = None
    for part in parts[1:]:
        match = _STATE_RE.fullmatch(part.upper())
        if match:
            state = match.group(1)
            break
    return city or None, state


def _posted_at(value) -> str | None:
    """Board posting date -> ISO timestamp, or None when the board omits it.

    Indeed reports a date on 100% of rows; LinkedIn on ~71%. A missing date is
    left null rather than defaulted to now(), because the feed's posted-date
    column is how an operator judges staleness before calling (ADR known issue
    4) — a fabricated "today" would make every stale lead look fresh.
    """
    if isinstance(value, (datetime, date)) and not isinstance(value, bool):
        if isinstance(value, datetime):
            stamped = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return stamped.isoformat()
        return datetime(
            value.year, value.month, value.day, tzinfo=timezone.utc
        ).isoformat()
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def is_negative(title: str, employer: str) -> bool:
    """Deterministic config prefilter. Every row killed here is saved spend."""
    pattern = lead_config.negative_pattern()
    if pattern is None:
        return False
    return bool(pattern.search(f"{title} {employer}"))


def normalise_row(source: str, row: dict, target: dict | None = None) -> dict | None:
    """One board record -> one `job_postings`-shaped dict, or None if dropped.

    Drops are, in order: the config prefilter, a missing id, and — when
    `include_confidential` is off — postings with no employer name.
    """
    opts = lead_config.options()
    target = target or {}

    title = _clean(row.get("title"))
    employer = _clean(row.get("company"))
    if not title:
        return None
    if is_negative(title, employer):
        return None

    url = _clean(row.get("job_url")) or _clean(row.get("url"))
    ext = external_id(source, url)
    if not ext:
        return None

    if not employer and not opts["include_confidential"]:
        return None

    location_raw = _clean(row.get("location"))
    city, state = split_location(location_raw)
    remote_flag = row.get("is_remote")
    description = _clean(row.get("description"))
    # Re-checked here on the full pre-truncation text so the flag is right
    # even on a code path where `_patch_jobspy_remote_flags` never ran.
    if remote_flag and description and _ONSITE_MARKERS.search(description):
        remote_flag = False

    return {
        "source": source,
        "external_id": ext,
        "url": url or None,
        "title": title,
        "employer_name": employer or None,
        "employer_name_norm": normalise_employer(employer),
        "location_raw": location_raw or None,
        "city": city,
        # A city query already knows its state; use it when the board's own
        # location string is too terse to parse one out ("Remote", "Florida").
        "state": state or (target.get("state") or None),
        "posted_at": _posted_at(row.get("date_posted")),
        "salary_min": _number(row.get("min_amount")),
        "salary_max": _number(row.get("max_amount")),
        "salary_interval": _clean(row.get("interval")) or None,
        "board_remote_flag": bool(remote_flag) if remote_flag is not None else None,
        "description": _capped_description(description, opts["description_max_chars"]),
        "search_term": target.get("term"),
        "search_location": target.get("location"),
        "service_line_hint": target.get("service_line"),
    }


def search_jobs(
    term: str,
    location: str,
    sources: list[str] | tuple[str, ...] | None = None,
    target: dict | None = None,
    results_wanted: int | None = None,
    hours_old: int | None = None,
    distance: int | None = None,
) -> tuple[list[dict], dict]:
    """Search one `(term, location)` pair and return `(rows, stats)`.

    `stats` is per-source `{rows, dropped, ms, error}`. A board raising is
    recorded and skipped rather than propagated: one source failing must
    degrade the run, not halt it, or an Indeed key rotation would also take
    LinkedIn down (ADR-02).
    """
    from jobspy import scrape_jobs

    _patch_jobspy_remote_flags()

    params = lead_config.search_params()
    sources = list(sources or lead_config.enabled_sources())
    found: list[dict] = []
    stats: dict[str, dict] = {}

    for source in sources:
        started = time.time()
        try:
            df = scrape_jobs(
                site_name=[source],
                search_term=term,
                location=location,
                results_wanted=results_wanted or params["results_wanted"],
                hours_old=hours_old or params["hours_old"],
                distance=distance or params["distance_miles"],
                country_indeed="USA",
                verbose=0,
            )
        except Exception as e:
            stats[source] = {
                "rows": 0, "dropped": 0,
                "ms": int((time.time() - started) * 1000),
                "error": f"{type(e).__name__}: {str(e)[:160]}",
            }
            log.warning("[leads.board.error] source=%s term=%s location=%s %s",
                        source, term, location, stats[source]["error"])
            continue

        ms = int((time.time() - started) * 1000)
        records = [] if df is None or df.empty else df.to_dict("records")

        kept = 0
        dropped = 0
        for record in records:
            row = normalise_row(source, record, target)
            if row is None:
                dropped += 1
                continue
            found.append(row)
            kept += 1

        stats[source] = {"rows": kept, "dropped": dropped, "ms": ms, "error": None}
        log.info("[leads.board] source=%s term=%r location=%r kept=%d dropped=%d ms=%d",
                 source, term, location, kept, dropped, ms)

    return found, stats
