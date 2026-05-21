import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


# ----- Logging setup (must happen before module imports below) -----
# Vercel captures anything written to stdout. Force INFO level on the hvsi.*
# loggers so call_log + salesforce traces show up in `vercel logs`.
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))
logging.getLogger("hvsi").handlers = [_log_handler]
logging.getLogger("hvsi").setLevel(logging.INFO)
logging.getLogger("hvsi").propagate = False
log = logging.getLogger("hvsi.api")

from src.analyzer import analyze_practice
from src.auth import get_admin_client, get_current_user, require_admin
from src.call_log import append_call_note, update_last_call_note
from src.salesforce import lead_view_url


def _analysis_input_fingerprint(record: dict) -> str:
    """Stable 16-char hash of the practice fields fed to the analyzer.

    Used to short-circuit Re-analyze when nothing material has changed.
    Includes the identity fields (name/website/category/state/city) but
    NOT volatile metrics (rating, review_count) — those wobble slightly
    every Rescan and would falsely invalidate the cache on every click.
    """
    fields = [
        (record.get("name") or "").strip().lower(),
        (record.get("website") or "").strip().lower(),
        (record.get("category") or "").strip().lower(),
        (record.get("state") or "").strip().upper(),
        (record.get("city") or "").strip().lower(),
    ]
    payload = "|".join(fields)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _attach_lead_url(practice: dict | None) -> dict | None:
    """Compute salesforce_lead_url from lead_id so the frontend always has it.

    Even if the DB column doesn't exist on this deployment, the URL is
    derivable from the lead id alone.
    """
    if not practice:
        return practice
    if not practice.get("salesforce_lead_url") and practice.get("salesforce_lead_id"):
        practice["salesforce_lead_url"] = lead_view_url(practice["salesforce_lead_id"])
    return practice
from src.clay import trigger_enrichment
from src.email_gen import generate_email_draft
from src.email_poll import poll_replies
from src.email_send import send_email
from src.models import Practice
from src.places import get_place, search_places
from src.reviews import fetch_reviews
from src.scriptgen import generate_script
from src.settings import settings as app_settings
from src.storage import (
    add_tags,
    find_duplicate_place_ids,
    get_cached_search,
    get_practice,
    increment_export_counts,
    insert_email_message,
    list_email_messages,
    list_outbound_message_ids,
    query_for_export,
    query_practices,
    save_search_cache,
    update_practice_analysis,
    update_practice_fields,
    upsert_practices,
)

app = FastAPI(title="HV Sales Intel", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def bootstrap_admin_on_startup():
    """If no admin exists and BOOTSTRAP_ADMIN_* env vars are set, seed one."""
    from src.settings import settings
    from src.validators import validate_password
    if not (settings.supabase_url and settings.supabase_service_role_key):
        return
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    try:
        validate_password(settings.bootstrap_admin_password)
    except ValueError as e:
        print(f"[bootstrap] BOOTSTRAP_ADMIN_PASSWORD invalid: {e} — admin not seeded.")
        return
    try:
        client = get_admin_client()
        existing = client.table("profiles").select("id").eq("role", "admin").execute()
        if existing.data:
            return
        created = client.auth.admin.create_user({
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
            "email_confirm": True,
            "user_metadata": {"name": "Bootstrap Admin"},
        })
        client.table("profiles").update({"role": "admin"}).eq("id", created.user.id).execute()
        print(f"[bootstrap] Seeded admin: {settings.bootstrap_admin_email}")
    except Exception as e:
        print(f"[bootstrap] Skipped ({e!r})")


STATUS_ORDER = [
    "NEW", "RESEARCHED", "SCRIPT READY", "CONTACTED",
    "FOLLOW UP", "MEETING SET", "PROPOSAL", "CLOSED WON", "CLOSED LOST",
]


def _should_auto_advance(current: str, target: str) -> bool:
    try:
        return STATUS_ORDER.index(target) > STATUS_ORDER.index(current)
    except ValueError:
        return False


class CreateUserRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "sdr"


@app.get("/api/admin/users")
def list_users(admin: dict = Depends(require_admin)):
    """List all profiles with per-user touched-practice count."""
    client = get_admin_client()
    profiles_res = client.table("profiles").select("*").execute()
    counts_res = client.table("practices").select("last_touched_by").execute()
    counts: dict[str, int] = {}
    for row in counts_res.data or []:
        uid = row.get("last_touched_by")
        if uid:
            counts[uid] = counts.get(uid, 0) + 1
    users = []
    for p in profiles_res.data or []:
        users.append({**p, "practices_touched": counts.get(p["id"], 0)})
    return {"users": users}


@app.post("/api/admin/users")
def create_user(body: CreateUserRequest, admin: dict = Depends(require_admin)):
    from src.validators import validate_email, validate_password

    try:
        validate_email(body.email)
        validate_password(body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if body.role not in ("admin", "sdr"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'sdr'")

    client = get_admin_client()
    try:
        created = client.auth.admin.create_user({
            "email": body.email,
            "password": body.password,
            "email_confirm": True,
            "user_metadata": {"name": body.name},
        })
    except Exception as e:
        msg = str(e)
        if "already registered" in msg.lower() or "already exists" in msg.lower():
            raise HTTPException(status_code=400, detail="Email already in use.")
        raise HTTPException(status_code=400, detail=msg)

    user_id = created.user.id
    if body.role == "admin":
        client.table("profiles").update({"role": "admin"}).eq("id", user_id).execute()
    profile = client.table("profiles").select("*").eq("id", user_id).single().execute()
    return profile.data


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: str, admin: dict = Depends(require_admin)):
    from src.auth import is_bootstrap_admin

    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete self")

    client = get_admin_client()
    target = (
        client.table("profiles").select("*")
        .eq("id", user_id).single().execute().data
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Only the bootstrap admin can delete another admin.
    if target.get("role") == "admin" and not is_bootstrap_admin(admin):
        raise HTTPException(
            status_code=403,
            detail="Only the bootstrap admin can delete another admin.",
        )

    try:
        client.auth.admin.delete_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


class PatchUserRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    disabled: bool | None = None  # True to disable, False to enable


@app.patch("/api/admin/users/{user_id}")
def patch_user(
    user_id: str,
    body: PatchUserRequest,
    admin: dict = Depends(require_admin),
):
    """Edit name/role and/or disable/enable a user.

    Same bootstrap-admin gating as reset-password: only the bootstrap admin
    can edit or disable another admin. Cannot disable self.
    """
    from src.auth import is_bootstrap_admin

    client = get_admin_client()
    target = (
        client.table("profiles")
        .select("*")
        .eq("id", user_id)
        .single()
        .execute()
        .data
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Self-disable guard
    if body.disabled is True and user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot disable self")

    # Bootstrap-admin gate: protects edits/disables on other admins
    target_is_admin = target.get("role") == "admin"
    becoming_admin = body.role == "admin"
    if (target_is_admin or becoming_admin) and not is_bootstrap_admin(admin) and user_id != admin["id"]:
        raise HTTPException(
            status_code=403,
            detail="Only the bootstrap admin can edit or disable another admin (or promote to admin).",
        )

    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.role is not None:
        if body.role not in ("admin", "sdr"):
            raise HTTPException(status_code=400, detail="role must be 'admin' or 'sdr'")
        fields["role"] = body.role
    if body.disabled is not None:
        fields["disabled_at"] = (
            datetime.now(timezone.utc).isoformat() if body.disabled else None
        )

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        result = client.table("profiles").update(fields).eq("id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result.data[0] if result.data else target


class ResetPasswordRequest(BaseModel):
    new_password: str


@app.post("/api/admin/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    admin: dict = Depends(require_admin),
):
    from src.auth import is_bootstrap_admin
    from src.validators import validate_password

    try:
        validate_password(body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client = get_admin_client()
    target = (
        client.table("profiles")
        .select("*")
        .eq("id", user_id)
        .single()
        .execute()
        .data
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.get("role") == "admin" and not is_bootstrap_admin(admin):
        raise HTTPException(
            status_code=403,
            detail="Only the bootstrap admin can reset another admin's password.",
        )

    try:
        client.auth.admin.update_user_by_id(user_id, {"password": body.new_password})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


# ======================= Email outreach endpoints =======================

def _email_configured() -> bool:
    return bool(
        app_settings.ms_tenant_id
        and app_settings.ms_client_id
        and app_settings.ms_client_secret
        and app_settings.ms_refresh_token
        and app_settings.ms_sender_email
    )


class EmailDraftPatch(BaseModel):
    subject: str | None = None
    body: str | None = None


def _parse_draft(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


@app.get("/api/practices/{place_id}/email/draft")
async def get_email_draft_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    cached = _parse_draft(practice.get("email_draft"))
    if cached:
        return cached

    draft = await generate_email_draft(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
    )
    update_practice_fields(
        place_id,
        {
            "email_draft": json.dumps(draft),
            "email_draft_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        touched_by=user["id"],
    )
    return draft


@app.post("/api/practices/{place_id}/email/draft")
async def regenerate_email_draft_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    draft = await generate_email_draft(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
    )
    update_practice_fields(
        place_id,
        {
            "email_draft": json.dumps(draft),
            "email_draft_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        touched_by=user["id"],
    )
    return draft


@app.patch("/api/practices/{place_id}/email/draft")
def patch_email_draft_endpoint(
    place_id: str,
    body: EmailDraftPatch,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    current = _parse_draft(practice.get("email_draft")) or {"subject": "", "body": ""}
    if body.subject is not None:
        current["subject"] = body.subject
    if body.body is not None:
        current["body"] = body.body

    update_practice_fields(
        place_id,
        {
            "email_draft": json.dumps(current),
            "email_draft_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        touched_by=user["id"],
    )
    return current


@app.post("/api/practices/{place_id}/email/send")
async def send_email_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    if not _email_configured():
        raise HTTPException(503, "Email not configured")

    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    email_to = practice.get("email")
    if not email_to:
        raise HTTPException(400, "Email address required")

    draft = _parse_draft(practice.get("email_draft"))
    if not draft or not draft.get("subject") or not draft.get("body"):
        raise HTTPException(400, "No draft to send")

    try:
        result = await send_email(email_to, draft["subject"], draft["body"])
    except Exception as e:
        insert_email_message(
            practice_id=practice["id"],
            user_id=user["id"],
            direction="out",
            subject=draft["subject"],
            body=draft["body"],
            message_id=None,
            in_reply_to=None,
            error=str(e),
        )
        raise HTTPException(500, f"Send failed: {e}") from e

    row = insert_email_message(
        practice_id=practice["id"],
        user_id=user["id"],
        direction="out",
        subject=draft["subject"],
        body=draft["body"],
        message_id=result.get("message_id"),
        in_reply_to=None,
        error=None,
    )

    current_status = practice.get("status", "NEW")
    fields: dict = {}
    if _should_auto_advance(current_status, "CONTACTED"):
        fields["status"] = "CONTACTED"
    update_practice_fields(place_id, fields, touched_by=user["id"])
    add_tags(place_id, ["CONTACTED"])

    return row


@app.get("/api/practices/{place_id}/email/messages")
def list_email_messages_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")
    return {"messages": list_email_messages(practice["id"])}


@app.post("/api/practices/{place_id}/email/poll")
async def poll_email_replies_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    if not _email_configured():
        raise HTTPException(503, "Email not configured")

    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    email_addr = practice.get("email")
    if not email_addr:
        raise HTTPException(400, "Practice has no email address")

    outbound = list_outbound_message_ids(practice["id"])
    since = (
        datetime.now(timezone.utc)
        - timedelta(days=app_settings.email_reply_lookback_days)
    ).isoformat()

    replies = await poll_replies(
        practice_email=email_addr,
        outbound_message_ids=outbound,
        since_iso=since,
    )

    existing = list_email_messages(practice["id"])
    existing_ids = {m.get("message_id") for m in existing if m.get("message_id")}

    new_rows: list[dict] = []
    for reply in replies:
        if reply["message_id"] in existing_ids:
            continue
        inserted = insert_email_message(
            practice_id=practice["id"],
            user_id=None,
            direction="in",
            subject=reply.get("subject"),
            body=reply.get("body"),
            message_id=reply.get("message_id"),
            in_reply_to=reply.get("in_reply_to"),
            error=None,
        )
        if inserted:
            new_rows.append(inserted)

    if new_rows:
        current_status = practice.get("status", "NEW")
        fields: dict = {}
        if _should_auto_advance(current_status, "FOLLOW UP"):
            fields["status"] = "FOLLOW UP"
        update_practice_fields(place_id, fields, touched_by=user["id"])
        add_tags(place_id, ["REPLIED"])

    return {
        "new_messages": new_rows,
        "total": len(list_email_messages(practice["id"])),
    }


@app.post("/api/practices/{place_id}/email/mark-replied")
def mark_email_replied_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    row = insert_email_message(
        practice_id=practice["id"],
        user_id=None,
        direction="in",
        subject=None,
        body=f"[manually marked as replied by {user.get('name') or user['email']}]",
        message_id=None,
        in_reply_to=None,
        error=None,
    )

    current_status = practice.get("status", "NEW")
    fields: dict = {}
    if _should_auto_advance(current_status, "FOLLOW UP"):
        fields["status"] = "FOLLOW UP"
    update_practice_fields(place_id, fields, touched_by=user["id"])
    add_tags(place_id, ["REPLIED"])

    return row


def _strip_joined(row: dict) -> dict:
    """Drop keys the Practice model doesn't know about (joins + attribution flat names)."""
    allowed = set(Practice.model_fields.keys())
    return {k: v for k, v in row.items() if k in allowed}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    from src.auth import is_bootstrap_admin
    return {**user, "is_bootstrap_admin": is_bootstrap_admin(user)}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def _anon_supabase_client():
    """Anon (non-admin) Supabase client used to verify a user's current password."""
    from supabase import create_client
    return create_client(app_settings.supabase_url, app_settings.supabase_key)


@app.post("/api/me/password")
def change_my_password(
    body: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
):
    from src.validators import validate_password

    try:
        validate_password(body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    anon = _anon_supabase_client()
    try:
        anon.auth.sign_in_with_password({
            "email": user["email"],
            "password": body.current_password,
        })
    except Exception:
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    admin = get_admin_client()
    try:
        admin.auth.admin.update_user_by_id(user["id"], {"password": body.new_password})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update password: {e}")
    return {"ok": True}


@app.get("/api/practices")
def list_practices(
    city: str | None = Query(None),
    category: str | None = Query(None),
    min_rating: float | None = Query(None),
    # Bumped 5000 → 50000 so operators can explore a five-figure book of leads.
    limit: int = Query(10_000, ge=1, le=50_000),
    user: dict = Depends(get_current_user),
):
    rows = query_practices(city=city, category=category, min_rating=min_rating, limit=limit)
    rows = [_attach_lead_url(r) for r in rows]
    return {"practices": rows, "count": len(rows)}


# CSV column order — kept in sync with the export endpoint below.
_EXPORT_COLUMNS = [
    "place_id", "name", "address", "city", "state",
    "phone", "website", "rating", "review_count", "category",
    "icp_vertical", "icp_tier", "lead_score",
    "urgency_score", "hiring_signal_score",
    "status", "tags",
    "owner_name", "owner_title", "owner_email", "owner_phone", "owner_linkedin",
    "enrichment_status", "enriched_at",
    "website_doctor_name", "website_doctor_phone",
    "summary", "pain_points", "sales_angles",
    "last_touched_by_name", "last_touched_at",
    "salesforce_lead_id", "salesforce_lead_url",
    "call_count", "call_notes",
    "export_count",
]


@app.get("/api/practices/export.csv")
def export_practices_csv(
    max_exports: str | None = Query(
        None,
        description=(
            "Filter on existing export_count. Missing/empty exports every row. "
            "0 = only never-exported rows. N = export_count <= N."
        ),
    ),
    user: dict = Depends(get_current_user),
):
    """Stream a CSV of practices and increment export_count on every row included.

    UX: leave `max_exports` empty to export everything; pass `0` next time
    to avoid duplicate downloads (only never-exported rows).
    """
    import csv
    import io

    # Parse the optional filter. Empty string is treated as "no filter" so the
    # frontend doesn't have to omit the param entirely.
    cap: int | None = None
    if max_exports is not None and max_exports != "":
        try:
            cap = max(0, int(max_exports))
        except ValueError:
            raise HTTPException(status_code=400, detail="max_exports must be an integer")

    rows = query_for_export(cap)

    def _serialize(value) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        if isinstance(value, (dict,)):
            return json.dumps(value)
        return str(value)

    def iter_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_EXPORT_COLUMNS)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for row in rows:
            writer.writerow([_serialize(row.get(col)) for col in _EXPORT_COLUMNS])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    increment_export_counts([r["place_id"] for r in rows if r.get("place_id")])

    cap_label = "all" if cap is None else f"max{cap}"
    filename = f"hv-leads-{cap_label}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.csv"
    return StreamingResponse(
        iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class SearchRequest(BaseModel):
    query: str
    refresh: bool = False


@app.post("/api/practices/search")
async def search(body: SearchRequest, user: dict = Depends(get_current_user)):
    # Repeat-query fast path: serve from DB if we ran the same query in the
    # last 24h. `refresh=True` forces a fresh Google Places call.
    if not body.refresh:
        cached = get_cached_search(body.query)
        if cached:
            return {"practices": cached, "count": len(cached), "upserted": 0, "cached": True}

    practices = await search_places(body.query)
    relevant = [p for p in practices if "IRRELEVANT" not in p.tags]
    irrelevant = [p for p in practices if "IRRELEVANT" in p.tags]

    # Dedup: Google sometimes returns two place_ids for the same business.
    # When (normalized name + address + phone) already exists under a different
    # place_id in the DB, rewrite the incoming place_id to the existing one so
    # the upsert UPDATEs the canonical row instead of inserting a duplicate.
    dupe_map = find_duplicate_place_ids(relevant)
    if dupe_map:
        for p in relevant:
            canonical = dupe_map.get(p.place_id)
            if canonical:
                p.place_id = canonical

    upserted = upsert_practices(relevant, touched_by=user["id"])

    # Re-fetch relevant practices from DB so the response carries joined
    # attribution (last_touched_by_name). Irrelevant ones never enter the DB
    # — we return their in-memory dump so the UI can show + grey them out.
    enriched: list[dict] = []
    for p in relevant:
        row = get_practice(p.place_id)
        enriched.append(_attach_lead_url(row) if row else p.model_dump())
    for p in irrelevant:
        enriched.append(p.model_dump())

    save_search_cache(body.query, [p.place_id for p in relevant])

    return {
        "practices": enriched,
        "count": len(practices),
        "upserted": upserted,
    }


@app.get("/api/practices/{place_id}")
def get_single(place_id: str, user: dict = Depends(get_current_user)):
    row = get_practice(place_id)
    if not row:
        raise HTTPException(status_code=404, detail="Practice not found")
    return _attach_lead_url(row)


class AnalyzeRequest(BaseModel):
    force: bool = False
    rescan: bool = False


@app.post("/api/practices/{place_id}/analyze")
async def analyze(
    place_id: str,
    body: AnalyzeRequest | None = None,
    user: dict = Depends(get_current_user),
):
    force = body.force if body else False
    rescan = body.rescan if body else False

    existing = get_practice(place_id)
    if existing and existing.get("lead_score") is not None and not force and not rescan:
        return existing

    current_record = existing
    if existing and rescan:
        refreshed = await get_place(place_id, fallback=Practice(**_strip_joined(existing)))
        if refreshed:
            upsert_practices([refreshed], touched_by=user["id"])
            current_record = get_practice(place_id) or refreshed.model_dump()

    if current_record:
        name = current_record["name"]
        website = current_record.get("website")
        category = current_record.get("category")
        city = current_record.get("city")
        state = current_record.get("state")
        rating = current_record.get("rating")
        review_count = current_record.get("review_count") or 0
    else:
        name = place_id
        website = None
        category = None
        city = None
        state = None
        rating = None
        review_count = 0

    # Fingerprint the analyzer inputs. If the practice was already analyzed
    # against the same input fingerprint, return the cached result — the AI
    # is non-deterministic, so re-running on identical inputs just produces
    # score noise. Rescan that materially changes Google data shifts the
    # fingerprint and forces a fresh AI run.
    fingerprint = _analysis_input_fingerprint({
        "name": name,
        "website": website,
        "category": category,
        "city": city,
        "state": state,
    })
    # Backfill bypass: if the cached row has no website_contacts but the
    # practice has a website, force a fresh AI run so the contact list
    # gets populated. After backfill, subsequent re-analyzes are cached.
    needs_contacts_backfill = (
        current_record is not None
        and current_record.get("website")
        and not current_record.get("website_contacts")
    )
    if (
        current_record
        and current_record.get("lead_score") is not None
        and current_record.get("analysis_input_hash") == fingerprint
        and not needs_contacts_backfill
    ):
        return current_record

    analysis = await analyze_practice(
        place_id, name, website, category,
        city=city, state=state,
        rating=rating, review_count=review_count,
    )
    analysis["analysis_input_hash"] = fingerprint

    if current_record:
        current_status = current_record.get("status", "NEW")
        if _should_auto_advance(current_status, "RESEARCHED"):
            analysis["status"] = "RESEARCHED"

    updated = update_practice_analysis(place_id, analysis, touched_by=user["id"])
    add_tags(place_id, ["RESEARCHED"])
    if updated:
        return updated

    if current_record:
        return {**current_record, **analysis}
    return {"place_id": place_id, "name": name, **analysis}


@app.post("/api/practices/{place_id}/rescan")
async def rescan_practice(place_id: str, user: dict = Depends(get_current_user)):
    existing = get_practice(place_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Practice not found")

    refreshed = await get_place(place_id, fallback=Practice(**_strip_joined(existing)))
    if not refreshed:
        return existing

    upsert_practices([refreshed], touched_by=user["id"])
    return get_practice(place_id) or refreshed.model_dump()


@app.get("/api/practices/{place_id}/script")
async def get_script(place_id: str, user: dict = Depends(get_current_user)):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(status_code=404, detail="Practice not found")

    if practice.get("call_script"):
        return json.loads(practice["call_script"])

    script = await _build_personalized_script(practice)

    update_practice_fields(place_id, {"call_script": json.dumps(script)}, touched_by=user["id"])
    add_tags(place_id, ["SCRIPT_READY"])

    current_status = practice.get("status", "NEW")
    if _should_auto_advance(current_status, "SCRIPT READY"):
        update_practice_fields(place_id, {"status": "SCRIPT READY"}, touched_by=user["id"])

    return script


@app.post("/api/practices/{place_id}/script")
async def regenerate_script_endpoint(place_id: str, user: dict = Depends(get_current_user)):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(status_code=404, detail="Practice not found")

    script = await _build_personalized_script(practice)

    update_practice_fields(place_id, {"call_script": json.dumps(script)}, touched_by=user["id"])
    add_tags(place_id, ["SCRIPT_READY"])
    return script


async def _build_personalized_script(practice: dict) -> dict:
    """Build script generation context from a practice row, fetch fresh review
    excerpts, and return the generated playbook."""
    try:
        reviews = await fetch_reviews(
            practice["place_id"],
            name=practice.get("name"),
            city=practice.get("city"),
            state=practice.get("state"),
            website=practice.get("website"),
        )
    except Exception:
        reviews = []
    review_excerpts = sorted(
        [r["text"] for r in (reviews or []) if r.get("text")],
        key=len,
    )[:3]
    # website_contacts is stored as a JSON string (or jsonb) — accept either.
    raw_contacts = practice.get("website_contacts")
    if isinstance(raw_contacts, str):
        try:
            website_contacts = json.loads(raw_contacts) or []
        except json.JSONDecodeError:
            website_contacts = []
    elif isinstance(raw_contacts, list):
        website_contacts = raw_contacts
    else:
        website_contacts = []

    return await generate_script(
        name=practice["name"],
        category=practice.get("category"),
        summary=practice.get("summary"),
        pain_points=practice.get("pain_points"),
        sales_angles=practice.get("sales_angles"),
        city=practice.get("city"),
        state=practice.get("state"),
        rating=practice.get("rating"),
        review_count=practice.get("review_count"),
        website_doctor_name=practice.get("website_doctor_name"),
        owner_name=practice.get("owner_name"),
        owner_title=practice.get("owner_title"),
        review_excerpts=review_excerpts,
        website_contacts=website_contacts,
    )


class PatchPracticeRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
    email: str | None = None
    assigned_to: str | None = None


@app.patch("/api/practices/{place_id}")
async def patch_practice(
    place_id: str,
    body: PatchPracticeRequest,
    user: dict = Depends(get_current_user),
):
    fields: dict = {}
    if body.status is not None:
        if body.status not in STATUS_ORDER:
            raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
        fields["status"] = body.status
    if body.notes is not None:
        fields["notes"] = body.notes
    if body.email is not None:
        fields["email"] = body.email
    if body.assigned_to is not None:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin only: assignment changes")
        if body.assigned_to == "":
            fields["assigned_to"] = None
            fields["assigned_at"] = None
            fields["assigned_by"] = None
        else:
            fields["assigned_to"] = body.assigned_to
            fields["assigned_at"] = datetime.now(timezone.utc).isoformat()
            fields["assigned_by"] = user["id"]
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    log.info(
        "[api.patch_practice] place_id=%s user=%s fields=%s",
        place_id, user.get("email"), list(fields.keys()),
    )
    updated = update_practice_fields(place_id, fields, touched_by=user["id"])
    if not updated:
        raise HTTPException(status_code=404, detail="Practice not found")

    STATUS_TAG_MAP = {
        "MEETING SET": "MEETING_SET",
        "CLOSED WON": "CLOSED_WON",
        "CLOSED LOST": "CLOSED_LOST",
    }
    if body.status and body.status in STATUS_TAG_MAP:
        add_tags(place_id, [STATUS_TAG_MAP[body.status]])

    # If notes changed AND practice has a Salesforce Lead, push the notes
    # into the Lead's Call_Notes__c field (overwriting). Fail-soft: log
    # + return sf_warning, never block the local save.
    if body.notes is not None and updated.get("salesforce_lead_id"):
        from src import salesforce
        if salesforce.is_configured():
            try:
                await salesforce.update_lead(
                    updated["salesforce_lead_id"],
                    updated.get("call_count") or 0,
                    body.notes,
                )
                log.info(
                    "[api.patch_practice.sf_call_notes_synced] place_id=%s lead_id=%s",
                    place_id, updated["salesforce_lead_id"],
                )
            except Exception as e:
                log.exception(
                    "[api.patch_practice.sf_call_notes_failed] place_id=%s err=%r",
                    place_id, e,
                )
                return {**updated, "sf_warning": f"Salesforce notes sync failed: {e}"}

    return updated


# ======================= Call log + Salesforce sync =======================


class CallLogRequest(BaseModel):
    note: str = ""


@app.post("/api/practices/{place_id}/call/log")
async def call_log_endpoint(
    place_id: str,
    body: CallLogRequest,
    user: dict = Depends(get_current_user),
):
    log.info(
        "[api.call_log] place_id=%s user=%s note_len=%d",
        place_id, user.get("email"), len(body.note or ""),
    )
    try:
        practice, warning = await append_call_note(place_id, body.note, user)
    except LookupError:
        log.warning("[api.call_log.404] place_id=%s", place_id)
        raise HTTPException(404, "Practice not found")
    add_tags(place_id, ["CONTACTED"])
    log.info(
        "[api.call_log.response] place_id=%s call_count=%s lead_id=%s warning=%s",
        place_id, practice.get("call_count"),
        practice.get("salesforce_lead_id"), warning,
    )
    return {"practice": _attach_lead_url(practice), "sf_warning": warning}


@app.put("/api/practices/{place_id}/call/last-note")
async def update_last_call_note_endpoint(
    place_id: str,
    body: CallLogRequest,
    user: dict = Depends(get_current_user),
):
    """Update the most recent call_notes line's text + resync to SF.

    Called by the post-call notes modal: the call entry was already
    created when the rep clicked Call, so this replaces its note text
    rather than appending a new line.
    """
    log.info(
        "[api.update_last_note] place_id=%s user=%s note_len=%d",
        place_id, user.get("email"), len(body.note or ""),
    )
    try:
        practice, warning = await update_last_call_note(place_id, body.note, user)
    except LookupError:
        log.warning("[api.update_last_note.404] place_id=%s", place_id)
        raise HTTPException(404, "Practice not found")
    log.info(
        "[api.update_last_note.response] place_id=%s lead_id=%s warning=%s",
        place_id, practice.get("salesforce_lead_id"), warning,
    )
    return {"practice": _attach_lead_url(practice), "sf_warning": warning}


@app.get("/api/debug/env")
async def debug_env(user: dict = Depends(require_admin)):
    """Admin-only sanity check: which env vars does the deployed function see?

    Values are not returned — only whether each is set. Lets us verify
    Vercel env-var configuration without leaking secrets.
    """
    return {
        "supabase_url_set": bool(app_settings.supabase_url),
        "supabase_service_role_set": bool(app_settings.supabase_service_role_key),
        "openai_api_key_set": bool(app_settings.openai_api_key),
        "sf_apex_url_set": bool(app_settings.sf_apex_url),
        "sf_apex_url_host": (app_settings.sf_apex_url.split("/")[2]
                             if app_settings.sf_apex_url else None),
        "sf_api_key_set": bool(app_settings.sf_api_key),
        "sf_api_key_first6": (app_settings.sf_api_key[:6] + "..."
                              if app_settings.sf_api_key else None),
        "clay_inbound_secret_set": bool(app_settings.clay_inbound_secret),
        "google_maps_set": bool(app_settings.google_maps_api_key),
        "bootstrap_admin_email": app_settings.bootstrap_admin_email or None,
    }


# ======================= Clay owner enrichment =======================


@app.post("/api/practices/{place_id}/enrich")
async def enrich_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    existing = get_practice(place_id)
    if not existing:
        raise HTTPException(404, "Practice not found")

    from src.models import Practice as _P

    try:
        trigger_result = await trigger_enrichment(_P(**existing))
    except Exception as e:
        final = update_practice_fields(
            place_id, {"enrichment_status": "failed"}, touched_by=None
        )
        return {"practice": final, "clay_warning": f"Enrichment trigger failed: {e}"}

    if trigger_result.get("skipped"):
        return {"practice": existing, "clay_warning": "Clay not configured. Enrichment skipped."}

    updated = update_practice_fields(place_id, {"enrichment_status": "pending"}, touched_by=None)
    return {"practice": updated, "clay_warning": None}


class ClayWebhookPayload(BaseModel):
    place_id: str
    owner_name: str | None = None
    owner_email: str | None = None
    owner_phone: str | None = None
    owner_title: str | None = None
    owner_linkedin: str | None = None


@app.post("/api/webhooks/clay")
def clay_webhook(
    body: ClayWebhookPayload,
    x_clay_secret: str | None = Header(default=None, alias="X-Clay-Secret"),
):
    if not app_settings.clay_inbound_secret or x_clay_secret != app_settings.clay_inbound_secret:
        raise HTTPException(401, "Invalid secret")

    existing = get_practice(body.place_id)
    if not existing:
        raise HTTPException(404, "Practice not found")

    fields: dict = {}
    for key in ("owner_name", "owner_email", "owner_phone", "owner_title", "owner_linkedin"):
        value = getattr(body, key)
        if value is not None and value != "":
            fields[key] = value

    has_any_contact = any(k in fields for k in ("owner_name", "owner_email", "owner_phone"))
    fields["enrichment_status"] = "enriched" if has_any_contact else "failed"
    fields["enriched_at"] = datetime.now(timezone.utc).isoformat()

    update_practice_fields(body.place_id, fields, touched_by=None)
    if has_any_contact:
        add_tags(body.place_id, ["ENRICHED"])
    return {"ok": True}
