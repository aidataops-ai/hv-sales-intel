"""Deterministic posting -> specialty track (ADR: docs/specs/2026-08-19-deterministic-track-resolver.md).

Precedence (track_for): the specialty named in the posting title/employer wins
(a deterministic lookup); else the model's own track (the generic front-office
split — MA vs Scheduler vs Scribe — is a judgment, so the LLM keeps it); else the
search-term hint. `from_posting` itself is pure, no I/O, no model, no practice data.

Precision is the whole game: a wrong deterministic label is confidently wrong. Only
the TITLE and EMPLOYER are scanned. Description scanning was tried and REJECTED —
on the 2026-08-19 post-resume batch it fired 25 description-only labels, 24 of them
false ("Benefits: • Dental insurance", "clinics or dental practices", "dental
practice management software" → Dental at nephrology / dermatology / mental-health
practices). The specialty of a practice lives in its name and the role title, not
in incidental prose, so the description is not read.
"""
from __future__ import annotations

import re

# The H&V specialty tracks the resolver can assign. Every value is a LIVE track in
# Supabase `search_terms` (each has active search terms), so `from_posting` only ever
# emits a real, searched-for track. Note ASSISTED_LIVING lives in `search_terms` but
# NOT in config/leads/roles.json (which seeds only the six original lines), so the
# resolver is intentionally NOT gated on `lead_config.service_lines()` — the DB is the
# authoritative track set, not roles.json. (PR #10 review; making service_lines()
# DB-derived is the tracked follow-up.)
CHIRO = "Virtual Chiropractic Assistant"
DENTAL = "Virtual Dental Assistant"
ASSISTED_LIVING = "Virtual Assisted Living Coordinator"
HOME_HEALTH = "Virtual Home Health Operations Coordinator"

# Applied to TITLE + EMPLOYER — the only fields read. Broad patterns are safe here
# because a practice's specialty shows up in its name and the role title.
_TITLE_EMP: list[tuple[str, re.Pattern[str]]] = [
    (CHIRO, re.compile(r"chiropract", re.I)),
    (DENTAL, re.compile(r"dental|dentist|orthodont|endodont|periodont|oral surg|maxillofacial|\bdds\b|\bdmd\b", re.I)),
    (ASSISTED_LIVING, re.compile(r"assisted living|senior living|memory care|skilled nursing|nursing home|\balf\b", re.I)),
    (HOME_HEALTH, re.compile(r"home health|home care|hospice|in-home care", re.I)),
]


def _matches(text: str, rules: list[tuple[str, re.Pattern[str]]]) -> set[str]:
    return {track for track, pat in rules if text and pat.search(text)}


def from_posting(posting: dict) -> str | None:
    """The specialty track named by the posting's TITLE or EMPLOYER, or None.

    Fires only on an UNAMBIGUOUS single specialty — if title/employer point at more
    than one, it declines (returns None) rather than guess. The description is not
    read (see module docstring: it produced ~96% false positives).
    """
    title_emp = f"{posting.get('title') or ''}  {posting.get('employer_name') or ''}"
    hits = _matches(title_emp, _TITLE_EMP)
    return next(iter(hits)) if len(hits) == 1 else None


def track_for(posting: dict, model_track: str | None = None) -> str | None:
    """The lead's track by precedence: the specialty named in the posting, else the
    model's own track (the generic front-office judgment), else the search hint.

    `model_track` is the qualifier's verdict at qualify time, or the lead's existing
    stored track when reconciling. The caller applies this only to KEPT leads.
    """
    return from_posting(posting) or model_track or (posting.get("service_line_hint") or None)
