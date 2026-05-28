"""GPT-driven ICP document parser.

Reads a free-text ICP document (pasted in the admin UI) and returns a
structured JSON object matching the schema in
`docs/specs/2026-05-24-multitenant-icp-upload-design.md`. Saved to
`companies.icp_parsed`; Phase 6 will template the analyzer prompt
from it so each tenant's leads are scored against THEIR criteria.
"""

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from src.md_input import savings_summary, to_markdown
from src.settings import settings

log = logging.getLogger("hvsi.icp_parser")


# Strict allowlist for vertical codes — the parser cannot invent new ones.
VALID_VERTICALS = {
    "medical", "mental_health", "dental",
    "alf_nh", "hotel_resort", "medspa_wellness",
}

VALID_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC",
    "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY",
}

VALID_TIERS = {"A", "B", "C", "D"}

DIMENSION_KEYS = (
    "vertical_fit",
    "operational_pain",
    "decision_maker_access",
    "remote_readiness",
    "role_clarity",
    "budget_maturity",
    "compliance_boundary",
)

DEFAULT_WEIGHTS = {
    "vertical_fit":          15,
    "operational_pain":      20,
    "decision_maker_access": 15,
    "remote_readiness":      15,
    "role_clarity":          15,
    "budget_maturity":       10,
    "compliance_boundary":   10,
}


SYSTEM_PROMPT = """You are reading an Ideal Customer Profile (ICP) document for a B2B
sales team and extracting it into a strict JSON schema that drives
their lead-scoring AI. Read the document carefully and output ONLY
JSON matching this exact shape:

{
  "verticals_in_scope":   ["dental", "medical", ...],
  "verticals_adjacent":   ["..."],
  "geographies": {
    "focus_states":     ["FL"],
    "operating_states": ["TX","NY",...],
    "outside_us":       "exclude"          // or "allow"
  },
  "size_categories": {
    "primary":       ["A","B"],
    "opportunistic": ["C","D"]
  },
  "dimension_weights": {
    "vertical_fit":          15,
    "operational_pain":      20,
    "decision_maker_access": 15,
    "remote_readiness":      15,
    "role_clarity":          15,
    "budget_maturity":       10,
    "compliance_boundary":   10
  },
  "in_scope_keywords":      ["assisted living","dental clinic",...],
  "disqualifiers":          ["wants licensed clinical work","outside US",...],
  "primary_decision_makers":["owner","practice manager","general manager",...],
  "service_catalog":        ["Virtual Scheduler","Patient Care Coordinator",...],
  "brand_voice":            "warm, direct, not pushy",
  "company_self_description": "single-sentence company description for the analyzer prompt"
}

RULES:
- `verticals_in_scope` and `verticals_adjacent` values MUST be one of:
  medical, mental_health, dental, alf_nh, hotel_resort, medspa_wellness.
  If the document mentions a vertical not on this list, drop it.
- `focus_states` and `operating_states` are 2-letter US codes only.
- `outside_us` is either "exclude" or "allow".
- `size_categories.primary` / `opportunistic` use only the letters A/B/C/D.
- `dimension_weights` MUST sum to exactly 100. If the document doesn't
  specify weights, use the defaults shown above. All seven keys
  must always be present.
- Empty arrays are fine ("[]"). Don't invent content the document
  doesn't suggest.

Output JSON only — no markdown fences, no prose, no comments."""


async def parse_icp_doc(
    raw_text: str,
    *,
    company_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Send the ICP text to GPT and return the structured dict.

    Falls back to a default skeleton if OpenAI isn't configured or the
    request fails (so the admin UI still has something to display).
    """
    raw = (raw_text or "").strip()
    if not raw:
        raise ValueError("ICP document is empty")
    # Normalize HTML / pasted-from-Word noise to Markdown before we hand
    # it to GPT — typically cuts the prompt by 30-50% on copy-pasted web
    # docs and keeps headings/bullets the model can reason over.
    cleaned = to_markdown(raw)
    if not cleaned:
        cleaned = raw  # fall back if normalization wiped everything
    # Hard cap input — the parser doesn't need novel-length docs.
    snippet = cleaned[:16_000]

    if not settings.openai_api_key:
        log.warning("[icp_parser.no_key] returning skeleton")
        return _validate(_skeleton())

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    log.info("[icp_parser.start] model=%s %s",
             settings.openai_model, savings_summary(raw, snippet))
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": snippet},
            ],
            temperature=0,
            seed=42,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        raw = json.loads(content)
        validated = _validate(raw)
        log.info("[icp_parser.done] verticals=%s states=%d",
                 validated["verticals_in_scope"],
                 len(validated["geographies"]["operating_states"]))
        try:
            from src.usage import record_openai
            record_openai(
                kind="openai_icp_parse",
                response=response,
                company_id=company_id,
                user_id=user_id,
                metadata={"chars": len(snippet)},
            )
        except Exception:
            pass
        return validated
    except Exception as e:
        log.error("[icp_parser.error] type=%s msg=%s",
                  type(e).__name__, str(e)[:600])
        return _validate(_skeleton())


# ---------------------------------------------------------------------------
# Validation — keeps the output trustworthy even if GPT goes sideways.
# ---------------------------------------------------------------------------


def _validate(raw: dict[str, Any]) -> dict:
    """Coerce, clamp, and fill defaults so the saved JSON matches the
    schema regardless of what GPT returned. Never raises."""
    if not isinstance(raw, dict):
        raw = {}

    verticals_in = _filter_verticals(raw.get("verticals_in_scope"))
    verticals_adj = _filter_verticals(raw.get("verticals_adjacent"))

    geo = raw.get("geographies") if isinstance(raw.get("geographies"), dict) else {}
    focus = _filter_states(geo.get("focus_states"))
    operating = _filter_states(geo.get("operating_states"))
    outside = "exclude" if geo.get("outside_us") not in ("allow", "exclude") else geo["outside_us"]

    size = raw.get("size_categories") if isinstance(raw.get("size_categories"), dict) else {}
    sizes_primary = [t for t in (size.get("primary") or []) if t in VALID_TIERS]
    sizes_opp = [t for t in (size.get("opportunistic") or []) if t in VALID_TIERS]

    weights = _normalize_weights(raw.get("dimension_weights"))

    return {
        "verticals_in_scope": verticals_in,
        "verticals_adjacent": verticals_adj,
        "geographies": {
            "focus_states":     focus,
            "operating_states": operating,
            "outside_us":       outside,
        },
        "size_categories": {
            "primary":       sizes_primary or ["A", "B"],
            "opportunistic": sizes_opp or ["C", "D"],
        },
        "dimension_weights": weights,
        "in_scope_keywords":      _str_list(raw.get("in_scope_keywords"), cap=40),
        "disqualifiers":          _str_list(raw.get("disqualifiers"), cap=20),
        "primary_decision_makers": _str_list(raw.get("primary_decision_makers"), cap=15),
        "service_catalog":         _str_list(raw.get("service_catalog"), cap=25),
        "brand_voice":             _coerce_str(raw.get("brand_voice"), "warm, direct, not pushy"),
        "company_self_description": _coerce_str(
            raw.get("company_self_description"),
            "a sales-development-focused services company",
        ),
    }


def _skeleton() -> dict:
    return {
        "verticals_in_scope": [],
        "verticals_adjacent": [],
        "geographies": {
            "focus_states":     [],
            "operating_states": [],
            "outside_us":       "exclude",
        },
        "size_categories": {"primary": ["A", "B"], "opportunistic": ["C", "D"]},
        "dimension_weights": dict(DEFAULT_WEIGHTS),
        "in_scope_keywords": [],
        "disqualifiers": [],
        "primary_decision_makers": [],
        "service_catalog": [],
        "brand_voice": "warm, direct, not pushy",
        "company_self_description": "",
    }


def _filter_verticals(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        if isinstance(v, str) and v.lower() in VALID_VERTICALS and v not in out:
            out.append(v.lower())
    return out


def _filter_states(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for s in value:
        if not isinstance(s, str):
            continue
        code = s.strip().upper()
        if code in VALID_STATES and code not in out:
            out.append(code)
    return out


def _str_list(value, cap: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for s in value:
        if not isinstance(s, str):
            continue
        clean = s.strip()
        if clean and clean not in out:
            out.append(clean)
        if len(out) >= cap:
            break
    return out


def _coerce_str(value, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _normalize_weights(raw) -> dict[str, int]:
    """Coerce to ints, fill missing keys with defaults, then rescale so
    the total equals 100 exactly."""
    weights: dict[str, int] = {}
    raw = raw if isinstance(raw, dict) else {}
    for key in DIMENSION_KEYS:
        v = raw.get(key, DEFAULT_WEIGHTS[key])
        try:
            weights[key] = max(0, int(v))
        except (TypeError, ValueError):
            weights[key] = DEFAULT_WEIGHTS[key]

    total = sum(weights.values())
    if total == 0:
        return dict(DEFAULT_WEIGHTS)
    if total == 100:
        return weights
    # Proportional rescale, then nudge the largest dimension to absorb
    # rounding error so the total lands on exactly 100.
    scaled = {k: round(v * 100 / total) for k, v in weights.items()}
    drift = 100 - sum(scaled.values())
    if drift != 0:
        biggest = max(scaled, key=lambda k: scaled[k])
        scaled[biggest] = max(0, scaled[biggest] + drift)
    return scaled
