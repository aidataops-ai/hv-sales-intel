import json
import random
from typing import Any

from openai import AsyncOpenAI

from src.crawler import crawl_website
from src.icp_scorer import score_icp
from src.reviews import fetch_reviews, format_reviews_for_prompt
from src.settings import settings

SYSTEM_PROMPT = """You are a sales intelligence analyst for Health & Virtuals (H&V), a managed remote-staffing company that places non-clinical virtual assistants (front desk, scheduler, admin, billing, coordinator) into US-based service businesses. H&V's focus market is Florida.

Your job is to evaluate a target account against the H&V Universal ICP and classify it across five verticals, each with A/B/C/D tiers.

VERTICALS (choose exactly one):
- medical            → Psychiatry, primary care, internal medicine, family medicine, parallel clinical (chiropractic, urgent care, specialty)
- dental             → General + specialty dental (ortho, perio, endo, oral surgery, pediatric)
- alf_nh             → Assisted living facilities, memory care, nursing homes, senior living groups
- hotel_resort       → Hotels, resorts, vacation rental managers, boutique properties
- medspa_wellness    → MedSpas, day spas, wellness/coaching studios, physician-led aesthetics clinics, resort spas
- other              → Does not fit any vertical above

TIERS (per the H&V ICP definitions):
- A → Small / single-location / owner-led. Primary entry motion. (1-3 providers / 6-50 beds / boutique 20-100 rooms / single-location medspa.)
- B → Growth-stage / mid-sized. Highest fit; often very high. (3-5 providers / mid-sized 50-150 beds / 100-250 rooms / 2-3 locations.)
- C → Mid-market / specialty / multi-property. Selective or opportunistic. (5-10 providers / 2-10 facilities / multi-property operator.)
- D → Enterprise / corporate / multi-state. Opportunistic only; longer cycle. (10+ providers / corporate-managed / regional or national operator.)

SCORE EACH OF THE FOLLOWING 0-100 (integers):

1. operational_pain_score
   How clearly the business shows admin / scheduling / documentation / communication / billing / follow-up / back-office burden. Evidence: negative reviews about wait times, missed calls, slow follow-up; overwhelmed staff; reviews mentioning understaffing; treatment plans / packages / leads not followed up; insurance/billing backlog. Higher = more obvious operational pain.

2. decision_maker_access_score
   Is there an identifiable owner, administrator, GM, director, practice manager, or managing partner who can approve spend? Evidence: owner/founder named on website, "meet the team" page, leadership info, LinkedIn presence. Higher = clearer single point of contact.

3. remote_readiness_score
   Do they use digital systems we can plug into? Evidence: mentions of EHR/PMS/CRM (Dentrix, Open Dental, Eaglesoft, PointClickCare, MatrixCare, Opera, Cloudbeds, Aesthetic Record, Boulevard, Zenoti, Mindbody, HubSpot, Salesforce, Weave, NexHealth, etc.), online booking, patient portal, customer portal, digital intake. Higher = stronger digital footprint.

4. role_clarity_score
   How easy is it to define ONE narrow remote role that solves a specific pain? Evidence: explicit job postings, careers page listing front desk / MA / coordinator / admin / receptionist roles; named functions that are clearly non-clinical and remotable. Higher = role can be defined in one sentence today.

5. budget_maturity_score
   Can they support a recurring monthly seat cost (not a one-time project)? Evidence: practice size, multiple providers/locations, paid software stack, premium service positioning, advertising spend, multi-year operation. Higher = obvious capacity to fund recurring support.

6. compliance_boundary_score
   How likely is the engagement to stay within H&V's non-clinical, non-physical scope? Evidence: clear separation of clinical work (handled in-office) from admin work (remotable). Lower this score if the business seems to expect remote staff to do licensed clinical work, in-person tasks, or unscoped catch-all duties. Higher = clean non-clinical boundary.

Also return:
- summary: 1-2 sentence overview from H&V's perspective (what's the staffing/admin opportunity here?)
- pain_points: 2-4 concrete pain points (quote evidence from the reviews/website where possible)
- sales_angles: 2-3 pitch angles tied to specific H&V roles (Virtual Scheduler, Virtual Dental Assistant, Virtual Wellness/Hospitality Assistant, Patient Care Coordinator, Executive Assistant, HR & Payroll Assistant, SDR, Medical Billing Coordinator).

Return ONLY valid JSON with this exact structure:
{
  "icp_vertical": "medical",
  "icp_tier": "B",
  "summary": "...",
  "pain_points": ["...", "..."],
  "sales_angles": ["...", "..."],
  "operational_pain_score": 0,
  "decision_maker_access_score": 0,
  "remote_readiness_score": 0,
  "role_clarity_score": 0,
  "budget_maturity_score": 0,
  "compliance_boundary_score": 0
}

All scores are integers 0-100. No prose outside the JSON."""


async def analyze_practice(
    place_id: str,
    name: str,
    website: str | None,
    category: str | None,
    city: str | None = None,
    state: str | None = None,
    rating: float | None = None,
    review_count: int = 0,
) -> dict:
    """Analyze a practice. Uses OpenAI if API key is set, otherwise returns mock data."""
    if not settings.openai_api_key:
        return _mock_analysis(
            name=name, category=category, state=state,
            rating=rating, review_count=review_count, website=website,
        )

    crawl_result = await crawl_website(website or "")
    website_text = crawl_result["text"]
    website_doctor_name = crawl_result["doctor_name"]
    website_doctor_phone = crawl_result["doctor_phone"]
    reviews = await fetch_reviews(
        place_id,
        name=name,
        city=city,
        state=state,
        website=website,
    )
    reviews_text = format_reviews_for_prompt(reviews)

    user_prompt = f"""Analyze this account for Health & Virtuals ICP fit.

Account: {name}
Google Places category hint: {category or 'Unknown'}
Location: {city or '?'}, {state or '?'}
Google rating: {rating if rating is not None else 'n/a'} ({review_count} reviews)
Website: {website or 'none on file'}

=== WEBSITE CONTENT ===
{website_text[:15000] if website_text else 'No website available.'}

=== CUSTOMER REVIEWS (GOOGLE + EXTERNAL SOURCES) ===
{reviews_text}
"""

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
    except Exception:
        return _mock_analysis(
            name=name, category=category, state=state,
            rating=rating, review_count=review_count, website=website,
        )

    return _build_analysis_record(
        result=result,
        state=state,
        website_doctor_name=website_doctor_name,
        website_doctor_phone=website_doctor_phone,
    )


def _build_analysis_record(
    result: dict,
    state: str | None,
    website_doctor_name: str | None,
    website_doctor_phone: str | None,
) -> dict:
    """Assemble the analyzer's return payload: AI fields + deterministic score."""
    vertical = _norm_vertical(result.get("icp_vertical"))
    tier = _norm_tier(result.get("icp_tier"))
    op_pain = _clamp(result.get("operational_pain_score", 0))
    dm_access = _clamp(result.get("decision_maker_access_score", 0))
    remote = _clamp(result.get("remote_readiness_score", 0))
    role = _clamp(result.get("role_clarity_score", 0))
    budget = _clamp(result.get("budget_maturity_score", 0))
    compliance = _clamp(result.get("compliance_boundary_score", 0))

    icp = score_icp({
        "state": state,
        "icp_vertical": vertical,
        "icp_tier": tier,
        "operational_pain_score": op_pain,
        "decision_maker_access_score": dm_access,
        "remote_readiness_score": remote,
        "role_clarity_score": role,
        "budget_maturity_score": budget,
        "compliance_boundary_score": compliance,
    })

    return {
        "summary": result.get("summary", ""),
        "pain_points": json.dumps(result.get("pain_points", [])),
        "sales_angles": json.dumps(result.get("sales_angles", [])),
        "lead_score": icp["total"],
        # Legacy columns kept populated so existing UI (badges, sort) keeps working.
        # urgency_score ≈ operational_pain (closest concept).
        # hiring_signal_score ≈ role_clarity (open-role visibility was the old proxy).
        "urgency_score": op_pain,
        "hiring_signal_score": role,
        "icp_breakdown": json.dumps(icp["breakdown"]),
        "icp_vertical": vertical,
        "icp_tier": tier,
        "call_script": None,
        "email_draft": None,
        "email_draft_updated_at": None,
        "website_doctor_name": website_doctor_name,
        "website_doctor_phone": website_doctor_phone,
    }


def _norm_vertical(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.lower().strip()
    if v in {"medical", "dental", "alf_nh", "hotel_resort", "medspa_wellness"}:
        return v
    return None


def _norm_tier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    t = value.upper().strip()
    return t if t in {"A", "B", "C", "D"} else None


def _clamp(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


# ----------------------------- mock analysis -------------------------------

MOCK_PAIN_POINTS = {
    "dental": [
        "Multiple reviews mention long wait times for appointments",
        "Website shows 3 open front desk positions unfilled for 2+ months",
        "Patient complaints about phone responsiveness and scheduling delays",
        "Small team handling high patient volume with no admin support",
    ],
    "chiropractic": [
        "Reviews cite difficulty reaching office by phone",
        "No online scheduling available — all booking is phone-based",
        "Single receptionist managing a multi-provider practice",
        "Patients report long hold times and missed callbacks",
    ],
    "urgent_care": [
        "Frequent reviews about excessive wait times (2+ hours)",
        "Website careers page lists multiple MA and front desk openings",
        "Staff turnover evident from reviews mentioning 'new staff every visit'",
        "Understaffed night and weekend shifts based on patient feedback",
    ],
    "mental_health": [
        "Weeks-long wait for new patient appointments",
        "Reviews mention difficulty with billing and insurance follow-up",
        "No dedicated admin staff — providers handling scheduling themselves",
        "Patient intake process described as slow and disorganized",
    ],
    "primary_care": [
        "Reviews frequently mention long wait times in lobby",
        "Website shows hiring for medical assistants and front desk",
        "Patients report difficulty getting referral paperwork processed",
        "Phone system overwhelmed — multiple reviews about busy signals",
    ],
    "specialty": [
        "Complex referral and prior-auth process causing patient frustration",
        "Reviews mention staff seeming overwhelmed and rushed",
        "Limited appointment availability suggesting capacity constraints",
        "Administrative delays in test results and follow-up communication",
    ],
}

MOCK_SALES_ANGLES = {
    "dental": [
        "Pitch Virtual Dental Assistant for chart prep and treatment-plan follow-up",
        "Propose Virtual Scheduler to absorb front-desk overflow",
        "Offer remote insurance verification coordinator",
    ],
    "chiropractic": [
        "Propose virtual receptionist to handle call volume and scheduling",
        "Pitch admin VA for patient intake and insurance processing",
        "Offer bilingual front desk staff for diverse patient base",
    ],
    "urgent_care": [
        "Pitch staffing packages for night/weekend coverage gaps",
        "Propose trained medical assistants for triage support",
        "Offer front desk temp staffing to reduce patient wait times",
    ],
    "mental_health": [
        "Pitch dedicated intake coordinator to reduce new patient wait",
        "Propose billing specialist VA for insurance and claims management",
        "Offer virtual admin assistant so providers can focus on patients",
    ],
    "primary_care": [
        "Pitch medical assistants to support providers and reduce burnout",
        "Propose virtual front desk staff for phone and scheduling overflow",
        "Offer admin VAs for referral processing and follow-up coordination",
    ],
    "specialty": [
        "Pitch prior-authorization specialist to streamline referral process",
        "Propose admin staff for test result follow-up and patient communication",
        "Offer medical assistants trained in specialty clinic workflows",
    ],
}

# Map the legacy Google-Places category → (vertical, default tier).
_LEGACY_CATEGORY_MAP = {
    "dental":         ("dental",          "A"),
    "mental_health":  ("medical",         "A"),
    "primary_care":   ("medical",         "A"),
    "chiropractic":   ("medical",         "A"),
    "urgent_care":    ("medical",         "C"),
    "specialty":      ("medical",         "C"),
}


def _mock_analysis(
    name: str,
    category: str | None,
    state: str | None = None,
    rating: float | None = None,
    review_count: int = 0,
    website: str | None = None,
) -> dict:
    """Realistic mock analysis used when OPENAI_API_KEY is empty."""
    cat = category or "primary_care"
    pain_points = MOCK_PAIN_POINTS.get(cat, MOCK_PAIN_POINTS["primary_care"])
    sales_angles = MOCK_SALES_ANGLES.get(cat, MOCK_SALES_ANGLES["primary_care"])

    selected_pains = random.sample(pain_points, min(3, len(pain_points)))
    selected_angles = random.sample(sales_angles, min(2, len(sales_angles)))

    vertical, tier_default = _LEGACY_CATEGORY_MAP.get(cat, ("other", None))

    # Bucket review_count into a coarse tier (heavier proxy than provider count
    # since the mock has no website to inspect).
    if review_count < 50:
        tier = "A"
    elif review_count < 150:
        tier = "B"
    elif review_count < 400:
        tier = "C"
    else:
        tier = "D"
    tier = tier_default or tier

    op_pain = random.randint(40, 85)
    dm_access = random.randint(35, 80)
    remote = random.randint(40, 85)
    role = random.randint(35, 80)
    budget = random.randint(40, 75)
    compliance = random.randint(50, 85)

    icp = score_icp({
        "state": state,
        "icp_vertical": vertical if vertical != "other" else None,
        "icp_tier": tier,
        "operational_pain_score": op_pain,
        "decision_maker_access_score": dm_access,
        "remote_readiness_score": remote,
        "role_clarity_score": role,
        "budget_maturity_score": budget,
        "compliance_boundary_score": compliance,
    })

    return {
        "summary": f"{name} shows signs of admin/staffing strain typical of {cat.replace('_', ' ')} practices. Review and website signals indicate opportunities for Health & Virtuals support.",
        "pain_points": json.dumps(selected_pains),
        "sales_angles": json.dumps(selected_angles),
        "lead_score": icp["total"],
        "urgency_score": op_pain,
        "hiring_signal_score": role,
        "icp_breakdown": json.dumps(icp["breakdown"]),
        "icp_vertical": vertical if vertical != "other" else None,
        "icp_tier": tier,
        "call_script": None,
        "email_draft": None,
        "email_draft_updated_at": None,
        "website_doctor_name": None,
        "website_doctor_phone": None,
    }
