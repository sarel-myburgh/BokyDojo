"""Production startup guards — TODO 0.1.4, SEC 2.4."""

import pytest

from config.settings.guards import UnsafeConfiguration, assert_safe_production_config

GOOD_KEY = "x" * 64


def test_accepts_a_safe_configuration():
    assert_safe_production_config(
        secret_key=GOOD_KEY, debug=False, allowed_hosts=["dojo.example.com"]
    )


def test_rejects_placeholder_secret_key():
    with pytest.raises(UnsafeConfiguration, match="SECRET_KEY"):
        assert_safe_production_config(
            secret_key="insecure-development-key-do-not-use-in-production",
            debug=False,
            allowed_hosts=["dojo.example.com"],
        )


def test_rejects_django_insecure_prefix():
    with pytest.raises(UnsafeConfiguration, match="SECRET_KEY"):
        assert_safe_production_config(
            secret_key="django-insecure-abc123", debug=False, allowed_hosts=["x.example.com"]
        )


def test_rejects_short_secret_key():
    with pytest.raises(UnsafeConfiguration, match="50 characters"):
        assert_safe_production_config(
            secret_key="short", debug=False, allowed_hosts=["x.example.com"]
        )


def test_rejects_debug_bound_beyond_loopback():
    with pytest.raises(UnsafeConfiguration, match="DEBUG"):
        assert_safe_production_config(
            secret_key=GOOD_KEY, debug=True, allowed_hosts=["dojo.example.com"]
        )


def test_permits_debug_on_loopback_only():
    assert_safe_production_config(
        secret_key=GOOD_KEY, debug=True, allowed_hosts=["localhost", "127.0.0.1"]
    )


def test_rejects_empty_allowed_hosts():
    with pytest.raises(UnsafeConfiguration, match="ALLOWED_HOSTS"):
        assert_safe_production_config(secret_key=GOOD_KEY, debug=False, allowed_hosts=[])


def test_rejects_wildcard_allowed_hosts():
    with pytest.raises(UnsafeConfiguration, match=r"\*"):
        assert_safe_production_config(secret_key=GOOD_KEY, debug=False, allowed_hosts=["*"])


def test_production_refuses_to_start_with_mfa_enforcement_off():
    """⚠ dev.py turns enforcement off so a demo login needs no authenticator app.

    That switch must never reach a real deployment: without it every privileged
    account is one password away from the medical and safeguarding data, which is
    exactly what 0.6.2 exists to prevent. The guard is the thing standing between
    "convenient for testing" and "shipped".
    """
    with pytest.raises(UnsafeConfiguration) as excinfo:
        assert_safe_production_config(
            secret_key="x" * 60,
            debug=False,
            allowed_hosts=["dojo.example.com"],
            mfa_enforced=False,
        )

    assert "MFA_ENFORCEMENT_ENABLED" in str(excinfo.value)


def test_production_is_happy_when_mfa_is_enforced():
    assert_safe_production_config(
        secret_key="x" * 60,
        debug=False,
        allowed_hosts=["dojo.example.com"],
        mfa_enforced=True,
    )
