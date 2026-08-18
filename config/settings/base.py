"""Base settings. All configuration comes from the environment.

See TODO.md 0.1.2. Environment-specific overrides live in dev.py / prod.py / test.py.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(key: str, default=None, *, required: bool = False) -> str | None:
    value = os.environ.get(key, default)
    if required and not value:
        raise RuntimeError(f"Required environment variable {key} is not set")
    return value


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(key, default).split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-development-key-do-not-use-in-production")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.identity",
    "apps.ranks",
    "apps.scheduling",
    "apps.staffing",
    "apps.attendance",
    "apps.imports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.csp.ContentSecurityPolicyMiddleware",
    # Must follow AuthenticationMiddleware, and precede the audit context so the
    # Actor is built from a session that has already been checked for expiry.
    "apps.core.sessions.SessionTimeoutMiddleware",
    # Enforce TOTP before privileged sessions can reach application or admin views.
    "apps.identity.middleware.MfaEnforcementMiddleware",
    # ⚠ After the MFA middleware, so somebody signing in with a temporary
    # password who also holds a TOTP credential finishes the second factor before
    # being sent to choose a password. Before the audit context is fine either
    # way; it only ever redirects.
    "apps.identity.middleware.PasswordChangeRequiredMiddleware",
    # Must follow AuthenticationMiddleware — it derives the Actor from request.user.
    "apps.core.audit.AuditContextMiddleware",
    # Must follow the audit context — it reads request.actor to decide which
    # timezone to render this request's dates in.
    "apps.core.timezones.ActiveTimezoneMiddleware",
    # While a check-in is running the device may be in a student's hands, so this
    # session may reach nothing but the kiosk. Must follow authentication; it is
    # about who holds the phone, which no permission check can answer.
    "apps.attendance.kiosk.KioskLockMiddleware",
    # Turns a refused action into a 403 instead of a 500. Last, so it sees
    # exceptions from everything above it.
    "apps.core.http.PermissionDeniedMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.csp.csp_nonce",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "dojomaster"),
        "USER": env("POSTGRES_USER", "dojomaster"),
        "PASSWORD": env("POSTGRES_PASSWORD", "dojomaster"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }
}

# ⚠ Lockout counters live in the cache. Django's default LocMemCache is
# per-process, and we run Gunicorn with four workers — five failed logins spread
# 2/1/1/1 across workers would never reach a threshold, and a lockout on one
# worker would not exist on the next. Found in adversarial review. Redis is
# already in docker-compose; wire it here or the throttle is decorative.
_REDIS_URL = env("REDIS_URL", "")
CACHES = {
    "default": (
        {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _REDIS_URL,
        }
        if _REDIS_URL
        else {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dojomaster-local",
        }
    )
}

AUTH_USER_MODEL = "identity.User"

# Field-level encryption master keys — TODO 0.3.8, SEC 2.3.
# Format: "1:<base64 32 bytes>", comma-separated for rotation. NEVER in the repo.
FIELD_ENCRYPTION_KEYS = env("DJANGO_FIELD_ENCRYPTION_KEYS", "")

# Argon2id first — TODO 0.6.1, SEC 2.1
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# i18n — TODO 0.4.x, plan 13.4. Chinese variant is zh-Hans (decision D6).
LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("km", "ភាសាខ្មែរ"),
    ("zh-hans", "简体中文"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
USE_TZ = True
TIME_ZONE = "UTC"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# ⚠ Without this the project-level static/ tree is invisible to the finders —
# no app ships its own static dir, so every asset 404s: the stylesheets, the
# PWA service worker, and the offline attendance queue. The pages still render,
# which is why the test suite never noticed.
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Session hardening — TODO 0.6.4
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 60 * 60 * 12

# Enforced by apps.core.sessions.SessionTimeoutMiddleware. Two limits, because
# idle timeout alone does not bound a stolen cookie that keeps being used.
# 90 minutes covers a class plus the admin afterwards; a shared tablet left on a
# bench does not stay signed in all evening.
SESSION_IDLE_TIMEOUT_SECONDS = int(env("DJANGO_SESSION_IDLE_TIMEOUT", 90 * 60))
SESSION_ABSOLUTE_TIMEOUT_SECONDS = int(env("DJANGO_SESSION_ABSOLUTE_TIMEOUT", 60 * 60 * 12))

FIRST_RUN_SETUP_TOKEN = env("DJANGO_FIRST_RUN_TOKEN", "")
DEMO_SEED_ENABLED = False
MFA_ENFORCEMENT_ENABLED = True
PASSWORD_RESET_TIMEOUT = 30 * 60
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "DojoMaster <noreply@localhost>")

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "today"
LOGOUT_REDIRECT_URL = "login"

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# Structured JSON logging to stdout — TODO 0.2.5
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}
