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
from .base import BASE_DIR, env, env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]
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
