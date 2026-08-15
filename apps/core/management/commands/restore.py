from django.core.management.base import BaseCommand

from apps.core.backups import restore_backup


class Command(BaseCommand):
    help = "Restore a PostgreSQL and media backup archive."

    def add_arguments(self, parser):
        parser.add_argument("archive")
        parser.add_argument("--confirm-database", required=True)

    def handle(self, *args, **options):
        restore_backup(
            options["archive"],
            confirm_database=options["confirm_database"],
        )
        self.stdout.write(self.style.SUCCESS("Backup restored successfully."))
