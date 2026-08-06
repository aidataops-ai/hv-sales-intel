#!/usr/bin/env python3
"""Link kept-independent job postings to their practice in the bank.

Persists the match measured in the matching PoC: `employer_name_norm` folded
against a same-city `practices.name` (both via `normalise_employer`, the exact
function that produced employer_name_norm), scoped to KEPT INDEPENDENT leads —
the only population where the link is well-defined (a system has no single
place_id to point at).

Writes to job_postings: practice_id, match_confidence, match_status
('auto' >= --auto, else 'review'), match_method, matched_at. Idempotent — a
re-run re-scores everything, updates changed links, and CLEARS any prior
name_city_v1 link that no longer qualifies (e.g. a practice dropped out).

    python scripts/link_postings.py --dry-run     # measure, write nothing
    python scripts/link_postings.py               # persist at >= 0.80
    python scripts/link_postings.py --min 0.85    # stricter floor

No Google calls, no credits — pure DB read + a handful of updates.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, __import__("os").path.dirname(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from src.job_boards import normalise_employer  # noqa: E402
from src.storage import _get_client  # noqa: E402

METHOD = "name_city_v1"


def city_key(c: str | None) -> str:
    if not c:
        return ""
    c = c.lower().strip()
    c = re.sub(r"\bst\.?\b", "saint", c)
    c = re.sub(r"\bft\.?\b", "fort", c)
    return re.sub(r"[^a-z0-9]", "", c)


def score(a: str, b: str) -> float:
    """Name similarity in [0,1]: max of token-set Jaccard and char-ratio."""
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    return max(jac, seq)


def load_practices(client) -> dict:
    """Service-line-tagged practices bucketed by city_key. Each record:
    (practice_id, norm_name, name, service_line)."""
    by_city: dict[str, list] = defaultdict(list)
    page, size = 0, 1000
    while True:
        rows = (
            client.table("practices")
            .select("id,name,city,service_line")
            .not_.is_("service_line", "null")
            .range(page * size, (page + 1) * size - 1)
            .execute().data
        )
        if not rows:
            break
        for r in rows:
            nn = normalise_employer(r.get("name"))
            if nn:
                by_city[city_key(r.get("city"))].append(
                    (r["id"], nn, r.get("name"), r.get("service_line")))
        if len(rows) < size:
            break
        page += 1
    return by_city


def load_kept_independent(client) -> list:
    """Kept-independent postings: (id, employer_name_norm, city)."""
    ids: list[int] = []
    page, size = 0, 1000
    while True:
        chunk = (
            client.table("company_job_leads").select("posting_id")
            .eq("decision", "keep").eq("employer_type", "independent")
            .range(page * size, (page + 1) * size - 1).execute().data
        )
        if not chunk:
            break
        ids.extend(r["posting_id"] for r in chunk)
        if len(chunk) < size:
            break
        page += 1
    ids = list(set(ids))

    out: list = []
    for i in range(0, len(ids), 400):
        rows = (
            client.table("job_postings")
            .select("id,employer_name_norm,city")
            .in_("id", ids[i:i + 400])
            .not_.is_("employer_name_norm", "null")
            .not_.is_("city", "null").execute().data
        )
        out.extend(rows)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min", type=float, default=0.80, help="link floor (default 0.80)")
    ap.add_argument("--auto", type=float, default=0.90,
                    help="auto vs review cutoff (default 0.90)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = _get_client()
    if not client:
        print("Supabase not configured.")
        return 2

    by_city = load_practices(client)
    postings = load_kept_independent(client)
    print(f"  {sum(len(v) for v in by_city.values())} tagged practices, "
          f"{len(postings)} kept-independent postings")

    matches: list[tuple] = []      # (posting_id, practice_id, conf, status)
    unmatched_ids: list[int] = []
    buckets: Counter = Counter()

    for p in postings:
        q = p["employer_name_norm"]
        cands = by_city.get(city_key(p.get("city")), [])
        best = max(cands, key=lambda r: score(q, r[1]), default=None)
        s = score(q, best[1]) if best else 0.0
        if best and s >= args.min:
            status = "auto" if s >= args.auto else "review"
            matches.append((p["id"], best[0], round(s, 2), status))
            buckets[status] += 1
        else:
            unmatched_ids.append(p["id"])

    print(f"  MATCH >= {args.min:.2f}: {len(matches)} "
          f"(auto {buckets['auto']}, review {buckets['review']}); "
          f"{len(unmatched_ids)} unmatched")

    if args.dry_run:
        print("  --dry-run: nothing written.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    for posting_id, practice_id, conf, status in matches:
        client.table("job_postings").update({
            "practice_id": practice_id,
            "match_confidence": conf,
            "match_status": status,
            "match_method": METHOD,
            "matched_at": now,
        }).eq("id", posting_id).execute()

    # Clear any prior link (from this method) that no longer qualifies, so a
    # re-run is fully deterministic and never leaves a stale relation behind.
    cleared = 0
    for i in range(0, len(unmatched_ids), 200):
        res = (
            client.table("job_postings").update({
                "practice_id": None, "match_confidence": None,
                "match_status": None, "match_method": None, "matched_at": None,
            })
            .in_("id", unmatched_ids[i:i + 200])
            .eq("match_method", METHOD).execute()
        )
        cleared += len(res.data or [])

    print(f"  wrote {len(matches)} links; cleared {cleared} stale.")
    print("  job_postings.practice_id is live — the similar-practices join is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
