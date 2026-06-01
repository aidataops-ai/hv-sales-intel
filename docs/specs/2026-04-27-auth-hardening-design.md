# Auth Hardening — Design Spec

**Date:** 2026-04-27
**Status:** Draft — awaiting review

## Goal

Tighten the existing admin-managed auth flow with three guardrails:
1. Validate email format and domain (`@apexvirtuals.com` only, no `--`, no duplicates).
2. Validate password complexity (8+ chars, ≥1 upper, ≥1 lower, ≥1 digit, ≥1 special).
3. Add self-service password change for any signed-in user.
4. Tighten admin password-reset privileges so only the bootstrap admin can reset other admins' passwords.

## Scope

### In scope
- New `src/validators.py` module with `validate_email` and `validate_password` helpers.
- Backend wiring on the user-create endpoint, admin password-reset endpoint, and (new) self-service password-change endpoint.
- Bootstrap admin identification via `settings.bootstrap_admin_email` (no DB schema change).
- Bootstrap admin startup hook validates the env-supplied password and fails fast with a clear error if non-compliant.
- Frontend: "Change password" entry in the user menu, opens a modal; live complexity hints; submit + error rendering.
- Frontend: Admin Users page hides/disables "Reset password" on other admin rows when caller isn't the bootstrap admin.
- **Rename role string `"rep"` → `"sdr"` everywhere** (data + code + UI labels). Display label is `"SDR"`; stored value is lowercase `"sdr"`.
- Tests for validators + endpoint behavior + role-based access.

### Out of scope
- Password rotation policy / mandatory expiry.
- Password history (preventing reuse of recent passwords).
- 2FA / MFA.
- Self-service email changes.
- Account lockout after N failed attempts.
- Email verification flow on user creation (Supabase already accepts our `email_confirm: True` shortcut).
- Forgot-password / magic-link reset (admins handle resets).
- Audit log of password changes (Supabase Auth keeps its own internal log; we don't surface it in our app yet).
- Admin role transfer (promoting a rep to admin from the UI). Existing flow stays: admin sets `role: 'admin'` at create time, no in-place role change UI.

## Validators

### `validate_email(email: str) -> None`
Raises `ValueError(<message>)` on failure.

Rules (in order — first failure wins):
1. **Non-empty + format**: regex `^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$` must match. Message: `"Email format is invalid."`.
2. **Domain**: lowercased local-part-stripped suffix must equal `"@apexvirtuals.com"`. Message: `"Email must be a @apexvirtuals.com address."`.
3. **No `--`**: substring `"--"` must not appear anywhere in the email. Message: `"Email cannot contain '--'."`.

Duplicate detection is NOT in this validator — it's enforced by Supabase Auth's unique constraint, which surfaces a clear error message that the endpoint maps to a 400 with `"Email already in use."`.

### `validate_password(password: str) -> None`
Raises `ValueError(<message>)` on failure.

Rules (all checked; first failure wins):
1. **Length**: `len(password) >= 8`. Message: `"Password must be at least 8 characters."`.
2. **At least one uppercase**: `re.search(r"[A-Z]", password)`. Message: `"Password must contain at least one uppercase letter."`.
3. **At least one lowercase**: `re.search(r"[a-z]", password)`. Message: `"Password must contain at least one lowercase letter."`.
4. **At least one digit**: `re.search(r"\d", password)`. Message: `"Password must contain at least one number."`.
5. **At least one special char**: `re.search(r"[^A-Za-z0-9]", password)`. Message: `"Password must contain at least one special character."`.

"Special character" intentionally permissive — anything that isn't a letter or digit. Avoids debates over which symbol set; matches typical password manager output.

## Bootstrap admin identification

New helper in `src/auth.py`:
```python
def is_bootstrap_admin(user: dict) -> bool:
    """True if this user's email matches the configured bootstrap admin."""
    bootstrap_email = settings.bootstrap_admin_email
    if not bootstrap_email:
        return False
    return user.get("email", "").lower() == bootstrap_email.lower()
```

Used by the admin reset-password endpoint to authorize cross-admin resets. No DB migration. If the operator rotates `BOOTSTRAP_ADMIN_EMAIL` in env, the bootstrap-admin privilege moves with the env value (acceptable for v1 — operator-owned config).

## Backend changes

### `POST /api/admin/users` (existing) — wire validators

Today this endpoint accepts `{email, name, password, role}`. Add validation at the top:

```python
@app.post("/api/admin/users")
def create_user(body: CreateUserRequest, admin: dict = Depends(require_admin)):
    try:
        validate_email(body.email)
        validate_password(body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ...
```

When Supabase Auth raises a duplicate-email error (its own unique constraint), the existing `except Exception as e: raise HTTPException(status_code=400, detail=str(e))` block surfaces it. We add a small wrapper to map Supabase's "User already registered" message to a friendlier `"Email already in use."`.

### `POST /api/admin/users/{user_id}/reset-password` (existing) — tighten + validate

Today any admin can reset any user's password. New rules:
1. Validate `body.new_password` against `validate_password` first.
2. Look up the target's `profiles` row.
3. If `target.role == "admin"` AND caller is NOT the bootstrap admin → 403 `"Only the bootstrap admin can reset another admin's password."`.
4. Otherwise proceed with the existing Supabase admin update.

Any admin can still reset their *own* password via this endpoint, but in practice the UI will route self-resets through the new `/api/me/password` endpoint.

### `POST /api/me/password` (new) — self-service password change

Auth: `get_current_user` (any signed-in user).

Request body:
```json
{
  "current_password": "...",
  "new_password": "..."
}
```

Behavior:
1. Validate `new_password` via `validate_password`. 400 on fail.
2. **Verify current password** by calling Supabase Auth's anon client `sign_in_with_password({email: user.email, password: current_password})`. If it fails → 401 `"Current password is incorrect."`. Use a fresh anon client for this; do NOT use the admin client.
3. Update the user's password via the admin client: `admin.update_user_by_id(user.id, {"password": new_password})`.
4. Return `{"ok": True}`.

Verifying current password protects against stolen session tokens. The signed-in user's session is unchanged — Supabase Auth doesn't invalidate sessions on password change by default, which is fine here.

### Bootstrap admin startup hook (existing) — validate env password

Today the hook seeds an admin if `profiles` has zero admins and the env vars are set. Add a single check before calling `auth.admin.create_user`:

```python
try:
    validate_password(settings.bootstrap_admin_password)
except ValueError as e:
    print(f"[bootstrap] BOOTSTRAP_ADMIN_PASSWORD invalid: {e} — admin not seeded.")
    return
```

Logged with `print` to stay consistent with the existing hook's log format. Vercel + uvicorn capture both prints and `hvsi.*` logger output, so the operator sees this at startup.

## Frontend changes

### User menu — add "Change password"

`web/components/user-menu.tsx`: new menu item between "Users" and "Sign out". Opens `<ChangePasswordModal>`. Available to all signed-in users.

### `web/components/change-password-modal.tsx` (new)

Three labeled fields: current, new, confirm. Submit button disabled until:
- Current is non-empty.
- New + Confirm match.
- New satisfies all 5 complexity rules.

Live complexity hints below the new-password field — five small rows, each with a ✓ (green) or ✗ (gray) and the rule label. Updates on every keystroke. Pattern:
```
✓ At least 8 characters
✗ One uppercase letter
✓ One lowercase letter
✗ One number
✗ One special character
```

On submit:
- Disable button, show spinner.
- POST `/api/me/password` with `{current_password, new_password}`.
- 200 → close modal, toast "Password updated".
- 401 → inline error: "Current password is incorrect."
- 400 → inline error with the message from the response.
- 5xx / network → inline error: "Couldn't save — try again."

### `web/lib/api.ts` — new helper

```typescript
export async function changeMyPassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ ok: true }> {
  return apiFetch("/api/me/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })
}
```

### Admin Users page — gate cross-admin resets

`web/app/admin/users/page.tsx`: pass `currentUserIsBootstrapAdmin: boolean` (computed in the page from `useAuth()` + `process.env.NEXT_PUBLIC_BOOTSTRAP_ADMIN_EMAIL` — or fetched via a small `/api/me` echo if we don't want to leak the email to the frontend env).

For each user row:
- If row is the current user → no Reset button (use Change Password modal instead).
- Else if row is admin AND current user isn't bootstrap admin → Reset button disabled, tooltip `"Only the bootstrap admin can reset another admin's password."`.
- Else → Reset button enabled, opens prompt for new password (existing flow), validates locally before sending, surfaces backend errors.

#### `currentUserIsBootstrapAdmin` source

Cleanest: small new endpoint `GET /api/me` that returns `{id, email, name, role, is_bootstrap_admin: boolean}`. Frontend's `AuthProvider` already loads the profile; extending the same call to include the boolean keeps the frontend simple. Avoids exposing the bootstrap admin's email to the browser.

If `/api/me` doesn't already exist (existing AuthProvider may use Supabase directly), this spec adds it. Backend: trivial — same `get_current_user` dep + `is_bootstrap_admin` helper.

### Reset-password validation on the admin form

The existing reset-password prompt accepts any string. Add the same complexity rules client-side (same 5 hints) before submitting. Backend rejects non-compliant via `validate_password` regardless, but client-side check saves a round trip and gives instant feedback.

## API surface summary

| Endpoint                                            | Auth          | Behavior                                                                  |
| --------------------------------------------------- | ------------- | ------------------------------------------------------------------------- |
| `POST /api/admin/users` (existing)                  | admin         | + email validator, + password validator, friendlier duplicate message     |
| `POST /api/admin/users/{user_id}/reset-password` (existing) | admin   | + password validator, + bootstrap-admin check for admin targets           |
| `POST /api/me/password` (new)                       | any user      | Verify current password, validate new, update via admin client            |
| `GET /api/me` (new or extended)                     | any user      | Returns current profile + `is_bootstrap_admin: bool`                      |

## Error handling & edge cases

| Situation                                              | Behavior                                                                            |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| Admin tries to create user with non-domain email       | 400 `"Email must be a @apexvirtuals.com address."`                                |
| Admin tries to create user with weak password          | 400 with the specific failing rule.                                                 |
| Admin tries to create user with `--` in email          | 400 `"Email cannot contain '--'."`                                                  |
| Admin tries to create user with duplicate email        | 400 `"Email already in use."`                                                       |
| Admin tries to reset another admin's password          | 403 `"Only the bootstrap admin can reset another admin's password."`                |
| Admin resets a rep's password                          | 200, password updated.                                                              |
| Bootstrap admin resets another admin's password        | 200, password updated.                                                              |
| Self-service: wrong current password                   | 401 `"Current password is incorrect."`                                              |
| Self-service: weak new password                        | 400 with specific rule.                                                             |
| Self-service: new == current (server doesn't catch)    | Allowed — Supabase will accept the same password. UI shows a soft warning if `new === current` and prompts to confirm. |
| Bootstrap admin env password is weak at startup        | Hook logs the validator error and skips seeding. Operator fixes env + redeploys.    |
| Caller has no `email` on profile (unlikely)            | `is_bootstrap_admin` returns False → cross-admin reset blocked → fail-safe.         |
| Frontend: complexity hints flicker on paste            | Acceptable — validation function is pure + cheap.                                   |

## Testing

`tests/test_validators.py` (new):
- 4 happy-path emails (variations of casing, plus signs, dots in local part).
- 6 sad emails: missing `@`, wrong domain, contains `--`, contains `--` in domain part, malformed TLD, empty string.
- 1 happy password: `"Healthy123!"`.
- 6 sad passwords, one per rule failure: too short, no upper, no lower, no digit, no special, empty.

`tests/test_api_users.py` (new or extends existing):
- Create with weak password → 400 + correct message.
- Create with non-domain email → 400.
- Create with `--` email → 400.
- Reset other admin's password as non-bootstrap admin → 403.
- Reset other admin's password as bootstrap admin → 200.
- Reset rep's password as any admin → 200.

`tests/test_api_me.py` (new):
- `POST /api/me/password` requires auth → 401 without token.
- Wrong current password → 401.
- Weak new password → 400.
- Happy path → 200, password updated.
- `GET /api/me` returns `is_bootstrap_admin: true` for the configured bootstrap user, false otherwise.

Frontend: typecheck-only (no unit tests in this codebase).

## `rep` → `sdr` rename

Stored value lowercased to match the existing `"admin"` / `"rep"` convention. UI displays uppercase `"SDR"`.

### One-time DB migration (manual, Supabase SQL editor)

```sql
update profiles set role = 'sdr' where role = 'rep';
```

No schema change — `role` is a free-text column. The migration is a data update only. Idempotent (running twice is a no-op).

### Backend touch points

- `api/index.py`:
  - `CreateUserRequest.role: str = "rep"` → `"sdr"` (default value).
  - `if body.role not in ("admin", "rep")` → `("admin", "sdr")` in `create_user`.
  - Any code that branches on `role == "rep"` switches to `"sdr"` (none today, but worth a final grep before merging).
- `src/auth.py`: `require_admin` already keys off `"admin"` so no change needed. `get_current_user` doesn't care about role names.
- `tests/conftest.py`:
  - Fixture `sample_rep_profile` → renamed to `sample_sdr_profile`. The dict's `"role"` field changes from `"rep"` to `"sdr"`.
  - All tests that import / use `sample_rep_profile` are updated by find-replace.

### Frontend touch points

- `web/lib/types.ts`: `User.role: "admin" | "rep"` → `"admin" | "sdr"`.
- `web/app/admin/users/page.tsx`:
  - Create-user form's role select: option label `"Rep"` → `"SDR"`, option value `"rep"` → `"sdr"`. Default selected value updated.
  - Role badge / display in the user list: `"rep"` → display as `"SDR"` (uppercase). Suggest a tiny helper `roleLabel(role)` returning `"Admin"` or `"SDR"` to keep render logic clean.
- Anywhere copy says "rep" in the UI (search for `\brep\b` in `web/`): swap to "SDR".

### Backwards compatibility

- A profile with the legacy `role: "rep"` after deploy but before the migration runs would behave the same as a non-admin (since `require_admin` checks for `== "admin"`). UX won't break for users; only the create-user role validator would reject `"rep"` if anyone sent it via the API directly.
- Once the SQL migration runs, no `"rep"` rows remain.

## Env vars

No new env vars. Reuses `BOOTSTRAP_ADMIN_EMAIL` to identify the bootstrap admin at runtime.

## Decision log (proposed; resolves on user approval)

1. **Bootstrap admin identified by email match, not DB flag.** Simpler, no migration. Trade-off: rotating bootstrap admin requires env change + redeploy. Acceptable.
2. **`@apexvirtuals.com` is hardcoded.** No multi-tenant variation expected. If we ever need to support multiple domains, swap to a `Settings.allowed_email_domains: list[str]`.
3. **Special char defined as "anything non-alphanumeric"**. Avoids OWASP-style explicit lists that get stale.
4. **Self-service requires current password.** Stolen-session protection.
5. **Modal, not `/account` page.** Compact; no separate route to maintain. Future settings page can host this modal as one of multiple sections.
6. **No password expiry / no history.** Defer until compliance requires it.
7. **Validators raise `ValueError` rather than returning a tuple.** Pydantic-friendly, simple control flow, matches existing convention in `email_send.py` etc.
8. **Role stored lowercased (`"sdr"`), displayed uppercase (`"SDR"`).** Matches existing `"admin"` storage convention; UI normalization keeps the wire format predictable.

## Success criteria

- Admin creating a user with a non-domain email gets a clear inline error in <500ms.
- Admin creating a user with a weak password sees the specific rule that failed.
- Any signed-in user can change their password via user menu → modal in <30s.
- Admin (non-bootstrap) attempting to reset another admin's password sees the disabled state in the UI; if they bypass it via direct API call, they get 403.
- Bootstrap admin can reset any password.
- `pytest -q` passes (≥ 80 + new validator + endpoint tests).
- `npx tsc --noEmit` clean.
