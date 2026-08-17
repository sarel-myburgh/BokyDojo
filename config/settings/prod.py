from .base import *  # noqa: F403
from .base import env, env_bool, env_list
from .guards import assert_safe_production_config

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
FIRST_RUN_SETUP_TOKEN = env("DJANGO_FIRST_RUN_TOKEN", required=True)

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("SMTP_HOST", required=True)
EMAIL_PORT = int(env("SMTP_PORT", "587"))
EMAIL_HOST_USER = env("SMTP_USER", "")
EMAIL_HOST_PASSWORD = env("SMTP_PASSWORD", "")
EMAIL_USE_TLS = env_bool("SMTP_USE_TLS", True)

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# TODO 0.1.4 / SEC 2.4 — refuse to boot on an unsafe configuration.
# Explicit rather than inherited, and read from the environment so that an
# attempt to switch it off is a *loud* boot failure from the guard below rather
# than a silent inheritance question.
MFA_ENFORCEMENT_ENABLED = env_bool("DJANGO_MFA_ENFORCEMENT", True)

assert_safe_production_config(
    secret_key=SECRET_KEY,
    debug=DEBUG,
    allowed_hosts=ALLOWED_HOSTS,
    field_encryption_keys=env("DJANGO_FIELD_ENCRYPTION_KEYS", ""),
    shared_cache_url=env("REDIS_URL", ""),
    mfa_enforced=MFA_ENFORCEMENT_ENABLED,
)
