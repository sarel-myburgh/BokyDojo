"""Create a Django superuser from environment variables — first-run convenience.

Idempotent: if a user with DJANGO_SUPERUSER_EMAIL already exists, do nothing.

Environment:
  DJANGO_SUPERUSER_EMAIL
  DJANGO_SUPERUSER_PASSWORD
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a superuser from DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD."

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")

        if not email:
            raise CommandError("DJANGO_SUPERUSER_EMAIL is not set")
        if not password:
            raise CommandError("DJANGO_SUPERUSER_PASSWORD is not set")

        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            self.stdout.write(self.style.WARNING(f"Superuser {email} already exists — skipped."))
            return

        User.objects.create_superuser(email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Superuser {email} created."))
