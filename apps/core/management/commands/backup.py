from django.core.management.base import BaseCommand

from apps.core.backups import create_backup


class Command(BaseCommand):
    help = "Create a PostgreSQL and media backup archive."

    def add_arguments(self, parser):
        parser.add_argument("destination", nargs="?")

    def handle(self, *args, **options):
        path = create_backup(options["destination"])
        self.stdout.write(self.style.SUCCESS(f"Backup created: {path}"))
