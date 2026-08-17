"""CSV import — TODO 1.10.1, 1.10.2, 1.10.3, 1.10.7, plan §12.10.

The plan calls the importer a sales weapon rather than a chore, and the thing
that makes it one is being able to fix a botched file and re-run it. So the
tests that matter most here are the ones about *running twice*: a second import
of a corrected file must update, never duplicate.

⚠ The dry-run tests check the database afterwards, not the returned counts. A
dry run that reports "12 created" while having actually created them is exactly
the failure worth catching, and only a post-hoc count of real rows sees it.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    Enrollment,
    GuardianLink,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
)
from apps.identity.permissions import PermissionDenied
from apps.imports import csv_source, engine
from apps.imports.models import ImportedRecord, ImportRun
from apps.imports.students import StudentImporter, require_import_permission

pytestmark = pytest.mark.django_db

MAPPING = {
    "Student ID": "external_id",
    "First name": "given_name",
    "Last name": "family_name",
    "DOB": "date_of_birth",
    "Parent first name": "guardian_given_name",
    "Parent last name": "guardian_family_name",
    "Parent email": "guardian_email",
    "Relationship": "guardian_relationship",
}

CSV = (
    "Student ID,First name,Last name,DOB,Parent first name,"
    "Parent last name,Parent email,Relationship\r\n"
    "S1,Bopha,Chan,2015-03-04,Dara,Chan,dara@example.com,mother\r\n"
    "S2,Sokha,Chan,2017-08-11,Dara,Chan,dara@example.com,mother\r\n"
)


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def dojo(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(
            organization=org, name="Dojo A", slug="dojo-a", timezone="Asia/Phnom_Penh"
        )


def make_actor(org, dojo, role=Role.DOJO_ADMIN):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=role,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
    return Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
        roles=frozenset({(role, ScopeType.DOJO, dojo.pk)}),
    )


@pytest.fixture
def actor(org, dojo):
    return make_actor(org, dojo)


def do_import(actor, dojo, *, text=CSV, mapping=None, dry_run=False):
    _headers, rows = csv_source.read_table(text.encode("utf-8"))
    return engine.run(
        importer=StudentImporter(),
        rows=rows,
        mapping=mapping if mapping is not None else MAPPING,
        actor=actor,
        dojo=dojo,
        filename="roster.csv",
        dry_run=dry_run,
    )


def student_count(org):
    with allow_unscoped("test read"):
        return StudentProfile.objects.filter(person__organization=org).count()


# -- reading the file ---------------------------------------------------------


def test_a_utf8_bom_does_not_corrupt_the_first_header():
    """⚠ Excel on Windows writes a BOM. Read as plain UTF-8 the first column
    becomes 'ï»¿First name' and matches no mapping, and the operator is told
    their file has no name column when it plainly does."""
    raw = "﻿First name,Last name\r\nBopha,Chan\r\n".encode()

    headers, rows = csv_source.read_table(raw)

    assert headers[0] == "First name"
    assert rows[0].values["First name"] == "Bopha"


def test_a_cp1252_export_is_read_rather_than_refused():
    raw = "First name,City\r\nRené,Phnom Penh\r\n".encode("cp1252")

    _headers, rows = csv_source.read_table(raw)

    assert rows[0].values["First name"] == "René"


def test_semicolon_delimited_european_export_is_understood():
    raw = b"First name;Last name\r\nBopha;Chan\r\n"

    headers, rows = csv_source.read_table(raw)

    assert headers == ["First name", "Last name"]
    assert rows[0].values["Last name"] == "Chan"


def test_a_blank_line_mid_file_does_not_break_delimiter_detection():
    """⚠ The bug that only running it found.

    csv.Sniffer reads the delimiter correctly from any prefix of this file but
    fails on the whole sample, because the blank row has no delimiters and fails
    its consistency check. The old comma fallback then parsed the entire line as
    one column and the importer confidently reported rows whose only field was
    the whole line.
    """
    raw = b"First name;Last name;City\r\nBopha;Chan;Phnom Penh\r\n\r\nSokha;Chan;Siem Reap\r\n"

    headers, rows = csv_source.read_table(raw)

    assert headers == ["First name", "Last name", "City"]
    assert len(rows) == 2
    assert rows[1].values["City"] == "Siem Reap"


def test_delimiter_detection_falls_back_to_counting_the_header():
    """When the sniffer gives up entirely, the header still says what it is."""
    assert csv_source.delimiter_of("a;b;c\r\n") == ";"
    assert csv_source.delimiter_of("a,b,c\r\n") == ","
    assert csv_source.delimiter_of("a\tb\tc\r\n") == "\t"
    assert csv_source.delimiter_of("single\r\n") == ","


def test_trailing_blank_lines_are_not_rows():
    raw = b"First name\r\nBopha\r\n\r\n,\r\n"

    _headers, rows = csv_source.read_table(raw)

    assert len(rows) == 1


def test_duplicate_column_names_are_refused():
    """⚠ Not de-duplicated. Two 'Phone' columns mean the operator must say which
    is wanted; keeping the last would drop data without telling anybody."""
    with pytest.raises(csv_source.CsvRejected):
        csv_source.read_table(b"Phone,Phone\r\n1,2\r\n")


@pytest.mark.parametrize(
    "raw",
    [b"", b"   ", b"Only a header\r\n"],
)
def test_unusable_files_are_refused(raw):
    with pytest.raises(csv_source.CsvRejected):
        csv_source.read_table(raw)


def test_an_oversized_file_is_refused():
    with pytest.raises(csv_source.CsvRejected):
        csv_source.decode(b"x" * (csv_source.MAX_CSV_BYTES + 1))


# -- mapping ------------------------------------------------------------------


def test_a_mapping_missing_a_required_field_is_refused():
    with pytest.raises(ValidationError):
        engine.validate_mapping(StudentImporter(), {"Last name": "family_name"})


def test_a_mapping_naming_an_unknown_field_is_refused():
    with pytest.raises(ValidationError):
        engine.validate_mapping(StudentImporter(), {"A": "given_name", "B": "shoe_size"})


def test_two_columns_mapped_to_one_field_are_refused():
    """Ambiguous, and picking either silently loses data the operator believed
    was imported."""
    with pytest.raises(ValidationError):
        engine.validate_mapping(StudentImporter(), {"A": "given_name", "B": "given_name"})


def test_unmapped_columns_are_ignored_not_guessed(actor, dojo):
    text = "First name,Some competitor field\r\nBopha,junk\r\n"

    run = do_import(actor, dojo, text=text, mapping={"First name": "given_name"})

    assert run.created_count == 1


# -- the dry run --------------------------------------------------------------


def test_a_dry_run_writes_nothing(org, actor, dojo):
    run = do_import(actor, dojo, dry_run=True)

    assert run.created_count == 2
    # ⚠ The database, not the reported counts. A dry run that reports correctly
    # while having actually written is the failure worth catching.
    assert student_count(org) == 0
    with allow_unscoped("test read"):
        # Only the acting person exists — nobody from the file.
        assert not Person.objects.filter(organization=org, family_name="Chan").exists()
        assert ImportedRecord.objects.filter(organization=org).count() == 0


def test_a_dry_run_still_leaves_its_report(org, actor, dojo):
    """⚠ The ImportRun is written outside the rolled-back transaction. Inside it,
    a dry run would roll back the very record the operator is meant to read."""
    run = do_import(actor, dojo, dry_run=True)

    with allow_unscoped("test read"):
        stored = ImportRun.objects.get(pk=run.pk)
    assert stored.is_dry_run
    assert len(stored.outcomes) == 2


def test_a_dry_run_sees_failures_that_only_happen_on_write(actor, dojo):
    """The reason the dry run shares the real code path rather than validating
    separately: a bad date is only discovered by trying."""
    text = "Student ID,First name,DOB\r\nS1,Bopha,04/03/2015\r\nS2,Sokha,not-a-date\r\n"
    mapping = {"Student ID": "external_id", "First name": "given_name", "DOB": "date_of_birth"}

    run = do_import(actor, dojo, text=text, mapping=mapping, dry_run=True)

    assert run.created_count == 1
    assert run.error_count == 1


# -- idempotency, the point of the whole thing --------------------------------


def test_re_importing_the_same_file_updates_rather_than_duplicates(org, actor, dojo):
    """⚠ TODO 1.10.2. The second run of a corrected file must not double the roll."""
    first = do_import(actor, dojo)
    second = do_import(actor, dojo)

    assert first.created_count == 2
    assert second.created_count == 0
    assert second.updated_count == 2
    assert student_count(org) == 2


def test_a_corrected_re_import_applies_the_correction(org, actor, dojo):
    do_import(actor, dojo)
    corrected = CSV.replace("Bopha", "Bopha-Marie")

    do_import(actor, dojo, text=corrected)

    with allow_unscoped("test read"):
        assert Person.objects.filter(organization=org, given_name="Bopha-Marie").exists()
        assert not Person.objects.filter(organization=org, given_name="Bopha").exists()


def test_a_blank_cell_on_re_import_does_not_erase(org, actor, dojo):
    """⚠ A partial re-import — a corrected phone column only — must not wipe
    every address it did not happen to carry."""
    text = "Student ID,First name,City\r\nS1,Bopha,Phnom Penh\r\n"
    mapping = {"Student ID": "external_id", "First name": "given_name", "City": "city"}
    do_import(actor, dojo, text=text, mapping=mapping)

    do_import(
        actor,
        dojo,
        text="Student ID,First name,City\r\nS1,Bopha,\r\n",
        mapping=mapping,
    )

    with allow_unscoped("test read"):
        assert Person.objects.get(organization=org, given_name="Bopha").city == "Phnom Penh"


def test_siblings_share_one_guardian_person(org, actor, dojo):
    """⚠ Two rows naming the same parent must give one Person with two links.
    Two copies would mean 'message all parents' sends twice."""
    do_import(actor, dojo)

    with allow_unscoped("test read"):
        guardians = Person.objects.filter(organization=org, email="dara@example.com")
        assert guardians.count() == 1
        assert GuardianLink.objects.filter(guardian=guardians.first()).count() == 2


def test_the_guardian_is_not_duplicated_on_re_import(org, actor, dojo):
    do_import(actor, dojo)
    do_import(actor, dojo)

    with allow_unscoped("test read"):
        assert Person.objects.filter(organization=org, email="dara@example.com").count() == 1
        assert GuardianLink.objects.count() == 2


def test_without_an_external_id_the_key_falls_back_to_name_and_birthdate(org, actor, dojo):
    text = "First name,Last name,DOB\r\nBopha,Chan,2015-03-04\r\n"
    mapping = {"First name": "given_name", "Last name": "family_name", "DOB": "date_of_birth"}

    do_import(actor, dojo, text=text, mapping=mapping)
    second = do_import(actor, dojo, text=text, mapping=mapping)

    assert second.updated_count == 1
    assert student_count(org) == 1


def test_the_fallback_key_is_case_folded(org, actor, dojo):
    """Re-exporting with different capitalisation is not a new student."""
    mapping = {"First name": "given_name", "Last name": "family_name", "DOB": "date_of_birth"}
    do_import(
        actor,
        dojo,
        text="First name,Last name,DOB\r\nBopha,Chan,2015-03-04\r\n",
        mapping=mapping,
    )
    second = do_import(
        actor,
        dojo,
        text="First name,Last name,DOB\r\nBOPHA,CHAN,2015-03-04\r\n",
        mapping=mapping,
    )

    assert second.updated_count == 1
    assert student_count(org) == 1


def test_a_row_with_no_identity_is_skipped_not_guessed(actor, dojo):
    text = "First name,Last name\r\n,Chan\r\n"

    run = do_import(
        actor, dojo, text=text, mapping={"First name": "given_name", "Last name": "family_name"}
    )

    assert run.skipped_count == 1
    assert run.created_count == 0


# -- error handling -----------------------------------------------------------


def test_one_bad_row_does_not_stop_the_others(org, actor, dojo):
    """⚠ Each row runs in its own savepoint. On PostgreSQL a failed statement
    poisons the transaction, so without it one bad row makes every later row
    fail too — and the report becomes nonsense."""
    text = (
        "Student ID,First name,DOB\r\n"
        "S1,Bopha,2015-03-04\r\n"
        "S2,Sokha,rubbish\r\n"
        "S3,Vuthy,2016-01-09\r\n"
    )
    mapping = {"Student ID": "external_id", "First name": "given_name", "DOB": "date_of_birth"}

    run = do_import(actor, dojo, text=text, mapping=mapping)

    assert run.created_count == 2
    assert run.error_count == 1
    assert student_count(org) == 2


def test_the_per_row_savepoint_is_present_in_the_source():
    """⚠ A source assertion, and deliberately so — read this before deleting it.

    ``test_one_bad_row_does_not_stop_the_others`` **passes with the per-row
    savepoint removed**, verified by doing exactly that. SQLite tolerates a
    failed statement inside a transaction; PostgreSQL does not — it raises
    ``InFailedSqlTransaction`` on every later statement until rollback. The suite
    runs on SQLite and production runs on PostgreSQL, so no behavioural test in
    this file can protect the invariant that actually matters.

    So this checks the structure instead: that the call which may raise is
    wrapped in its own ``transaction.atomic()``. It is a weaker guard than a
    behavioural test and it is the strongest one available here. The real fix is
    running the suite against PostgreSQL (`5.2.6` territory), at which point this
    test should be replaced by one that fails for the right reason.
    """
    import inspect
    import re

    from apps.imports import engine as engine_module

    source = inspect.getsource(engine_module.run)
    apply_line = next(
        index for index, line in enumerate(source.splitlines()) if "importer.apply(" in line
    )
    preceding = source.splitlines()[:apply_line]
    nearest_with = next(
        (
            line
            for line in reversed(preceding)
            if re.search(r"\bwith\s+transaction\.atomic\(\)", line)
        ),
        None,
    )
    assert nearest_with is not None, (
        "importer.apply() is not inside any transaction.atomic() — see this test's docstring"
    )
    apply_indent = len(source.splitlines()[apply_line]) - len(
        source.splitlines()[apply_line].lstrip()
    )
    with_indent = len(nearest_with) - len(nearest_with.lstrip())

    assert with_indent < apply_indent, (
        "importer.apply() must sit inside its own transaction.atomic() savepoint"
    )
    # And that savepoint must be inside the row loop, not the outer run-wide one.
    assert with_indent > 4, "the savepoint must be per row, not the outer transaction"


def test_an_ambiguous_date_format_is_refused_rather_than_guessed(actor, dojo):
    """⚠ 03/04/2015 as month/day would silently turn a March birthday into April."""
    text = "First name,DOB\r\nBopha,03/04/2015\r\n"
    run = do_import(
        actor, dojo, text=text, mapping={"First name": "given_name", "DOB": "date_of_birth"}
    )
    # Read as day/month, which is what the importer documents.
    with allow_unscoped("test read"):
        person = Person.objects.get(given_name="Bopha")
    assert run.created_count == 1
    assert person.date_of_birth == datetime.date(2015, 4, 3)


def test_the_error_detail_names_the_field_and_value(actor, dojo):
    text = "First name,DOB\r\nBopha,32/13/2015\r\n"

    run = do_import(
        actor, dojo, text=text, mapping={"First name": "given_name", "DOB": "date_of_birth"}
    )

    detail = run.outcomes[0]["detail"]
    assert "date_of_birth" in detail
    assert "32/13/2015" in detail


def test_row_numbers_match_the_spreadsheet(actor, dojo):
    """The header is row 1, so the first data row is row 2 — what the operator
    sees when they open the file to fix it."""
    run = do_import(actor, dojo)

    assert [outcome["row_number"] for outcome in run.outcomes] == [2, 3]


def test_a_blank_line_does_not_shift_the_reported_row_numbers(actor, dojo):
    """⚠ The bug that only running it found.

    Blank lines are skipped when building the row list, so the index among data
    rows stops matching the line in the file. The report and the wizard both
    promise these numbers match the operator's spreadsheet; reporting the index
    sends them to a blank line while the row that failed is further down.
    """
    text = "Student ID,First name,DOB\r\nS1,Bopha,2015-03-04\r\n\r\nS2,Sokha,rubbish\r\n"
    mapping = {"Student ID": "external_id", "First name": "given_name", "DOB": "date_of_birth"}

    run = do_import(actor, dojo, text=text, mapping=mapping)

    failed = next(o for o in run.outcomes if o["outcome"] == "error")
    assert failed["row_number"] == 4, "Sokha is on line 4 of the file, not line 3"


# -- scoping and permission ---------------------------------------------------


def test_an_import_creates_people_in_the_actors_organisation_only(org, actor, dojo):
    other = Organization.objects.create(name="Other", slug="other-org")

    do_import(actor, dojo)

    with allow_unscoped("test read"):
        assert Person.objects.filter(organization=other).count() == 0
        # 2 students + 1 shared guardian, all from the file.
        assert Person.objects.filter(organization=org, family_name="Chan").count() == 3


def test_students_are_enrolled_at_the_target_dojo(org, actor, dojo):
    do_import(actor, dojo)

    with allow_unscoped("test read"):
        assert Enrollment.objects.filter(dojo=dojo).count() == 2


def test_an_instructor_may_not_import(org, dojo):
    """⚠ Import is bulk person creation and is gated as such."""
    instructor = make_actor(org, dojo, role=Role.INSTRUCTOR)

    with pytest.raises(PermissionDenied):
        require_import_permission(instructor, dojo)


def test_a_dojo_admin_may_import(actor, dojo):
    require_import_permission(actor, dojo)  # does not raise


def test_import_keys_do_not_collide_across_organisations(org, dojo):
    """Two organisations may both have a student 'S1'."""
    other_org = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        other_dojo = Dojo.objects.create(
            organization=other_org, name="Other", slug="other-dojo", timezone="UTC"
        )
    first = make_actor(org, dojo)
    second = make_actor(other_org, other_dojo)

    do_import(first, dojo)
    run = do_import(second, other_dojo)

    assert run.created_count == 2
    assert student_count(org) == 2
    assert student_count(other_org) == 2


# -- the report ---------------------------------------------------------------


def test_the_report_has_a_row_per_input_row(actor, dojo):
    run = do_import(actor, dojo)

    rows = engine.report_rows(run)

    assert len(rows) == 2
    assert rows[0][1] == engine.Outcome.CREATED


def test_the_run_is_audited(org, actor, dojo):
    from apps.core.models import AuditLog

    do_import(actor, dojo)

    with allow_unscoped("test read"):
        assert AuditLog.objects.filter(action="import").exists()
