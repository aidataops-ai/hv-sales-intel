import logging
import re
from datetime import datetime, timezone

from src import salesforce
from src.models import Practice
from src.storage import get_practice, update_practice_fields


log = logging.getLogger("hvsi.call_log")


EMPTY_MARKER = "(call logged, no note)"


async def polish_note(raw_note: str) -> str:
    """Return the rep's note verbatim. GPT polish is disabled — reps want
    their exact words preserved in the call log and synced to Salesforce."""
    if not raw_note or not raw_note.strip():
        return EMPTY_MARKER
    return raw_note.strip()


def _format_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def append_call_note(
    place_id: str, raw_note: str, user: dict
) -> tuple[dict, str | None]:
    """Polish raw_note, append to practice.call_notes, increment count, sync SF.

    Returns (updated_practice_row, sf_warning_or_none). Raises LookupError if
    the practice does not exist. Local save is always persisted; SF failures
    surface as a warning string rather than an exception.
    """
    log.info(
        "[call_log.start] place_id=%s user=%s raw_note_len=%d",
        place_id, user.get("email"), len(raw_note or ""),
    )

    existing = get_practice(place_id)
    if not existing:
        log.warning("[call_log.404] place_id=%s not in supabase", place_id)
        raise LookupError(f"Practice not found: {place_id}")

    log.info(
        "[call_log.fetched] place_id=%s name=%r call_count=%s sf_lead_id=%s",
        place_id, existing.get("name"), existing.get("call_count"),
        existing.get("salesforce_lead_id"),
    )

    polished = await polish_note(raw_note)
    rep_name = user.get("name") or user.get("email") or "Unknown"
    line = f"[{_format_timestamp()}] {rep_name}: {polished}"

    prior_notes = existing.get("call_notes")
    new_notes = f"{prior_notes}\n{line}" if prior_notes else line
    new_count = (existing.get("call_count") or 0) + 1

    updates: dict = {
        "call_count": new_count,
        "call_notes": new_notes,
    }

    sync_view = Practice(**{**existing, **updates})

    warning: str | None = None
    try:
        log.info(
            "[call_log.sync.attempt] place_id=%s lead_id=%s configured=%s",
            place_id, sync_view.salesforce_lead_id, salesforce.is_configured(),
        )
        sync_result = await salesforce.sync_practice(sync_view, line)
        log.info("[call_log.sync.result] place_id=%s result=%s", place_id, sync_result)
        if not sync_result.get("skipped"):
            updates["salesforce_lead_id"] = sync_result["sf_lead_id"]
            updates["salesforce_synced_at"] = sync_result["synced_at"]
            if "sf_owner_id" in sync_result:
                updates["salesforce_owner_id"] = sync_result["sf_owner_id"]
            if "sf_owner_name" in sync_result:
                updates["salesforce_owner_name"] = sync_result["sf_owner_name"]
    except Exception as e:
        log.exception("[call_log.sync.error] place_id=%s err=%r", place_id, e)
        warning = f"Salesforce sync failed: {e}. Local log saved."

    updated = update_practice_fields(place_id, updates, touched_by=user.get("id"))
    log.info(
        "[call_log.done] place_id=%s call_count=%s lead_id=%s warning=%s",
        place_id, updates.get("call_count"),
        updates.get("salesforce_lead_id"), warning,
    )
    return updated or {**existing, **updates}, warning


# Pattern for a call_notes line: "[YYYY-MM-DD HH:MM UTC] Name: text"
_LINE_PREFIX_RE = re.compile(r"^(\[[^\]]+\]\s+[^:]+:\s*)(.*)$")


async def update_last_call_note(
    place_id: str, raw_note: str, user: dict
) -> tuple[dict, str | None]:
    """Replace the text portion of the most recent call_notes line.

    Used by the post-call notes modal: the rep already clicked "Call"
    (which created/updated the SF Lead with a placeholder line), then
    after the call they record what happened. This rewrites the last
    log entry's note text and resyncs the full call_notes blob to
    Salesforce — call_count is NOT incremented, no new line is added.

    Falls back to append_call_note semantics if there's no existing log
    line to update (defensive — should not happen in the normal flow).
    """
    log.info(
        "[call_log.update_last.start] place_id=%s user=%s raw_note_len=%d",
        place_id, user.get("email"), len(raw_note or ""),
    )

    existing = get_practice(place_id)
    if not existing:
        log.warning("[call_log.update_last.404] place_id=%s", place_id)
        raise LookupError(f"Practice not found: {place_id}")

    prior_notes = existing.get("call_notes") or ""
    if not prior_notes.strip():
        # No existing log entry — just delegate to append_call_note.
        log.info("[call_log.update_last.fallback_append] place_id=%s", place_id)
        return await append_call_note(place_id, raw_note, user)

    new_text = raw_note.strip() if raw_note and raw_note.strip() else EMPTY_MARKER

    lines = prior_notes.split("\n")
    last = lines[-1]
    match = _LINE_PREFIX_RE.match(last)
    if match:
        lines[-1] = f"{match.group(1)}{new_text}"
    else:
        # Last line doesn't fit the expected shape — append a fresh line
        # rather than mangle whatever is there.
        rep_name = user.get("name") or user.get("email") or "Unknown"
        lines.append(f"[{_format_timestamp()}] {rep_name}: {new_text}")
    new_notes = "\n".join(lines)

    updates: dict = {"call_notes": new_notes}
    sync_view = Practice(**{**existing, **updates})

    warning: str | None = None
    try:
        if salesforce.is_configured() and sync_view.salesforce_lead_id:
            log.info(
                "[call_log.update_last.sf_sync] lead_id=%s call_count=%s",
                sync_view.salesforce_lead_id, sync_view.call_count,
            )
            await salesforce.update_lead(
                sync_view.salesforce_lead_id,
                sync_view.call_count,
                new_notes,
            )
    except Exception as e:
        log.exception("[call_log.update_last.sf_error] place_id=%s err=%r", place_id, e)
        warning = f"Salesforce sync failed: {e}. Local log saved."

    updated = update_practice_fields(place_id, updates, touched_by=user.get("id"))
    log.info(
        "[call_log.update_last.done] place_id=%s warning=%s", place_id, warning,
    )
    return updated or {**existing, **updates}, warning
