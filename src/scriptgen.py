import json
import logging

from openai import AsyncOpenAI

from src.settings import settings

log = logging.getLogger("hvsi.scriptgen")

SYSTEM_PROMPT = """You are a cold call script writer for ApexVirtuals.

The lead has already been analyzed. The user prompt below contains the
analyzer's output for THIS specific practice — pain_points,
sales_angles, summary, review_excerpts, decision-maker contacts. Your
job is to weave those into a five-section playbook that sounds like
the rep already did their homework. A generic script ("how are you
handling front desk coverage?") fails the assignment. EVERY section
must reference at least one practice-specific signal from the inputs.

Return ONLY valid JSON with this exact structure:
{
  "sections": [
    {"title": "Opening", "icon": "phone", "content": "..."},
    {"title": "Discovery Questions", "icon": "search", "content": "..."},
    {"title": "Pitch", "icon": "target", "content": "..."},
    {"title": "Objection Handling", "icon": "shield", "content": "..."},
    {"title": "Closing", "icon": "check", "content": "..."}
  ]
}

HARD RULES (the script must reflect these — if you cannot, write less
content for the section but never substitute generic filler):

1. OPENING (3–5 sentences):
   - If website_contacts has a primary decision-maker, ask for them
     BY NAME AND TITLE ("may I speak with Sarah Smith, the practice
     manager?"). Mention their direct phone in parens if present.
   - List up to 2 secondary contacts as fallbacks on a single line
     ("Other contacts on file: Jane Doe (office mgr), Dr. Patel").
   - If no contacts, fall back to website_doctor_name, then to the
     practice name.
   - Tease ONE specific signal from pain_points — paraphrase it as
     "I noticed X" rather than asking a generic discovery question.
   - Mention the city.

2. DISCOVERY QUESTIONS (4 numbered items):
   - Numbered 1–4.
   - Items 1 and 2 MUST directly reference distinct pain_points by
     paraphrase — "You mentioned/your reviews mention X — is that
     because Y?" — not generic "how do you handle staffing?".
   - Items 3 and 4 dig into the implied root cause or quantify
     impact ("How many calls go to voicemail on a typical day?").

3. PITCH (3–6 sentences):
   - Quote ONE review excerpt verbatim with leading attribution
     ("One of your patient reviews mentioned, '...'") if any are
     provided. Skip the quote ONLY if review_excerpts is empty.
   - Name EACH sales_angle and tie it explicitly to the matching
     pain_point. Example: "Your reviews mention long phone-hold
     times — our Virtual Scheduler picks up within two rings."
   - Mention ApexVirtuals by name once.
   - If the decision-maker's title is "Owner & Lead Dentist" or
     similar, tailor the framing to that role.

4. OBJECTION HANDLING (4 paired Objection/Response blocks):
   - Cover three standard objections: "We already have a
     recruiter", "We can't afford it", "We're not hiring right
     now". Each response must reference the practice's category or
     a specific pain_point so the rebuttal feels prepared.
   - Add ONE category-specific objection (dental / medical / ALF /
     hotel / medspa). Example for dental: "Our hygienists handle
     scheduling between patients" → "That's exactly why a
     dedicated remote scheduler lifts that load off them."
   - If a secondary contact exists, end with one line:
     "If [primary] isn't available, could you point me to
     [secondary name / role]?".

5. CLOSING (2–4 sentences):
   - Reference the city ("we've placed staff at multiple
     [city]-area practices").
   - Name ONE specific sales_angle as the meeting hook.
   - Ask for a 15-minute meeting + offer a free staffing assessment.
   - If the primary contact has an email on file, offer to send
     the follow-up to that exact address.

GLOBAL RULES:
- Use the rep's perspective ("I", "we at ApexVirtuals").
- Names + phone numbers + emails from website_contacts MUST be
  copied verbatim — do not paraphrase or invent.
- If pain_points / sales_angles are empty arrays, say so honestly
  ("we'd love a few minutes to understand your current setup")
  instead of inventing detail.
- Conversational, not robotic. Avoid corporate jargon.
- No prose outside the JSON object."""


async def generate_script(
    name: str,
    category: str | None,
    summary: str | None,
    pain_points: str | None,
    sales_angles: str | None,
    *,
    city: str | None = None,
    state: str | None = None,
    rating: float | None = None,
    review_count: int | None = None,
    website_doctor_name: str | None = None,
    owner_name: str | None = None,
    owner_title: str | None = None,
    review_excerpts: list[str] | None = None,
    website_contacts: list[dict] | None = None,
    company_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Generate a cold call playbook personalized to the practice."""
    if not settings.openai_api_key:
        return _mock_script(
            name=name,
            category=category,
            website_doctor_name=website_doctor_name,
            city=city,
            website_contacts=website_contacts,
        )

    excerpts = review_excerpts or []
    location = (
        f"{city}, {state}" if (city and state) else (city or state or "Unknown")
    )
    excerpts_block = (
        "\n".join(f'- "{ex}"' for ex in excerpts) if excerpts else "(none available)"
    )
    contacts_block = _format_contacts_for_prompt(website_contacts)
    user_prompt = f"""Generate a personalized cold call playbook for this practice:

Practice: {name}
Category: {category or 'Healthcare'}
Location: {location}
Rating: {rating if rating is not None else 'unknown'} ({review_count or 0} reviews)
Lead Doctor: {website_doctor_name or 'Unknown'}
Owner Contact: {owner_name or 'Unknown'} ({owner_title or 'no title'})

Decision-maker contacts from the practice's website (use these names + numbers VERBATIM where the prompt instructs):
{contacts_block}

Analysis Summary: {summary or 'No analysis available'}
Pain Points: {pain_points or '[]'}
Sales Angles: {sales_angles or '[]'}

Verbatim Patient Review Excerpts:
{excerpts_block}
"""

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    log.info("[scriptgen.start] practice=%r model=%s contacts=%d",
             name, settings.openai_model, len(website_contacts or []))
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        if "sections" in result and len(result["sections"]) == 5:
            log.info("[scriptgen.done] practice=%r", name)
            try:
                from src.usage import record_openai
                record_openai(
                    kind="openai_script",
                    response=response,
                    company_id=company_id,
                    user_id=user_id,
                    metadata={"practice": name},
                )
            except Exception:
                pass
            return result
        log.warning("[scriptgen.bad_shape] practice=%r keys=%s",
                    name, list(result.keys()))
    except Exception as e:
        log.error("[scriptgen.openai_error] practice=%r model=%s type=%s msg=%s",
                  name, settings.openai_model,
                  type(e).__name__, str(e)[:600])

    return _mock_script(
        name=name,
        category=category,
        website_doctor_name=website_doctor_name,
        city=city,
        website_contacts=website_contacts,
    )


def _format_contacts_for_prompt(contacts: list[dict] | None) -> str:
    """Render the website_contacts list as a numbered block for the GPT prompt."""
    if not contacts:
        return "(none extracted from the website)"
    lines = []
    for i, c in enumerate(contacts, start=1):
        bits = [c.get("name") or "Unknown"]
        if c.get("title"):
            bits.append(f"— {c['title']}")
        if c.get("phone"):
            bits.append(f"· direct: {c['phone']}")
        if c.get("email"):
            bits.append(f"· email: {c['email']}")
        lines.append(f"{i}. " + " ".join(bits))
    return "\n".join(lines)


def _mock_script(
    name: str,
    category: str | None,
    website_doctor_name: str | None = None,
    city: str | None = None,
    website_contacts: list[dict] | None = None,
) -> dict:
    """Return a category-appropriate mock playbook with optional personalization."""
    cat_label = (category or "healthcare").replace("_", " ")
    # Prefer a contact from website_contacts over the legacy lead-doctor field.
    primary = (website_contacts or [None])[0]
    if primary and primary.get("name"):
        title_part = f", the {primary['title']}" if primary.get("title") else ""
        phone_part = (
            f" (direct line on file: {primary['phone']})"
            if primary.get("phone")
            else ""
        )
        doctor_greeting = (
            f"Hi, may I speak with {primary['name']}{title_part}?{phone_part}"
        )
    elif website_doctor_name:
        doctor_greeting = f"Hi, may I speak with {website_doctor_name}?"
    else:
        doctor_greeting = f"Hi, this is [Your Name] calling from ApexVirtuals about {name}."

    # Build an "other contacts on file" line for the rep's reference.
    secondary_lines = []
    for c in (website_contacts or [])[1:4]:  # up to 3 fallbacks
        bits = [c["name"]]
        if c.get("title"):
            bits.append(f"({c['title']})")
        if c.get("phone"):
            bits.append(f"— {c['phone']}")
        secondary_lines.append(" ".join(bits))
    other_contacts_line = (
        f"\nOther contacts on file: {'; '.join(secondary_lines)}."
        if secondary_lines
        else ""
    )

    city_phrase = f" in the {city} area" if city else ""

    return {
        "sections": [
            {
                "title": "Opening",
                "icon": "phone",
                "content": (
                    f"{doctor_greeting} I'm reaching out because ApexVirtuals "
                    f"helps {cat_label} practices{city_phrase} with staffing solutions. "
                    f"Do you have a quick moment?{other_contacts_line}"
                ),
            },
            {
                "title": "Discovery Questions",
                "icon": "search",
                "content": (
                    "1. How are you currently handling front desk coverage when staff call out?\n"
                    "2. Are you finding it challenging to recruit and retain qualified staff in this market?\n"
                    "3. How much time does your team spend on admin tasks versus patient coordination?\n"
                    "4. If you could add one more person to your team tomorrow, what role would make the biggest impact?"
                ),
            },
            {
                "title": "Pitch",
                "icon": "target",
                "content": (
                    f"At ApexVirtuals, we provide pre-vetted front desk staff, medical "
                    f"assistants, and administrative support specifically for practices like "
                    f"{name}. We handle recruiting, screening, and onboarding so you can focus "
                    "on patient care."
                ),
            },
            {
                "title": "Objection Handling",
                "icon": "shield",
                "content": (
                    'Objection: "We already have a recruiter."\n'
                    "Response: We complement existing recruiters with healthcare specialists.\n\n"
                    'Objection: "We can\'t afford it right now."\n'
                    "Response: Many of our clients save money via temp-to-perm placements that "
                    "prevent costly bad hires.\n\n"
                    'Objection: "We\'re not hiring right now."\n'
                    "Response: Many practices work with us proactively so they have qualified "
                    "candidates ready when a position opens."
                ),
            },
            {
                "title": "Closing",
                "icon": "check",
                "content": (
                    f"I'd love to set up a quick 15-minute call to learn more about {name}"
                    f"{city_phrase} and share how we've helped similar practices. Would Tuesday "
                    "or Wednesday work for a brief chat?"
                ),
            },
        ]
    }
