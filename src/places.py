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


async def search_places(query: str) -> list[Practice]:
    """Search for practices. Uses Google Places API if key is set, else mock data."""
    if settings.google_maps_api_key:
        return await _google_search(query)
    return _mock_search(query)


async def _google_search(query: str) -> list[Practice]:
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
    log.info("[places.google.start] query=%r", query)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for page in range(3):  # cap at 3 pages = 60 results
                body: dict = {"textQuery": query, "maxResultCount": 20}
                if page_token:
                    body["pageToken"] = page_token
                resp = await client.post(url, json=body, headers=headers)
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
        # Fall back to backend mock so the endpoint still returns something,
        # but tag the practices so the operator can tell mock vs real.
        return _mock_search(query)

    log.info("[places.google.done] query=%r count=%d", query, len(all_places))
    return [_map_google_place(p) for p in all_places]


async def get_place(place_id: str, fallback: Practice | None = None) -> Practice | None:
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
    if not _is_healthcare(types, name):
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

# Hard-disqualifiers — if Google tags the place with any of these, it's
# definitely not healthcare regardless of what's in the name (e.g.
# "Doctors Café", "Dental Bar Restaurant").
NEGATIVE_TYPES = frozenset({
    "cafe", "coffee_shop", "restaurant", "food", "bar", "bakery",
    "meal_takeaway", "meal_delivery", "night_club",
    "gym", "fitness_center", "beauty_salon", "hair_care", "spa",
    "store", "supermarket", "shopping_mall", "clothing_store",
    "convenience_store", "grocery_or_supermarket", "department_store",
    "lodging", "tourist_attraction", "park", "school", "university",
    "car_dealer", "car_repair", "car_rental", "gas_station",
    "real_estate_agency", "insurance_agency", "lawyer", "bank", "atm",
})

# Keywords in the place name that strongly suggest healthcare. Note: we do
# NOT include the bare word "doctor" because it appears in restaurant /
# café names ("Doctors Café"). The phrase "Dr." with a trailing dot is a
# stronger signal because it almost always precedes a person's name.
HEALTHCARE_NAME_KEYWORDS = (
    "clinic", "hospital", "medical", "dental", "dentist", "orthodont",
    "psychiatr", "psycholog", "mental health", "behavioral health",
    "primary care", "urgent care", "physiotherap", "chiropract",
    "rehab", "physiotherapy", "podiatr", "dermatolog", "optomet",
    "ophthalmolog", "veterinar", "pediatr", "obgyn", "obstetr",
    "gynecolog", "cardiolog", "neurolog", "oncolog", "radiolog",
    "dr.", "md ", "m.d.", "dds", "d.d.s.",
)


def _is_healthcare(types: list[str], name: str) -> bool:
    """True if the place looks like a healthcare practice we'd want to target.

    Logic:
      1. If Google flags it as a café / restaurant / shop / etc → reject.
      2. If Google flags it as a healthcare type → accept.
      3. Otherwise inspect the display name (solo practitioners often only
         get the generic 'doctor' or 'point_of_interest' type).
    """
    type_set = set(types or [])
    if NEGATIVE_TYPES & type_set:
        return False
    if HEALTHCARE_TYPES & type_set:
        return True
    name_lower = (name or "").lower()
    return any(k in name_lower for k in HEALTHCARE_NAME_KEYWORDS)


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

    Google's types are unreliable for solo psychiatry/dental practices —
    they often only get the generic 'doctor' type. We use the display name
    as a fallback signal so e.g. "Dr Smith Psychiatrist" doesn't end up as
    primary_care.
    """
    type_set = set(types)
    name_lower = (name or "").lower()

    # Mental health gets checked first because psychiatrists frequently
    # carry only the 'doctor' type — name-based detection is the only
    # reliable signal for them.
    if (
        type_set & {"psychiatrist", "psychologist", "mental_health"}
        or any(
            keyword in name_lower
            for keyword in (
                "psychiatrist", "psychiatric", "psychiatry",
                "psychologist", "psychology", "mental health",
                "behavioral health", "psychotherapy", "counseling",
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

    if type_set & {"hospital", "urgent_care_center", "emergency_room"} or "urgent care" in name_lower:
        return "urgent_care"

    if type_set & {"doctor", "general_practitioner", "primary_care"}:
        return "primary_care"

    return "specialty"
