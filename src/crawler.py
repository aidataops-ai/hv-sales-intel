import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

PRIORITY_PATTERNS = re.compile(
    r"(career|job|hiring|team|about|staff|service|contact|provider|doctor|meet)",
    re.IGNORECASE,
)

DOCTOR_NAME_DR_PREFIX = re.compile(
    r"(?:Dr\.?|Doctor)\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)"
)
DOCTOR_NAME_CRED_SUFFIX = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s*,?\s*(MD|DDS|DO|DPM|DC|FNP|PA-C)\b"
)
PHONE_PATTERN = re.compile(
    r"(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})"
)

MAX_PAGES = 10
TIMEOUT = 10


async def crawl_website(url: str) -> dict:
    """Crawl a website and extract bulk text + best-guess doctor name and phone.

    Returns:
        {
          "text": <combined page text>,
          "doctor_name": <"Dr. Firstname Lastname"> or None,
          "doctor_phone": <"(555) 123-4567"> or None,
        }
    """
    if not url:
        return {"text": "", "doctor_name": None, "doctor_phone": None}

    visited: set[str] = set()
    texts: list[str] = []
    raw_html_chunks: list[str] = []
    base_domain = urlparse(url).netloc

    to_visit = [url]
    discovered: list[str] = []

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=TIMEOUT,
        headers={"User-Agent": "ApexSalesIntel/1.0"},
    ) as client:
        while (to_visit or discovered) and len(visited) < MAX_PAGES:
            current = to_visit.pop(0) if to_visit else discovered.pop(0)
            normalized = _normalize_url(current)
            if normalized in visited:
                continue
            visited.add(normalized)

            try:
                resp = await client.get(current)
                if resp.status_code != 200:
                    continue
                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type:
                    continue
            except (httpx.HTTPError, Exception):
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            raw_html_chunks.append(resp.text)
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            if text:
                texts.append(text[:5000])

            if len(visited) <= 3:
                for a in soup.find_all("a", href=True):
                    href = urljoin(current, a["href"])
                    parsed = urlparse(href)
                    if parsed.netloc != base_domain:
                        continue
                    if parsed.scheme not in ("http", "https"):
                        continue
                    norm = _normalize_url(href)
                    if norm in visited:
                        continue
                    if PRIORITY_PATTERNS.search(href):
                        to_visit.append(href)
                    else:
                        discovered.append(href)

    combined_text = "\n\n---\n\n".join(texts)
    combined_html = "\n".join(raw_html_chunks)
    doctor_name = _extract_doctor_name(combined_html)
    doctor_phone = _extract_doctor_phone(
        combined_html,
        doctor_name=doctor_name,
        front_desk_phone=None,
    )
    return {
        "text": combined_text,
        "doctor_name": doctor_name,
        "doctor_phone": doctor_phone,
    }


def _extract_doctor_name(html_or_text: str) -> str | None:
    """Find the most frequent Dr.-prefix or credential-suffix name in the text."""
    if not html_or_text:
        return None
    counts: Counter[str] = Counter()
    for match in DOCTOR_NAME_DR_PREFIX.finditer(html_or_text):
        counts[f"Dr. {match.group(1)}"] += 1
    for match in DOCTOR_NAME_CRED_SUFFIX.finditer(html_or_text):
        counts[f"Dr. {match.group(1)}"] += 1
    if not counts:
        return None
    most_common, _ = counts.most_common(1)[0]
    return most_common


def _extract_doctor_phone(
    html_or_text: str,
    doctor_name: str | None,
    front_desk_phone: str | None,
) -> str | None:
    """Find a phone near the doctor name; skip if it equals the front desk phone."""
    if not html_or_text:
        return None
    front_desk_digits = re.sub(r"\D", "", front_desk_phone or "")

    def _digit_match(phone: str) -> bool:
        digits = re.sub(r"\D", "", phone)
        if len(digits) not in (10, 11):
            return False
        if front_desk_digits and digits.endswith(front_desk_digits[-10:]):
            return False
        return True

    if doctor_name:
        bare_name = doctor_name.replace("Dr. ", "")
        for needle in (doctor_name, bare_name):
            for match in re.finditer(re.escape(needle), html_or_text):
                start = max(0, match.start() - 200)
                end = min(len(html_or_text), match.end() + 200)
                window = html_or_text[start:end]
                phone_match = PHONE_PATTERN.search(window)
                if phone_match and _digit_match(phone_match.group(1)):
                    return phone_match.group(1).strip()

    label_pattern = re.compile(
        r"(?:direct|personal|cell|mobile|doctor's)[^.\n]{0,40}?"
        + PHONE_PATTERN.pattern,
        re.IGNORECASE,
    )
    label_match = label_pattern.search(html_or_text)
    if label_match and _digit_match(label_match.group(1)):
        return label_match.group(1).strip()
    return None


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"
