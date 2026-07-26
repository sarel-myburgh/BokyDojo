"""Reset the database to a clean seeded state — TODO 0.7.2.

Idempotent and safe to run on a cron schedule. Wipes all data and re-seeds.
Designed for the public demo instance (§12.13) and development environments.

Usage:
    python manage.py reset_demo
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reset the database to a clean seeded state (idempotent, safe to cron)."

    def handle(self, *args, **options):
        self.stdout.write("Resetting demo database...")

        # The seed command with --clear handles everything:
        # clearing existing data and re-seeding from scratch.
        call_command("seed", "--clear", verbosity=1)

        self.stdout.write(self.style.SUCCESS("Demo database reset complete."))
