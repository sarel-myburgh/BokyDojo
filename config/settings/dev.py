from .base import *  # noqa: F403
from .base import env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
