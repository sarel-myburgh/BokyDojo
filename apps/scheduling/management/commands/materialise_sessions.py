"""Top up materialised class sessions — TODO 1.4.2.

Run nightly. Safe to run repeatedly and safe to run twice concurrently: existing
occurrences are left alone.

    python manage.py materialise_sessions
    python manage.py materialise_sessions --horizon-days 30 --dojo ppka-central
"""

from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo
from apps.scheduling.materialise import DEFAULT_HORIZON_DAYS, materialise_sessions


class Command(BaseCommand):
    help = "Materialise ClassSessions from templates on a rolling horizon."

    def add_arguments(self, parser):
        parser.add_argument(
            "--horizon-days",
            type=int,
            default=DEFAULT_HORIZON_DAYS,
            help=f"How far ahead to generate (default {DEFAULT_HORIZON_DAYS}).",
        )
        parser.add_argument(
            "--dojo",
            help="Limit to one dojo, by slug.",
        )
        parser.add_argument(
            "--today",
            help="Treat this ISO date as today. For backfills and testing.",
        )

    def handle(self, *args, **options):
        today = None
        if options["today"]:
            try:
                today = datetime.date.fromisoformat(options["today"])
            except ValueError as exc:
                raise CommandError(f"--today must be an ISO date: {exc}") from exc

        dojo = None
        if options["dojo"]:
            # A management command legitimately runs across tenants; the slug is
            # an operator argument, not request input.
            with allow_unscoped("management command resolving a dojo by slug"):
                dojo = Dojo.objects.filter(slug=options["dojo"]).first()
            if dojo is None:
                raise CommandError(f"No dojo with slug {options['dojo']!r}")

        result = materialise_sessions(
            actor=Actor.system(),
            horizon_days=options["horizon_days"],
            today=today,
            dojo=dojo,
        )

        for problem in result.errors:
            self.stderr.write(self.style.WARNING(f"skipped: {problem}"))
        self.stdout.write(self.style.SUCCESS(str(result)))
