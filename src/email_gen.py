import json
from typing import Any

from openai import AsyncOpenAI

from src.settings import settings

# Interactive path: the user is watching a spinner, so fail fast rather than
# hang on the SDK's 600s default.
_OPENAI_TIMEOUT = 60.0

# One cached client for the process. Building `AsyncOpenAI` per call paid a
# fresh TCP+TLS handshake to api.openai.com every time and abandoned the
# connection pool to the GC. The cache is a single slot keyed on both the
# API key and the *class object* currently bound to `AsyncOpenAI`, so a test
# that patches either `settings` or `AsyncOpenAI` gets a client built from
# its own patch instead of one left behind by an earlier test.
_client_cache: tuple[Any, Any, Any] | None = None


def _get_client() -> Any:
    """Return the process-wide AsyncOpenAI client, building it on first use."""
    global _client_cache
    cls, api_key = AsyncOpenAI, settings.openai_api_key
    if _client_cache is not None:
        cached_cls, cached_key, cached_client = _client_cache
        if cached_cls is cls and cached_key == api_key:
            return cached_client
    client = cls(api_key=api_key, timeout=_OPENAI_TIMEOUT)
    _client_cache = (cls, api_key, client)
    return client


def _reset_client() -> None:
    """Drop the cached client. Test hook — see `tests/conftest.py`."""
    global _client_cache
    _client_cache = None


SYSTEM_PROMPT = """You are a cold outreach email writer for Health & Virtuals, a healthcare staffing and talent acquisition company.

Given information about a healthcare practice (name, category, analysis summary, pain points, sales angles), write a short personalized cold email (80-140 words) to the practice from a Health & Virtuals rep.

Reference ONE specific pain point and ONE specific sales angle from the analysis. End with a clear ask: a 15-minute call.

Return ONLY valid JSON with this exact structure, no other text:
{
  "subject": "a concise subject line (under 70 chars)",
  "body": "the email body as plain text with paragraph breaks as \\n\\n"
}

Tone: warm, direct, not pushy. First person ("I", "we at Health & Virtuals")."""


async def generate_email_draft(
    name: str,
    category: str | None,
    summary: str | None,
    pain_points: str | None,
    sales_angles: str | None,
    *,
    company_id: str | None = None,
    user_id: str | None = None,
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

    client = _get_client()
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
            try:
                from src.usage import record_openai
                record_openai(
                    kind="openai_email",
                    response=response,
                    company_id=company_id,
                    user_id=user_id,
                    metadata={"practice": name},
                )
            except Exception:
                pass
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
            f"I'm reaching out from Health & Virtuals — we specialize in staffing "
            f"for {cat} practices. I noticed {name} could benefit from front-desk "
            f"or admin support, and wanted to introduce myself.\n\n"
            f"We place pre-vetted healthcare staff (front desk, medical assistants, "
            f"admin VAs) within 48 hours. Most clients see scheduling delays drop "
            f"meaningfully in the first month.\n\n"
            f"Would a 15-minute call this week work to explore whether we'd be "
            f"a fit for your practice?\n\n"
            f"Best,\n"
            f"[Your Name]\n"
            f"Health & Virtuals"
        ),
    }
