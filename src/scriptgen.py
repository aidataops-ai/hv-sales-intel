import json
import logging

from openai import AsyncOpenAI

from src.settings import settings

log = logging.getLogger("hvsi.scriptgen")

SYSTEM_PROMPT = """You are a cold call script writer for ApexVirtuals, a healthcare staffing and talent acquisition company.

Given information about a practice (name, category, location, lead doctor, owner, analysis summary, pain points, sales angles, review excerpts, and a list of decision-maker contacts pulled from the practice's website), generate a personalized cold call playbook tailored to THIS specific practice.

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

Personalization requirements:
- Opening: When website_contacts contains a decision-maker (owner / practice manager / GM / lead doctor), ask for that person BY NAME AND TITLE — e.g., "Hi, may I speak with Sarah Smith, the practice manager?" or "Hi, is Dr. Patel, the owner, available?". If a direct phone is listed for that person, mention it in a parenthetical so the rep can dial it next time ("(direct line on file: 555-555-0100)"). If website_contacts has more than one name, name the primary in the greeting and list the others in a short "Other contacts on file" bullet line beneath the opening greeting so the rep has fallbacks. If no contacts are available, fall back to the legacy lead doctor field, then to a generic practice greeting. Reference the city if provided.
- Discovery Questions: Reference 1-2 specific items from the provided pain_points by name (not generic). 3-4 numbered questions total.
- Pitch: If review_excerpts are provided, quote ONE excerpt verbatim with leading attribution ("One of your patient reviews mentioned, '...'") and tie it to a ApexVirtuals staffing solution. Mention ApexVirtuals by name. If website_contacts indicates the decision-maker's title (e.g. "Owner & Lead Dentist"), tailor the pitch to that role.
- Objection Handling: Cover "We already have a recruiter", "We can't afford it", "We're not hiring right now", and one objection specific to this category. If a secondary contact is available (a manager other than the primary), include one objection-recovery line of the form "If [Name] isn't available, could you point me to [secondary name / role]?".
- Closing: Reference the city when present ("we've placed staff at multiple [city]-area clinics"). Suggest a 15-minute meeting and a free staffing assessment. If an email is listed for the primary contact, offer to send a follow-up to that exact email address.

Keep each section 3-6 sentences. Be conversational, not robotic. Use the rep's perspective ("I", "we at ApexVirtuals"). When you mention a name or phone number from website_contacts, use the EXACT spelling and formatting given — do not paraphrase or guess."""


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
