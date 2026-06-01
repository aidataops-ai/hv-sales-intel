# Auth Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire email-domain + password-complexity validation into user create + admin reset, add a self-service password change modal, restrict cross-admin resets to the bootstrap admin, and rename the `rep` role to `sdr`.

**Architecture:** New `src/validators.py` module enforces both rules with `ValueError`-raising helpers. Existing `/api/admin/users` and `/api/admin/users/{id}/reset-password` endpoints adopt the validators. New `/api/me/password` handles self-service. The `is_bootstrap_admin` flag is added to `/api/me`. Frontend gains a `ChangePasswordModal` reachable from the user menu. The Admin Users page learns to gate cross-admin resets by reading `is_bootstrap_admin`.

**Tech Stack:** FastAPI, pydantic-settings, Supabase admin + anon clients, Next.js 14 App Router, React.

**Spec:** [docs/specs/2026-04-27-auth-hardening-design.md](../../specs/2026-04-27-auth-hardening-design.md)

---

## File Structure

**Backend — create:**
- `src/validators.py` — `validate_email`, `validate_password`
- `tests/test_validators.py`
- `tests/test_api_me.py` — covers `GET /api/me` extended fields + `POST /api/me/password`

**Backend — modify:**
- `src/auth.py` — add `is_bootstrap_admin(user)` helper
- `api/index.py`:
  - Extend `/api/me` to return `is_bootstrap_admin`
  - Wire validators into `POST /api/admin/users`
  - Tighten `POST /api/admin/users/{user_id}/reset-password` with validator + role check
  - Add `POST /api/me/password`
  - Validate password in bootstrap admin startup hook
  - Update `CreateUserRequest.role` default + valid set (`"sdr"` instead of `"rep"`)
- `tests/conftest.py` — rename fixture `sample_rep_profile` → `sample_sdr_profile`, role `"rep"` → `"sdr"`
- All existing tests that import `sample_rep_profile` (find-replace)

**Frontend — create:**
- `web/components/change-password-modal.tsx`

**Frontend — modify:**
- `web/lib/types.ts` — `User.role: "admin" | "sdr"`, add `is_bootstrap_admin?: boolean`
- `web/lib/api.ts` — add `changeMyPassword`
- `web/components/user-menu.tsx` — add "Change password" entry that opens the modal
- `web/app/admin/users/page.tsx`:
  - Role select: `"rep"`/`"Rep"` → `"sdr"`/`"SDR"`
  - Heading + form copy: "Create rep" → "Create SDR"
  - Add Reset password button to each row (gated for cross-admin)
  - Display role uppercase via small `roleLabel()` helper

**Migration — manual:**
- One-line SQL in Supabase SQL editor: `update profiles set role = 'sdr' where role = 'rep';`

`src/storage.py`, `src/auth.require_admin`, `src/auth.get_current_user` are unchanged — they don't branch on role values beyond `"admin"`.

---

### Task 1: `src/validators.py` — email + password validators

**Files:**
- Create: `src/validators.py`
- Create: `tests/test_validators.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_validators.py`:

```python
import pytest

from src.validators import validate_email, validate_password


# ---------- email ----------

def test_validate_email_accepts_basic_company_address():
    validate_email("sarah@apexvirtuals.com")


def test_validate_email_accepts_dots_and_plus():
    validate_email("sarah.khan+team@apexvirtuals.com")


def test_validate_email_accepts_uppercase_local_part():
    validate_email("Sarah.Khan@apexvirtuals.com")


def test_validate_email_rejects_missing_at():
    with pytest.raises(ValueError, match="format"):
        validate_email("sarahapexvirtuals.com")


def test_validate_email_rejects_wrong_domain():
    with pytest.raises(ValueError, match="@apexvirtuals.com"):
        validate_email("sarah@example.com")


def test_validate_email_rejects_double_dash_anywhere():
    with pytest.raises(ValueError, match="--"):
        validate_email("sarah--khan@apexvirtuals.com")


def test_validate_email_rejects_empty_string():
    with pytest.raises(ValueError):
        validate_email("")


def test_validate_email_rejects_malformed_tld():
    with pytest.raises(ValueError, match="format"):
        validate_email("sarah@apexvirtuals.")


# ---------- password ----------

def test_validate_password_accepts_compliant():
    validate_password("Healthy123!")


def test_validate_password_rejects_too_short():
    with pytest.raises(ValueError, match="8 characters"):
        validate_password("Ab1!")


def test_validate_password_rejects_no_uppercase():
    with pytest.raises(ValueError, match="uppercase"):
        validate_password("healthy123!")


def test_validate_password_rejects_no_lowercase():
    with pytest.raises(ValueError, match="lowercase"):
        validate_password("HEALTHY123!")


def test_validate_password_rejects_no_digit():
    with pytest.raises(ValueError, match="number"):
        validate_password("HealthyAbc!")


def test_validate_password_rejects_no_special():
    with pytest.raises(ValueError, match="special"):
        validate_password("Healthy123A")


def test_validate_password_rejects_empty():
    with pytest.raises(ValueError):
        validate_password("")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_validators.py -v`
Expected: all tests FAIL with `ModuleNotFoundError: No module named 'src.validators'`.

- [ ] **Step 3: Implement `src/validators.py`**

```python
import re

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
ALLOWED_DOMAIN = "@apexvirtuals.com"


def validate_email(email: str) -> None:
    """Raise ValueError if email is malformed, off-domain, or contains '--'."""
    if not email or not EMAIL_REGEX.match(email):
        raise ValueError("Email format is invalid.")
    if not email.lower().endswith(ALLOWED_DOMAIN):
        raise ValueError(f"Email must be a {ALLOWED_DOMAIN} address.")
    if "--" in email:
        raise ValueError("Email cannot contain '--'.")


def validate_password(password: str) -> None:
    """Raise ValueError if password fails complexity checks."""
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters.")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter.")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one number.")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError("Password must contain at least one special character.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_validators.py -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add src/validators.py tests/test_validators.py
git commit -m "feat(auth): add email + password validators"
```

---

### Task 2: `is_bootstrap_admin` helper

**Files:**
- Modify: `src/auth.py`
- Modify: `tests/test_validators.py` (extend) OR create new test file

We'll add the test to `tests/test_validators.py` since both validators and this helper are tightly related auth utilities. (If the project later grows separate auth tests, this can move.)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_validators.py`:

```python
from unittest.mock import patch

from src.auth import is_bootstrap_admin


def test_is_bootstrap_admin_matches_email_case_insensitive():
    user = {"email": "Admin@ApexVirtuals.com"}
    with patch("src.auth.settings") as s:
        s.bootstrap_admin_email = "admin@apexvirtuals.com"
        assert is_bootstrap_admin(user) is True


def test_is_bootstrap_admin_returns_false_for_non_bootstrap():
    user = {"email": "other@apexvirtuals.com"}
    with patch("src.auth.settings") as s:
        s.bootstrap_admin_email = "admin@apexvirtuals.com"
        assert is_bootstrap_admin(user) is False


def test_is_bootstrap_admin_returns_false_when_setting_empty():
    user = {"email": "admin@apexvirtuals.com"}
    with patch("src.auth.settings") as s:
        s.bootstrap_admin_email = ""
        assert is_bootstrap_admin(user) is False


def test_is_bootstrap_admin_handles_user_without_email():
    with patch("src.auth.settings") as s:
        s.bootstrap_admin_email = "admin@apexvirtuals.com"
        assert is_bootstrap_admin({}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_validators.py -v -k bootstrap`
Expected: 4 tests FAIL with `ImportError: cannot import name 'is_bootstrap_admin'`.

- [ ] **Step 3: Add helper to `src/auth.py`**

Add at the end of `src/auth.py`:

```python
def is_bootstrap_admin(user: dict) -> bool:
    """True if this user's email matches the configured bootstrap admin.

    Used to gate cross-admin operations (e.g., resetting another admin's
    password). Comparison is case-insensitive.
    """
    bootstrap_email = (settings.bootstrap_admin_email or "").lower()
    if not bootstrap_email:
        return False
    return (user.get("email") or "").lower() == bootstrap_email
```

If `settings` isn't imported at the top of the file, add `from src.settings import settings` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_validators.py -v`
Expected: 19 passed (15 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/auth.py tests/test_validators.py
git commit -m "feat(auth): add is_bootstrap_admin helper"
```

---

### Task 3: Extend `GET /api/me` to expose `is_bootstrap_admin`

**Files:**
- Modify: `api/index.py:449-451` (the `me` handler)
- Create: `tests/test_api_me.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_api_me.py`:

```python
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import get_current_user


def _override_user(user: dict):
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_me_returns_is_bootstrap_admin_true_for_bootstrap(sample_admin_profile):
    bootstrap_user = {**sample_admin_profile, "email": "boss@apexvirtuals.com"}
    _override_user(bootstrap_user)

    with patch("api.index.app_settings") as s:
        s.bootstrap_admin_email = "boss@apexvirtuals.com"
        client = TestClient(app)
        resp = client.get("/api/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "boss@apexvirtuals.com"
    assert body["is_bootstrap_admin"] is True


def test_me_returns_is_bootstrap_admin_false_for_other_admin(sample_admin_profile):
    other_admin = {**sample_admin_profile, "email": "other@apexvirtuals.com"}
    _override_user(other_admin)

    with patch("api.index.app_settings") as s:
        s.bootstrap_admin_email = "boss@apexvirtuals.com"
        client = TestClient(app)
        resp = client.get("/api/me")

    assert resp.status_code == 200
    assert resp.json()["is_bootstrap_admin"] is False


def test_me_requires_auth():
    client = TestClient(app)
    resp = client.get("/api/me")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_me.py -v`
Expected: the two `is_bootstrap_admin` tests FAIL — the field isn't in the response yet. The auth test should PASS already.

- [ ] **Step 3: Modify the `me` handler in `api/index.py`**

Replace this block (currently `api/index.py:449-451`):

```python
@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    return user
```

with:

```python
@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    from src.auth import is_bootstrap_admin
    return {**user, "is_bootstrap_admin": is_bootstrap_admin(user)}
```

(`is_bootstrap_admin` is imported lazily to avoid circular-import risk; if the file already imports `from src.auth import ...`, prefer adding to the existing import line at the top.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_me.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_me.py
git commit -m "feat(auth): /api/me returns is_bootstrap_admin flag"
```

---

### Task 4: Wire validators into `POST /api/admin/users`

**Files:**
- Modify: `api/index.py` (`create_user` handler)
- Create: `tests/test_api_users.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_api_users.py`:

```python
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.index import app
from src.auth import require_admin


def _override_admin(profile: dict):
    app.dependency_overrides[require_admin] = lambda: profile


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_create_user_rejects_bad_email_domain(sample_admin_profile):
    _override_admin(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/admin/users", json={
        "email": "rep@example.com",
        "name": "Rep",
        "password": "Healthy123!",
    })
    assert resp.status_code == 400
    assert "@apexvirtuals.com" in resp.json()["detail"]


def test_create_user_rejects_double_dash_email(sample_admin_profile):
    _override_admin(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/admin/users", json={
        "email": "rep--admin@apexvirtuals.com",
        "name": "Rep",
        "password": "Healthy123!",
    })
    assert resp.status_code == 400
    assert "--" in resp.json()["detail"]


def test_create_user_rejects_weak_password(sample_admin_profile):
    _override_admin(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/admin/users", json={
        "email": "rep@apexvirtuals.com",
        "name": "Rep",
        "password": "weak",
    })
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Password" in detail


def test_create_user_maps_duplicate_email_to_friendly_message(sample_admin_profile):
    _override_admin(sample_admin_profile)

    fake_admin = MagicMock()
    fake_admin.auth.admin.create_user.side_effect = Exception("User already registered")

    with patch("api.index.get_admin_client", return_value=fake_admin):
        client = TestClient(app)
        resp = client.post("/api/admin/users", json={
            "email": "rep@apexvirtuals.com",
            "name": "Rep",
            "password": "Healthy123!",
        })

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Email already in use."


def test_create_user_happy_path(sample_admin_profile):
    _override_admin(sample_admin_profile)

    created_user = MagicMock()
    created_user.user.id = "new-user-id"
    fake_admin = MagicMock()
    fake_admin.auth.admin.create_user.return_value = created_user
    profile_select = MagicMock()
    profile_select.execute.return_value.data = {
        "id": "new-user-id",
        "email": "rep@apexvirtuals.com",
        "name": "Rep",
        "role": "sdr",
        "created_at": "2026-04-27T00:00:00Z",
    }
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value = profile_select

    with patch("api.index.get_admin_client", return_value=fake_admin):
        client = TestClient(app)
        resp = client.post("/api/admin/users", json={
            "email": "rep@apexvirtuals.com",
            "name": "Rep",
            "password": "Healthy123!",
            "role": "sdr",
        })

    assert resp.status_code == 200
    assert resp.json()["email"] == "rep@apexvirtuals.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_users.py -v`
Expected: validator tests FAIL because `create_user` doesn't validate yet; the duplicate-mapping test FAILS because the friendly mapping isn't in place; the happy-path test may FAIL because `role: "sdr"` isn't accepted.

- [ ] **Step 3: Update `create_user` in `api/index.py`**

Find the existing `create_user` handler. Replace its body so the final shape is:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_users.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_users.py
git commit -m "feat(auth): validate email + password on user create, friendlier dup msg"
```

---

### Task 5: Update `CreateUserRequest` default + role rename in conftest

**Files:**
- Modify: `api/index.py` (`CreateUserRequest`)
- Modify: `tests/conftest.py`
- Modify: any test file importing `sample_rep_profile`

- [ ] **Step 1: Find places that reference the old fixture name**

Run: `grep -rn "sample_rep_profile" tests/`
Note the file paths. Common ones: `tests/test_api_call_log.py`, `tests/test_api_enrich.py`, `tests/test_api_auth.py` (if it exists).

- [ ] **Step 2: Rename the fixture**

Edit `tests/conftest.py`:

```python
import pytest


@pytest.fixture
def sample_sdr_profile() -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "sdr@example.com",
        "name": "Test SDR",
        "role": "sdr",
        "created_at": "2026-04-22T00:00:00Z",
    }


@pytest.fixture
def sample_admin_profile() -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000002",
        "email": "admin@example.com",
        "name": "Test Admin",
        "role": "admin",
        "created_at": "2026-04-22T00:00:00Z",
    }
```

- [ ] **Step 3: Find-replace the fixture name across `tests/`**

In each file that imports / receives `sample_rep_profile` as a parameter, replace with `sample_sdr_profile`. Run:

```bash
grep -rln "sample_rep_profile" tests/ | xargs sed -i 's/sample_rep_profile/sample_sdr_profile/g'
```

(On Windows / PowerShell, use `Get-ChildItem -Recurse tests\ | Select-String 'sample_rep_profile' -List | %{ (Get-Content $_.Path) -replace 'sample_rep_profile','sample_sdr_profile' | Set-Content $_.Path }` — or just edit the files manually if there are only 1-3.)

- [ ] **Step 4: Update `CreateUserRequest` default in `api/index.py`**

Find the model definition and change the default:

```python
class CreateUserRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "sdr"
```

- [ ] **Step 5: Run the full backend suite**

Run: `pytest -q`
Expected: all tests pass — no `KeyError: 'sample_rep_profile'` from leftover renames.

- [ ] **Step 6: Commit**

```bash
git add api/index.py tests/conftest.py tests/
git commit -m "refactor(auth): rename rep -> sdr in role values + test fixture"
```

---

### Task 6: Tighten `POST /api/admin/users/{user_id}/reset-password`

**Files:**
- Modify: `api/index.py` (`reset_password` handler)
- Modify: `tests/test_api_users.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_api_users.py`:

```python
def test_reset_rejects_weak_password(sample_admin_profile):
    _override_admin(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/admin/users/some-id/reset-password", json={
        "new_password": "weak",
    })
    assert resp.status_code == 400
    assert "Password" in resp.json()["detail"]


def test_reset_admin_target_blocked_for_non_bootstrap(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target_admin = {
        "id": "target-admin-id",
        "email": "other@apexvirtuals.com",
        "role": "admin",
    }

    fake_admin = MagicMock()
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = target_admin

    with patch("api.index.get_admin_client", return_value=fake_admin):
        with patch("api.index.app_settings") as s:
            s.bootstrap_admin_email = "boss@apexvirtuals.com"
            # caller is sample_admin_profile (admin@example.com), NOT bootstrap
            client = TestClient(app)
            resp = client.post(
                "/api/admin/users/target-admin-id/reset-password",
                json={"new_password": "Healthy123!"},
            )

    assert resp.status_code == 403
    assert "bootstrap admin" in resp.json()["detail"].lower()


def test_reset_admin_target_allowed_for_bootstrap(sample_admin_profile):
    bootstrap = {**sample_admin_profile, "email": "boss@apexvirtuals.com"}
    _override_admin(bootstrap)
    target_admin = {
        "id": "target-admin-id",
        "email": "other@apexvirtuals.com",
        "role": "admin",
    }

    fake_admin = MagicMock()
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = target_admin

    with patch("api.index.get_admin_client", return_value=fake_admin):
        with patch("api.index.app_settings") as s:
            s.bootstrap_admin_email = "boss@apexvirtuals.com"
            client = TestClient(app)
            resp = client.post(
                "/api/admin/users/target-admin-id/reset-password",
                json={"new_password": "Healthy123!"},
            )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_reset_sdr_target_allowed_for_any_admin(sample_admin_profile):
    _override_admin(sample_admin_profile)
    target_sdr = {
        "id": "target-sdr-id",
        "email": "rep@apexvirtuals.com",
        "role": "sdr",
    }

    fake_admin = MagicMock()
    fake_admin.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = target_sdr

    with patch("api.index.get_admin_client", return_value=fake_admin):
        with patch("api.index.app_settings") as s:
            s.bootstrap_admin_email = "boss@apexvirtuals.com"
            client = TestClient(app)
            resp = client.post(
                "/api/admin/users/target-sdr-id/reset-password",
                json={"new_password": "Healthy123!"},
            )

    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_users.py -v -k reset`
Expected: the 4 new tests FAIL.

- [ ] **Step 3: Update the handler**

Replace the existing `reset_password` handler in `api/index.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_users.py -v -k reset`
Expected: 4 passed (plus prior `create_user` tests still passing).

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_users.py
git commit -m "feat(auth): admin password reset gates cross-admin to bootstrap"
```

---

### Task 7: New `POST /api/me/password` self-service endpoint

**Files:**
- Modify: `api/index.py`
- Modify: `tests/test_api_me.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_api_me.py`:

```python
from unittest.mock import MagicMock


def test_me_password_rejects_weak_new_password(sample_admin_profile):
    _override_user(sample_admin_profile)
    client = TestClient(app)
    resp = client.post("/api/me/password", json={
        "current_password": "Whatever1!",
        "new_password": "weak",
    })
    assert resp.status_code == 400
    assert "Password" in resp.json()["detail"]


def test_me_password_rejects_wrong_current(sample_admin_profile):
    _override_user(sample_admin_profile)

    fake_anon = MagicMock()
    fake_anon.auth.sign_in_with_password.side_effect = Exception("Invalid login credentials")

    with patch("api.index._anon_supabase_client", return_value=fake_anon):
        client = TestClient(app)
        resp = client.post("/api/me/password", json={
            "current_password": "WrongPass1!",
            "new_password": "Healthy123!",
        })

    assert resp.status_code == 401
    assert "current password" in resp.json()["detail"].lower()


def test_me_password_happy_path(sample_admin_profile):
    _override_user(sample_admin_profile)

    fake_anon = MagicMock()
    fake_anon.auth.sign_in_with_password.return_value = MagicMock()

    fake_admin = MagicMock()

    with patch("api.index._anon_supabase_client", return_value=fake_anon):
        with patch("api.index.get_admin_client", return_value=fake_admin):
            client = TestClient(app)
            resp = client.post("/api/me/password", json={
                "current_password": "OldPass1!",
                "new_password": "NewHealthy1!",
            })

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    fake_admin.auth.admin.update_user_by_id.assert_called_once()
    args, kwargs = fake_admin.auth.admin.update_user_by_id.call_args
    assert args[0] == sample_admin_profile["id"]
    assert args[1] == {"password": "NewHealthy1!"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_me.py -v -k password`
Expected: 3 tests FAIL — endpoint doesn't exist yet.

- [ ] **Step 3: Implement the endpoint**

Add a new helper + endpoint to `api/index.py` (anywhere after `me`):

```python
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
```

If `BaseModel` isn't already imported at the top of the file, add it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_me.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_api_me.py
git commit -m "feat(auth): self-service POST /api/me/password"
```

---

### Task 8: Validate password in bootstrap startup hook

**Files:**
- Modify: `api/index.py` (`bootstrap_admin_on_startup`)

- [ ] **Step 1: Add validation call**

Find the existing `bootstrap_admin_on_startup` function. Add the validation step early in the function:

```python
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
```

- [ ] **Step 2: Verify the rest of the suite still passes**

Run: `pytest -q`
Expected: same number of tests passing as before; no regression.

- [ ] **Step 3: Commit**

```bash
git add api/index.py
git commit -m "feat(auth): validate BOOTSTRAP_ADMIN_PASSWORD complexity at startup"
```

---

### Task 9: Frontend types + extend `User` with `is_bootstrap_admin`

**Files:**
- Modify: `web/lib/types.ts`

- [ ] **Step 1: Update the `User` interface**

Open `web/lib/types.ts`. Find:

```typescript
export interface User {
  id: string
  email: string
  name: string | null
  role: "admin" | "rep"
  created_at?: string
}
```

Replace with:

```typescript
export interface User {
  id: string
  email: string
  name: string | null
  role: "admin" | "sdr"
  created_at?: string
  is_bootstrap_admin?: boolean
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: errors only at the call sites that compare `role === "rep"` — those will be fixed in Task 12. If the only error is in `web/app/admin/users/page.tsx`, that's expected.

- [ ] **Step 3: Commit (with Task 12) — do NOT commit yet**

We'll commit together with the admin page changes since they're a typecheck-clean unit.

---

### Task 10: `changeMyPassword` API helper

**Files:**
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Append helper**

Open `web/lib/api.ts` and append at the end:

```typescript
export async function changeMyPassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>("/api/me/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add web/lib/api.ts
git commit -m "feat(auth): add changeMyPassword frontend helper"
```

---

### Task 11: `ChangePasswordModal` component

**Files:**
- Create: `web/components/change-password-modal.tsx`

- [ ] **Step 1: Create the component**

Create `web/components/change-password-modal.tsx`:

```tsx
"use client"

import { useState } from "react"
import { Loader2, X, Check } from "lucide-react"
import { changeMyPassword } from "@/lib/api"

interface ChangePasswordModalProps {
  open: boolean
  onClose: () => void
}

const RULES: { label: string; test: (pw: string) => boolean }[] = [
  { label: "At least 8 characters", test: (pw) => pw.length >= 8 },
  { label: "One uppercase letter", test: (pw) => /[A-Z]/.test(pw) },
  { label: "One lowercase letter", test: (pw) => /[a-z]/.test(pw) },
  { label: "One number", test: (pw) => /\d/.test(pw) },
  { label: "One special character", test: (pw) => /[^A-Za-z0-9]/.test(pw) },
]

export default function ChangePasswordModal({ open, onClose }: ChangePasswordModalProps) {
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  if (!open) return null

  const allRulesPassed = RULES.every((r) => r.test(next))
  const matches = next.length > 0 && next === confirm
  const canSubmit = current.length > 0 && allRulesPassed && matches && !submitting

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      await changeMyPassword(current, next)
      setSuccess(true)
      setTimeout(() => {
        setCurrent("")
        setNext("")
        setConfirm("")
        setSuccess(false)
        onClose()
      }, 1200)
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      if (message.includes("401")) setError("Current password is incorrect.")
      else if (message.includes("400")) setError("New password doesn't meet the requirements.")
      else setError("Couldn't save — try again.")
    } finally {
      setSubmitting(false)
    }
  }

  function handleClose() {
    if (submitting) return
    setCurrent("")
    setNext("")
    setConfirm("")
    setError(null)
    setSuccess(false)
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={handleClose}
    >
      <div
        className="w-full max-w-md rounded-xl bg-white shadow-xl p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="font-serif text-base font-bold text-gray-900">Change password</h3>
          <button onClick={handleClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-3">
          <label className="block">
            <span className="text-xs text-gray-500">Current password</span>
            <input
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              disabled={submitting}
              className="w-full text-sm mt-1 rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
            />
          </label>

          <label className="block">
            <span className="text-xs text-gray-500">New password</span>
            <input
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              disabled={submitting}
              className="w-full text-sm mt-1 rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
            />
          </label>

          <ul className="space-y-1 pl-1">
            {RULES.map((r) => {
              const pass = r.test(next)
              return (
                <li key={r.label} className="flex items-center gap-1.5 text-xs">
                  <Check className={`w-3 h-3 ${pass ? "text-teal-600" : "text-gray-300"}`} />
                  <span className={pass ? "text-gray-700" : "text-gray-400"}>{r.label}</span>
                </li>
              )
            })}
          </ul>

          <label className="block">
            <span className="text-xs text-gray-500">Confirm new password</span>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={submitting}
              className="w-full text-sm mt-1 rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
            />
            {confirm.length > 0 && !matches && (
              <span className="text-[11px] text-rose-600">Passwords don't match.</span>
            )}
          </label>

          {error && <p className="text-xs text-rose-600">{error}</p>}
          {success && <p className="text-xs text-teal-700">Password updated.</p>}
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={handleClose}
            disabled={submitting}
            className="text-xs px-4 py-2 rounded-lg text-gray-700 hover:bg-gray-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="inline-flex items-center gap-1 text-xs px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50"
          >
            {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
            {submitting ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors from the new file.

- [ ] **Step 3: Commit**

```bash
git add web/components/change-password-modal.tsx
git commit -m "feat(auth): add ChangePasswordModal with live complexity hints"
```

---

### Task 12: Wire modal into user menu

**Files:**
- Modify: `web/components/user-menu.tsx`

- [ ] **Step 1: Add modal trigger to the menu**

Open `web/components/user-menu.tsx`. Replace the file contents:

```tsx
"use client"

import { useState } from "react"
import Link from "next/link"
import { LogOut, UserCog, KeyRound } from "lucide-react"
import { useAuth } from "@/lib/auth"
import ChangePasswordModal from "./change-password-modal"

export default function UserMenu() {
  const { user, loading, signOut } = useAuth()
  const [pwOpen, setPwOpen] = useState(false)

  if (loading || !user) return null

  return (
    <div className="flex items-center gap-2">
      {user.role === "admin" && (
        <Link
          href="/admin/users"
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg
                     border border-gray-300 text-gray-700 hover:bg-gray-50 transition"
        >
          <UserCog className="w-3.5 h-3.5" /> Users
        </Link>
      )}
      <button
        onClick={() => setPwOpen(true)}
        title="Change password"
        className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg
                   border border-gray-300 text-gray-700 hover:bg-gray-50 transition"
      >
        <KeyRound className="w-3.5 h-3.5" /> Password
      </button>
      <div className="flex items-center gap-2 text-sm">
        <div className="w-7 h-7 rounded-full bg-teal-600 text-white grid place-items-center text-xs font-semibold">
          {(user.name?.[0] ?? user.email[0]).toUpperCase()}
        </div>
        <span className="text-gray-700 max-w-[120px] truncate">
          {user.name ?? user.email}
        </span>
      </div>
      <button
        onClick={signOut}
        title="Sign out"
        className="p-1.5 rounded-lg text-gray-500 hover:text-gray-900 hover:bg-gray-100 transition"
      >
        <LogOut className="w-4 h-4" />
      </button>

      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/components/user-menu.tsx
git commit -m "feat(auth): user menu opens ChangePasswordModal"
```

---

### Task 13: Admin Users page — role rename + reset-password button

**Files:**
- Modify: `web/app/admin/users/page.tsx`
- Modify: `web/lib/types.ts` (already done in Task 9 — verify no leftover errors)

- [ ] **Step 1: Update local types + form defaults + role labels**

Open `web/app/admin/users/page.tsx`. Make these changes:

a) Update the local `AdminUser` interface (around line 8):
```typescript
interface AdminUser {
  id: string
  email: string
  name: string | null
  role: "admin" | "sdr"
  created_at: string
  practices_touched: number
}
```

b) Update the `useState` initial form values (around line 23):
```typescript
const [form, setForm] = useState({ email: "", name: "", password: "", role: "sdr" })
```

c) Update the form-reset call inside `handleCreate` (around line 61):
```typescript
setForm({ email: "", name: "", password: "", role: "sdr" })
```

d) Update the create-section heading (around line 103):
```tsx
<h2 className="font-serif text-xl font-bold mb-4">Create user</h2>
```

e) Update the role select option (around line 134):
```tsx
<option value="sdr">SDR</option>
<option value="admin">Admin</option>
```

f) Update the role display in the user list (the cell that has `capitalize`, around line 170):
```tsx
<td className="p-3 uppercase">{u.role}</td>
```

(Keep `uppercase` — both `"admin"` → `ADMIN` and `"sdr"` → `SDR` look fine. Future-proofs if more role values are added.)

- [ ] **Step 2: Add reset-password button + handler**

Add this state + handler to the component (alongside the existing `handleDelete`):

```typescript
const [resetTarget, setResetTarget] = useState<AdminUser | null>(null)
const [resetPassword, setResetPassword] = useState("")
const [resetting, setResetting] = useState(false)

async function handleReset(target: AdminUser) {
  if (!resetPassword) return
  setResetting(true)
  setError(null)
  try {
    const res = await fetch(`${API_URL}/api/admin/users/${target.id}/reset-password`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_password: resetPassword }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail ?? `HTTP ${res.status}`)
    }
    setResetTarget(null)
    setResetPassword("")
  } catch (e) {
    setError(e instanceof Error ? e.message : String(e))
  } finally {
    setResetting(false)
  }
}
```

Update the table row's actions cell (around line 173) to include a Reset button **before** the Delete:

```tsx
<td className="p-3 text-right space-x-2">
  {u.id !== user.id && (
    <>
      {(() => {
        const blocked = u.role === "admin" && !user.is_bootstrap_admin
        return (
          <button
            onClick={() => !blocked && setResetTarget(u)}
            disabled={blocked}
            title={blocked ? "Only the bootstrap admin can reset another admin's password." : "Reset password"}
            className={`text-xs underline ${blocked ? "text-gray-300 cursor-not-allowed" : "text-teal-700 hover:text-teal-900"}`}
          >
            Reset password
          </button>
        )
      })()}
      <button
        onClick={() => handleDelete(u.id)}
        className="text-rose-600 hover:text-rose-800"
        title="Delete user"
      >
        <Trash2 className="w-4 h-4" />
      </button>
    </>
  )}
</td>
```

- [ ] **Step 3: Add the reset password modal block**

At the bottom of the `<main>` block (just before its closing tag), add a small inline modal for entering the new password:

```tsx
{resetTarget && (
  <div
    className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    onClick={() => !resetting && setResetTarget(null)}
  >
    <div
      className="w-full max-w-sm rounded-xl bg-white shadow-xl p-5 space-y-3"
      onClick={(e) => e.stopPropagation()}
    >
      <h3 className="font-serif text-base font-bold">
        Reset password for {resetTarget.email}
      </h3>
      <input
        type="text"
        value={resetPassword}
        onChange={(e) => setResetPassword(e.target.value)}
        placeholder="New password"
        className="w-full text-sm rounded-lg border border-gray-200 px-3 py-2"
      />
      <p className="text-[11px] text-gray-500">
        Min 8 chars · 1 upper · 1 lower · 1 number · 1 special.
      </p>
      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={() => setResetTarget(null)}
          disabled={resetting}
          className="text-xs px-3 py-1.5 rounded-lg text-gray-700 hover:bg-gray-100 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          onClick={() => handleReset(resetTarget)}
          disabled={resetting || !resetPassword}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50"
        >
          {resetting && <Loader2 className="w-3 h-3 animate-spin" />}
          {resetting ? "Saving..." : "Save"}
        </button>
      </div>
    </div>
  </div>
)}
```

- [ ] **Step 4: Typecheck the whole frontend**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit Tasks 9, 12, and 13 together**

```bash
git add web/lib/types.ts web/components/user-menu.tsx web/app/admin/users/page.tsx
git commit -m "feat(auth): admin Users page reset button + role rename + bootstrap gating"
```

---

### Task 14: Apply DB migration

**Files:** none — manual Supabase SQL.

- [ ] **Step 1: Run migration in Supabase SQL editor**

Paste and run:

```sql
update profiles set role = 'sdr' where role = 'rep';
```

- [ ] **Step 2: Verify in Supabase Table editor**

Open the `profiles` table. Check that no rows have `role = 'rep'`. Existing admins (`role = 'admin'`) are untouched.

- [ ] **Step 3: Confirm with the user**

Tell the user: migration applied. Then proceed to Task 15.

---

### Task 15: E2E smoke test

**Files:** none — manual verification.

- [ ] **Step 1: Run full backend suite**

Run: `pytest -q`
Expected: all tests pass (≥ 95 tests, depending on prior count).

- [ ] **Step 2: Frontend typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Restart backend (env may have changed)**

Run: `uvicorn api.index:app --reload --port 8000`
Expected: startup completes; if `BOOTSTRAP_ADMIN_PASSWORD` is non-compliant, look for `[bootstrap] BOOTSTRAP_ADMIN_PASSWORD invalid: ... — admin not seeded.` log line.

- [ ] **Step 4: Smoke — Change password as any user**

1. Sign in.
2. Click the **Password** button in the user menu.
3. Enter wrong current password → expect inline `"Current password is incorrect."`.
4. Enter correct current + a weak new password → confirm hints show ✗ for failing rules → Save button stays disabled.
5. Enter correct current + compliant new + matching confirm → Save → modal shows "Password updated." → closes.
6. Sign out → sign in with the new password → success.

- [ ] **Step 5: Smoke — Admin creates user**

1. As admin, go to **/admin/users**.
2. Try creating with `email: foo@example.com` → expect 400 `"Email must be a @apexvirtuals.com address."` shown inline.
3. Try `email: a--b@apexvirtuals.com` → 400 `"--"` message.
4. Try `email: legit@apexvirtuals.com`, `password: weak` → 400 password message.
5. Submit a compliant user → row appears, role displayed as `SDR`.

- [ ] **Step 6: Smoke — Cross-admin reset gating**

1. Sign in as a non-bootstrap admin (or create one if none exists).
2. On `/admin/users`, find another admin's row → confirm Reset button is disabled with tooltip.
3. Find an SDR row → click Reset → enter compliant password → confirm 200 and they can sign in with the new password.
4. Sign in as bootstrap admin (the one matching `BOOTSTRAP_ADMIN_EMAIL`).
5. On the same page, the Reset button on other admin rows is now enabled. Reset succeeds.

- [ ] **Step 7: Final commit (if any cleanup needed)**

If smoke testing surfaced minor issues that needed fixes, commit them with descriptive messages. Otherwise:

```bash
git log --oneline -20
```

Should show ~13–14 clean commits from this plan. Feature complete.

---

## Self-review

**Spec coverage:**
- `validate_email` (domain, no `--`, format) → Task 1.
- `validate_password` (5 rules) → Task 1.
- `is_bootstrap_admin` helper → Task 2.
- `/api/me` returns flag → Task 3.
- Wire validators into `create_user` + duplicate map → Task 4.
- Role default + valid set + conftest rename → Task 5.
- Tighten `reset_password` (validate + cross-admin gate) → Task 6.
- `/api/me/password` (verify current + validate new) → Task 7.
- Bootstrap startup password validation → Task 8.
- Frontend `User.role` + `is_bootstrap_admin` → Task 9.
- `changeMyPassword` helper → Task 10.
- `ChangePasswordModal` → Task 11.
- User-menu wiring → Task 12.
- Admin Users page (role rename + reset-button + bootstrap gating) → Task 13.
- DB migration → Task 14.
- E2E smoke → Task 15.

Every spec section has a task. No gaps.

**Placeholder scan:** No TBDs. Every code block has real code; every test has assertions; every commit command is exact.

**Type consistency:**
- `User.role: "admin" | "sdr"` defined in Task 9, consumed by Tasks 12 (`user.role === "admin"`) and 13 (`AdminUser.role`, `u.role === "admin"`).
- `User.is_bootstrap_admin?: boolean` defined in Task 9, consumed by Task 13's `user.is_bootstrap_admin` check.
- `ChangePasswordRequest` (backend) defined in Task 7, matches frontend `changeMyPassword` body (Task 10): `{current_password, new_password}`.
- `validate_email`, `validate_password` signatures (Task 1) match every call site (Tasks 4, 6, 7, 8).
- `is_bootstrap_admin(user)` signature (Task 2) matches call sites in `/api/me` (Task 3) and `reset_password` (Task 6).
- `sample_sdr_profile` fixture (Task 5) replaces every `sample_rep_profile` reference (Task 5 step 3).

All consistent.
