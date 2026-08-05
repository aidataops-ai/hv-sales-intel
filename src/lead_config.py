"""Reader and validator for `config/leads/`.

**This module is the only thing in the codebase that opens those files.**
Everything downstream reads `company_search_targets` (ADR-03) or calls the
accessors here. The seam matters: search-term quality is the single largest
driver of lead quality, so the terms need to be tunable by hand in a
reviewable diff — and exactly one place needs to know their file layout.

`tests/test_lead_config.py` asserts the seam holds.

Config is read once and cached. The files are checked in, so a change means
a deploy; `reload()` exists for tests.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any

CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "leads",
)

# Sources the collector knows how to normalise. A config file naming anything
# else is a typo, not a feature request — job_boards.py has per-source
# external_id extraction that has to exist first.
KNOWN_SOURCES = ("indeed", "linkedin")


class LeadConfigError(ValueError):
    """A config file is missing, malformed, or internally inconsistent."""


def _read(name: str) -> dict[str, Any]:
    path = os.path.join(CONFIG_DIR, name)
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise LeadConfigError(f"missing config file: {path}") from e
    except json.JSONDecodeError as e:
        raise LeadConfigError(f"{name} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise LeadConfigError(f"{name} must contain a JSON object")
    return data


# ---------------------------------------------------------------------------
# roles.json
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def role_terms() -> tuple[dict[str, str], ...]:
    """Every enabled `(term, service_line)` pair, in file order.

    Keys prefixed with `_` are prose notes for whoever tunes the file and are
    ignored here — including `_parked_terms`, which holds out-of-scope tracks
    so re-enabling one is a copy-paste rather than a rebuild.
    """
    raw = _read("roles.json").get("terms")
    if not isinstance(raw, list) or not raw:
        raise LeadConfigError("roles.json: `terms` must be a non-empty list")

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise LeadConfigError(f"roles.json: terms[{i}] must be an object")
        term = (entry.get("term") or "").strip()
        service_line = (entry.get("service_line") or "").strip()
        if not term:
            raise LeadConfigError(f"roles.json: terms[{i}] has no `term`")
        if not service_line:
            raise LeadConfigError(f"roles.json: terms[{i}] ({term}) has no `service_line`")
        key = term.lower()
        if key in seen:
            raise LeadConfigError(f"roles.json: duplicate term {term!r}")
        seen.add(key)
        out.append({"term": term, "service_line": service_line})
    return tuple(out)


def service_lines() -> tuple[str, ...]:
    """The distinct service lines in scope, in first-appearance order.

    The qualifier constrains its `service_line` output to exactly this set,
    so it is derived from the terms rather than listed separately — a track
    with no search term would produce leads nothing could ever collect.
    """
    seen: list[str] = []
    for entry in role_terms():
        if entry["service_line"] not in seen:
            seen.append(entry["service_line"])
    return tuple(seen)


# ---------------------------------------------------------------------------
# geography.json
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def locations() -> tuple[dict[str, str], ...]:
    """Enabled states flattened into concrete board `location` strings.

    Both granularities are emitted per state: the statewide query for
    breadth, then each city. Statewide relevance ranking buries the
    single-location practices that city queries surface.
    """
    states = _read("geography.json").get("states")
    if not isinstance(states, list) or not states:
        raise LeadConfigError("geography.json: `states` must be a non-empty list")

    out: list[dict[str, str]] = []
    for i, state in enumerate(states):
        if not isinstance(state, dict):
            raise LeadConfigError(f"geography.json: states[{i}] must be an object")
        if not state.get("enabled"):
            continue
        code = (state.get("code") or "").strip().upper()
        if len(code) != 2:
            raise LeadConfigError(
                f"geography.json: states[{i}] needs a 2-letter `code` (got {code!r})"
            )
        statewide = (state.get("statewide_query") or "").strip()
        if statewide:
            out.append({"query": statewide, "state": code, "granularity": "state"})
        for city in state.get("cities") or []:
            city = (city or "").strip()
            if city:
                out.append({"query": city, "state": code, "granularity": "city"})

    if not out:
        raise LeadConfigError("geography.json: no enabled state produced a location")
    return tuple(out)


@lru_cache(maxsize=1)
def search_params() -> dict[str, int]:
    """Board search knobs: how far back to look and how much to pull.

    `hours_old` is the recency window of the SEARCH. It is unrelated to lead
    lifetime — v1 keeps leads indefinitely (design doc §11.1).
    """
    raw = _read("geography.json").get("search") or {}
    return {
        "hours_old": int(raw.get("hours_old") or 168),
        "results_wanted": int(raw.get("results_wanted") or 40),
        "distance_miles": int(raw.get("distance_miles") or 50),
    }


# ---------------------------------------------------------------------------
# filters.json
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def negative_pattern() -> re.Pattern[str] | None:
    """One compiled alternation of every deterministic drop pattern.

    Applied to `"<title> <employer_name>"` before any model call. Returns
    None when the list is empty, so callers can skip the match entirely.
    """
    patterns = _read("filters.json").get("negative_patterns") or []
    cleaned = [p for p in patterns if isinstance(p, str) and p.strip()]
    if not cleaned:
        return None
    try:
        return re.compile("|".join(cleaned), re.IGNORECASE)
    except re.error as e:
        raise LeadConfigError(f"filters.json: bad negative_patterns regex: {e}") from e


@lru_cache(maxsize=1)
def enabled_sources() -> tuple[str, ...]:
    """Board names to collect from, heaviest-weighted first.

    Weight drives rotation share, not per-query behaviour: Indeed is ~15x
    faster than LinkedIn, so it runs frequently and broadly while LinkedIn
    supplements on a slower cycle (design doc §6).
    """
    sources = _read("filters.json").get("sources") or {}
    picked: list[tuple[int, str]] = []
    for name, cfg in sources.items():
        if name.startswith("_"):
            continue
        if name not in KNOWN_SOURCES:
            raise LeadConfigError(
                f"filters.json: unknown source {name!r} "
                f"(known: {', '.join(KNOWN_SOURCES)})"
            )
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        picked.append((int(cfg.get("weight") or 1), name))
    if not picked:
        raise LeadConfigError("filters.json: no source is enabled")
    picked.sort(key=lambda pair: (-pair[0], pair[1]))
    return tuple(name for _, name in picked)


def source_weight(source: str) -> int:
    """Rotation weight for one source. 0 when it is disabled or unknown."""
    sources = _read("filters.json").get("sources") or {}
    cfg = sources.get(source)
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return 0
    return int(cfg.get("weight") or 1)


@lru_cache(maxsize=1)
def options() -> dict[str, Any]:
    """Collection options with their defaults applied.

    `include_confidential` resolves design doc §11.3 — ~12% of Indeed
    postings carry no employer name. They are still qualifiable on role and
    location and the posting URL is workable, so v1 surfaces them flagged
    rather than dropping them. Flip the config key to suppress at collection.
    """
    raw = _read("filters.json").get("options") or {}
    return {
        "include_confidential": bool(raw.get("include_confidential", True)),
        "description_max_chars": int(raw.get("description_max_chars") or 2000),
        "qualifier_excerpt_chars": int(raw.get("qualifier_excerpt_chars") or 280),
    }


# ---------------------------------------------------------------------------


def validate() -> dict[str, int]:
    """Parse every file and return a summary. Raises LeadConfigError on the
    first problem. Called by the seed-targets endpoint so a bad config fails
    loudly at deploy time instead of silently collecting nothing."""
    terms = role_terms()
    locs = locations()
    return {
        "terms": len(terms),
        "service_lines": len(service_lines()),
        "locations": len(locs),
        "sources": len(enabled_sources()),
        "targets": len(terms) * len(locs),
        "negative_patterns": 0 if negative_pattern() is None else 1,
    }


def reload() -> None:
    """Drop the cache. For tests and for a future hot-reload endpoint."""
    for fn in (
        role_terms, locations, search_params,
        negative_pattern, enabled_sources, options,
    ):
        fn.cache_clear()
