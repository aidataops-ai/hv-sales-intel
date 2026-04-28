import logging
from datetime import datetime, timezone

import httpx

from src.models import Practice
from src.settings import settings


log = logging.getLogger("hvsi.salesforce")


def is_configured() -> bool:
    """True when both Apex endpoint URL and API key are set."""
    return bool(settings.sf_apex_url and settings.sf_api_key)


def _scores_description(practice: Practice) -> str | None:
    scores = (practice.lead_score, practice.urgency_score, practice.hiring_signal_score)
    if all(s is None for s in scores):
        return None
    return (
        f"Lead Score: {practice.lead_score or 0} | "
        f"Urgency: {practice.urgency_score or 0} | "
        f"Hiring Signal: {practice.hiring_signal_score or 0}"
    )


def _build_create_payload(practice: Practice, call_note_line: str) -> dict:
    """Build the POST body for the Apex REST Lead create endpoint.

    Includes every field the Apex handler expects, including the required
    custom field Lead_Type__c. Omits None values so Salesforce keeps its
    own defaults rather than receiving "None" strings.
    """
    payload: dict = {
        "Company": practice.name,
        "OwnerName": practice.owner_name or "",
        "OwnerPhone": practice.owner_phone or practice.phone or "",
        "OwnerEmail": practice.owner_email or "",
        "Email": practice.email or "",
        "Website": practice.website or "",
        "Street": practice.address or "",
        "City": practice.city or "",
        "State": practice.state or "",
        "PostalCode": "",
        "Country": "USA",
        "Industry": "Healthcare",
        "LeadSource": "HV Sales Intel",
        "Status": "Working - Contacted",
        "Lead_Type__c": "Outbound",
        "Description": _scores_description(practice) or "",
        "Call_Count__c": str(practice.call_count or 1),
        "Call_Notes__c": call_note_line,
    }
    return payload


def _build_update_payload(sf_lead_id: str, call_count: int, call_notes: str) -> dict:
    return {
        "Id": sf_lead_id,
        "Status": "Working - Contacted",
        "Lead_Type__c": "Outbound",
        "Call_Count__c": str(call_count),
        "Call_Notes__c": call_notes,
    }


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "x-api-key": settings.sf_api_key,
    }


def _redacted_endpoint() -> str:
    """Return the apex URL with the trailing path only — for logs."""
    url = settings.sf_apex_url
    if not url:
        return "(unset)"
    # Show host + last path segment only
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.netloc}{p.path}"
    except Exception:
        return url


async def create_lead(practice: Practice, call_note_line: str) -> dict:
    """POST a new Lead to the Apex endpoint. Returns the parsed JSON response."""
    body = _build_create_payload(practice, call_note_line)
    log.info(
        "[sf.create.request] endpoint=%s company=%r note_len=%d call_count=%s",
        _redacted_endpoint(), body["Company"], len(call_note_line), body["Call_Count__c"],
    )
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(settings.sf_apex_url, headers=_headers(), json=body)
        except httpx.HTTPError as e:
            log.error("[sf.create.network_error] err=%r", e)
            raise
        log.info(
            "[sf.create.response] status=%s body=%s",
            resp.status_code, resp.text[:500],
        )
        resp.raise_for_status()
    return resp.json()


async def update_lead(sf_lead_id: str, call_count: int, call_notes: str) -> dict:
    """PUT updates to an existing Lead. Returns the parsed JSON response."""
    body = _build_update_payload(sf_lead_id, call_count, call_notes)
    log.info(
        "[sf.update.request] endpoint=%s lead_id=%s call_count=%s notes_len=%d",
        _redacted_endpoint(), sf_lead_id, call_count, len(call_notes),
    )
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.put(settings.sf_apex_url, headers=_headers(), json=body)
        except httpx.HTTPError as e:
            log.error("[sf.update.network_error] lead_id=%s err=%r", sf_lead_id, e)
            raise
        log.info(
            "[sf.update.response] lead_id=%s status=%s body=%s",
            sf_lead_id, resp.status_code, resp.text[:500],
        )
        resp.raise_for_status()
    return resp.json()


async def update_lead_description(sf_lead_id: str, description: str) -> dict:
    """PUT only the Description field on an existing Lead.

    Used by the Notes panel: rep's free-text notes go into the Lead's
    Description field on Salesforce, separate from the per-call log
    that lives in Call_Notes__c.
    """
    body = {
        "Id": sf_lead_id,
        "Status": "Working - Contacted",
        "Lead_Type__c": "Outbound",
        "Description": description or "",
    }
    log.info(
        "[sf.update_desc.request] endpoint=%s lead_id=%s desc_len=%d",
        _redacted_endpoint(), sf_lead_id, len(description or ""),
    )
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.put(settings.sf_apex_url, headers=_headers(), json=body)
        except httpx.HTTPError as e:
            log.error("[sf.update_desc.network_error] lead_id=%s err=%r", sf_lead_id, e)
            raise
        log.info(
            "[sf.update_desc.response] lead_id=%s status=%s body=%s",
            sf_lead_id, resp.status_code, resp.text[:500],
        )
        resp.raise_for_status()
    return resp.json()


def lead_view_url(sf_lead_id: str | None) -> str | None:
    """Build the Lightning Lead view URL from a lead id."""
    if not sf_lead_id:
        return None
    base = settings.sf_lead_view_base_url.rstrip("/")
    return f"{base}/{sf_lead_id}/view"


async def sync_practice(practice: Practice, polished_line: str) -> dict:
    """Create or update the SF Lead for this practice via the Apex endpoint.

    Returns dict with sf_lead_id + sf_lead_url + synced_at on success, or
    {'skipped': True, 'reason': ...} when SF is not configured. Raises on
    network/API failures so the caller can surface a warning.
    """
    if not is_configured():
        log.warning(
            "[sf.sync.skipped] reason=not_configured sf_apex_url_set=%s sf_api_key_set=%s",
            bool(settings.sf_apex_url), bool(settings.sf_api_key),
        )
        return {"skipped": True, "reason": "sf_not_configured"}

    now_iso = datetime.now(timezone.utc).isoformat()

    if practice.salesforce_lead_id:
        log.info(
            "[sf.sync.update_branch] lead_id=%s call_count=%s",
            practice.salesforce_lead_id, practice.call_count,
        )
        await update_lead(
            practice.salesforce_lead_id,
            practice.call_count,
            practice.call_notes or "",
        )
        return {
            "sf_lead_id": practice.salesforce_lead_id,
            "sf_lead_url": lead_view_url(practice.salesforce_lead_id),
            "sf_owner_name": practice.owner_name or "",
            "synced_at": now_iso,
        }

    log.info("[sf.sync.create_branch] practice=%r", practice.name)
    created = await create_lead(practice, polished_line)
    sf_lead_id = created.get("leadId") or created.get("id")
    if not sf_lead_id:
        log.error("[sf.sync.bad_response] response=%s", created)
        raise RuntimeError(f"Salesforce response missing leadId: {created}")
    log.info("[sf.sync.created] lead_id=%s", sf_lead_id)
    return {
        "sf_lead_id": sf_lead_id,
        "sf_lead_url": lead_view_url(sf_lead_id),
        "sf_owner_name": practice.owner_name or "",
        "synced_at": now_iso,
    }
