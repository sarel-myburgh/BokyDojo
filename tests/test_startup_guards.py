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
