from .base import *  # noqa: F403
from .base import BASE_DIR

DEBUG = False
SECRET_KEY = "test-only-key"

# Tests run on SQLite so the suite needs no external services. Anything relying on
# Postgres-specific behaviour must be marked and run against Postgres in CI.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test.sqlite3",
        "TEST": {"NAME": ":memory:"},
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Fixed test key. Never used anywhere real — production refuses to start without
# its own (see config/settings/guards.py).
FIELD_ENCRYPTION_KEYS = "1:ZG9qb21hc3Rlci10ZXN0LWtleS1kby1ub3QtdXNlISE="
