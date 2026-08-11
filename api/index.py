import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
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
from src.credits import (
    ANALYZE_RANGE_CREDITS,
    BULK_SCAN_RANGE_CREDITS,
    CALL_SCRIPT_RANGE_CREDITS,
    COST_MULTIPLIER,
    CREDIT_VALUE_CENTS,
    EMAIL_DRAFT_RANGE_CREDITS,
    ENRICHMENT_COST_CENTS,
    InsufficientCreditsError,
    PLACES_DETAILS_CREDITS,
    cost_cents_to_credits,
    get_balance,
    topup as credits_topup,
)
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
    query_practices_page,
    resolve_user_names,
    save_search_cache,
    update_practice_analysis,
    update_practice_fields,
    upsert_practices,
)

app = FastAPI(title="Apex Sales Intel", version="0.2.0")

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

    try:
        draft = await generate_email_draft(
            name=practice["name"],
            category=practice.get("category"),
            summary=practice.get("summary"),
            pain_points=practice.get("pain_points"),
            sales_angles=practice.get("sales_angles"),
            company_id=user.get("company_id"),
            user_id=user.get("id"),
        )
    except InsufficientCreditsError:
        raise HTTPException(
            status_code=402,
            detail={"error": "INSUFFICIENT_CREDITS", "action": "email_draft"},
        )
    update_practice_fields(
        place_id,
        {
            "email_draft": json.dumps(draft),
            "email_draft_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        touched_by=user["id"],
        company_id=user["company_id"],
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

    try:
        draft = await generate_email_draft(
            name=practice["name"],
            category=practice.get("category"),
            summary=practice.get("summary"),
            pain_points=practice.get("pain_points"),
            sales_angles=practice.get("sales_angles"),
            company_id=user.get("company_id"),
            user_id=user.get("id"),
        )
    except InsufficientCreditsError:
        raise HTTPException(
            status_code=402,
            detail={"error": "INSUFFICIENT_CREDITS", "action": "email_draft"},
        )
    update_practice_fields(
        place_id,
        {
            "email_draft": json.dumps(draft),
            "email_draft_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        touched_by=user["id"],
        company_id=user["company_id"],
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
        company_id=user["company_id"],
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
            company_id=user["company_id"],
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
        company_id=user["company_id"],
    )

    current_status = practice.get("status", "NEW")
    fields: dict = {}
    if _should_auto_advance(current_status, "CONTACTED"):
        fields["status"] = "CONTACTED"
    update_practice_fields(place_id, fields, touched_by=user["id"], company_id=user["company_id"])
    add_tags(place_id, ["CONTACTED"], company_id=user["company_id"])

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
            company_id=user["company_id"],
        )
        if inserted:
            new_rows.append(inserted)

    if new_rows:
        current_status = practice.get("status", "NEW")
        fields: dict = {}
        if _should_auto_advance(current_status, "FOLLOW UP"):
            fields["status"] = "FOLLOW UP"
        update_practice_fields(place_id, fields, touched_by=user["id"], company_id=user["company_id"])
        add_tags(place_id, ["REPLIED"], company_id=user["company_id"])

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
        company_id=user["company_id"],
    )

    current_status = practice.get("status", "NEW")
    fields: dict = {}
    if _should_auto_advance(current_status, "FOLLOW UP"):
        fields["status"] = "FOLLOW UP"
    update_practice_fields(place_id, fields, touched_by=user["id"], company_id=user["company_id"])
    add_tags(place_id, ["REPLIED"], company_id=user["company_id"])

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


# ---------------------------------------------------------------------------
# Multi-tenant: companies + sign-up + switcher
# ---------------------------------------------------------------------------

import re as _re
import secrets as _secrets


def _slugify_company(name: str) -> str:
    base = _re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (base[:40] or "company") + "-" + _secrets.token_hex(3)


class SignupRequest(BaseModel):
    email: str
    password: str
    company_name: str
    full_name: str | None = None


@app.post("/api/signup")
def signup(body: SignupRequest):
    """Create an auth user + a brand-new company + admin membership.

    Returns the new company_id + slug. The frontend then signs the user
    in (Supabase doesn't auto-sign admin-created users) and the
    onboarding wizard takes over.
    """
    from src.validators import validate_email, validate_password

    try:
        validate_email(body.email)
        validate_password(body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not (body.company_name or "").strip():
        raise HTTPException(status_code=400, detail="Company name is required.")

    admin = get_admin_client()

    # 1. Create the auth user.
    try:
        new_user = admin.auth.admin.create_user({
            "email": body.email,
            "password": body.password,
            "email_confirm": True,
            "user_metadata": {
                "name": (body.full_name or body.company_name).strip(),
            },
        })
    except Exception as e:
        msg = str(e).lower()
        if "already" in msg or "registered" in msg or "exists" in msg:
            raise HTTPException(status_code=400, detail="Email already in use.")
        raise HTTPException(status_code=400, detail=str(e))

    auth_user_id = new_user.user.id

    # 2. The DB trigger auto-creates the `profiles` row with role='sdr'.
    # Promote to admin so they can manage their own company.
    try:
        admin.table("profiles").update({"role": "admin"}).eq(
            "id", auth_user_id
        ).execute()
    except Exception:
        pass  # Trigger may not have run yet; not fatal.

    # 3. Create the company.
    slug = _slugify_company(body.company_name)
    try:
        company_row = admin.table("companies").insert({
            "slug": slug,
            "name": body.company_name.strip(),
            "branding": {
                "display_name": body.company_name.strip(),
                "short_name": body.company_name.strip(),
            },
            "created_by": auth_user_id,
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create company: {e}")
    company_id = company_row.data[0]["id"]

    # 4. Add the user as admin of the new company.
    try:
        admin.table("company_members").insert({
            "company_id": company_id,
            "user_id": auth_user_id,
            "role": "admin",
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create membership: {e}")

    return {
        "company_id": company_id,
        "company_slug": slug,
        "user_id": auth_user_id,
    }


def _has_icp_defined(icp_parsed) -> bool:
    """A company has a usable ICP iff icp_parsed has at least one
    vertical in scope. Brand-new tenants signed up via /signup land
    with icp_parsed = null and have_icp = false until the admin pastes
    + saves their ICP on /admin/icp."""
    if not isinstance(icp_parsed, dict):
        return False
    return bool(icp_parsed.get("verticals_in_scope"))


@app.get("/api/me/companies")
def list_my_companies(user: dict = Depends(get_current_user)):
    """List every company the current user is a member of."""
    client = get_admin_client()
    try:
        result = (
            client.table("company_members")
            .select(
                "role,company:companies(id,slug,name,branding,icp_parsed,archived_at)"
            )
            .eq("user_id", user["id"])
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    out = []
    for row in (result.data or []):
        company = row.get("company") or {}
        if not company or company.get("archived_at"):
            continue
        out.append({
            "id": company["id"],
            "slug": company["slug"],
            "name": company["name"],
            "branding": company.get("branding"),
            "role": row["role"],
            "is_current": company["id"] == user.get("company_id"),
            "has_icp": _has_icp_defined(company.get("icp_parsed")),
        })
    return {"companies": out, "current_company_id": user.get("company_id")}


# ---------------------------------------------------------------------------
# Credits — balance, transactions, top-ups.
# ---------------------------------------------------------------------------


@app.get("/api/me/credits")
def my_credits(user: dict = Depends(get_current_user)):
    """Current credit balance + recent transactions for the active
    company. Every authenticated user can read their own tenant's
    balance (so the topbar pill works for SDRs, not just admins).
    """
    company_id = user.get("company_id")
    if not company_id:
        return {"balance": 0, "purchased": 0, "consumed": 0, "transactions": []}

    client = get_admin_client()
    purchased = 0.0
    consumed = 0.0
    balance = 0.0
    try:
        row = (
            client.table("companies")
            .select("credit_balance,credits_purchased,credits_consumed")
            .eq("id", company_id)
            .limit(1)
            .execute()
        )
        if row.data:
            r0 = row.data[0]
            balance   = float(r0.get("credit_balance") or 0)
            purchased = float(r0.get("credits_purchased") or 0)
            consumed  = float(r0.get("credits_consumed") or 0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        tx = (
            client.table("credit_transactions")
            .select("id,kind,delta,balance_after,action,related_id,cost_cents,notes,created_at")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        transactions = tx.data or []
    except Exception:
        transactions = []

    return {
        "balance":         round(balance, 4),
        "purchased":       round(purchased, 4),
        "consumed":        round(consumed, 4),
        "credit_value_cents":  CREDIT_VALUE_CENTS,
        "cost_multiplier":     COST_MULTIPLIER,
        "rates": {
            "analyze":          list(ANALYZE_RANGE_CREDITS),
            "call_script":      list(CALL_SCRIPT_RANGE_CREDITS),
            "email_draft":      list(EMAIL_DRAFT_RANGE_CREDITS),
            "bulk_scan_query":  list(BULK_SCAN_RANGE_CREDITS),
            "places_details":   PLACES_DETAILS_CREDITS,
            "enrichment":       cost_cents_to_credits(ENRICHMENT_COST_CENTS),
        },
        "transactions":    transactions,
    }


class CreditTopupRequest(BaseModel):
    amount: float
    notes: str | None = None
    source: str | None = None


@app.post("/api/admin/credits/topup")
def admin_credits_topup(
    body: CreditTopupRequest,
    admin: dict = Depends(require_admin),
):
    """Admin grants credits to the active company. Mock-only for now —
    a real billing integration (Stripe) would call add_credits from a
    webhook handler instead.
    """
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    if body.amount > 1_000_000:
        raise HTTPException(status_code=400, detail="amount too large")
    new_balance = credits_topup(
        company_id=admin["company_id"],
        amount=body.amount,
        user_id=admin.get("id"),
        source=body.source or "admin_topup",
        notes=body.notes,
    )
    if new_balance is None:
        raise HTTPException(status_code=500, detail="Top-up failed")
    return {"balance": round(new_balance, 4)}


# ---------------------------------------------------------------------------
# Usage + cost stats — admin-only.
# ---------------------------------------------------------------------------


@app.post("/api/admin/usage/recompute-costs")
def admin_usage_recompute(admin: dict = Depends(require_admin)):
    """Recalculate `cost_cents` on every `usage_events` row for the
    active company using the CURRENT pricing constants in src/usage.py.

    Idempotent. Run it after a pricing edit (e.g. correcting gpt-4o
    from 250/1000 → 500/1500) so historical events catch up to the
    new bands.
    """
    from src.usage import estimate_openai_cost, estimate_places_cost

    client = get_admin_client()
    page_size = 1000
    page = 0
    updated = 0
    scanned = 0
    while True:
        start = page * page_size
        end = start + page_size - 1
        try:
            batch = (
                client.table("usage_events")
                .select("id,kind,model,input_tokens,output_tokens,cached_input_tokens,calls,cost_cents")
                .eq("company_id", admin["company_id"])
                .order("id")
                .range(start, end)
                .execute()
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        rows = batch.data or []
        if not rows:
            break
        for r in rows:
            scanned += 1
            kind = r.get("kind") or ""
            if kind.startswith("openai_"):
                new_cost = estimate_openai_cost(
                    r.get("model"),
                    int(r.get("input_tokens") or 0),
                    int(r.get("output_tokens") or 0),
                    int(r.get("cached_input_tokens") or 0),
                )
            elif kind.startswith("places_"):
                new_cost = estimate_places_cost(kind, int(r.get("calls") or 1))
            else:
                continue
            old_cost = float(r.get("cost_cents") or 0.0)
            if abs(old_cost - new_cost) < 0.0001:
                continue  # no change → skip the write
            try:
                client.table("usage_events").update(
                    {"cost_cents": new_cost}
                ).eq("id", r["id"]).execute()
                updated += 1
            except Exception:
                continue
        if len(rows) < page_size:
            break
        page += 1

    return {"scanned": scanned, "updated": updated}


@app.get("/api/admin/usage")
def admin_usage(
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(require_admin),
):
    """Aggregate Places + OpenAI usage for the active company.

    Returns:
      {
        "window_days": 30,
        "by_kind":  [{kind, count_events, total_calls, input_tokens, output_tokens, cost_cents}],
        "by_model": [{model, count_events, input_tokens, output_tokens, cost_cents}],
        "totals":   {events, places_calls, openai_calls, input_tokens, output_tokens, cost_cents,
                     places_cost_cents, openai_cost_cents},
        "recent":   [{created_at, kind, model, input_tokens, output_tokens, calls, cost_cents, metadata}],
        "pricing":  {openai_per_million_tokens, places_per_call}
      }
    """
    from datetime import datetime, timedelta, timezone
    from src.usage import OPENAI_COST_PER_MILLION_TOKENS, PLACES_COST_CENTS

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    client = get_admin_client()

    rows: list[dict]
    try:
        result = (
            client.table("usage_events").select("*")
            .eq("company_id", admin["company_id"])
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        log.warning("[usage.fetch.error] %s", e)
        rows = []

    by_kind: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    totals = {
        "events": 0,
        "places_calls": 0,
        "openai_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_cents": 0.0,
        "places_cost_cents": 0.0,
        "openai_cost_cents": 0.0,
    }
    for r in rows:
        kind = r.get("kind") or "other"
        model = r.get("model") or "—"
        in_tok = int(r.get("input_tokens") or 0)
        out_tok = int(r.get("output_tokens") or 0)
        calls = int(r.get("calls") or 1)
        cost = float(r.get("cost_cents") or 0.0)

        kb = by_kind.setdefault(kind, {
            "kind": kind, "count_events": 0, "total_calls": 0,
            "input_tokens": 0, "output_tokens": 0, "cost_cents": 0.0,
        })
        kb["count_events"] += 1
        kb["total_calls"] += calls
        kb["input_tokens"] += in_tok
        kb["output_tokens"] += out_tok
        kb["cost_cents"] += cost

        if kind.startswith("openai_") and r.get("model"):
            mb = by_model.setdefault(model, {
                "model": model, "count_events": 0,
                "input_tokens": 0, "output_tokens": 0, "cost_cents": 0.0,
            })
            mb["count_events"] += 1
            mb["input_tokens"] += in_tok
            mb["output_tokens"] += out_tok
            mb["cost_cents"] += cost

        totals["events"] += 1
        totals["input_tokens"] += in_tok
        totals["output_tokens"] += out_tok
        totals["cost_cents"] += cost
        if kind.startswith("places_"):
            totals["places_calls"] += calls
            totals["places_cost_cents"] += cost
        elif kind.startswith("openai_"):
            totals["openai_calls"] += calls
            totals["openai_cost_cents"] += cost

    # Round to keep the JSON tidy for the UI.
    def _round_costs(d: dict) -> None:
        for k in ("cost_cents", "places_cost_cents", "openai_cost_cents"):
            if k in d:
                d[k] = round(float(d[k]), 4)
    _round_costs(totals)
    for d in by_kind.values():
        _round_costs(d)
    for d in by_model.values():
        _round_costs(d)

    recent = [{
        "created_at": r.get("created_at"),
        "kind": r.get("kind"),
        "model": r.get("model"),
        "input_tokens": r.get("input_tokens"),
        "output_tokens": r.get("output_tokens"),
        "calls": r.get("calls") or 1,
        "cost_cents": round(float(r.get("cost_cents") or 0), 4),
        "metadata": r.get("metadata"),
    } for r in rows[:50]]

    return {
        "window_days": days,
        "by_kind":  sorted(by_kind.values(), key=lambda x: x["cost_cents"], reverse=True),
        "by_model": sorted(by_model.values(), key=lambda x: x["cost_cents"], reverse=True),
        "totals":   totals,
        "recent":   recent,
        "pricing":  {
            "openai_per_million_tokens": OPENAI_COST_PER_MILLION_TOKENS,
            "places_per_call":           PLACES_COST_CENTS,
        },
    }


# ---------------------------------------------------------------------------
# ICP definition — admin uploads a free-text ICP doc, GPT parses it into a
# structured schema, admin reviews + saves to companies.icp_parsed.
# ---------------------------------------------------------------------------


@app.get("/api/companies/me")
def get_my_company(user: dict = Depends(get_current_user)):
    """Return the active company row including its parsed ICP, if any."""
    client = get_admin_client()
    try:
        result = (
            client.table("companies")
            .select("id,slug,name,branding,icp_doc_text,icp_parsed,scoring_config,created_at")
            .eq("id", user["company_id"])
            .maybe_single().execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Company not found")
    return result.data


class ParseICPRequest(BaseModel):
    raw_text: str


@app.post("/api/companies/me/icp/parse")
async def parse_my_icp(
    body: ParseICPRequest,
    admin: dict = Depends(require_admin),
):
    """Send the pasted ICP doc to GPT and return the structured JSON
    (NOT saved — admin reviews + edits before calling PUT below)."""
    from src.icp_parser import parse_icp_doc

    try:
        parsed = await parse_icp_doc(
            body.raw_text,
            company_id=admin.get("company_id"),
            user_id=admin.get("id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"icp_parsed": parsed}


class SaveICPRequest(BaseModel):
    icp_parsed: dict
    icp_doc_text: str | None = None


@app.put("/api/companies/me/icp")
def save_my_icp(
    body: SaveICPRequest,
    admin: dict = Depends(require_admin),
):
    """Persist the (possibly edited) parsed ICP onto the active company.
    Also stores the raw doc text for audit / re-parse later."""
    from src.icp_parser import _validate

    payload: dict = {"icp_parsed": _validate(body.icp_parsed)}
    if body.icp_doc_text is not None:
        payload["icp_doc_text"] = body.icp_doc_text

    client = get_admin_client()
    try:
        result = (
            client.table("companies").update(payload)
            .eq("id", admin["company_id"]).execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not result.data:
        raise HTTPException(status_code=404, detail="Company not found")
    return result.data[0]


@app.post("/api/me/companies/{company_id}/switch")
def switch_company(
    company_id: str,
    response: Response,
    user: dict = Depends(get_current_user),
):
    """Set the apex_current_company cookie to the requested company,
    after verifying the user is a member. Subsequent requests resolve
    via get_current_user to the new company."""
    client = get_admin_client()
    try:
        member = (
            client.table("company_members").select("role")
            .eq("user_id", user["id"])
            .eq("company_id", company_id)
            .maybe_single().execute()
        )
    except Exception:
        member = None
    if not member or not member.data:
        raise HTTPException(status_code=403, detail="Not a member of that company.")

    # 30-day cookie, readable by JS so the frontend can show the active
    # company without an extra round-trip.
    response.set_cookie(
        key="apex_current_company",
        value=company_id,
        max_age=60 * 60 * 24 * 30,
        httponly=False,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "company_id": company_id, "role": member.data["role"]}


@app.get("/api/practices")
def list_practices(
    search: str | None = Query(None),
    category: str | None = Query(None),
    vertical: str | None = Query(None),
    geo: str | None = Query(None),              # "US" | "UK" | "<state code>"
    tier: str | None = Query(None),             # A | B | C | D
    status: str | None = Query(None),           # pipeline status
    min_rating: float | None = Query(None),
    min_score: int | None = Query(None),        # lead_score lower bound
    max_score: int | None = Query(None),        # lead_score upper bound
    enriched: str | None = Query(None),         # "yes" | "no"
    owner: str | None = Query(None),            # profiles.id
    tags: str | None = Query(None),             # comma-separated
    sort: str = Query("lead_score"),
    dir: str = Query("desc"),                   # "asc" | "desc"
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_current_user),
):
    tag_list = [t for t in (tags.split(",") if tags else []) if t]
    rows, total = query_practices_page(
        search=search, category=category, vertical=vertical, geo=geo,
        tier=tier, status=status, min_rating=min_rating,
        min_score=min_score, max_score=max_score, enriched=enriched,
        owner=owner, tags=tag_list, sort=sort, direction=dir,
        offset=offset, limit=limit,
    )
    rows = [_attach_lead_url(r) for r in rows]
    return {
        "practices": rows,
        "count": len(rows),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
    }


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
    "export_count", "last_exported_at", "last_exported_by_name",
]


class BulkExportRequest(BaseModel):
    place_ids: list[str]
    max_exports: int | None = None


@app.post("/api/practices/export.csv")
def export_practices_csv_by_ids(
    body: BulkExportRequest,
    user: dict = Depends(get_current_user),
):
    """Stream a CSV containing only the rows whose `place_id` is in the
    posted list. Used by the Bulk Scan modal's "Export these results to
    CSV" action — the modal accumulates the place_ids returned by every
    query in the run and posts them here so the rep gets a CSV of *just
    this scan*, not the whole DB.

    `max_exports` still works the same way as the GET endpoint — pass
    `0` to skip rows that were already exported."""
    import csv
    import io
    from src.storage import _get_client, _flatten_attribution

    place_ids = [p for p in (body.place_ids or []) if p]
    if not place_ids:
        raise HTTPException(status_code=400, detail="place_ids is empty")

    client = _get_client()
    rows: list[dict] = []
    if client:
        # PostgREST .in_() doesn't paginate, but it does enforce a max
        # URL length; chunk to be safe at thousands of ids.
        from src.storage import PROFILE_JOIN_SELECT
        CHUNK = 500
        for i in range(0, len(place_ids), CHUNK):
            chunk = place_ids[i:i + CHUNK]
            try:
                q = client.table("practices").select(PROFILE_JOIN_SELECT).in_("place_id", chunk)
                if body.max_exports is not None:
                    q = q.lte("export_count", body.max_exports)
                result = q.execute()
            except Exception:
                continue
            for r in (result.data or []):
                rows.append(_flatten_attribution(r))

    # Resolve last_exported_by display names for the CSV.
    exporter_ids = [r.get("last_exported_by") for r in rows if r.get("last_exported_by")]
    name_by_id = resolve_user_names(exporter_ids) if exporter_ids else {}
    for r in rows:
        eid = r.get("last_exported_by")
        r["last_exported_by_name"] = name_by_id.get(eid, "") if eid else ""

    def _serialize(value) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            return json.dumps(value)
        return str(value)

    def iter_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_EXPORT_COLUMNS)
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
        for row in rows:
            writer.writerow([_serialize(row.get(col)) for col in _EXPORT_COLUMNS])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    increment_export_counts(
        [r["place_id"] for r in rows if r.get("place_id")],
        user_id=user.get("id"),
        company_id=user.get("company_id"),
    )

    filename = f"apex-leads-bulk-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.csv"
    return StreamingResponse(
        iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    # Resolve last_exported_by UUIDs to display names so the CSV has a
    # readable "last exported by" column instead of an opaque UUID.
    exporter_ids = [r.get("last_exported_by") for r in rows if r.get("last_exported_by")]
    name_by_id = resolve_user_names(exporter_ids) if exporter_ids else {}
    for r in rows:
        eid = r.get("last_exported_by")
        r["last_exported_by_name"] = name_by_id.get(eid, "") if eid else ""

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

    increment_export_counts(
        [r["place_id"] for r in rows if r.get("place_id")],
        user_id=user.get("id"),
        company_id=user.get("company_id"),
    )

    cap_label = "all" if cap is None else f"max{cap}"
    filename = f"apex-leads-{cap_label}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.csv"
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

    # Pre-flight credit check — Bulk Scan is dynamic (1-3 Places pages
    # per query, ~0.97-2.91 credits each). Gate on the LOW end of the
    # range so a typical balance doesn't get blocked for a 1-page result.
    # Cached hits above are free (no Places call → no deduction).
    if user.get("company_id"):
        bal = get_balance(user["company_id"])
        needed = BULK_SCAN_RANGE_CREDITS[0]
        if bal < needed:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "INSUFFICIENT_CREDITS",
                    "balance": bal,
                    "needed": needed,
                    "action": "bulk_scan_query",
                },
            )

    try:
        practices = await search_places(
            body.query,
            company_id=user.get("company_id"),
            user_id=user.get("id"),
        )
    except InsufficientCreditsError as e:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "INSUFFICIENT_CREDITS",
                "action": "bulk_scan_query",
                "message": str(e),
            },
        )
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

    upserted = upsert_practices(relevant, touched_by=user["id"], company_id=user["company_id"])

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


def _practice_exported(company_id: str, practice_id: int | None) -> bool:
    """True when the practice's newest linked posting has already been exported
    to Talent-DB for this company.

    Mirrors the dedup guard in POST /api/practices/{place_id}/import-lead so the
    practice-detail Import Lead button can render 'Exported' after a reload
    instead of reverting to its default state. A practice with no linked posting
    is never deduped (always sendable), so it reports False.
    """
    from src import lead_store

    if not practice_id:
        return False
    posting = lead_store.newest_posting_for_practice(practice_id)
    if not posting:
        return False
    lead_row = lead_store.find_lead_by_posting(company_id, posting["id"])
    return bool(lead_row and lead_row.get("talentdb_exported_at"))


@app.get("/api/practices/{place_id}")
def get_single(place_id: str, user: dict = Depends(get_current_user)):
    row = get_practice(place_id)
    if not row:
        raise HTTPException(status_code=404, detail="Practice not found")
    result = _attach_lead_url(row)
    result["exported"] = _practice_exported(user["company_id"], row.get("id"))
    return result


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
        refreshed = await get_place(
            place_id,
            fallback=Practice(**_strip_joined(existing)),
            company_id=user.get("company_id"),
            user_id=user.get("id"),
        )
        if refreshed:
            upsert_practices([refreshed], touched_by=user["id"], company_id=user["company_id"])
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

    # Pre-flight credit check — fail fast before burning OpenAI tokens
    # we won't bill for. Cost is dynamic; we gate on the low end of the
    # observed range so the typical analyze isn't blocked at exactly
    # the right balance.
    if user.get("company_id"):
        balance = get_balance(user["company_id"])
        if balance < ANALYZE_RANGE_CREDITS[0]:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "INSUFFICIENT_CREDITS",
                    "balance": balance,
                    "needed": ANALYZE_RANGE_CREDITS[0],
                    "action": "analyze",
                },
            )

    try:
        analysis = await analyze_practice(
            place_id, name, website, category,
            city=city, state=state,
            rating=rating, review_count=review_count,
            company_id=user.get("company_id"),
            user_id=user.get("id"),
        )
    except InsufficientCreditsError as e:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "INSUFFICIENT_CREDITS",
                "action": "analyze",
                "message": str(e),
            },
        )
    analysis["analysis_input_hash"] = fingerprint

    if current_record:
        current_status = current_record.get("status", "NEW")
        if _should_auto_advance(current_status, "RESEARCHED"):
            analysis["status"] = "RESEARCHED"

    updated = update_practice_analysis(place_id, analysis, touched_by=user["id"], company_id=user["company_id"])
    add_tags(place_id, ["RESEARCHED"], company_id=user["company_id"])
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

    refreshed = await get_place(
            place_id,
            fallback=Practice(**_strip_joined(existing)),
            company_id=user.get("company_id"),
            user_id=user.get("id"),
        )
    if not refreshed:
        return existing

    upsert_practices([refreshed], touched_by=user["id"], company_id=user["company_id"])
    return get_practice(place_id) or refreshed.model_dump()


@app.get("/api/practices/{place_id}/script")
async def get_script(place_id: str, user: dict = Depends(get_current_user)):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(status_code=404, detail="Practice not found")

    if practice.get("call_script"):
        return json.loads(practice["call_script"])

    try:
        script = await _build_personalized_script(practice, user)
    except InsufficientCreditsError:
        raise HTTPException(
            status_code=402,
            detail={"error": "INSUFFICIENT_CREDITS", "action": "call_script"},
        )

    update_practice_fields(place_id, {"call_script": json.dumps(script)}, touched_by=user["id"], company_id=user["company_id"])
    add_tags(place_id, ["SCRIPT_READY"], company_id=user["company_id"])

    current_status = practice.get("status", "NEW")
    if _should_auto_advance(current_status, "SCRIPT READY"):
        update_practice_fields(place_id, {"status": "SCRIPT READY"}, touched_by=user["id"], company_id=user["company_id"])

    return script


@app.post("/api/practices/{place_id}/script")
async def regenerate_script_endpoint(place_id: str, user: dict = Depends(get_current_user)):
    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(status_code=404, detail="Practice not found")

    try:
        script = await _build_personalized_script(practice, user)
    except InsufficientCreditsError:
        raise HTTPException(
            status_code=402,
            detail={"error": "INSUFFICIENT_CREDITS", "action": "call_script"},
        )

    update_practice_fields(place_id, {"call_script": json.dumps(script)}, touched_by=user["id"], company_id=user["company_id"])
    add_tags(place_id, ["SCRIPT_READY"], company_id=user["company_id"])
    return script


async def _build_personalized_script(practice: dict, user: dict) -> dict:
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
        company_id=user.get("company_id"),
        user_id=user.get("id"),
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
    updated = update_practice_fields(place_id, fields, touched_by=user["id"], company_id=user["company_id"])
    if not updated:
        raise HTTPException(status_code=404, detail="Practice not found")

    STATUS_TAG_MAP = {
        "MEETING SET": "MEETING_SET",
        "CLOSED WON": "CLOSED_WON",
        "CLOSED LOST": "CLOSED_LOST",
    }
    if body.status and body.status in STATUS_TAG_MAP:
        add_tags(place_id, [STATUS_TAG_MAP[body.status]], company_id=user["company_id"])

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
    add_tags(place_id, ["CONTACTED"], company_id=user["company_id"])
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


def _talentdb_response(result: dict) -> dict:
    """Shape a talentdb.import_lead result into the endpoint response.

    A non-ok result becomes a non-blocking `talentdb_warning` string rather
    than an error — the Import Lead button is fail-soft.
    """
    warning = None
    if not result.get("ok"):
        msg = result.get("message") or result.get("status") or "unknown error"
        warning = f"Talent-DB import failed: {msg}"
    return {
        "talentdb_status": result.get("status"),
        "talentdb_warning": warning,
        "local_entity_id": result.get("local_entity_id"),
    }


@app.post("/api/practices/{place_id}/import-lead")
async def import_lead_practice_endpoint(
    place_id: str,
    user: dict = Depends(get_current_user),
):
    """Push a practice (+ its newest linked job posting) to Talent-DB as a Lead.

    One-way and fire-and-forget. Deduped per (company, posting): a posting
    already exported is not re-sent. Practices with no linked posting are always
    sendable (there is nothing to dedup on).
    """
    from src import lead_store, talentdb

    practice = get_practice(place_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    company_id = user["company_id"]
    posting = None
    lead_row = None
    pid = practice.get("id")
    if pid:
        posting = lead_store.newest_posting_for_practice(pid)
    if posting:
        lead_row = lead_store.find_lead_by_posting(company_id, posting["id"])
        if lead_row and lead_row.get("talentdb_exported_at"):
            log.info("[api.import_lead.skip] place_id=%s already_exported", place_id)
            return {"talentdb_status": "already_exported", "talentdb_warning": None,
                    "local_entity_id": None}

    log.info("[api.import_lead] place_id=%s user=%s posting=%s",
             place_id, user.get("email"), posting["id"] if posting else None)
    result = await talentdb.import_lead(practice, posting)
    if result.get("ok") and lead_row:
        lead_store.mark_lead_exported(company_id, lead_row["id"])
    log.info("[api.import_lead.response] place_id=%s ok=%s status=%s",
             place_id, result.get("ok"), result.get("status"))
    return _talentdb_response(result)


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
            place_id, {"enrichment_status": "failed"},
            touched_by=None, company_id=user["company_id"],
        )
        return {"practice": final, "clay_warning": f"Enrichment trigger failed: {e}"}

    if trigger_result.get("skipped"):
        return {"practice": existing, "clay_warning": "Clay not configured. Enrichment skipped."}

    updated = update_practice_fields(
        place_id, {"enrichment_status": "pending"},
        touched_by=None, company_id=user["company_id"],
    )
    return {"practice": updated, "clay_warning": None}


class ClayWebhookPayload(BaseModel):
    place_id: str
    owner_name: str | None = None
    owner_email: str | None = None
    owner_phone: str | None = None
    owner_title: str | None = None
    owner_linkedin: str | None = None
    # Headcount from Clay. Accepted as int or str (Clay returns messy values like
    # "1,200" or "50-100"); coerced to an int below so a bad value never rejects
    # the whole enrichment callback.
    organization_size: int | str | None = None


def _coerce_org_size(value) -> int | None:
    """Best-effort parse Clay's org-size into a positive int, else None.

    Takes the first run of digits (commas stripped), so "1,200" -> 1200 and
    "50-100 employees" -> 50. Anything without digits -> None (skipped)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if n > 0 else None
    if isinstance(value, str):
        digits = ""
        for ch in value.replace(",", "").strip():
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        return int(digits) if digits else None
    return None


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

    org_size = _coerce_org_size(body.organization_size)
    if org_size is not None:
        fields["organization_size"] = org_size

    has_any_contact = any(k in fields for k in ("owner_name", "owner_email", "owner_phone"))
    fields["enrichment_status"] = "enriched" if has_any_contact else "failed"
    fields["enriched_at"] = datetime.now(timezone.utc).isoformat()

    update_practice_fields(body.place_id, fields, touched_by=None)
    if has_any_contact:
        add_tags(body.place_id, ["ENRICHED"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Job-posting leads — background stages.
#
# Two idempotent, bounded, cron-triggered stages: collect -> qualify (ADR-09).
# The API deploys as serverless functions behind
# `rewrites: /api/(.*) -> /api/index.py`, and a full sweep is minutes of wall
# clock, so neither stage tries to finish the job. Each drains a bounded slice
# and may not assume the previous one completed — which also makes retries free.
#
# See docs/specs/2026-08-05-hiring-signal-collector-{adr,design}.md.
# ---------------------------------------------------------------------------

import time as _time

from src import lead_config, lead_store, lead_targets
from src.credits import InsufficientCreditsError as _InsufficientCredits


def _require_cron_secret(
    secret: str | None,
    authorization: str | None = None,
) -> None:
    """Authenticate a cron stage against the shared secret.

    Two headers are accepted for one reason: `X-Cron-Secret` matches the
    existing Clay webhook pattern and is what a manual `curl` or an external
    scheduler would send, while Vercel's own cron sends
    `Authorization: Bearer $CRON_SECRET` and cannot be configured to send
    anything else.

    An unset secret disables the routes rather than leaving them open — a
    background stage that spends model credits is not something to leave
    unauthenticated by omission.
    """
    expected = app_settings.lead_cron_secret
    if not expected:
        raise HTTPException(503, "Lead cron is not configured")
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[len("bearer "):].strip()
    if secret != expected and bearer != expected:
        raise HTTPException(401, "Invalid secret")


# GET is registered alongside POST on both stages because Vercel's scheduler
# only ever issues a GET. The stages are idempotent and claim a bounded slice,
# so neither is a meaningful mutation of anything a GET shouldn't touch.
@app.post("/api/cron/leads/collect")
@app.get("/api/cron/leads/collect")
def cron_collect_leads(
    company_id: str | None = Query(None, description="Scope to one tenant"),
    limit: int | None = Query(None, ge=1, le=200),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    authorization: str | None = Header(default=None),
):
    """Claim the least-recently-run targets, search the boards, upsert postings.

    Boards rotate on wall clock rather than a stored cursor: consecutive
    firings advance without any state to lose, and a crashed run costs at most
    one slice of freshness.
    """
    _require_cron_secret(x_cron_secret, authorization)
    from src.job_boards import search_jobs

    batch = limit or app_settings.lead_collect_batch
    # 30-minute buckets, so the LinkedIn cycle advances at a human-legible rate.
    run_index = int(_time.time() // 1800)
    sources = lead_targets.sources_for_run(run_index)

    try:
        tenant = company_id or lead_targets.resolve_company_id()
    except lead_targets.NoLeadCompany as e:
        raise HTTPException(503, f"No lead tenant resolved: {e}")

    # Seeds on the very first run so collection works without a separate admin
    # step. A no-op once the tenant has targets.
    seeded = lead_targets.ensure_targets(tenant)

    summary = {
        "run_index": run_index, "sources": sources,
        "company_id": tenant, "seeded": seeded["inserted"], "targets": 0,
        "rows": 0, "written": 0, "zero_row_targets": 0, "errors": [],
    }

    for target in lead_targets.claim_targets(tenant, batch):
        summary["targets"] += 1
        try:
            rows, stats = search_jobs(
                target["term"], target["location"],
                sources=sources, target=target,
            )
        except Exception as e:
            summary["errors"].append(f"{target['term']}@{target['location']}: {e}")
            continue
        for source, stat in stats.items():
            if stat.get("error"):
                summary["errors"].append(f"{source}: {stat['error']}")
        summary["rows"] += len(rows)
        if rows:
            summary["written"] += lead_store.upsert_postings(rows)
        else:
            summary["zero_row_targets"] += 1
        lead_targets.record_target_result(target["id"], len(rows))

    # The Indeed failure mode is silence, not an error (ADR-02): the library
    # reaches an undocumented mobile API whose key can rotate upstream, after
    # which every query returns zero rows and nothing raises. Log it loudly —
    # this line is what an alert should watch.
    if summary["targets"] and summary["zero_row_targets"] == summary["targets"]:
        summary["alert"] = (
            "Every target returned zero rows. Check the python-jobspy pin — "
            "this is the Indeed API-key rotation failure mode."
        )
        log.error("[leads.collect.zero_rows] targets=%d sources=%s",
                  summary["targets"], sources)
    else:
        log.info("[leads.collect] targets=%d rows=%d written=%d zero=%d",
                 summary["targets"], summary["rows"], summary["written"],
                 summary["zero_row_targets"])
    return summary


@app.post("/api/cron/leads/qualify")
@app.get("/api/cron/leads/qualify")
def cron_qualify_leads(
    company_id: str | None = Query(None, description="Scope to one tenant"),
    limit: int | None = Query(None, ge=1, le=500),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    authorization: str | None = Header(default=None),
):
    """Claim unqualified postings per tenant, batch them to the model, write
    the verdict half of each lead row.

    `unique (company_id, posting_id)` enforces at the database that a posting
    is never re-qualified — and so never re-billed — for the same tenant, so
    re-running this stage after a partial failure is free.
    """
    _require_cron_secret(x_cron_secret, authorization)
    from src import lead_qualifier

    batch = limit or app_settings.lead_qualify_batch
    try:
        tenant = company_id or lead_targets.resolve_company_id()
    except lead_targets.NoLeadCompany as e:
        raise HTTPException(503, f"No lead tenant resolved: {e}")

    summary = {
        "company_id": tenant, "claimed": 0, "verdicts": 0,
        "keeps": 0, "missing": 0, "linked": 0, "link_review": 0, "errors": [],
    }

    postings = lead_store.claim_unqualified(tenant, batch)
    summary["claimed"] = len(postings)
    for chunk in lead_qualifier.batched(postings):
        try:
            verdicts, stats = lead_qualifier.qualify_batch(chunk, company_id=tenant)
        except _InsufficientCredits:
            # Stop the run rather than burn the remaining batches against a
            # balance that can't pay. The postings stay unqualified and are
            # re-claimed after a top-up.
            summary["errors"].append("insufficient credits")
            break
        except Exception as e:
            summary["errors"].append(f"{type(e).__name__}: {str(e)[:160]}")
            continue
        summary["verdicts"] += lead_store.write_verdicts(tenant, verdicts)
        summary["keeps"] += stats.get("keeps", 0)
        summary["missing"] += stats.get("missing", 0)

    log.info("[leads.qualify] claimed=%d verdicts=%d keeps=%d missing=%d",
             summary["claimed"], summary["verdicts"], summary["keeps"],
             summary["missing"])

    # Link the fresh keepers to their practice while the batch is hot. The
    # matcher scopes to the postings we just claimed and to their cities, so
    # this is a small incremental pass, not the full 20k-row universe. A match
    # failure must never fail the qualify run — the verdicts are already
    # written and billed, and the next run re-matches for free.
    if summary["keeps"]:
        try:
            from src import practice_matcher
            m = practice_matcher.link_postings(
                tenant, posting_ids=[p["id"] for p in postings],
            )
            summary["linked"] = m["linked"]
            summary["link_review"] = m["review"]
            log.info("[leads.qualify.match] linked=%d review=%d cleared=%d",
                     m["linked"], m["review"], m["cleared"])
        except Exception as e:
            summary["errors"].append(f"match: {type(e).__name__}: {str(e)[:120]}")

    return summary


@app.post("/api/admin/leads/seed-targets")
def seed_lead_targets(admin: dict = Depends(require_admin)):
    """Re-seed this tenant's search targets after a config change.

    Existing rows are left alone: `enabled` and `last_run_at` belong to the
    tenant, so adding a term must not reset the rotation or re-enable a city
    an operator switched off.
    """
    try:
        config_summary = lead_config.validate()
    except lead_config.LeadConfigError as e:
        raise HTTPException(400, f"Lead config is invalid: {e}")
    seeded = lead_targets.seed_search_targets(admin["company_id"])
    return {"config": config_summary, "seeded": seeded}


@app.post("/api/admin/leads/retrigger")
async def retrigger_lead_pipeline(admin: dict = Depends(require_admin)):
    """Kick off the full collect + qualify sweep on demand.

    Dispatches the GitHub Actions workflow rather than running the sweep in this
    process: a full sweep can outlast a serverless invocation, which is why the
    scheduled run already lives on a GitHub runner (`.github/workflows/leads.yml`).
    This fires that same workflow, so a manual run and the hourly cron take the
    identical path. It always runs the whole sweep (`stage=both`); the workflow's
    own defaults set the batch size.
    """
    token = app_settings.github_token
    if not token:
        raise HTTPException(
            503,
            "Manual retrigger is not configured — set GITHUB_TOKEN to a token "
            "with actions:write on the repo.",
        )

    url = (
        f"https://api.github.com/repos/{app_settings.github_repo}"
        f"/actions/workflows/{app_settings.github_leads_workflow}/dispatches"
    )
    payload = {
        "ref": app_settings.github_workflow_ref,
        "inputs": {"stage": "both"},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except Exception as e:
        log.warning("[leads.retrigger.error] %s: %s", type(e).__name__, str(e)[:200])
        raise HTTPException(502, "Could not reach GitHub to dispatch the workflow")

    # A successful dispatch is 204 No Content; anything else is a rejection
    # (bad ref, workflow not found on that ref, token missing the scope).
    if resp.status_code != 204:
        detail = (resp.text or "").strip()[:200]
        log.warning("[leads.retrigger.rejected] status=%s body=%s",
                    resp.status_code, detail)
        raise HTTPException(
            502, f"GitHub rejected the dispatch ({resp.status_code}): {detail}"
        )

    log.info("[leads.retrigger] stage=both ref=%s by=%s",
             app_settings.github_workflow_ref, admin.get("id"))
    return {
        "ok": True,
        "ref": app_settings.github_workflow_ref,
        "workflow": app_settings.github_leads_workflow,
    }


# ---------------------------------------------------------------------------
# Job-posting leads — operator routes.
#
# Route order matters: `/api/leads/export.csv` and the other literal paths must
# be declared BEFORE `/api/leads/{lead_id}`, or FastAPI matches the parameter
# route first and rejects "export.csv" as a non-integer id.
# ---------------------------------------------------------------------------

# CSV column order — kept in sync with the export endpoint below.
# The posting facts an operator acts on, then the outreach contact columns.
def _posting_from_lead(lead: dict) -> dict:
    """Reconstruct a posting dict from a flattened export lead.

    `leads_for_export` lifts the posting's columns onto the lead (renaming
    `first_seen_at` → `posting_created_at` and dropping the posting `id`), so
    talentdb's builders — which expect a raw posting dict — get their keys back.
    """
    return {
        "id": lead.get("posting_id"),
        "source": lead.get("source"),
        "url": lead.get("url"),
        "title": lead.get("title"),
        "posted_at": lead.get("posted_at"),
        "board_remote_flag": lead.get("board_remote_flag"),
        "description": lead.get("description"),
        "search_term": lead.get("search_term"),
        "search_location": lead.get("search_location"),
        "service_line_hint": lead.get("service_line_hint"),
        "first_seen_at": lead.get("posting_created_at"),
        "last_seen_at": lead.get("last_seen_at"),
        "match_confidence": lead.get("match_confidence"),
        "match_status": lead.get("match_status"),
        "matched_at": lead.get("matched_at"),
        "employer_name": lead.get("employer_name"),
        "city": lead.get("city"),
        "state": lead.get("state"),
    }


def _lead_filters(
    cities: str | None, tracks: str | None, disposition: str | None,
    band: str | None, decision: str | None, work_mode: str | None,
    source: str | None, states: str | None, salary: str | None,
    search: str | None, practice: str | None = None,
) -> dict:
    """Build the filter dict once, so the feed and the export cannot drift.

    Exporting the whole table while the operator is looking at "Miami + Tampa,
    Dental track" is the obvious trap here; sharing this is what prevents it —
    including the keeps-only default, which would otherwise put every discard
    in a CSV the operator never saw on screen.
    """
    if decision and decision not in lead_store.DECISION_FILTERS:
        raise HTTPException(
            400, f"decision must be one of: {', '.join(lead_store.DECISION_FILTERS)}"
        )
    return {
        "cities": [c for c in (cities.split(",") if cities else []) if c],
        "tracks": [t for t in (tracks.split(",") if tracks else []) if t],
        "states": [s for s in (states.split(",") if states else []) if s],
        "disposition": disposition, "band": band, "decision": decision,
        "work_mode": work_mode, "source": source,
        "salary": salary, "search": search, "practice": practice,
    }


@app.get("/api/leads/filters")
def lead_filter_options(user: dict = Depends(get_current_user)):
    """Distinct cities and tracks present in this tenant's leads.

    Derived from the data rather than a fixed list — a tenant whose targets
    cover three cities should not scroll past thirty empty ones.
    """
    return lead_store.filter_options(user["company_id"])


@app.get("/api/leads/analytics")
def lead_analytics(
    days: int = Query(30, ge=1, le=365),
    user: dict = Depends(get_current_user),
):
    return lead_store.lead_analytics(user["company_id"], days=days)


@app.get("/api/leads/export.csv")
def export_leads_csv(
    max_exports: str | None = Query(
        None,
        description=(
            "Filter on existing export_count. Missing/empty exports every row. "
            "0 = only never-exported rows. N = export_count <= N."
        ),
    ),
    cities: str | None = Query(None),
    tracks: str | None = Query(None),
    disposition: str | None = Query(None),
    band: str | None = Query(None),
    decision: str | None = Query(None),
    work_mode: str | None = Query(None),
    source: str | None = Query(None),
    states: str | None = Query(None, description="comma-separated 2-letter codes"),
    salary: str | None = Query(None),
    search: str | None = Query(None),
    practice: str | None = Query(None),
    user: dict = Depends(get_current_user),
):
    """Stream a filtered CSV and stamp export_count on every row included.

    Takes the same filter params as `GET /api/leads` so a filtered view
    exports exactly what it is showing. `max_exports` has the same semantics
    as the practices export: leave it empty to take everything, pass `0` next
    time to skip rows already downloaded.
    """
    import csv
    import io

    cap: int | None = None
    if max_exports is not None and max_exports != "":
        try:
            cap = max(0, int(max_exports))
        except ValueError:
            raise HTTPException(status_code=400, detail="max_exports must be an integer")

    from src import talentdb
    from src.storage import get_practices_by_place_ids

    rows = lead_store.leads_for_export(
        user["company_id"],
        filters=_lead_filters(cities, tracks, disposition, band, decision,
                              work_mode, source, states, salary, search, practice),
        max_exports=cap,
    )

    # Attach the FULL practice record to each lead in one query — the embedded
    # practice on the lead only carries a handful of columns, but the Talent-DB
    # mapping needs the analysis/CRM fields too.
    place_ids = [
        (r.get("practice") or {}).get("place_id")
        for r in rows
        if (r.get("practice") or {}).get("place_id")
    ]
    practices_by_place = get_practices_by_place_ids(place_ids)

    def _serialize(value) -> str:
        if value is None:
            return ""
        # Lists/dicts as JSON so structured fields (tags, sales_angles,
        # icp_breakdown, website_contacts) round-trip into a CSV import.
        if isinstance(value, (list, dict)):
            return json.dumps(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def iter_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(talentdb.CSV_COLUMNS)
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
        for row in rows:
            embedded = row.get("practice") or {}
            full = practices_by_place.get(embedded.get("place_id")) or embedded or None
            fields = talentdb.build_fields(full, _posting_from_lead(row))
            writer.writerow([_serialize(fields.get(col)) for col in talentdb.CSV_COLUMNS])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    lead_store.increment_export_counts(
        [r["id"] for r in rows if r.get("id")], user_id=user.get("id"),
    )

    cap_label = "all" if cap is None else f"max{cap}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"instant-signals-{cap_label}-{stamp}.csv"
    return StreamingResponse(
        iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/leads")
def list_leads_endpoint(
    cities: str | None = Query(None, description="comma-separated"),
    tracks: str | None = Query(None, description="comma-separated"),
    disposition: str | None = Query(
        None, description="undecided | approved | rejected",
    ),
    band: str | None = Query(None),          # ready | check | decide
    decision: str | None = Query(
        None, description="keep (default) | discard | all",
    ),
    work_mode: str | None = Query(None),     # onsite | remote | hybrid
    source: str | None = Query(None),        # indeed | linkedin
    states: str | None = Query(None),        # comma-separated 2-letter codes
    salary: str | None = Query(None),        # "yes" | "no"
    search: str | None = Query(None),        # employer + title
    practice: str | None = Query(None),      # "yes" | "no" — linked to a practice
    sort: str = Query("band"),
    dir: str = Query("asc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    rows, total = lead_store.list_leads(
        user["company_id"],
        filters=_lead_filters(cities, tracks, disposition, band, decision,
                              work_mode, source, states, salary, search, practice),
        sort=sort, direction=dir, offset=offset, limit=limit,
    )
    return {
        "leads": rows,
        "count": len(rows),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
    }


@app.get("/api/leads/{lead_id}")
def get_lead_endpoint(lead_id: int, user: dict = Depends(get_current_user)):
    lead = lead_store.get_lead(user["company_id"], lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


class PatchLeadRequest(BaseModel):
    disposition: str | None = None
    reject_reason: str | None = None
    notes: str | None = None


@app.patch("/api/leads/{lead_id}")
def patch_lead_endpoint(
    lead_id: int,
    body: PatchLeadRequest,
    user: dict = Depends(get_current_user),
):
    """Update the workflow half of a lead.

    Only the three fields above are accepted, and `lead_store` strips anything
    outside `WORKFLOW_COLUMNS` again before writing — an operator action must
    never rewrite the reason the lead was surfaced.
    """
    if body.disposition and body.disposition not in lead_store.LEAD_DISPOSITIONS:
        raise HTTPException(
            400,
            f"disposition must be one of: {', '.join(lead_store.LEAD_DISPOSITIONS)}",
        )
    if not lead_store.get_lead(user["company_id"], lead_id):
        raise HTTPException(404, "Lead not found")

    fields = body.model_dump(exclude_unset=True)
    updated = lead_store.update_lead_workflow(
        user["company_id"], lead_id, fields, user_id=user.get("id"),
    )
    if not updated:
        raise HTTPException(500, "Lead update failed")
    return updated


@app.post("/api/leads/{lead_id}/import")
async def import_lead_signal_endpoint(
    lead_id: int,
    user: dict = Depends(get_current_user),
):
    """Push a signals lead (its posting + linked practice) to Talent-DB.

    The lead IS a (company, posting) row, so its `talentdb_exported_at` marker
    is the dedup key: an already-exported lead is not re-sent. Fail-soft.
    """
    from src import talentdb

    company_id = user["company_id"]
    lead = lead_store.get_lead(company_id, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    if lead.get("talentdb_exported_at"):
        log.info("[api.lead_import.skip] lead_id=%s already_exported", lead_id)
        return {"talentdb_status": "already_exported", "talentdb_warning": None,
                "local_entity_id": None}

    posting = lead_store.get_posting(lead.get("posting_id"))
    practice = None
    place_id = (lead.get("practice") or {}).get("place_id")
    if place_id:
        practice = get_practice(place_id)

    log.info("[api.lead_import] lead_id=%s user=%s place_id=%s",
             lead_id, user.get("email"), place_id)
    result = await talentdb.import_lead(practice, posting)
    if result.get("ok"):
        lead_store.mark_lead_exported(company_id, lead_id)
    log.info("[api.lead_import.response] lead_id=%s ok=%s status=%s",
             lead_id, result.get("ok"), result.get("status"))
    return _talentdb_response(result)
