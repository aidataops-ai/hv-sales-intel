"""Match kept-independent job postings to their practice in the Places bank.

The employer on a posting IS a practice in the universe; this resolves that
mapping. `employer_name_norm` is folded against a same-city-AND-STATE
`practices.name` (both through `normalise_employer`, the exact function that
produced employer_name_norm), scoped to KEPT INDEPENDENT leads — the only
population where the link is well-defined (a hospital system has no single
place_id).

State is part of the bucket key, not just the city, because the multi-state
geography expansion introduced cross-state duplicate city names (Greenville,
NC vs Greenville, SC; Smyrna, GA vs Smyrna, TN; ...) — city-only bucketing
would let a posting auto-link to a practice in the WRONG state whenever two
states share a city name. A posting with no state on record (state is
optional on `job_postings`) falls back to every practice in the city
regardless of state, but that unscoped match is capped at `match_status`
'review' — never 'auto' — since it is exactly the ambiguity state-scoping
exists to remove.

One module, two callers:
  * `scripts/link_postings.py` — a full bulk pass over every kept-independent
    posting (posting_ids=None).
  * the qualify cron — an incremental pass over just the batch it qualified
    (posting_ids=<claimed>), so a new keeper links the moment it is judged,
    against only the practices in the cities that batch touched.

Writes to job_postings: practice_id, match_confidence, match_status
('auto' >= auto_score and state-scoped, else 'review'), match_method,
matched_at. Idempotent — a re-run re-scores and clears any prior
name_city_v1 link that no longer qualifies. No Google calls, no credits.
"""
from __future__ import annotations

import difflib
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

from src.job_boards import normalise_employer

log = logging.getLogger("hvsi.leads.matcher")

METHOD = "name_city_v1"
MIN_SCORE = 0.80    # link floor
AUTO_SCORE = 0.90   # auto vs review cutoff


def city_key(c: str | None) -> str:
    """Fold a city label so 'St. Petersburg' and 'Saint Petersburg' collide."""
    if not c:
        return ""
    c = c.lower().strip()
    c = re.sub(r"\bst\.?\b", "saint", c)
    c = re.sub(r"\bft\.?\b", "fort", c)
    return re.sub(r"[^a-z0-9]", "", c)


def location_key(c: str | None, state: str | None) -> str:
    """Fold a city+state pair into the bucket key exact matches are grouped
    by. `state` blank/None folds to `''` — used only to build the
    city-and-state index; the NULL-state fallback path in `link_postings`
    looks candidates up by `city_key` alone instead of this, on purpose, so
    it is never mistaken for a real state match."""
    return f"{city_key(c)}|{(state or '').strip().upper()}"


def score(a: str, b: str) -> float:
    """Name similarity in [0,1]: max of token-set Jaccard and char-ratio."""
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    return max(jac, seq)


def _client():
    from src.storage import _get_client
    return _get_client()


def load_practices_by_city(
    client, only_cities: set[str] | None = None,
) -> tuple[dict, dict]:
    """Service-line-tagged practices, indexed two ways. Each record:
    (practice_id, norm_name, name, service_line).

    Returns `(by_location, by_city)`:
      * `by_location` keys on `location_key(city, state)` — the primary
        index, used whenever the posting being matched has a state.
      * `by_city` keys on `city_key(city)` alone — used ONLY as the fallback
        for postings with no recorded state, since there is no state to
        scope by. `link_postings` caps anything found this way at 'review'.

    One DB pass builds both from the same rows, so a state-less posting's
    fallback costs nothing extra. `only_cities` restricts the load to a set
    of city_keys (city-only — the practice's state isn't known until the row
    loads) — the cron passes just the batch's cities so it never pages the
    whole 20k-row universe.
    """
    by_location: dict[str, list] = defaultdict(list)
    by_city: dict[str, list] = defaultdict(list)
    page, size = 0, 1000
    while True:
        rows = (
            client.table("practices")
            .select("id,name,city,state,service_line")
            .not_.is_("service_line", "null")
            .range(page * size, (page + 1) * size - 1)
            .execute().data
        )
        if not rows:
            break
        for r in rows:
            ck = city_key(r.get("city"))
            if only_cities is not None and ck not in only_cities:
                continue
            nn = normalise_employer(r.get("name"))
            if not nn:
                continue
            record = (r["id"], nn, r.get("name"), r.get("service_line"))
            by_city[ck].append(record)
            by_location[location_key(r.get("city"), r.get("state"))].append(record)
        if len(rows) < size:
            break
        page += 1
    return by_location, by_city


def _kept_independent(client, company_id: str, posting_ids: list[int] | None) -> list:
    """Kept-independent postings for this tenant: (id, employer_name_norm,
    city, state).

    `posting_ids`, when given, restricts to that set — the cron's incremental
    scope. The verdict in company_job_leads stays the source of truth for what
    'kept independent' means; passing ids only narrows, never widens it.

    `state` is NOT required to be non-null like `city` is — a posting with no
    state on record still gets matched (city-only fallback, capped at
    'review' by `link_postings`), only one with no city at all is unmatchable.
    """
    kept: list[int] = []
    page, size = 0, 1000
    while True:
        q = (
            client.table("company_job_leads").select("posting_id")
            .eq("company_id", company_id)
            .eq("decision", "keep").eq("employer_type", "independent")
        )
        if posting_ids is not None:
            q = q.in_("posting_id", posting_ids)
        chunk = q.range(page * size, (page + 1) * size - 1).execute().data
        if not chunk:
            break
        kept.extend(r["posting_id"] for r in chunk)
        if len(chunk) < size:
            break
        page += 1
    kept = list(set(kept))
    if not kept:
        return []

    out: list = []
    for i in range(0, len(kept), 400):
        rows = (
            client.table("job_postings")
            .select("id,employer_name_norm,city,state")
            .in_("id", kept[i:i + 400])
            .not_.is_("employer_name_norm", "null")
            .not_.is_("city", "null").execute().data
        )
        out.extend(rows)
    return out


def link_postings(
    company_id: str,
    posting_ids: list[int] | None = None,
    *,
    min_score: float = MIN_SCORE,
    auto_score: float = AUTO_SCORE,
    dry_run: bool = False,
) -> dict:
    """Match kept-independent postings and persist the links. Returns stats.

    `posting_ids=None` is a full pass; a list is the incremental cron scope.
    """
    client = _client()
    if not client or not company_id:
        return {"candidates": 0, "auto": 0, "review": 0, "linked": 0, "cleared": 0}

    postings = _kept_independent(client, company_id, posting_ids)
    if not postings:
        return {"candidates": 0, "auto": 0, "review": 0, "linked": 0, "cleared": 0}

    # Load only the practices in the cities these postings live in — the cron
    # scope stays cheap, the full pass loads (almost) everything anyway.
    cities = {city_key(p.get("city")) for p in postings}
    by_location, by_city = load_practices_by_city(client, only_cities=cities)

    matches: list[tuple] = []    # (posting_id, practice_id, conf, status)
    unmatched: list[int] = []
    for p in postings:
        q = p["employer_name_norm"]
        state = p.get("state")
        if state:
            # The common, trusted path: candidates scoped to this posting's
            # own state, so "Greenville, NC" can never match a practice in
            # "Greenville, SC" no matter how similar the names score.
            cands = by_location.get(location_key(p.get("city"), state), [])
            state_scoped = True
        else:
            # No state to scope by — fall back to every practice in the
            # city regardless of state. That is exactly the ambiguity
            # state-scoping exists to remove, so a match found this way is
            # never trusted at 'auto' — only 'review', below.
            cands = by_city.get(city_key(p.get("city")), [])
            state_scoped = False
        best = max(cands, key=lambda r: score(q, r[1]), default=None)
        s = score(q, best[1]) if best else 0.0
        if best and s >= min_score:
            status = "auto" if (state_scoped and s >= auto_score) else "review"
            matches.append((p["id"], best[0], round(s, 2), status))
        else:
            unmatched.append(p["id"])

    stats = {
        "candidates": len(postings),
        "auto": sum(1 for m in matches if m[3] == "auto"),
        "review": sum(1 for m in matches if m[3] == "review"),
        "linked": 0,
        "cleared": 0,
    }
    if dry_run:
        stats["linked"] = len(matches)
        return stats

    now = datetime.now(timezone.utc).isoformat()
    for posting_id, practice_id, conf, status in matches:
        try:
            client.table("job_postings").update({
                "practice_id": practice_id,
                "match_confidence": conf,
                "match_status": status,
                "match_method": METHOD,
                "matched_at": now,
            }).eq("id", posting_id).execute()
            stats["linked"] += 1
        except Exception as e:
            log.warning("[leads.match.write] posting=%s %s: %s",
                        posting_id, type(e).__name__, str(e)[:160])

    # Clear any prior name_city_v1 link (within this scope) that no longer
    # qualifies, so the relation never goes stale after a re-match.
    for i in range(0, len(unmatched), 200):
        try:
            res = (
                client.table("job_postings").update({
                    "practice_id": None, "match_confidence": None,
                    "match_status": None, "match_method": None, "matched_at": None,
                })
                .in_("id", unmatched[i:i + 200])
                .eq("match_method", METHOD).execute()
            )
            stats["cleared"] += len(res.data or [])
        except Exception as e:
            log.warning("[leads.match.clear] %s: %s", type(e).__name__, str(e)[:160])

    log.info("[leads.match] candidates=%d auto=%d review=%d linked=%d cleared=%d",
             stats["candidates"], stats["auto"], stats["review"],
             stats["linked"], stats["cleared"])
    return stats
