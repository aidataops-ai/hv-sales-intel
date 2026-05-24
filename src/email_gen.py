import json

from openai import AsyncOpenAI

from src.settings import settings

SYSTEM_PROMPT = """You are a cold outreach email writer for ApexVirtuals, a healthcare staffing and talent acquisition company.

Given information about a healthcare practice (name, category, analysis summary, pain points, sales angles), write a short personalized cold email (80-140 words) to the practice from a ApexVirtuals rep.

Reference ONE specific pain point and ONE specific sales angle from the analysis. End with a clear ask: a 15-minute call.

Return ONLY valid JSON with this exact structure, no other text:
{
  "subject": "a concise subject line (under 70 chars)",
  "body": "the email body as plain text with paragraph breaks as \\n\\n"
}

Tone: warm, direct, not pushy. First person ("I", "we at ApexVirtuals")."""


async def generate_email_draft(
    name: str,
    category: str | None,
    summary: str | None,
    pain_points: str | None,
    sales_angles: str | None,
) -> dict:
    """Return {subject, body}. Uses GPT if OPENAI_API_KEY set, mock otherwise."""
    if not settings.openai_api_key:
        return _mock_draft(name, category)

    user_prompt = f"""Write a cold outreach email for this practice:

Practice: {name}
Category: {category or 'Healthcare'}
Analysis Summary: {summary or 'No analysis available'}
Pain Points: {pain_points or '[]'}
Sales Angles: {sales_angles or '[]'}
"""

    client = AsyncOpenAI(api_key=settings.openai_api_key)
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
        if "subject" in result and "body" in result:
            return {"subject": result["subject"], "body": result["body"]}
    except Exception:
        pass

    return _mock_draft(name, category)


def _mock_draft(name: str, category: str | None) -> dict:
    cat = (category or "healthcare").replace("_", " ")
    return {
        "subject": f"Staffing support for {name}",
        "body": (
            f"Hi there,\n\n"
            f"I'm reaching out from ApexVirtuals — we specialize in staffing "
            f"for {cat} practices. I noticed {name} could benefit from front-desk "
            f"or admin support, and wanted to introduce myself.\n\n"
            f"We place pre-vetted healthcare staff (front desk, medical assistants, "
            f"admin VAs) within 48 hours. Most clients see scheduling delays drop "
            f"meaningfully in the first month.\n\n"
            f"Would a 15-minute call this week work to explore whether we'd be "
            f"a fit for your practice?\n\n"
            f"Best,\n"
            f"[Your Name]\n"
            f"ApexVirtuals"
        ),
    }
