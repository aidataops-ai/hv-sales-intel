import re
from collections import defaultdict
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.settings import settings

REVIEWS_FIELD_MASK = "reviews.text,reviews.rating,reviews.originalText"
REVIEW_LINK_KEYWORDS = ("review", "reviews", "testimonial", "testimonials", "feedback")
REVIEW_TEXT_KEYWORDS = (
    "review",
    "patient",
    "staff",
    "front desk",
    "wait time",
    "appointment",
    "schedule",
    "responsive",
    "helpful",
    "billing",
    "team",
)
KNOWN_REVIEW_DOMAINS = (
    "yelp.",
    "facebook.",
    "healthgrades.",
    "zocdoc.",
    "birdeye.",
    "rater8.",
    "rater8.com",
    "vitals.",
    "webmd.",
    "ratemds.",
    "demandforce.",
)
MAX_EXTERNAL_PAGES = 4
MAX_SNIPPETS_PER_PAGE = 4


async def fetch_reviews(
    place_id: str,
    name: str | None = None,
    city: str | None = None,
    state: str | None = None,
    website: str | None = None,
) -> list[dict]:
    """Fetch reviews from Google and other review sources for richer analysis."""
    google_reviews = await fetch_google_reviews(place_id)
    external_reviews = await fetch_external_reviews(
        name=name,
        city=city,
        state=state,
        website=website,
    )
    combined = google_reviews + external_reviews

    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for review in combined:
        text = (review.get("text") or "").strip()
        source = (review.get("source") or "unknown").strip()
        if not text:
            continue
        key = (source, text.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "text": text,
                "rating": review.get("rating"),
                "source": source,
                "url": review.get("url"),
            }
        )
    return deduped


async def fetch_google_reviews(place_id: str) -> list[dict]:
    """Fetch up to 5 Google reviews for a place."""
    if not settings.google_maps_api_key:
        return []
    if place_id.startswith(("mock_", "real_")):
        return []

    url = f"https://places.googleapis.com/v1/places/{place_id}"
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": REVIEWS_FIELD_MASK,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
    except (httpx.HTTPError, Exception):
        return []

    data = resp.json()
    reviews = []
    for r in data.get("reviews", []):
        text = r.get("originalText", {}).get("text") or r.get("text", {}).get("text", "")
        rating = r.get("rating")
        if text:
            reviews.append(
                {
                    "text": text.strip(),
                    "rating": rating,
                    "source": "Google Reviews",
                    "url": None,
                }
            )
    return reviews


async def fetch_external_reviews(
    name: str | None,
    city: str | None,
    state: str | None,
    website: str | None,
) -> list[dict]:
    """Discover third-party and first-party review pages, then extract review-like snippets."""
    if not name:
        return []

    candidates: list[tuple[str, str]] = []
    if website:
        candidates.extend(await _discover_first_party_review_pages(website))
    candidates.extend(await _discover_third_party_review_pages(name, city, state))

    seen_urls: set[str] = set()
    unique_candidates: list[tuple[str, str]] = []
    for source, url in candidates:
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique_candidates.append((source, url))

    reviews: list[dict] = []
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=12,
        headers={"User-Agent": "HVSalesIntel/1.0"},
    ) as client:
        for source, url in unique_candidates[:MAX_EXTERNAL_PAGES]:
            reviews.extend(await _extract_reviews_from_page(client, source, url))
    return reviews


async def _discover_first_party_review_pages(website: str) -> list[tuple[str, str]]:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10,
            headers={"User-Agent": "HVSalesIntel/1.0"},
        ) as client:
            resp = await client.get(website)
            resp.raise_for_status()
    except (httpx.HTTPError, Exception):
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    base_domain = urlparse(website).netloc
    pages: list[tuple[str, str]] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(website, link["href"])
        parsed = urlparse(href)
        if parsed.netloc != base_domain:
            continue
        hay = f"{href} {link.get_text(' ', strip=True)}".lower()
        if any(keyword in hay for keyword in REVIEW_LINK_KEYWORDS):
            pages.append(("Practice site reviews", href))
    return pages[:2]


async def _discover_third_party_review_pages(
    name: str,
    city: str | None,
    state: str | None,
) -> list[tuple[str, str]]:
    query = " ".join(part for part in [name, city, state, "reviews"] if part).strip()
    search_url = "https://html.duckduckgo.com/html/"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10,
            headers={"User-Agent": "HVSalesIntel/1.0"},
        ) as client:
            resp = await client.get(search_url, params={"q": query})
            resp.raise_for_status()
    except (httpx.HTTPError, Exception):
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[tuple[str, str]] = []
    for link in soup.find_all("a", href=True):
        raw_href = link["href"]
        href = _unwrap_duckduckgo_url(raw_href)
        if not href:
            continue
        parsed = urlparse(href)
        domain = parsed.netloc.lower()
        if any(review_domain in domain for review_domain in KNOWN_REVIEW_DOMAINS):
            results.append((parsed.netloc, href))
    return results[:4]


def _unwrap_duckduckgo_url(url: str) -> str | None:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    parsed = urlparse(url)
    if parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [])
        if uddg:
            return unquote(uddg[0])
    return None


async def _extract_reviews_from_page(
    client: httpx.AsyncClient,
    source: str,
    url: str,
) -> list[dict]:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except (httpx.HTTPError, Exception):
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)
    snippets = _extract_review_snippets(text)
    return [
        {
            "text": snippet,
            "rating": None,
            "source": source,
            "url": url,
        }
        for snippet in snippets
    ]


def _extract_review_snippets(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", unescape(text)).strip()
    if not normalized:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    picked: list[str] = []
    for sentence in sentences:
        clean = sentence.strip()
        if len(clean) < 50 or len(clean) > 320:
            continue
        lower = clean.lower()
        if any(keyword in lower for keyword in REVIEW_TEXT_KEYWORDS):
            picked.append(clean)
        if len(picked) >= MAX_SNIPPETS_PER_PAGE:
            break
    return picked


def format_reviews_for_prompt(reviews: list[dict]) -> str:
    """Format reviews from multiple sources into a compact prompt block."""
    if not reviews:
        return "No customer reviews available from Google or other discovered review sources."

    grouped: dict[str, list[dict]] = defaultdict(list)
    for review in reviews:
        grouped[review.get("source") or "Unknown source"].append(review)

    lines: list[str] = []
    for source, source_reviews in grouped.items():
        lines.append(f"{source}:")
        for i, review in enumerate(source_reviews[:5], 1):
            stars = f"{review['rating']}/5" if review.get("rating") else "no rating"
            lines.append(f"  - Review {i} ({stars}): {review['text']}")
    return "\n".join(lines)
