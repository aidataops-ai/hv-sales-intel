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
        "description": description[: opts["description_max_chars"]] or None,
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
