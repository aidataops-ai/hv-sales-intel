"""Tests for the H&V Universal ICP scorer (7 dimensions, 100 pts)."""

from src.icp_scorer import classify, score_icp


def _ai_scores(value: int = 0) -> dict:
    """Return all six AI-derived dimension scores set to `value`."""
    return {
        "operational_pain_score": value,
        "decision_maker_access_score": value,
        "remote_readiness_score": value,
        "role_clarity_score": value,
        "budget_maturity_score": value,
        "compliance_boundary_score": value,
    }


def test_perfect_florida_dental_b_strong_icp():
    """Florida dental Tier B + maxed AI signals should yield 'Strong ICP'."""
    p = {
        "state": "FL",
        "icp_vertical": "dental",
        "icp_tier": "B",
        **_ai_scores(100),
    }
    result = score_icp(p)
    # 15 + 20 + 15 + 15 + 15 + 10 + 10 = 100
    assert result["total"] == 100
    assert result["classification"] == "Strong ICP"
    by_label = {b["label"]: b for b in result["breakdown"]}
    assert by_label["Vertical fit"]["score"] == 15


def test_other_us_state_gets_partial_vertical_fit():
    """A Tier B target outside Florida should score 60% of the FL base."""
    p = {
        "state": "TX",
        "icp_vertical": "medical",
        "icp_tier": "B",
        **_ai_scores(0),
    }
    result = score_icp(p)
    by_label = {b["label"]: b for b in result["breakdown"]}
    # 15 * 0.6 = 9 (rounded)
    assert by_label["Vertical fit"]["score"] == 9


def test_outside_us_zero_vertical_fit():
    p = {
        "state": "NSW",
        "icp_vertical": "dental",
        "icp_tier": "A",
        **_ai_scores(50),
    }
    result = score_icp(p)
    by_label = {b["label"]: b for b in result["breakdown"]}
    assert by_label["Vertical fit"]["score"] == 0


def test_unclassified_vertical_falls_to_minimum():
    """Practices that don't match any of the 5 verticals get a token score."""
    p = {
        "state": "FL",
        "icp_vertical": "other",
        "icp_tier": "A",
        **_ai_scores(80),
    }
    result = score_icp(p)
    by_label = {b["label"]: b for b in result["breakdown"]}
    assert by_label["Vertical fit"]["score"] == 3


def test_tier_d_lower_fit_than_tier_b():
    """Enterprise (D) is opportunistic; should score below growth-stage (B)."""
    base = {
        "state": "FL",
        "icp_vertical": "alf_nh",
        **_ai_scores(60),
    }
    tier_b = score_icp({**base, "icp_tier": "B"})
    tier_d = score_icp({**base, "icp_tier": "D"})
    assert tier_b["total"] > tier_d["total"]


def test_all_seven_dimensions_present():
    p = {
        "state": "FL",
        "icp_vertical": "medical",
        "icp_tier": "A",
        **_ai_scores(50),
    }
    result = score_icp(p)
    labels = [b["label"] for b in result["breakdown"]]
    assert labels == [
        "Vertical fit",
        "Operational pain",
        "Decision-maker access",
        "Remote readiness",
        "Role clarity",
        "Budget maturity",
        "Compliance boundary",
    ]


def test_each_breakdown_row_has_required_fields():
    p = {
        "state": "FL",
        "icp_vertical": "dental",
        "icp_tier": "B",
        **_ai_scores(50),
    }
    result = score_icp(p)
    for row in result["breakdown"]:
        assert set(row.keys()) == {"label", "score", "max", "reason"}
        assert 0 <= row["score"] <= row["max"]
        assert isinstance(row["reason"], str) and row["reason"]


def test_total_caps_at_100():
    p = {
        "state": "FL",
        "icp_vertical": "medspa_wellness",
        "icp_tier": "B",
        **_ai_scores(100),
    }
    result = score_icp(p)
    assert result["total"] <= 100


def test_ai_score_above_100_is_clamped():
    """Out-of-range AI scores should clamp to 100, not blow up the total."""
    p = {
        "state": "FL",
        "icp_vertical": "hotel_resort",
        "icp_tier": "B",
        "operational_pain_score": 500,
        "decision_maker_access_score": -20,
        "remote_readiness_score": "not a number",
        "role_clarity_score": None,
        "budget_maturity_score": 50,
        "compliance_boundary_score": 50,
    }
    result = score_icp(p)
    # All dimensions should land within their per-dim max.
    for row in result["breakdown"]:
        assert row["score"] <= row["max"]
    assert 0 <= result["total"] <= 100


def test_classify_buckets():
    assert classify(90) == "Strong ICP"
    assert classify(85) == "Strong ICP"
    assert classify(84) == "Qualified with conditions"
    assert classify(70) == "Qualified with conditions"
    assert classify(69) == "Weak / exploratory"
    assert classify(55) == "Weak / exploratory"
    assert classify(54) == "Poor fit"
    assert classify(0) == "Poor fit"


def test_vertical_fit_reason_includes_state_and_tier():
    p = {
        "state": "FL",
        "icp_vertical": "dental",
        "icp_tier": "A",
        **_ai_scores(0),
    }
    result = score_icp(p)
    by_label = {b["label"]: b for b in result["breakdown"]}
    reason = by_label["Vertical fit"]["reason"]
    assert "dental" in reason
    assert "A" in reason
    assert "Florida" in reason or "FL" in reason
