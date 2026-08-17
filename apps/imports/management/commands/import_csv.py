"""Run an import from the command line — TODO 1.10.1/1.10.3.

The web wizard is the operator-facing path. This exists because the first real
import is a migration job done by whoever is onboarding the dojo, often against a
file that needs three attempts, and doing that through a browser upload is slower
and leaves less of a trail.

⚠ Defaults to a dry run. Writing requires ``--commit``, spelled out, because the
whole point of the dry run is that somebody looks at it first.
"""

from __future__ import annotations

import json
import pathlib

from django.core.management.base import BaseCommand, CommandError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo, Person, RoleAssignment
from apps.imports import csv_source, engine
from apps.imports.attendance import AttendanceImporter, require_attendance_import_permission
from apps.imports.ranks import RankImporter, require_rank_import_permission
from apps.imports.students import StudentImporter, require_import_permission

IMPORTERS = {
    "students": (StudentImporter, require_import_permission),
    "attendance": (AttendanceImporter, require_attendance_import_permission),
    "ranks": (RankImporter, require_rank_import_permission),
}


class Command(BaseCommand):
    help = "Import a CSV of students and guardians into a dojo."

    def add_arguments(self, parser):
        parser.add_argument("path", help="CSV file to import")
        parser.add_argument("--dojo", required=True, help="Dojo slug to import into")
        parser.add_argument(
            "--as-person",
            required=True,
            help="Person id to act as. Permissions are checked against them, not bypassed.",
        )
        parser.add_argument("--kind", default="students", choices=sorted(IMPORTERS))
        parser.add_argument(
            "--map",
            required=True,
            help='JSON of {"CSV column": "field"}, or a path to a .json file containing it',
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually write. Without this the run is a dry run and is rolled back.",
        )
        parser.add_argument("--report", help="Write the per-row report to this CSV path")

    def handle(self, *args, **options):
        path = pathlib.Path(options["path"])
        if not path.is_file():
            raise CommandError(f"No such file: {path}")

        mapping_argument = options["map"]
        mapping_path = pathlib.Path(mapping_argument)
        raw_mapping = (
            mapping_path.read_text(encoding="utf-8")
            if mapping_path.suffix == ".json" and mapping_path.is_file()
            else mapping_argument
        )
        try:
            mapping = json.loads(raw_mapping)
        except json.JSONDecodeError as exc:
            raise CommandError(f"--map is not valid JSON: {exc}") from exc

        # ⚠ Acting *as* a real person, not as the system actor. An import is bulk
        # person creation and must be attributable and permission-checked; a
        # command that quietly ran unscoped would be the widest hole in the app.
        with allow_unscoped("resolving the acting person for an import"):
            person = Person.objects.filter(pk=options["as_person"]).first()
            if person is None:
                raise CommandError("No such person")
            assignments = list(RoleAssignment.objects.filter(person=person))
            dojo = (
                Dojo.objects.select_related("organization")
                .filter(slug=options["dojo"], organization_id=person.organization_id)
                .first()
            )
        if dojo is None:
            raise CommandError("No such dojo in that person's organisation")

        actor = Actor(
            user_id=None,
            person_id=person.pk,
            organization_id=person.organization_id,
            dojo_ids=None
            if any(a.scope_type == "org" for a in assignments)
            else frozenset(a.dojo_id for a in assignments if a.dojo_id),
            roles=frozenset((a.role, a.scope_type, a.dojo_id) for a in assignments),
        )
        importer_class, permission_check = IMPORTERS[options["kind"]]
        permission_check(actor, dojo)

        headers, rows = csv_source.read_table(path.read_bytes())
        self.stdout.write(f"{len(rows)} data row(s), columns: {', '.join(headers)}")

        import_run = engine.run(
            importer=importer_class(),
            rows=rows,
            mapping=mapping,
            actor=actor,
            dojo=dojo,
            filename=path.name,
            dry_run=not options["commit"],
        )

        mode = "DRY RUN (nothing written)" if import_run.is_dry_run else "COMMITTED"
        self.stdout.write(
            f"{mode}: {import_run.created_count} created, "
            f"{import_run.updated_count} updated, "
            f"{import_run.skipped_count} skipped, "
            f"{import_run.error_count} errored"
        )
        for outcome in import_run.outcomes:
            if outcome["outcome"] in ("error", "skipped"):
                self.stdout.write(
                    f"  row {outcome['row_number']}: {outcome['outcome']} — {outcome['detail']}"
                )

        if options["report"]:
            import csv as csv_module

            with open(options["report"], "w", newline="", encoding="utf-8") as handle:
                writer = csv_module.writer(handle)
                writer.writerow(["Row", "Outcome", "Source key", "Detail"])
                writer.writerows(engine.report_rows(import_run))
            self.stdout.write(f"Report written to {options['report']}")

        if import_run.is_dry_run:
            self.stdout.write(self.style.WARNING("Re-run with --commit to write."))
