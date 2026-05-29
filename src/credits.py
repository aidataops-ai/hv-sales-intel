"""Prepaid credits — pricing, conversion, and deduction helpers.

Customers buy credits in advance. A credit has a fixed cash value
(`CREDIT_VALUE_CENTS = 33` → $0.33 per credit). Every billable action
deducts credits from the company's balance.

Two billing models live side-by-side here:

  DYNAMIC (OpenAI-driven):
    For `analyze`, `call_script`, `email_draft` — we charge a multiple
    of our actual OpenAI cost. `OPENAI_COST_MULTIPLIER = 10.0` means
    a $0.016 analyze becomes $0.16 customer cost → ~0.48 credits.
    The exact amount isn't known until OpenAI returns usage tokens, so
    the UI shows a RANGE upfront (see ANALYZE_RANGE_CREDITS) and the
    server deducts the precise amount after the call returns.

  FIXED:
    For `bulk_scan_query`, `enrichment` — flat per-action cost. Bulk
    scan = 1 credit per Google Places search; enrichment = 2 credits
    per Clay/Apollo lookup. Predictable so the modal can show a hard
    total before the run starts.

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

from src.usage import estimate_openai_cost

log = logging.getLogger(__name__)

# 1 credit = 33¢. The fundamental unit of pricing for the product.
# Customers buy credits at this rate; every action's credit cost can
# be reasoned about as `credits * CREDIT_VALUE_CENTS / 100` in USD.
CREDIT_VALUE_CENTS: float = 33.0

# Customer-charged multiple of our underlying OpenAI cost. 10× gives us
# the gross-margin profile in docs/pricing-model.md (≈90% on AI usage)
# and absorbs the variable overhead (Places lookups, crawler bandwidth,
# the rest of the stack). Tune here to change the whole pricing curve.
OPENAI_COST_MULTIPLIER: float = 10.0

# Fixed costs in CREDITS for actions whose underlying cost is either
# predictable (Places API has a flat per-call rate) or paid to a
# third-party provider (Clay/Apollo enrichment).
FIXED_CREDIT_COSTS: dict[str, float] = {
    "bulk_scan_query": 1.0,   # one Google Places search = 1 credit
    "enrichment":      2.0,   # one Clay/Apollo enrichment = 2 credits
}

# Range shown in the UI before an analyze runs. Pulled from observed
# distributions in production logs: a thin practice page lands at
# ~0.3 credits; a long dental site with 50 reviews → ~1.5 credits.
# These are display-only — server still deducts the exact amount.
ANALYZE_RANGE_CREDITS: tuple[float, float] = (0.3, 1.5)
CALL_SCRIPT_RANGE_CREDITS: tuple[float, float] = (0.1, 0.4)
EMAIL_DRAFT_RANGE_CREDITS: tuple[float, float] = (0.05, 0.20)

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
    credits = (cost_cents * OPENAI_COST_MULTIPLIER) / CREDIT_VALUE_CENTS
    return round(credits, 4)


def credits_to_dollars(credits: float) -> float:
    return round((credits * CREDIT_VALUE_CENTS) / 100.0, 4)


def quote(action: CreditAction, **kwargs) -> CreditQuote:
    """Return an upfront credit quote for `action`.

    For dynamic actions we use the display range constants — the actual
    deduction happens server-side after OpenAI reports usage.
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
    if action in FIXED_CREDIT_COSTS:
        n = FIXED_CREDIT_COSTS[action]
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


# Maps usage_events.kind → (credit action label, predicate(record) → credits)
# Returning None from the predicate means "don't deduct" (skip).
def _kind_to_credits(
    kind: str,
    model: str | None,
    in_tok: int,
    out_tok: int,
    cached_tok: int,
) -> tuple[CreditAction, float] | None:
    if kind == "openai_analyze":
        return ("analyze",     credits_for_openai_response(model, in_tok, out_tok, cached_tok))
    if kind == "openai_script":
        return ("call_script", credits_for_openai_response(model, in_tok, out_tok, cached_tok))
    if kind == "openai_email":
        return ("email_draft", credits_for_openai_response(model, in_tok, out_tok, cached_tok))
    if kind == "places_search":
        return ("bulk_scan_query", FIXED_CREDIT_COSTS["bulk_scan_query"])
    # openai_icp_parse / places_details / others — operator-side, not billed.
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
        kind, model,
        input_tokens or 0, output_tokens or 0, cached_input_tokens or 0,
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
