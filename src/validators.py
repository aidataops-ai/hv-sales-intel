import re

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def validate_email(email: str) -> None:
    """Raise ValueError if email is malformed or contains '--'.

    Domain allowlist was removed for the demo — any well-formed email
    is accepted. Re-add ALLOWED_DOMAINS here if you need to lock new
    accounts to a corporate domain again.
    """
    if not email or not EMAIL_REGEX.match(email):
        raise ValueError("Email format is invalid.")
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
