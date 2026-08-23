import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        """Say once, at boot, what the security-relevant switches are set to.

        ⚠ Exists because "is MFA compulsory on this deployment?" has now been
        asked twice and could only be answered by reading the source and
        guessing at the environment. A container that behaves unexpectedly
        should be able to tell you why from its own logs.
        """
        from django.conf import settings

        from apps.core.version import display_version

        logger.info(
            "BokyDojo %s starting | MFA enforcement: %s | debug: %s",
            display_version(),
            "ON — privileged roles must enrol"
            if getattr(settings, "MFA_ENFORCEMENT_ENABLED", False)
            else "OFF — enrolment is encouraged, never required",
            getattr(settings, "DEBUG", False),
        )
