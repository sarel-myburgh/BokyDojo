"""Startup guards — TODO 0.1.4, SEC 2.4.

The application must refuse to start rather than run in a knowingly unsafe
configuration. Silent insecure defaults are how small deployments get breached;
a loud failure at boot is always cheaper than a quiet compromise.
"""

from __future__ import annotations

INSECURE_SECRET_KEYS = {
    "",
    "insecure-development-key-do-not-use-in-production",
    "test-only-key",
    "changeme",
    "secret",
    "django-insecure",
}

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeConfiguration(RuntimeError):
    """Raised at import time when production settings are unsafe."""


def _binds_beyond_loopback(allowed_hosts: list[str]) -> bool:
    """True if ALLOWED_HOSTS admits anything that is not plainly loopback."""
    for host in allowed_hosts:
        normalised = host.strip().lower()
        if not normalised:
            continue
        if normalised not in LOOPBACK_HOSTS:
            return True
    return False


def assert_safe_production_config(
    *,
    secret_key: str,
    debug: bool,
    allowed_hosts: list[str],
    field_encryption_keys: str | None = None,
    shared_cache_url: str | None = None,
    mfa_enforced: bool | None = None,
) -> None:
    problems: list[str] = []

    # ⚠ dev.py turns enforcement off so a demo login does not need an
    # authenticator app. That switch must never reach a real deployment: without
    # it, every privileged account is a password away from the medical and
    # safeguarding data, which is precisely what 0.6.2 exists to prevent.
    if mfa_enforced is not None and not mfa_enforced:
        problems.append(
            "MFA_ENFORCEMENT_ENABLED is off. Multi-factor authentication is "
            "mandatory for privileged roles (SEC 2.4); this setting exists for "
            "local testing only."
        )

    # Rate limiting and lockout state live in the cache. With per-process
    # LocMemCache and multiple Gunicorn workers, failures split across workers
    # and no threshold is ever reached — the throttle becomes decorative.
    if shared_cache_url is not None and not shared_cache_url.strip():
        problems.append(
            "REDIS_URL is not set. Login lockouts would use a per-process cache "
            "while Gunicorn runs several workers, so failed attempts would split "
            "across processes and never reach a lockout threshold."
        )

    # TODO 0.3.8 / SEC 2.3 — without this, medical and safeguarding data would be
    # written in plaintext. Fail at boot rather than discover it in a breach.
    if field_encryption_keys is not None and not field_encryption_keys.strip():
        problems.append(
            "DJANGO_FIELD_ENCRYPTION_KEYS is not set. Medical and safeguarding data "
            "cannot be encrypted without it. Generate one with:\n"
            "  python -c \"import base64,os;print('1:'+base64.urlsafe_b64encode("
            'os.urandom(32)).decode())"'
        )

    key = (secret_key or "").strip()
    if key in INSECURE_SECRET_KEYS or key.startswith("django-insecure"):
        problems.append(
            "DJANGO_SECRET_KEY is unset or a known placeholder. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
    elif len(key) < 50:
        problems.append("DJANGO_SECRET_KEY is shorter than 50 characters.")

    if debug and _binds_beyond_loopback(allowed_hosts):
        problems.append(
            "DJANGO_DEBUG is enabled while ALLOWED_HOSTS admits non-loopback hosts "
            f"({', '.join(allowed_hosts)}). Debug mode leaks settings, stack traces and "
            "SQL. Either disable DEBUG or restrict ALLOWED_HOSTS to loopback."
        )

    if not allowed_hosts:
        problems.append("DJANGO_ALLOWED_HOSTS is empty. Set the hostnames this instance serves.")

    if "*" in allowed_hosts:
        problems.append("DJANGO_ALLOWED_HOSTS contains '*'. Name the hosts explicitly.")

    if problems:
        raise UnsafeConfiguration(
            "Refusing to start — unsafe production configuration:\n\n  - " + "\n  - ".join(problems)
        )
