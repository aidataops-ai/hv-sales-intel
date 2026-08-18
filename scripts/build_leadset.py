"""Build a balanced working set of leads for the posting_to_talentdb run.

Selects the newest KEEP + not-yet-exported `company_job_leads`, balanced across
the six in-scope tracks, and writes them as an array of `job_postings`-shaped
objects to a JSON file. That file is the input batch for
scripts/posting_to_talentdb.py (one object per posting).

Read-only: it only SELECTs from Supabase — no Places / OpenAI / Clay / Talent-DB
calls, no writes. Safe to run repeatedly. Re-running after some leads are
exported returns a fresh set (exported rows drop out via the marker).

Quotas default to ~100 total split evenly across the six tracks; a track with
fewer available keeps contributes what it has and the shortfall is reported.

Usage:
    .venv/bin/python -m scripts.build_leadset                       # → leadset-100.json (100 total)
    .venv/bin/python -m scripts.build_leadset --per-track 20        # 20 each (120 total)
    .venv/bin/python -m scripts.build_leadset --out my-set.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from src.settings import settings
from src.storage import _get_client

# The six in-scope service lines (config/leads/roles.json). Order fixes the
# quota assignment below.
TRACKS = [
    "Virtual Medical Assistant",
    "Virtual Dental Assistant",
    "Virtual Medical Scheduler",
    "Virtual Chiropractic Assistant",
    "Virtual Home Health Operations Coordinator",
    "Virtual Medical Scribe",
]

# Posting columns the downstream script needs (a job_postings-shaped row).
_POSTING_FIELDS = (
    "id, source, external_id, url, title, employer_name, location_raw, "
    "city, state, posted_at, salary_min, salary_max, salary_interval, "
    "board_remote_flag, search_term, search_location, service_line_hint, "
    "description, practice_id, first_seen_at, last_seen_at"
)


def _quotas(total: int, per_track: int | None) -> dict[str, int]:
    """Per-track target counts. Even split of `total`, or a flat `per_track`."""
    if per_track is not None:
        return {t: per_track for t in TRACKS}
    base, extra = divmod(total, len(TRACKS))
    # The first `extra` tracks get one more, so the total lands exactly on `total`.
    return {t: base + (1 if i < extra else 0) for i, t in enumerate(TRACKS)}


# Confidence priority: take the qualifier's confident keeps first, and only
# dip into the low-confidence 'decide' band to fill a track that runs short.
_PRIORITY_BANDS = ["ready", "check"]
_FALLBACK_BANDS = ["decide"]


def _fetch_bands(company_id: str, track: str, bands: list[str], limit: int) -> list[dict]:
    """Newest-posted keep + un-exported postings for one track within `bands`.

    `job_postings` is the PARENT here so `posted_at` is a real top-level column
    the order actually sorts on — ordering a parent by an *embedded* column
    (company_job_leads→job_postings the other way round) sorts only the embed,
    not the returned rows, which silently defeats the recency ranking. The lead
    is embedded `!inner` purely to filter on decision / band / export marker.
    """
    client = _get_client()
    return (
        client.table("job_postings")
        .select(
            f"{_POSTING_FIELDS}, "
            "lead:company_job_leads!inner(company_id, decision, confidence, "
            "confidence_band, talentdb_exported_at)"
        )
        .eq("service_line_hint", track)
        .not_.is_("employer_name", "null")
        .eq("lead.company_id", company_id)
        .eq("lead.decision", "keep")
        .is_("lead.talentdb_exported_at", "null")
        .in_("lead.confidence_band", bands)
        # Rank by posting recency (newest job first). Postings with no posted_at
        # sort last, so a dated lead is never demoted below an undated one; id
        # breaks ties for a stable order.
        .order("posted_at", desc=True, nullsfirst=False)
        .order("id", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def _fetch_track(company_id: str, track: str, quota: int) -> list[dict]:
    """Fill a track's quota, confident keeps first, 'decide' only as fallback.

    Confidential listings carry no `employer_name`, which the Places step can't
    search on, so they're dropped and back-filled to keep the track at quota.
    """
    out: list[dict] = []
    seen: set[int] = set()

    def _take(rows: list[dict]) -> None:
        for posting in rows:
            if len(out) >= quota:
                return
            # No employer to search Places with → unusable for this flow.
            if not posting or not (posting.get("employer_name") or "").strip():
                continue
            if posting["id"] in seen:
                continue
            seen.add(posting["id"])
            # The embedded lead is a one-element list (single tenant, !inner).
            lead = posting.pop("lead", None) or [{}]
            verdict = lead[0] if isinstance(lead, list) else lead
            # Carry the qualifier verdict alongside for visibility; the script
            # re-derives its own, so these are informational only.
            posting["_qualifier_decision"] = verdict.get("decision")
            posting["_qualifier_confidence"] = verdict.get("confidence")
            posting["_qualifier_band"] = verdict.get("confidence_band")
            out.append(posting)

    # ready + check first; buffer of +10 covers empty-employer rows dropped above.
    _take(_fetch_bands(company_id, track, _PRIORITY_BANDS, quota + 10))
    # Only if the confident bands couldn't fill the quota, fall back to 'decide'.
    if len(out) < quota:
        _take(_fetch_bands(company_id, track, _FALLBACK_BANDS, quota - len(out) + 10))
    return out


def _age_days(value) -> int | None:
    """Whole days between `value` (ISO date or timestamp) and now, or None."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    dt = None
    for candidate in (text, f"{text}T00:00:00+00:00"):
        try:
            dt = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def _report_recency(leadset: list[dict]) -> None:
    """Print how fresh the set is, so 'latest, not old' is verifiable per run."""
    print("\n[build] recency (age in days):")
    for field in ("posted_at", "first_seen_at"):
        ages = sorted(a for a in (_age_days(x.get(field)) for x in leadset) if a is not None)
        missing = len(leadset) - len(ages)
        if not ages:
            print(f"  {field:<14} (no dates)")
            continue
        within7 = sum(1 for a in ages if a <= 7)
        within14 = sum(1 for a in ages if a <= 14)
        miss = f", missing={missing}" if missing else ""
        print(f"  {field:<14} newest={ages[0]}d  median={ages[len(ages)//2]}d  "
              f"oldest={ages[-1]}d  (≤7d: {within7}, ≤14d: {within14}{miss})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a balanced lead set JSON for the Talent-DB run.")
    ap.add_argument("--total", type=int, default=100, help="target total across all tracks (default 100)")
    ap.add_argument("--per-track", type=int, default=None,
                    help="fixed count per track (overrides --total)")
    ap.add_argument("--company-id", default=None, help="tenant (default: settings.lead_company_id)")
    ap.add_argument("--out", default="leadset-100.json", help="output JSON file")
    args = ap.parse_args()

    company_id = args.company_id or settings.lead_company_id
    if not company_id:
        raise SystemExit("No company_id — pass --company-id or set LEAD_COMPANY_ID.")
    if not _get_client():
        raise SystemExit("No Supabase client — check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.")

    quotas = _quotas(args.total, args.per_track)
    leadset: list[dict] = []
    print(f"[build] company_id={company_id}")
    print(f"[build] keep + un-exported, confident bands (ready/check) first, "
          f"'decide' only as fallback:\n")
    for track in TRACKS:
        want = quotas[track]
        rows = _fetch_track(company_id, track, want)
        got = len(rows)
        confident = sum(1 for r in rows if r.get("_qualifier_band") in _PRIORITY_BANDS)
        fallback = got - confident
        flag = "" if got >= want else f"  ⚠ only {got} available"
        print(f"  {track:<48} want={want:<3} got={got}  "
              f"(ready/check={confident}, decide={fallback}){flag}")
        leadset.extend(rows)

    _report_recency(leadset)

    with open(args.out, "w") as f:
        json.dump(leadset, f, indent=2, default=str)

    print(f"\n[build] wrote {len(leadset)} leads → {args.out}")
    print("[build] feed each object to scripts.posting_to_talentdb "
          "(the leads are already 'keep', so --skip-qualify is safe).")


if __name__ == "__main__":
    main()
