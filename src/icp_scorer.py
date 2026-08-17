"""H&V universal ICP scorer.

Implements the 7-dimension Universal ICP Qualification Model defined in
`docs/icp-scoring.md` (which mirrors the H&V ICP Definitions PDF). Each
target is scored 0-100 across:

1. Vertical fit (15)
2. Operational pain (20)
3. Decision-maker access (15)
4. Remote readiness (15)
5. Role clarity (15)
6. Budget maturity (10)
7. Compliance boundary clarity (10)

Six of the seven dimensions are derived from AI analysis of the practice's
website + reviews (returned by `src/analyzer.py`). Vertical fit is computed
deterministically from the classified vertical + tier and the practice's
state (Florida is the focus market; other US gets 60% credit; outside US
scores zero).

The scorer also returns the score classification per the PDF cutoffs:
- 85-100  Strong ICP
- 70-84   Qualified with conditions
- 55-69   Weak / exploratory
- <55     Poor fit
"""

from typing import Any


# ----------------------------- vertical fit ----------------------------------
#
# Per-vertical tier base score (out of 15). Higher = stronger fit per the PDFs.
# Tier A/B are the "primary motion" for every vertical. C is selective or
# opportunistic. D is opportunistic / longer cycle / enterprise procurement.

_TIER_BASE_SCORE = {
    "A": 13,
    "B": 15,
    "C": 9,
    "D": 6,
}

_VALID_VERTICALS = frozenset({
    "medical",          # Medical Practices (psychiatry, primary care, internal, family)
    "dental",           # Dental Practices
    "alf_nh",           # Assisted Living Facilities / Nursing Homes
    "hotel_resort",     # Hotels / Resorts
    "medspa_wellness",  # MedSpa / Spa / Wellness Facilities
})


def _vertical_fit(
    vertical: str | None,
    tier: str | None,
    state: str,
) -> tuple[int, str]:
    """Return (score, reason) for the Vertical fit dimension (max 15)."""
    v = (vertical or "").lower().strip()
    t = (tier or "").upper().strip()

    if v not in _VALID_VERTICALS:
        return 3, f"Outside defined ICP verticals ({v or 'unclassified'})"

    base = _TIER_BASE_SCORE.get(t)
    if base is None:
        # Tier not classified — treat as adjacent (single-seat entry possible)
        base = 9

    if state == "FL":
        return base, f"{v} · Tier {t or '?'} in Florida (focus market)"

    if _is_us_state(state):
        # Other US: 60% of base, rounded
        scaled = round(base * 0.6)
        return scaled, f"{v} · Tier {t or '?'} in {state} (US, non-focus)"

    return 0, f"{state or 'Unknown'} — outside US operating geography"


# ----------------------------- main scorer ----------------------------------


def score_icp(practice: dict[str, Any]) -> dict:
    """Score a practice 0-100 against the universal H&V ICP.

    Expected input keys:
      - state (str | None)
      - icp_vertical (str | None) — one of `_VALID_VERTICALS`
      - icp_tier (str | None) — "A" | "B" | "C" | "D"
      - operational_pain_score (int, 0-100)
      - decision_maker_access_score (int, 0-100)
      - remote_readiness_score (int, 0-100)
      - role_clarity_score (int, 0-100)
      - budget_maturity_score (int, 0-100)
      - compliance_boundary_score (int, 0-100)

    Returns `{total, classification, breakdown}` where each breakdown row is
    `{label, score, max, reason}`.
    """
    breakdown: list[dict] = []
    state = (practice.get("state") or "").upper().strip()

    # 1. Vertical fit (max 15)
    vfit_score, vfit_reason = _vertical_fit(
        practice.get("icp_vertical"),
        practice.get("icp_tier"),
        state,
    )
    breakdown.append({
        "label": "Vertical fit",
        "score": vfit_score,
        "max": 15,
        "reason": vfit_reason,
    })

    # 2-7. AI-derived dimensions
    for label, key, max_pts in (
        ("Operational pain",          "operational_pain_score",       20),
        ("Decision-maker access",     "decision_maker_access_score",  15),
        ("Remote readiness",          "remote_readiness_score",       15),
        ("Role clarity",              "role_clarity_score",           15),
        ("Budget maturity",           "budget_maturity_score",        10),
        ("Compliance boundary",       "compliance_boundary_score",    10),
    ):
        raw = _clamp_0_100(practice.get(key))
        scaled = round(raw * max_pts / 100)
        breakdown.append({
            "label": label,
            "score": scaled,
            "max": max_pts,
            "reason": f"AI assessment: {raw}/100",
        })

    total = sum(b["score"] for b in breakdown)
    return {
        "total": total,
        "classification": classify(total),
        "breakdown": breakdown,
    }


# ----------------------------- helpers --------------------------------------


def classify(total: int) -> str:
    """Map a total 0-100 score to the H&V classification bucket."""
    if total >= 85:
        return "Strong ICP"
    if total >= 70:
        return "Qualified with conditions"
    if total >= 55:
        return "Weak / exploratory"
    return "Poor fit"


def _clamp_0_100(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


_US_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
})


def _is_us_state(state: str) -> bool:
    return state.upper() in _US_STATES
