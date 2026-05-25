import json
import logging
from pathlib import Path

import httpx

from src.models import Practice
from src.settings import settings

log = logging.getLogger("hvsi.places")

MOCK_PATH = Path(__file__).parent / "mock_practices.json"

FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.location,places.rating,places.userRatingCount,"
    "places.nationalPhoneNumber,places.websiteUri,"
    "places.types,places.regularOpeningHours"
)


async def search_places(
    query: str,
    company_id: str | None = None,
    user_id: str | None = None,
) -> list[Practice]:
    """Search for practices. Uses Google Places API if key is set, else mock data."""
    if settings.google_maps_api_key:
        return await _google_search(query, company_id=company_id, user_id=user_id)
    return _mock_search(query)


async def _google_search(
    query: str,
    company_id: str | None = None,
    user_id: str | None = None,
) -> list[Practice]:
    """Call Google Places Text Search (New) API, paginating up to 60 results.

    Google caps maxResultCount at 20 per request but supports up to 60 total
    via nextPageToken (3 pages). Each page is a separate billable call.

    On API failure, falls back to mock_practices.json with a logged error so
    the search endpoint stays available and the operator can diagnose via
    Vercel logs.
    """
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": f"{FIELD_MASK},nextPageToken",
        "Content-Type": "application/json",
    }

    all_places: list[dict] = []
    page_token: str | None = None
    pages_fetched = 0
    log.info("[places.google.start] query=%r", query)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for page in range(3):  # cap at 3 pages = 60 results
                body: dict = {"textQuery": query, "maxResultCount": 20}
                if page_token:
                    body["pageToken"] = page_token
                resp = await client.post(url, json=body, headers=headers)
                pages_fetched += 1
                if resp.status_code != 200:
                    log.error(
                        "[places.google.error] status=%s page=%s body=%s",
                        resp.status_code,
                        page,
                        resp.text[:500],
                    )
                    resp.raise_for_status()
                data = resp.json()
                all_places.extend(data.get("places", []))
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
    except Exception as e:
        log.error("[places.google.exception] type=%s msg=%s", type(e).__name__, str(e)[:500])
        # Still log the calls we did make before the error fired so the
        # admin sees the spend even on failed runs.
        if pages_fetched:
            try:
                from src.usage import record_places
                record_places(
                    kind="places_search",
                    calls=pages_fetched,
                    company_id=company_id,
                    user_id=user_id,
                    metadata={"query": query, "error": str(e)[:200]},
                )
            except Exception:
                pass
        return _mock_search(query)

    # Log usage — one row per outbound HTTP call, even though they share a query.
    try:
        from src.usage import record_places
        record_places(
            kind="places_search",
            calls=pages_fetched,
            company_id=company_id,
            user_id=user_id,
            metadata={"query": query, "results": len(all_places)},
        )
    except Exception:
        pass

    log.info("[places.google.done] query=%r count=%d", query, len(all_places))
    return [_map_google_place(p) for p in all_places]


async def get_place(
    place_id: str,
    fallback: Practice | None = None,
    company_id: str | None = None,
    user_id: str | None = None,
) -> Practice | None:
    """Fetch the latest place details for a known Google place id."""
    if not settings.google_maps_api_key:
        return fallback
    if place_id.startswith(("mock_", "real_")):
        return fallback

    url = f"https://places.googleapis.com/v1/places/{place_id}"
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
    except (httpx.HTTPError, Exception):
        return fallback

    payload = resp.json()
    payload.setdefault("id", place_id)

    try:
        from src.usage import record_places
        record_places(
            kind="places_details",
            company_id=company_id,
            user_id=user_id,
            metadata={"place_id": place_id},
        )
    except Exception:
        pass

    return _map_google_place(payload)


def _mock_search(query: str) -> list[Practice]:
    """Filter mock data by keyword matching on name, category, city."""
    with open(MOCK_PATH) as f:
        raw = json.load(f)
    query_lower = query.lower()
    tokens = query_lower.split()
    matches = []
    for item in raw:
        searchable = f"{item['name']} {item['category']} {item['city']}".lower()
        if any(tok in searchable for tok in tokens):
            matches.append(Practice(**item))
    return matches if matches else [Practice(**item) for item in raw[:20]]


def _map_google_place(place: dict) -> Practice:
    loc = place.get("location", {})
    hours_periods = place.get("regularOpeningHours", {}).get("weekdayDescriptions", [])
    name = place.get("displayName", {}).get("text", "Unknown")
    types = place.get("types", [])
    tags: list[str] = []
    if not _is_in_scope(types, name):
        tags.append("IRRELEVANT")
    return Practice(
        place_id=place.get("id") or place.get("name", "").rsplit("/", 1)[-1] or "unknown_place",
        name=name,
        address=place.get("formattedAddress"),
        city=_extract_city(place.get("formattedAddress", "")),
        state=_extract_state(place.get("formattedAddress", "")),
        phone=place.get("nationalPhoneNumber"),
        website=place.get("websiteUri"),
        rating=place.get("rating"),
        review_count=place.get("userRatingCount", 0),
        category=_classify_types(types, name=name),
        lat=loc.get("latitude"),
        lng=loc.get("longitude"),
        opening_hours="; ".join(hours_periods) if hours_periods else None,
        tags=tags,
    )


# Healthcare types — original ICP segment.
HEALTHCARE_TYPES = frozenset({
    "doctor", "dentist", "dental_clinic",
    "physiotherapist", "chiropractor",
    "hospital", "urgent_care_center", "emergency_room",
    "psychiatrist", "psychologist", "mental_health", "mental_health_clinic",
    "general_practitioner", "primary_care", "primary_care_physician",
    "health", "medical_lab", "pharmacy",
    "optometrist", "ophthalmologist", "dermatologist", "podiatrist",
    "veterinary_care",
})

# Assisted Living / Nursing Home types.
ALF_TYPES = frozenset({
    "nursing_home", "assisted_living_facility",
    "retirement_community", "elderly_care_facility",
})

# Hotels / Resorts types.
HOTEL_TYPES = frozenset({
    "lodging", "hotel", "motel", "resort_hotel", "bed_and_breakfast",
    "extended_stay_hotel",
})

# MedSpa / Spa / Wellness types. Google bundles medspas under spa /
# beauty_salon; we let them in and re-classify via name keywords.
MEDSPA_TYPES = frozenset({
    "spa", "beauty_salon", "wellness_center",
})

# Every Google type that signals an in-scope ICP segment. Replaces the old
# healthcare-only check.
IN_SCOPE_TYPES = HEALTHCARE_TYPES | ALF_TYPES | HOTEL_TYPES | MEDSPA_TYPES

# Hard-disqualifiers — if Google tags the place with any of these alone
# (and none of the in-scope types apply), it's definitely not target ICP
# (e.g. "Doctors Café", "Dental Bar Restaurant", standalone gym).
NEGATIVE_TYPES = frozenset({
    "cafe", "coffee_shop", "restaurant", "food", "bar", "bakery",
    "meal_takeaway", "meal_delivery", "night_club",
    "gym", "fitness_center", "hair_care",
    "store", "supermarket", "shopping_mall", "clothing_store",
    "convenience_store", "grocery_or_supermarket", "department_store",
    "tourist_attraction", "park", "school", "university",
    "car_dealer", "car_repair", "car_rental", "gas_station",
    "real_estate_agency", "insurance_agency", "lawyer", "bank", "atm",
})

# Keywords in the place name that strongly suggest each in-scope segment.
# "Dr." with trailing dot is a stronger person-name signal than the bare
# word "doctor", which appears in restaurant names ("Doctors Café").
HEALTHCARE_NAME_KEYWORDS = (
    "clinic", "hospital", "medical", "dental", "dentist", "orthodont",
    "psychiatr", "psycholog", "mental health", "behavioral health",
    "primary care", "urgent care", "physiotherap", "chiropract",
    "rehab", "physiotherapy", "podiatr", "dermatolog", "optomet",
    "ophthalmolog", "veterinar", "pediatr", "obgyn", "obstetr",
    "gynecolog", "cardiolog", "neurolog", "oncolog", "radiolog",
    "dr.", "md ", "m.d.", "dds", "d.d.s.",
)

ALF_NAME_KEYWORDS = (
    "assisted living", "memory care", "nursing home", "senior living",
    "skilled nursing", "retirement home", "retirement community",
    "elder care", "elderly care", "senior care", "alzheimer", "dementia care",
)

HOTEL_NAME_KEYWORDS = (
    "hotel", "resort", "motel", "lodge", "suites",
    "bed & breakfast", "bed and breakfast", "vacation rental",
    "boutique hotel", "marriott", "hilton", "hyatt",
)

MEDSPA_NAME_KEYWORDS = (
    "medspa", "med spa", "aesthetics", "anti-aging", "day spa",
    "rejuvenation", "skin clinic", "laser clinic", "cosmetic clinic",
    "wellness center", "wellness clinic", "iv lounge", "iv therapy",
    "botox", "filler",
)


def _is_in_scope(types: list[str], name: str) -> bool:
    """True if the place looks like an in-scope ICP target (healthcare,
    ALF/nursing, hotel/resort, or medspa/wellness).

    Logic:
      1. If Google flags it as an in-scope type → accept regardless of
         any incidental negative type (e.g. a hotel that also lists 'spa').
      2. If Google flags it as ONLY a negative type → reject.
      3. Otherwise inspect the display name — solo practitioners, small
         medspas, and boutique hotels often only get a generic type.
    """
    type_set = set(types or [])
    if IN_SCOPE_TYPES & type_set:
        return True
    if NEGATIVE_TYPES & type_set:
        return False
    name_lower = (name or "").lower()
    keywords = (
        HEALTHCARE_NAME_KEYWORDS
        + ALF_NAME_KEYWORDS
        + HOTEL_NAME_KEYWORDS
        + MEDSPA_NAME_KEYWORDS
    )
    return any(k in name_lower for k in keywords)


# Backward-compatible alias — older imports may reference _is_healthcare.
_is_healthcare = _is_in_scope


def _extract_city(address: str) -> str | None:
    """Best-effort city extraction from formatted address."""
    parts = address.split(",")
    if len(parts) >= 3:
        state_zip = parts[-2].strip()
        city_part = parts[-3].strip() if len(parts) >= 4 else state_zip.rsplit(" ", 1)[0].strip()
        return city_part
    return None


def _extract_state(address: str) -> str | None:
    """Best-effort state extraction from formatted address."""
    parts = address.split(",")
    if len(parts) >= 2:
        state_zip = parts[-1].strip() if "USA" not in parts[-1] else parts[-2].strip()
        tokens = state_zip.split()
        return tokens[0] if tokens else None
    return None


def _classify_types(types: list[str], name: str = "") -> str:
    """Map Google Places types to our category taxonomy.

    Order matters — more specific signals win. Solo practitioners and
    boutique businesses often only get a generic Google type, so the
    name string is the fallback for every branch.

    Possible return values:
        mental_health, dental, chiropractic, urgent_care,
        alf_nh, hotel_resort, medspa_wellness,
        primary_care, specialty (catchall)
    """
    type_set = set(types)
    name_lower = (name or "").lower()

    # Mental health first — psychiatrists frequently carry only the
    # generic 'doctor' type, so name-based detection is the only reliable
    # signal.
    if (
        type_set & {"psychiatrist", "psychologist", "mental_health"}
        or any(
            keyword in name_lower
            for keyword in (
                "psychiatrist", "psychiatric", "psychiatry",
                "psychologist", "psychology", "mental health",
                "behavioral health", "psychotherapy", "counseling",
                "therapist", "therapy",
            )
        )
    ):
        return "mental_health"

    if type_set & {"dentist", "dental_clinic"} or any(
        k in name_lower for k in ("dentist", "dental", "orthodont")
    ):
        return "dental"

    if type_set & {"physiotherapist", "chiropractor"} or any(
        k in name_lower for k in ("chiropractor", "chiropractic", "physiotherap")
    ):
        return "chiropractic"

    if (
        type_set & ALF_TYPES
        or any(k in name_lower for k in ALF_NAME_KEYWORDS)
    ):
        return "alf_nh"

    if (
        type_set & HOTEL_TYPES
        or any(k in name_lower for k in HOTEL_NAME_KEYWORDS)
    ):
        return "hotel_resort"

    if type_set & {"hospital", "urgent_care_center", "emergency_room"} or "urgent care" in name_lower:
        return "urgent_care"

    # MedSpa / wellness — checked AFTER the harder medical categories so a
    # "Wellness Pediatrics Clinic" stays medical, not medspa.
    if (
        type_set & MEDSPA_TYPES
        or any(k in name_lower for k in MEDSPA_NAME_KEYWORDS)
    ):
        return "medspa_wellness"

    if type_set & {"doctor", "general_practitioner", "primary_care"}:
        return "primary_care"

    return "specialty"
