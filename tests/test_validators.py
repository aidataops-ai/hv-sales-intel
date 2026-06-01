import pytest

from src.validators import validate_email, validate_password


# ---------- email ----------

def test_validate_email_accepts_basic_company_address():
    validate_email("sarah@apexvirtuals.com")


def test_validate_email_accepts_dots_and_plus():
    validate_email("sarah.khan+team@apexvirtuals.com")


def test_validate_email_accepts_uppercase_local_part():
    validate_email("Sarah.Khan@apexvirtuals.com")


def test_validate_email_accepts_any_domain():
    # Domain allowlist was removed for the demo — any well-formed
    # address must be accepted regardless of domain.
    validate_email("sarah@anywhere.com")
    validate_email("sarah@example.org")
    validate_email("sarah@apexvirtuals.com")


def test_validate_email_rejects_missing_at():
    with pytest.raises(ValueError, match="format"):
        validate_email("sarahapexvirtuals.com")


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


# ---------- is_bootstrap_admin ----------

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
