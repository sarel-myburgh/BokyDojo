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
) -> None:
    problems: list[str] = []

    key = (secret_key or "").strip()
    if key in INSECURE_SECRET_KEYS or key.startswith("django-insecure"):
        problems.append(
            "DJANGO_SECRET_KEY is unset or a known placeholder. Generate one with:\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(64))\""
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
            "Refusing to start — unsafe production configuration:\n\n  - "
            + "\n  - ".join(problems)
        )
