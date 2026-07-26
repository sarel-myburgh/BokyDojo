from .base import *  # noqa: F403
from .base import env, env_bool, env_list
from .guards import assert_safe_production_config

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# TODO 0.1.4 / SEC 2.4 — refuse to boot on an unsafe configuration.
assert_safe_production_config(
    secret_key=SECRET_KEY,
    debug=DEBUG,
    allowed_hosts=ALLOWED_HOSTS,
    field_encryption_keys=env("DJANGO_FIELD_ENCRYPTION_KEYS", ""),
)
