#!/usr/bin/env python3
"""Link kept-independent job postings to their practice — full bulk pass.

A thin CLI over `src.practice_matcher`, which is the single implementation
shared with the qualify cron (that runs the same matcher incrementally on each
batch's new keepers). Running this by hand does the whole tenant at once — for
a first backfill, or after a fresh practices scan widened the bank.

    python scripts/link_postings.py --dry-run     # measure, write nothing
    python scripts/link_postings.py               # persist at >= 0.80
    python scripts/link_postings.py --min 0.85    # stricter floor

No Google calls, no credits — pure DB read + a handful of updates.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import lead_targets, practice_matcher  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min", type=float, default=practice_matcher.MIN_SCORE,
                    help=f"link floor (default {practice_matcher.MIN_SCORE})")
    ap.add_argument("--auto", type=float, default=practice_matcher.AUTO_SCORE,
                    help=f"auto vs review cutoff (default {practice_matcher.AUTO_SCORE})")
    ap.add_argument("--company-id", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        company_id = args.company_id or lead_targets.resolve_company_id()
    except lead_targets.NoLeadCompany as e:
        print(f"No tenant: {e}")
        return 2

    stats = practice_matcher.link_postings(
        company_id, posting_ids=None,
        min_score=args.min, auto_score=args.auto, dry_run=args.dry_run,
    )
    print(f"  candidates : {stats['candidates']} kept-independent postings")
    print(f"  match >= {args.min:.2f}: {stats['auto'] + stats['review']} "
          f"(auto {stats['auto']}, review {stats['review']})")
    if args.dry_run:
        print("  --dry-run: nothing written.")
    else:
        print(f"  wrote {stats['linked']} links; cleared {stats['cleared']} stale.")
        print("  job_postings.practice_id is live — the similar-practices join is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
