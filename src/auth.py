import json
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from supabase import create_client

from src.settings import settings

_admin_client: Any = None


def get_admin_client():
    """Supabase client with service-role key. Lazily instantiated."""
    global _admin_client
    if _admin_client is None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError("Supabase service-role client not configured")
        _admin_client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
    return _admin_client


def _read_supabase_token(request: Request) -> str | None:
    """Reassemble the access token from @supabase/ssr cookies.

    Cookie is named `sb-<project-ref>-auth-token`, sometimes chunked
    into `.0` / `.1`. Value is a JSON blob with an `access_token` field.
    """
    auth_cookies = {
        name: value
        for name, value in request.cookies.items()
        if name.startswith("sb-") and "auth-token" in name
    }
    if not auth_cookies:
        return None

    bases: dict[str, dict[int, str]] = {}
    singles: dict[str, str] = {}
    for name, value in auth_cookies.items():
        if "." in name and name.rsplit(".", 1)[-1].isdigit():
            base, idx = name.rsplit(".", 1)
            bases.setdefault(base, {})[int(idx)] = value
        else:
            singles[name] = value

    candidates: list[str] = []
    for base, parts in bases.items():
        candidates.append("".join(parts[i] for i in sorted(parts)))
    candidates.extend(singles.values())

    for raw in candidates:
        # Newer @supabase/ssr prefixes the value with `base64-` and stores the
        # JSON blob base64-encoded. Older versions store the JSON directly.
        if raw.startswith("base64-"):
            import base64
            payload = raw[len("base64-"):]
            # Accept both URL-safe and standard base64; pad as needed.
            padded = payload + "=" * (-len(payload) % 4)
            try:
                decoded_bytes = base64.urlsafe_b64decode(padded)
            except Exception:
                try:
                    decoded_bytes = base64.b64decode(padded)
                except Exception:
                    continue
            try:
                raw = decoded_bytes.decode("utf-8")
            except UnicodeDecodeError:
                continue
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        token = decoded.get("access_token")
        if token:
            return token
    return None


CURRENT_COMPANY_COOKIE = "apex_current_company"

# Supabase stamps signed-in access tokens with this audience.
_JWT_AUDIENCE = "authenticated"
# Tolerate a little clock drift between this host and the auth server. Past
# that the token really is expired, which is a 401 — never a 500.
_JWT_LEEWAY_SECONDS = 30


def _auth_user_id(client, token: str) -> str:
    """Return the auth user id a token belongs to, or raise 401.

    With `supabase_jwt_secret` configured this verifies the HS256 signature,
    audience and expiry locally and reads `sub` — no network at all. Left
    empty it falls back to the GoTrue `auth.get_user` round trip, which is
    what every request used to pay unconditionally.
    """
    secret = settings.supabase_jwt_secret
    if secret:
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],          # pinned: no `alg: none` downgrade
                audience=_JWT_AUDIENCE,
                leeway=_JWT_LEEWAY_SECONDS,
                options={"require": ["exp", "sub"]},
            )
        except Exception:
            # Expired, wrong signature, wrong audience, or not a JWT at all —
            # every one of those means "not authenticated", not "server bug".
            raise HTTPException(status_code=401, detail="Invalid token") from None
        user_id = claims.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id

    try:
        user_id = client.auth.get_user(token).user.id
    except Exception:
        # Covers a rejected token and a `user`-less response alike.
        raise HTTPException(status_code=401, detail="Invalid token") from None
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


def get_current_user(request: Request) -> dict:
    """Resolve JWT → profiles row + active company. 401 if no token,
    403 if no profile or no company membership.

    Plain `def`, deliberately: FastAPI runs sync dependencies in the
    threadpool, so the blocking Supabase calls below stay off the event
    loop instead of stalling every other in-flight request.

    Costs at most two queries — profile, then memberships — plus a GoTrue
    round trip only when `supabase_jwt_secret` is unset.

    The returned dict carries:
      - All `profiles` columns (id, email, name, role, etc.)
      - `company_id`   — the active company UUID
      - `company_role` — 'admin' or 'sdr' WITHIN that company
      - `role` is overridden with `company_role` so existing code that
        gates on `user["role"] == "admin"` automatically respects the
        per-company role instead of the legacy global one.
    """
    token = _read_supabase_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    client = get_admin_client()
    user_id = _auth_user_id(client, token)

    result = (
        client.table("profiles").select("*")
        .eq("id", user_id).single().execute()
    )
    if not result.data:
        raise HTTPException(status_code=403, detail="No profile for this user")
    if result.data.get("disabled_at"):
        raise HTTPException(status_code=401, detail="Account disabled")
    profile = result.data

    # Resolve the active company. Cookie wins if the user is actually a
    # member of that company; otherwise fall back to the oldest membership.
    company_id, company_role = _resolve_current_company(
        client, profile["id"], request.cookies.get(CURRENT_COMPANY_COOKIE),
    )

    return {
        **profile,
        "company_id": company_id,
        "company_role": company_role,
        # Override the legacy global role with the per-company role so
        # require_admin and existing role checks work per-company.
        "role": company_role,
        # Preserve the global profile role under a separate key for the
        # few places that need it (e.g. a future super-admin path).
        "global_role": profile.get("role"),
    }


def _resolve_current_company(
    client, user_id: str, cookie_value: str | None,
) -> tuple[str, str]:
    """Return (company_id, role) for the user's active company.

    If the cookie names a company the user belongs to, use it.
    Otherwise pick the oldest membership. Raises 403 if the user is in
    no companies — every authenticated user must belong to at least one.

    One round trip, not the two this used to take. It can't be folded into
    the profile query either: `company_members.user_id` and `profiles.id`
    both point at `auth.users` with no foreign key between them, so
    PostgREST has no relationship to embed and
    `profiles?select=*,company_members(...)` fails with PGRST200. So we
    fetch the whole (small) membership set once, still ordered by
    `joined_at` server-side, and make the cookie-vs-oldest choice here.
    """
    try:
        result = (
            client.table("company_members")
            .select("company_id,role")
            .eq("user_id", user_id)
            .order("joined_at")
            .execute()
        )
        memberships = result.data or []
    except Exception:
        memberships = []

    if not memberships:
        raise HTTPException(
            status_code=403,
            detail="User belongs to no company. Sign up to create one.",
        )

    if cookie_value:
        for row in memberships:
            if row["company_id"] == cookie_value:
                return cookie_value, row["role"]

    # Oldest membership: the query is already ordered by joined_at.
    row = memberships[0]
    return row["company_id"], row["role"]


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Raise 403 if the current user isn't an admin of the active company."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def is_bootstrap_admin(user: dict) -> bool:
    """True if this user's email matches the configured bootstrap admin.

    Used to gate cross-admin operations (e.g., resetting another admin's
    password). Comparison is case-insensitive.
    """
    bootstrap_email = (settings.bootstrap_admin_email or "").lower()
    if not bootstrap_email:
        return False
    return (user.get("email") or "").lower() == bootstrap_email
