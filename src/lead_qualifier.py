"""The one AI pass over a raw posting (ADR-05).

Two independent questions, and **both** must pass:

1. **Employer** — a small independent practice, or a hospital system /
   multi-site group / DSO / staffing agency / out-of-scope business?
2. **Role** — could a remote, non-clinical person perform the core duties?

The second test is not optional. Without it the qualifier keeps clinical roles
at genuinely independent practices: employer right, lead unusable.

The prompt below is ported from the evaluation prototype essentially verbatim.
It has been through two revisions driven by *measured* failures — an earlier
version that said "guess the work mode, defaulting to onsite" returned onsite
for almost everything including postings explicitly flagged remote, and an
earlier version without TEST 2 kept dental hygienist roles at perfect-fit
practices. Reword it from the ADR prose and those regressions come back. The
only deliberate edit is the company name, which follows this repo's brand.

The model's field names (`practice_type`, `role_remotable`) are kept exactly as
measured and mapped onto the `company_job_leads` columns in `parse_verdict`,
rather than renamed in the prompt where the change would be untested.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from src import lead_config, lead_store
from src.settings import settings

log = logging.getLogger("hvsi.leads.qualifier")

WORK_MODES = ("onsite", "remote", "hybrid")
EMPLOYER_TYPES = ("independent", "group", "system", "dso", "vet",
                  "agency", "nonhealthcare")

# Employer types that are, by definition, a discard. Used only to sanity-check
# a self-contradictory verdict — never to override the model's own decision.
DISQUALIFYING_TYPES = ("system", "dso", "agency", "vet", "nonhealthcare")

COMPANY_NAME = "Apex&Virtuals (Apex)"


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


def _posting_row(index: int, posting: dict) -> str:
    excerpt_len = lead_config.options()["qualifier_excerpt_chars"]
    snippet = (
        (posting.get("description") or "")
        .replace("\n", " ")
        .replace('"', "'")[:excerpt_len]
    )
    salary_min = posting.get("salary_min")
    salary = (
        f"${salary_min:.0f}-{(posting.get('salary_max') or salary_min):.0f} "
        f"{posting.get('salary_interval') or ''}".strip()
        if salary_min else "not stated"
    )
    return (
        f'{index}. id={posting["id"]} | title="{posting.get("title") or ""}" '
        f'| company="{posting.get("employer_name") or ""}" '
        f'| location="{posting.get("location_raw") or ""}" '
        f'| remote_flag={str(bool(posting.get("board_remote_flag"))).lower()} '
        f'| salary="{salary}" '
        f'| hint_service_line="{posting.get("service_line_hint") or ""}" '
        f'| snippet="{snippet}"'
    )


def build_prompt(postings: list[dict]) -> str:
    tracks = lead_config.service_lines()
    rows = "\n".join(_posting_row(i + 1, p) for i, p in enumerate(postings))
    lines = ", ".join(f'"{t}"' for t in tracks)
    tracks_bullets = "\n".join("  - " + t for t in tracks)
    return f"""You qualify US job postings as sales leads for {COMPANY_NAME}, which places HIPAA-trained REMOTE, NON-CLINICAL admin and support staff (schedulers, receptionists, front office, billing, prior-auth, insurance verification, coordinators) into SMALL INDEPENDENT US practices.

{COMPANY_NAME} currently sells only these service lines ("tracks"):
{tracks_bullets}

For EACH posting below, decide KEEP or DISCARD. A posting must pass BOTH tests to be KEPT.

TEST 1 — IS THE EMPLOYER RIGHT?
KEEP only if the employer is a SMALL INDEPENDENT practice — roughly solo to 15 providers, single or a few locations, where an owner or office manager makes the hiring decision. Target types: independent medical, dental, and home-health practices/agencies.
DISCARD if the employer is: a hospital or health system; a multi-site corporate group or DSO (dental service organization); a staffing/recruiting agency; a veterinary practice; a large company; or not a professional practice at all.
Be strict: recognizable national/regional chains, DSOs (e.g. Heartland, Sage, Aspen, Smile, Pacific, DECA, Coast, Specialty1), large multi-site groups (e.g. Gastro Health, American Oncology Network) and hospital systems (e.g. HCA, AdventHealth, Orlando Health, Baptist Health, University of...) are NOT independent.

TEST 2 — IS THE ROLE PLACEABLE REMOTELY?
Our staff work remotely and do NOT perform clinical or physical tasks.
DISCARD if the role requires a clinical licence or physical presence with patients — e.g. Registered Nurse, LPN, X-Ray/Radiologic Technologist, Phlebotomist, Dental Hygienist, chairside Dental Assistant, Physical Therapist, Surgical Tech, caregiver, home health aide, driver.
KEEP only if the core duties are administrative and could be performed by a remote person: scheduling, phones, intake, front-desk coordination, billing, prior authorization, insurance verification, records, staffing coordination.
A job TITLE containing "Assistant" is not enough — read the snippet. "Medical Assistant" is often a clinical role; "Front Office Assistant" usually is not. An administrative role AT a clinical practice (e.g. "Front Desk Administrator, physical therapy office") DOES pass — judge the duties, not the setting.

For EACH posting produce an object with:
- "external_id": the id exactly as given
- "decision": "keep" or "discard"
- "service_line": for KEEP, EXACTLY one of: {lines}. For discard, null.
- "work_mode": "onsite", "remote", or "hybrid". Determine this from EVIDENCE, not assumption: if remote_flag=true, or the title/snippet says remote / work from home / telecommute, answer "remote". If it says hybrid, answer "hybrid". Only answer "onsite" when there is no remote or hybrid signal.
- "role_remotable": true or false — does this role pass TEST 2?
- "practice_type": one of "independent","group","system","dso","vet","agency","nonhealthcare"
- "provider_count": integer estimate of providers, or null
- "confidence": number 0.0-1.0 — your confidence in the decision. Use the salary and snippet as evidence; be more confident when they corroborate the employer type.
- "reason": one sentence explaining the decision. If you discarded on TEST 2, say which clinical/physical requirement caused it.
- "draft": for KEEP only, a warm 2-3 sentence outreach message to the practice. Reference their specific role and city. Offer a HIPAA-trained [service line] — write the service line name exactly as given, do NOT prefix it with another "virtual" — at roughly 70% less than a local hire, with a 2-week free trial. End with a soft call to action. If work_mode is "onsite" and a salary was stated, reference that local wage as the comparison. For discard, null.

Rules:
- Do not invent facts. Base the decision on the company name, title, location, salary and snippet provided.
- If the employer is right but the role is clinical, DISCARD and set role_remotable=false.
- Return one object per posting, in the same order as the input.

Respond with a JSON object of exactly this shape and nothing else:
{{"results": [ {{...}}, {{...}} ]}}

JOBS:
{rows}
"""


# ---------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------


def parse_payload(content: str | None) -> list[dict]:
    """Pull the verdict list out of whatever the model wrapped it in.

    JSON mode is requested, but a fenced block or a bare array still shows up
    occasionally and costing a whole batch over a ``` is not worth it.
    """
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        for key in ("results", "postings", "jobs", "data", "output"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data] if "external_id" in data else []
    return data if isinstance(data, list) else []


def _clamp01(value) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _one_of(value, allowed: tuple[str, ...]) -> str | None:
    text = str(value or "").lower().strip()
    return text if text in allowed else None


def parse_verdict(raw: dict, posting: dict, model: str | None) -> dict | None:
    """One model object -> one `company_job_leads` verdict row, or None.

    Every enum is validated before persistence, so a malformed field degrades
    a single row rather than failing the batch. This is also where the
    prototype's field names are mapped onto the schema's: `practice_type` ->
    `employer_type`, `role_remotable` -> `role_suitable`.
    """
    if not isinstance(raw, dict):
        return None

    decision = _one_of(raw.get("decision"), ("keep", "discard"))
    if decision is None:
        return None

    confidence = _clamp01(raw.get("confidence"))
    band, band_rank = lead_store.band_for(confidence)
    service_line = raw.get("service_line")
    if service_line not in lead_config.service_lines():
        service_line = None
    provider_count = raw.get("provider_count")

    employer_type = _one_of(raw.get("practice_type"), EMPLOYER_TYPES)
    role_suitable = raw.get("role_remotable")

    # A "keep" at a hospital system contradicts TEST 1. Rather than override
    # the model, drop the lead into the review queue where a human sees it —
    # ADR-07 measured that misclassifications reliably self-flag as low
    # confidence, and overriding would hide the signal that the prompt needs
    # tuning.
    if decision == "keep" and employer_type in DISQUALIFYING_TYPES:
        band, band_rank = "decide", lead_store.BAND_RANK["decide"]

    return {
        "posting_id": posting["id"],
        "decision": decision,
        "confidence": confidence,
        "confidence_band": band,
        "band_rank": band_rank,
        "reason": str(raw.get("reason") or "")[:2000] or None,
        "employer_type": employer_type,
        "role_suitable": bool(role_suitable) if role_suitable is not None else None,
        "work_mode": _one_of(raw.get("work_mode"), WORK_MODES),
        # Fall back to the search term's track so a kept lead is never
        # untracked — the operator filters by track, and a null hides it.
        "service_line": service_line or (
            posting.get("service_line_hint") if decision == "keep" else None
        ),
        "provider_count": provider_count if isinstance(provider_count, int) else None,
        "draft": str(raw["draft"])[:8000] if raw.get("draft") else None,
        "model": model,
        "qualified_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


def _client():
    from openai import OpenAI
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=settings.openai_api_key, timeout=300)


def _is_parameter_rejection(message: str) -> str | None:
    """Which unsupported parameter the API just complained about, if any.

    Checked BEFORE any model-not-found handling on purpose. `gpt-5.6-terra`
    accepts only the default temperature, and its rejection message contains
    the word "model" — which a naive "model → abort" branch reads as a fatal
    unknown-model error and kills an otherwise valid run.
    """
    low = message.lower()
    if "reasoning" in low:
        return "reasoning_effort"
    if "temperature" in low:
        return "temperature"
    return None


def qualify_batch(
    postings: list[dict],
    *,
    company_id: str | None = None,
    user_id: str | None = None,
) -> tuple[list[dict], dict]:
    """Qualify up to `qualifier_batch_size` postings in one call.

    Returns `(verdicts, stats)`. Model calls are metered through the existing
    usage ledger and debit tenant credits (ADR-10) — a new subsystem making
    unmetered calls would put real cost outside the billing model.

    Temperature is never sent. The model accepts only its default of 1, so
    runs are not byte-reproducible: 95% decision stability was measured, with
    a median per-posting confidence drift of 0.01. That matters if anyone
    tunes the 0.85 band boundary from a single run.
    """
    from src import usage

    if not postings:
        return [], {"batches": 0, "verdicts": 0, "missing": 0}

    client = _client()
    model = settings.qualifier_model
    effort: str | None = settings.qualifier_reasoning_effort
    prompt = build_prompt(postings)

    content = ""
    response = None
    last_error: Exception | None = None

    for attempt in range(1, 4):
        kwargs: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        if effort:
            kwargs["reasoning_effort"] = effort
        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            break
        except Exception as e:
            last_error = e
            rejected = _is_parameter_rejection(str(e))
            if rejected == "reasoning_effort" and effort:
                log.warning("[leads.qualify] model rejects reasoning_effort — retrying without it")
                effort = None
                continue
            log.warning("[leads.qualify.error] attempt=%d %s: %s",
                        attempt, type(e).__name__, str(e)[:250])

    if response is None:
        raise RuntimeError(
            f"qualifier gave up after 3 attempts: {str(last_error)[:250]}"
        )

    usage.record_openai(
        kind="openai_qualify",
        response=response,
        company_id=company_id,
        user_id=user_id,
        metadata={"postings": len(postings), "model": model},
    )

    by_id = {str(p["id"]): p for p in postings}
    verdicts: list[dict] = []
    for raw in parse_payload(content):
        posting = by_id.get(str((raw or {}).get("external_id")))
        if posting is None:
            continue
        verdict = parse_verdict(raw, posting, getattr(response, "model", model))
        if verdict is not None:
            verdicts.append(verdict)

    stats = {
        "batches": 1,
        "verdicts": len(verdicts),
        # Postings the model skipped or mangled. They stay unqualified and are
        # picked up by the next run rather than being written as a discard —
        # a parse failure is not a verdict.
        "missing": len(postings) - len(verdicts),
        "keeps": sum(1 for v in verdicts if v["decision"] == "keep"),
    }
    log.info("[leads.qualify] postings=%d verdicts=%d keeps=%d missing=%d",
             len(postings), stats["verdicts"], stats["keeps"], stats["missing"])
    return verdicts, stats


def batched(postings: list[dict], size: int | None = None):
    """Split a claim into model-sized batches (20 by default, per ADR-06)."""
    size = size or settings.qualifier_batch_size
    for i in range(0, len(postings), max(1, size)):
        yield postings[i:i + max(1, size)]
