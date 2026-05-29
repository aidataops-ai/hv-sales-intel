"""Prepaid credits — pricing, conversion, and deduction helpers.

Customers buy credits in advance. A credit has a fixed cash value
(`CREDIT_VALUE_CENTS = 33` → $0.33 per credit). Every billable action
deducts credits from the company's balance at a universal multiple of
our underlying vendor cost: `COST_MULTIPLIER = 10.0`.

That means a $0.016 OpenAI call bills the customer $0.16 (~0.48
credits), and a $0.10 Google Places search bills $1.00 (~3 credits).
The same rule applies everywhere — OpenAI tokens, Places calls,
enrichment — so unit economics stay consistent across actions.

Two display modes follow from the one rule:

  DYNAMIC (`analyze`, `call_script`, `email_draft`, `bulk_scan_query`,
    `places_details`): the exact cost isn't known until the vendor
    response comes back (tokens / pages_fetched). The UI shows a
    range upfront, and the server deducts the precise amount after.

  TYPICAL (`enrichment`): we don't track a per-call cost from
    Clay/Apollo yet so we use a representative cost
    (`ENRICHMENT_COST_CENTS`) — still computed via the 10× rule, so
    the constant captures the assumed vendor price, not a markup.

Deduction is done via the `consume_credits` Postgres RPC (see the
2026-05-29-credits migration) which atomically locks the company row,
verifies the balance, decrements it, and writes a `credit_transactions`
ledger entry. If the balance is insufficient the RPC raises with
SQLSTATE `P0001`; the API layer converts that to HTTP 402.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.usage import estimate_openai_cost, estimate_places_cost, PLACES_COST_CENTS

log = logging.getLogger(__name__)

# 1 credit = 33¢. The fundamental unit of pricing for the product.
# Customers buy credits at this rate; every action's credit cost can
# be reasoned about as `credits * CREDIT_VALUE_CENTS / 100` in USD.
CREDIT_VALUE_CENTS: float = 33.0

# Universal multiple of vendor cost. Applies to OpenAI tokens, Google
# Places calls, and enrichment alike — every billable action runs the
# customer at 10× our underlying cost. Change this number to retune
# every action's price in one shot.
COST_MULTIPLIER: float = 10.0

# Backwards-compatible alias for callers that imported the OpenAI-only
# name from the previous design. Both point to the same constant.
OPENAI_COST_MULTIPLIER: float = COST_MULTIPLIER

# Representative cost (¢) of a single Clay/Apollo enrichment lookup.
# We don't currently track per-call enrichment cost from the provider,
# so the rate uses this assumption. Update if the provider changes.
ENRICHMENT_COST_CENTS: float = 6.6   # → 2 credits at 10× / 33¢ per credit

# Display ranges shown in the UI before an action runs. Derived from
# observed distributions of underlying cost — server still deducts the
# exact amount post-call.
#   analyze:        gpt-4.1 @ 1k-15k prompt tokens + 0.5k-2k output
#   call_script:    gpt-4.1 @ shorter prompts
#   email_draft:    gpt-4.1 @ very short prompts
#   bulk_scan_query: 1-3 Places pages per query (3.2¢ each, 10× = ~1c/p)
#   places_details: single 1.7¢ call
ANALYZE_RANGE_CREDITS: tuple[float, float] = (0.3, 1.5)
CALL_SCRIPT_RANGE_CREDITS: tuple[float, float] = (0.1, 0.4)
EMAIL_DRAFT_RANGE_CREDITS: tuple[float, float] = (0.05, 0.20)
BULK_SCAN_RANGE_CREDITS: tuple[float, float] = (0.97, 2.91)   # 1-3 pages
PLACES_DETAILS_CREDITS: float = 0.52                          # 1 call

CreditAction = Literal[
    "analyze",
    "call_script",
    "email_draft",
    "bulk_scan_query",
    "enrichment",
    "topup",
    "adjustment",
    "refund",
]


@dataclass
class CreditQuote:
    """What an action will cost (estimated, displayed before the call)."""

    action: CreditAction
    low: float           # minimum credits — usually what we display
    high: float          # maximum credits — for range display
    is_fixed: bool       # True when low == high (no uncertainty)

    def display(self) -> str:
        if self.is_fixed:
            return f"{format_credits(self.low)} credit{'' if abs(self.low - 1) < 1e-9 else 's'}"
        return f"{format_credits(self.low)}–{format_credits(self.high)} credits"


# ---------------------------------------------------------------------------
# Conversion: actual cost (cents) → credit charge
# ---------------------------------------------------------------------------


def cost_cents_to_credits(cost_cents: float) -> float:
    """Convert an underlying ¢ cost to credits at the 10× multiplier.

    Rounded to 4 decimal places so the ledger stays clean and small
    sub-credit charges still net to a meaningful number.
    """
    if cost_cents <= 0:
        return 0.0
    credits = (cost_cents * COST_MULTIPLIER) / CREDIT_VALUE_CENTS
    return round(credits, 4)


def credits_to_dollars(credits: float) -> float:
    return round((credits * CREDIT_VALUE_CENTS) / 100.0, 4)


def quote(action: CreditAction, **kwargs) -> CreditQuote:
    """Return an upfront credit quote for `action`.

    Every action is priced at 10× our underlying vendor cost. Dynamic
    actions (variable token count or page count) show a range upfront;
    the server deducts the precise amount once the vendor reports.
    """
    if action == "analyze":
        low, high = ANALYZE_RANGE_CREDITS
        return CreditQuote(action, low, high, False)
    if action == "call_script":
        low, high = CALL_SCRIPT_RANGE_CREDITS
        return CreditQuote(action, low, high, False)
    if action == "email_draft":
        low, high = EMAIL_DRAFT_RANGE_CREDITS
        return CreditQuote(action, low, high, False)
    if action == "bulk_scan_query":
        low, high = BULK_SCAN_RANGE_CREDITS
        return CreditQuote(action, low, high, False)
    if action == "enrichment":
        n = cost_cents_to_credits(ENRICHMENT_COST_CENTS)
        return CreditQuote(action, n, n, True)
    # Unknown actions don't consume credits (e.g. topup, adjustment).
    return CreditQuote(action, 0.0, 0.0, True)


def credits_for_openai_response(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    """Compute exact credits to deduct for an OpenAI call from its
    `response.usage` numbers. Re-uses the existing cost estimator so
    new model bands automatically flow through to credit pricing."""
    cents = estimate_openai_cost(
        model, input_tokens, output_tokens, cached_input_tokens,
    )
    return cost_cents_to_credits(cents)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Deduction — Supabase RPC wrappers.
# These are called from usage.py after every billable event so credit
# accounting stays in lockstep with usage telemetry.
# ---------------------------------------------------------------------------


# Maps usage_events.kind → (credit action label, credits to deduct).
# Returning None means "don't deduct" (skip — e.g. operator-side calls
# like openai_icp_parse which the customer doesn't pay for).
def _kind_to_credits(
    kind: str,
    *,
    model: str | None,
    in_tok: int,
    out_tok: int,
    cached_tok: int,
    calls: int,
) -> tuple[CreditAction, float] | None:
    if kind == "openai_analyze":
        return ("analyze",     credits_for_openai_response(model, in_tok, out_tok, cached_tok))
    if kind == "openai_script":
        return ("call_script", credits_for_openai_response(model, in_tok, out_tok, cached_tok))
    if kind == "openai_email":
        return ("email_draft", credits_for_openai_response(model, in_tok, out_tok, cached_tok))
    if kind == "places_search":
        return ("bulk_scan_query", cost_cents_to_credits(
            estimate_places_cost("places_search", max(1, calls))
        ))
    if kind == "places_details":
        return ("places_details", cost_cents_to_credits(
            estimate_places_cost("places_details", max(1, calls))
        ))
    # openai_icp_parse: admin-side internal — not billed.
    return None


def consume_for_record(
    *,
    kind: str,
    company_id: str | None,
    user_id: str | None,
    model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    calls: int = 1,
    cost_cents: float = 0.0,
    related_id: str | None = None,
) -> float | None:
    """Deduct credits for a just-recorded usage event.

    Returns the new balance after the deduction, or None when nothing
    was deducted (no company_id, unknown kind, zero amount, or RPC
    failure). Failure is silent because credit accounting must NEVER
    block the user-facing flow that already paid the underlying cost.
    """
    if not company_id:
        return None
    mapped = _kind_to_credits(
        kind,
        model=model,
        in_tok=input_tokens or 0,
        out_tok=output_tokens or 0,
        cached_tok=cached_input_tokens or 0,
        calls=calls or 1,
    )
    if mapped is None:
        return None
    action, credits = mapped
    if credits <= 0:
        return None

    try:
        from src.storage import _get_client  # type: ignore
    except Exception:
        return None
    client = _get_client()
    if not client:
        return None

    try:
        resp = client.rpc("consume_credits", {
            "p_company_id":  company_id,
            "p_user_id":     user_id,
            "p_amount":      round(float(credits), 4),
            "p_action":      action,
            "p_related_id":  related_id,
            "p_cost_cents":  round(float(cost_cents), 4) if cost_cents else None,
            "p_notes":       None,
        }).execute()
        new_balance = resp.data if resp and hasattr(resp, "data") else None
        log.info(
            "[credits.consume] company=%s action=%s credits=%.4f new_balance=%s",
            company_id, action, credits, new_balance,
        )
        return float(new_balance) if new_balance is not None else None
    except Exception as e:
        # Postgres P0001 = insufficient credits. We surface that to the
        # caller via a sentinel exception so the API layer can return
        # HTTP 402. All other errors swallow silently.
        msg = str(e)
        if "INSUFFICIENT_CREDITS" in msg:
            raise InsufficientCreditsError(msg)
        log.warning("[credits.consume.error] %s: %s",
                    type(e).__name__, msg[:300])
        return None


class InsufficientCreditsError(Exception):
    """Raised when consume_credits fails the balance check.

    The API layer catches this and returns HTTP 402.
    """
    pass


def get_balance(company_id: str) -> float:
    """Read the current credit balance for a company. Returns 0.0 on
    any storage failure (defensive — never crashes a request)."""
    if not company_id:
        return 0.0
    try:
        from src.storage import _get_client  # type: ignore
    except Exception:
        return 0.0
    client = _get_client()
    if not client:
        return 0.0
    try:
        resp = (
            client.table("companies")
            .select("credit_balance")
            .eq("id", company_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return 0.0
        return float(rows[0].get("credit_balance") or 0)
    except Exception as e:
        log.warning("[credits.balance.error] %s: %s",
                    type(e).__name__, str(e)[:300])
        return 0.0


def topup(
    *,
    company_id: str,
    amount: float,
    user_id: str | None = None,
    source: str = "admin_topup",
    notes: str | None = None,
) -> float | None:
    """Grant `amount` credits to a company. Returns the new balance."""
    if not company_id or amount <= 0:
        return None
    try:
        from src.storage import _get_client  # type: ignore
    except Exception:
        return None
    client = _get_client()
    if not client:
        return None
    try:
        resp = client.rpc("add_credits", {
            "p_company_id": company_id,
            "p_user_id":    user_id,
            "p_amount":     round(float(amount), 4),
            "p_kind":       "topup",
            "p_source":     source,
            "p_notes":      notes,
        }).execute()
        return float(resp.data) if resp and resp.data is not None else None
    except Exception as e:
        log.warning("[credits.topup.error] %s: %s",
                    type(e).__name__, str(e)[:300])
        return None


def format_credits(n: float) -> str:
    """Render a credit amount for the UI.

    Whole-credit values come out as integers; sub-credit values use up
    to two decimals trimmed of trailing zeros. Examples:
        1.0   → "1"
        2.5   → "2.5"
        0.48  → "0.48"
        0.5   → "0.5"
    """
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return s
