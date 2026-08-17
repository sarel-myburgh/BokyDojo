"""Development settings.

Runs with **no infrastructure at all** by default: SQLite, local-memory cache,
console email. `python manage.py runserver` works on a clean checkout with no
Docker, no Postgres and no `.env`.

That matters more than it sounds. A demo should be five minutes from clone to
clicking around; one that needs a database container first is a demo that
mostly does not get given.

Set `POSTGRES_HOST` to use Postgres instead — docker compose does exactly that,
so the containerised path is unchanged.

⚠ Never use these settings for anything real. Production is
`config/settings/prod.py`, which refuses to start without proper keys.
"""

from .base import *  # noqa: F403
from .base import BASE_DIR, env, env_bool, env_list

DEBUG = env_bool("DJANGO_DEBUG", True)
# ⚠ The loopback names, plus anything DJANGO_ALLOWED_HOSTS adds. The kiosk
# (1.7) is a phone feature and cannot be exercised on localhost — testing it
# means reaching this server from a handset on the same wifi, which needs the
# machine's LAN address allowed. Additive rather than replacing, so forgetting
# the variable cannot lock you out of 127.0.0.1.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"] + env_list(  # nosec B104
    "DJANGO_ALLOWED_HOSTS"
)

# Same reason: a POST from http://<lan-ip>:8080 is same-origin, but being
# explicit costs nothing and saves a confusing 403 on the check-in screen.
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# SQLite unless a Postgres host is named. Zero first-run friction, container
# path untouched.
if not env("POSTGRES_HOST", ""):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "dev.sqlite3",
        }
    }

# A fixed development key so encrypted fields survive a restart. Obviously not
# a secret — production refuses to boot without a real one of its own.
if not env("DJANGO_FIELD_ENCRYPTION_KEYS", ""):
    FIELD_ENCRYPTION_KEYS = "1:ZG9qb21hc3Rlci1kZXYta2V5LW5vdC1hLXNlY3JldCE="

DEMO_SEED_ENABLED = True

# ⚠ Local testing only, and production cannot inherit this: prod.py passes the
# flag to assert_safe_production_config, which refuses to boot when it is off.
#
# Turned off so a demo sign-in does not require enrolling an authenticator app
# first — the org and dojo admin accounts are otherwise held at the TOTP setup
# screen and cannot reach anything. The control itself (0.6.2) is unchanged and
# still fully tested; this only stops the *enforcement* middleware in dev.
MFA_ENFORCEMENT_ENABLED = env_bool("DJANGO_MFA_ENFORCEMENT", False)
