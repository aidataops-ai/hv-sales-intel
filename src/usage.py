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
    # ¢ per 1M tokens. Pulled live from developers.openai.com/api/docs
    # on 2026-05-29; see docs/pricing-model.md for context.
    # Three bands per model: fresh-prompt input, cached-prompt input
    # (when OpenAI's prompt-cache hits), and output. cached_input
    # defaults to input/4 if a model is missing the explicit value.
    #
    # Legacy GPT-4.x / GPT-4o family (still live on the API):
    "o4-mini":      {"input": 110,   "cached_input": 27.5,   "output": 440},
    "gpt-4.1":      {"input": 200,   "cached_input": 50,     "output": 800},
    "gpt-4.1-mini": {"input": 40,    "cached_input": 10,     "output": 160},
    "gpt-4.1-nano": {"input": 10,    "cached_input": 2.5,    "output": 40},
    "gpt-4o":       {"input": 250,   "cached_input": 125,    "output": 1000},
    "gpt-4o-mini":  {"input": 15,    "cached_input": 7.5,    "output": 60},
    # Current GPT-5.x flagship line (on the main pricing page):
    "gpt-5.5":      {"input": 500,   "cached_input": 50,     "output": 3000},
    "gpt-5.5-pro":  {"input": 3000,  "cached_input": 750,    "output": 18000},
    "gpt-5.4":      {"input": 250,   "cached_input": 25,     "output": 1500},
    "gpt-5.4-mini": {"input": 75,    "cached_input": 7.5,    "output": 450},
    "gpt-5.4-nano": {"input": 20,    "cached_input": 2,      "output": 125},
    "gpt-5.4-pro":  {"input": 3000,  "cached_input": 750,    "output": 18000},
    # Default mirrors gpt-4.1 since that's the analyzer's pinned model.
    # If settings.openai_model changes, update this band so unmapped
    # rows are estimated against the right tier.
    "default":      {"input": 200,   "cached_input": 50,     "output": 800},
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
    cached_input_tokens: int = 0,
) -> float:
    """Return the estimated cost in cents (fractional) for an OpenAI call.

    `input_tokens` is the TOTAL prompt token count as returned by OpenAI
    (`response.usage.prompt_tokens`). `cached_input_tokens` is the subset
    that hit the prompt cache (`prompt_tokens_details.cached_tokens`).
    Fresh prompt tokens = max(0, input_tokens - cached_input_tokens) and
    are billed at the `input` rate; the cached portion is billed at
    `cached_input` (defaults to input/4 if the model band omits it).
    """
    if not input_tokens and not output_tokens:
        return 0.0
    bands = OPENAI_COST_PER_MILLION_TOKENS.get(
        model or "", OPENAI_COST_PER_MILLION_TOKENS["default"]
    )
    cached_rate = bands.get("cached_input", bands["input"] / 4.0)
    fresh_input = max(0, (input_tokens or 0) - (cached_input_tokens or 0))
    cents = (
        fresh_input * bands["input"]
        + (cached_input_tokens or 0) * cached_rate
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
    cached_input_tokens: int | None = None,
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
                model,
                input_tokens or 0,
                output_tokens or 0,
                cached_input_tokens or 0,
            )
        elif kind.startswith("places_"):
            cost_cents = estimate_places_cost(kind, calls)
        else:
            cost_cents = 0.0

    payload: dict[str, Any] = {
        "kind": kind,
        "company_id": company_id,
        "user_id": user_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
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
    """Extract usage from an OpenAI chat completion response and log it,
    including the cached-prompt-token subset when the API reports it.

    Also deducts the customer-facing credit charge from the active
    company's prepaid balance. Insufficient-credit errors propagate so
    the API layer can return HTTP 402.
    """
    in_tok = 0
    out_tok = 0
    cached_tok = 0
    model = settings.openai_model
    try:
        usage = getattr(response, "usage", None)
        if usage is not None:
            in_tok = getattr(usage, "prompt_tokens", 0) or 0
            out_tok = getattr(usage, "completion_tokens", 0) or 0
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached_tok = getattr(details, "cached_tokens", 0) or 0
            elif isinstance(usage, dict):
                cached_tok = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        model = getattr(response, "model", None) or settings.openai_model
    except Exception:
        pass
    record_event(
        kind=kind,
        company_id=company_id,
        user_id=user_id,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_input_tokens=cached_tok,
        metadata=metadata,
    )
    # Credit deduction is a no-op when company_id is None or the kind
    # isn't billable (e.g. openai_icp_parse). Failures other than
    # insufficient-credits are swallowed inside the helper.
    try:
        from src.credits import consume_for_record
        cost_cents = estimate_openai_cost(model, in_tok, out_tok, cached_tok)
        related_id = (metadata or {}).get("place_id") if metadata else None
        consume_for_record(
            kind=kind,
            company_id=company_id,
            user_id=user_id,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_input_tokens=cached_tok,
            cost_cents=cost_cents,
            related_id=related_id,
        )
    except Exception:
        # InsufficientCreditsError is allowed to propagate (caller may
        # want to surface HTTP 402); other exceptions stay silent so
        # credit bookkeeping never breaks the AI flow.
        raise


def record_places(
    *,
    kind: str,                 # 'places_search' | 'places_details'
    calls: int = 1,
    company_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a Places API call (one row per HTTP request, even for a paged search).

    Also deducts 1 credit per Places SEARCH (not per page) — see
    src/credits.py:FIXED_CREDIT_COSTS. places_details is operator-side
    and not billed.
    """
    record_event(
        kind=kind,
        calls=calls,
        company_id=company_id,
        user_id=user_id,
        metadata=metadata,
    )
    try:
        from src.credits import consume_for_record
        related_id = (metadata or {}).get("query") if metadata else None
        consume_for_record(
            kind=kind,
            company_id=company_id,
            user_id=user_id,
            calls=calls,
            cost_cents=estimate_places_cost(kind, calls),
            related_id=related_id,
        )
    except Exception:
        raise
