"""Usage + cost recording for Places API and OpenAI calls.

Every external call records a row in `usage_events` so the admin
console can show consumption + estimated cost broken down by kind,
model, and company. The numbers feed the per-tenant pricing model.

Recording is fail-soft — if Supabase is down, we log and move on.
External call results are never blocked by usage-tracking failures.
"""

from __future__ import annotations

import logging
from typing import Any

from src.settings import settings

log = logging.getLogger("hvsi.usage")


# ---------------------------------------------------------------------------
# Pricing constants. All values are in **cents per million tokens** (OpenAI)
# or **cents per call** (Google Places). Update when vendor pricing changes.
# Operators can override via env in a follow-up — fine for the demo as is.
# ---------------------------------------------------------------------------

OPENAI_COST_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    # ¢ per 1M tokens. $2 input / $8 output = 200¢ / 800¢ per million.
    "gpt-4.1":          {"input": 200,  "output": 800},
    "gpt-4.1-mini":     {"input": 40,   "output": 160},
    "gpt-4o":           {"input": 250,  "output": 1000},
    "gpt-4o-mini":      {"input": 15,   "output": 60},
    "gpt-4-turbo":      {"input": 1000, "output": 3000},
    "gpt-3.5-turbo":    {"input": 50,   "output": 150},
    # Fallback used when the model is unknown.
    "default":          {"input": 200,  "output": 800},
}

# Cents per Places-API call. Pro SKU Text Search is $0.032 = 3.2¢; Place
# Details (Pro) is $0.017 = 1.7¢. Update if you're on a different SKU.
PLACES_COST_CENTS: dict[str, float] = {
    "places_search":  3.2,
    "places_details": 1.7,
}


# ---------------------------------------------------------------------------
# Cost calculators.
# ---------------------------------------------------------------------------


def estimate_openai_cost(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Return the estimated cost in cents (fractional) for an OpenAI call."""
    if not input_tokens and not output_tokens:
        return 0.0
    bands = OPENAI_COST_PER_MILLION_TOKENS.get(
        model or "", OPENAI_COST_PER_MILLION_TOKENS["default"]
    )
    cents = (
        (input_tokens or 0) * bands["input"]
        + (output_tokens or 0) * bands["output"]
    ) / 1_000_000.0
    return round(cents, 4)


def estimate_places_cost(kind: str, calls: int = 1) -> float:
    """Return the estimated cost in cents for a Places API call.
    `kind` is one of 'places_search' or 'places_details'."""
    per_call = PLACES_COST_CENTS.get(kind, 0.0)
    return round(per_call * max(0, calls), 4)


# ---------------------------------------------------------------------------
# Recorder. Silent on failure so usage logging never breaks the caller.
# ---------------------------------------------------------------------------


def record_event(
    *,
    kind: str,
    company_id: str | None = None,
    user_id: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    calls: int = 1,
    cost_cents: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert a usage_events row. Computes `cost_cents` from inputs if not
    explicitly provided. Drops the write silently if Supabase isn't
    configured or the insert fails."""
    if cost_cents is None:
        if kind.startswith("openai_"):
            cost_cents = estimate_openai_cost(
                model, input_tokens or 0, output_tokens or 0,
            )
        elif kind.startswith("places_"):
            cost_cents = estimate_places_cost(kind, calls)
        else:
            cost_cents = 0.0

    payload = {
        "kind": kind,
        "company_id": company_id,
        "user_id": user_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "calls": calls,
        "cost_cents": cost_cents,
        "metadata": metadata,
    }

    # Lazy import to avoid a Supabase client init at module load time.
    try:
        from src.storage import _get_client  # type: ignore
    except Exception:
        return
    client = _get_client()
    if not client:
        return
    try:
        client.table("usage_events").insert(payload).execute()
    except Exception as e:
        log.warning("[usage.record.error] type=%s msg=%s",
                    type(e).__name__, str(e)[:300])


# ---------------------------------------------------------------------------
# Convenience wrappers used by the instrumentation call sites.
# ---------------------------------------------------------------------------


def record_openai(
    *,
    kind: str,                 # 'openai_analyze' | 'openai_script' | 'openai_email' | 'openai_icp_parse'
    response: Any,             # the openai chat completion response
    company_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Extract usage from an OpenAI chat completion response and log it."""
    try:
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        model = getattr(response, "model", None) or settings.openai_model
    except Exception:
        in_tok = 0
        out_tok = 0
        model = settings.openai_model
    record_event(
        kind=kind,
        company_id=company_id,
        user_id=user_id,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        metadata=metadata,
    )


def record_places(
    *,
    kind: str,                 # 'places_search' | 'places_details'
    calls: int = 1,
    company_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a Places API call (one row per HTTP request, even for a paged search)."""
    record_event(
        kind=kind,
        calls=calls,
        company_id=company_id,
        user_id=user_id,
        metadata=metadata,
    )
