"""Attendance and rank-history import — TODO 1.10.4, 1.10.5, 1.10.6.

Both importers exist to load a migration's worth of past facts, and both are
required to go through the service that already owns those facts —
``mark_attendance`` and ``promote_student``. The tests that matter here are the
ones proving they did not go around it, and the ones proving that an ambiguous
row is refused rather than guessed: attaching a grading to the wrong child is not
recoverable by anything downstream.
"""

from __future__ import annotations

import datetime

import pytest

from apps.attendance.models import AttendanceRecord
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    Enrollment,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
)
from apps.identity.permissions import PermissionDenied
from apps.imports import csv_source, engine, presets
from apps.imports.attendance import AttendanceImporter, require_attendance_import_permission
from apps.imports.ranks import RankImporter
from apps.ranks.models import Rank, RankAward, RankLadder, StudentStyleTrack, Style
from apps.scheduling.models import ClassSession, ClassTemplate
from apps.staffing.models import InstructorProfile

pytestmark = pytest.mark.django_db

DAY = datetime.date(2026, 6, 10)


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


@pytest.fixture
def actor(org, dojo):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
    return Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
        roles=frozenset({(Role.DOJO_ADMIN, ScopeType.DOJO, dojo.pk)}),
    )


def make_student(org, dojo, given, family="Chan", dob=None):
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=org, given_name=given, family_name=family, date_of_birth=dob
        )
        StudentProfile.objects.create(
            person=person, status=StudentProfile.Status.ACTIVE, home_dojo=dojo
        )
        Enrollment.objects.create(
            student=person, dojo=dojo, started_on=datetime.date(2024, 1, 1), is_primary=True
        )
        return person


def make_session(dojo, day=DAY, name="Adults", hour=11):
    """hour is UTC; +7 makes 11:00 UTC an 18:00 Phnom Penh class."""
    with allow_unscoped("test setup"):
        template, _ = ClassTemplate.objects.get_or_create(
            dojo=dojo,
            name=name,
            defaults={
                "rrule": "FREQ=WEEKLY;BYDAY=MO",
                "start_time": datetime.time(18, 0),
                "duration_minutes": 60,
                "active_from": datetime.date(2024, 1, 1),
            },
        )
        starts = datetime.datetime.combine(day, datetime.time(hour, 0), tzinfo=datetime.UTC)
        return ClassSession.objects.create(
            dojo=dojo,
            template=template,
            starts_at=starts,
            ends_at=starts + datetime.timedelta(hours=1),
        )


def run_import(importer, text, mapping, actor, dojo, *, dry_run=False):
    _headers, rows = csv_source.read_table(text.encode("utf-8"))
    return engine.run(
        importer=importer,
        rows=rows,
        mapping=mapping,
        actor=actor,
        dojo=dojo,
        filename="history.csv",
        dry_run=dry_run,
    )


# -- attendance ---------------------------------------------------------------

ATT_MAP = {
    "First name": "given_name",
    "Last name": "family_name",
    "Date": "date",
    "Class": "class_name",
    "Status": "status",
}


def test_attendance_is_recorded_through_the_service(org, dojo, actor):
    """⚠ Method IMPORT proves it went through mark_attendance rather than an
    INSERT — nothing else sets that."""
    make_student(org, dojo, "Bopha")
    make_session(dojo)
    text = "First name,Last name,Date,Class,Status\r\nBopha,Chan,2026-06-10,Adults,present\r\n"

    run = run_import(AttendanceImporter(), text, ATT_MAP, actor, dojo)

    assert run.created_count == 1
    with allow_unscoped("test read"):
        record = AttendanceRecord.objects.get()
    assert record.method == AttendanceRecord.Method.IMPORT
    assert record.status == AttendanceRecord.Status.PRESENT


def test_re_importing_attendance_does_not_double_mark(org, dojo, actor):
    """Idempotency is the service's own, via a deterministic client_generated_id."""
    make_student(org, dojo, "Bopha")
    make_session(dojo)
    text = "First name,Last name,Date,Class,Status\r\nBopha,Chan,2026-06-10,Adults,present\r\n"

    run_import(AttendanceImporter(), text, ATT_MAP, actor, dojo)
    run_import(AttendanceImporter(), text, ATT_MAP, actor, dojo)

    with allow_unscoped("test read"):
        assert AttendanceRecord.objects.count() == 1


def test_a_class_that_never_happened_is_an_error_not_an_invention(org, dojo, actor):
    """⚠ Attendance is evidence about a class. A class conjured from an
    attendance file is evidence of nothing."""
    make_student(org, dojo, "Bopha")
    text = "First name,Last name,Date,Class,Status\r\nBopha,Chan,2026-06-10,Adults,present\r\n"

    run = run_import(AttendanceImporter(), text, ATT_MAP, actor, dojo)

    assert run.error_count == 1
    assert "No class was scheduled" in run.outcomes[0]["detail"]
    with allow_unscoped("test read"):
        assert ClassSession.objects.count() == 0


def test_two_classes_that_day_without_a_class_column_is_refused(org, dojo, actor):
    """⚠ Putting the juniors' register against the adults' class is the kind of
    error nobody finds until a grading is refused."""
    make_student(org, dojo, "Bopha")
    make_session(dojo, name="Adults", hour=11)
    make_session(dojo, name="Juniors", hour=9)
    text = "First name,Last name,Date,Status\r\nBopha,Chan,2026-06-10,present\r\n"

    run = run_import(
        AttendanceImporter(),
        text,
        {k: v for k, v in ATT_MAP.items() if k != "Class"},
        actor,
        dojo,
    )

    assert run.error_count == 1
    assert "Add a class column" in run.outcomes[0]["detail"]


def test_the_session_is_matched_on_the_dojos_local_date(org, dojo, actor):
    """⚠ A 06:00 Phnom Penh class is 23:00 the previous day in UTC. Matching on
    the stored date would file it against the wrong day — the same trap 1.4.9
    exists to avoid."""
    make_student(org, dojo, "Bopha")
    make_session(dojo, day=datetime.date(2026, 6, 9), hour=23)  # 06:00 on the 10th local
    text = "First name,Last name,Date,Class,Status\r\nBopha,Chan,2026-06-10,Adults,present\r\n"

    run = run_import(AttendanceImporter(), text, ATT_MAP, actor, dojo)

    assert run.created_count == 1


def test_an_unknown_student_is_an_error(org, dojo, actor):
    make_session(dojo)
    text = "First name,Last name,Date,Class,Status\r\nNobody,Here,2026-06-10,Adults,present\r\n"

    run = run_import(AttendanceImporter(), text, ATT_MAP, actor, dojo)

    assert run.error_count == 1
    assert "No student here matches" in run.outcomes[0]["detail"]


def test_an_ambiguous_student_name_is_refused_not_guessed(org, dojo, actor):
    """⚠ Two students called Sokha Chan is ordinary in a dojo of two hundred."""
    make_student(org, dojo, "Sokha")
    make_student(org, dojo, "Sokha")
    make_session(dojo)
    text = "First name,Last name,Date,Class,Status\r\nSokha,Chan,2026-06-10,Adults,present\r\n"

    run = run_import(AttendanceImporter(), text, ATT_MAP, actor, dojo)

    assert run.error_count == 1
    assert "More than one student" in run.outcomes[0]["detail"]


def test_status_synonyms_are_understood(org, dojo, actor):
    make_student(org, dojo, "Bopha")
    make_student(org, dojo, "Sokha")
    make_session(dojo)
    text = (
        "First name,Last name,Date,Class,Status\r\n"
        "Bopha,Chan,2026-06-10,Adults,Y\r\n"
        "Sokha,Chan,2026-06-10,Adults,absent\r\n"
    )

    run = run_import(AttendanceImporter(), text, ATT_MAP, actor, dojo)

    assert run.created_count == 2
    with allow_unscoped("test read"):
        statuses = set(AttendanceRecord.objects.values_list("status", flat=True))
    assert statuses == {AttendanceRecord.Status.PRESENT, AttendanceRecord.Status.ABSENT}


def test_an_instructor_cannot_import_attendance(org, dojo):
    """⚠ Needs the retroactive edit right, which a plain instructor lacks —
    a historical import is retroactive by definition."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Sen", family_name="Sei")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
    instructor = Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
        roles=frozenset({(Role.INSTRUCTOR, ScopeType.DOJO, dojo.pk)}),
    )

    with pytest.raises(PermissionDenied):
        require_attendance_import_permission(instructor, dojo)


# -- rank history -------------------------------------------------------------

RANK_MAP = {
    "First name": "given_name",
    "Last name": "family_name",
    "Rank": "rank",
    "Date": "awarded_on",
}


@pytest.fixture
def ladder(org):
    with allow_unscoped("test setup"):
        style = Style.objects.create(organization=org, name="Shotokan")
        ladder = RankLadder.objects.create(style=style, name="Adult")
        for order, name in enumerate(["9th Kyu", "8th Kyu", "7th Kyu"], start=1):
            Rank.objects.create(ladder=ladder, name=name, order=order)
        return ladder


def test_rank_history_is_recorded_through_the_promotion_service(org, dojo, actor, ladder):
    make_student(org, dojo, "Bopha")
    text = (
        "First name,Last name,Rank,Date\r\n"
        "Bopha,Chan,9th Kyu,2024-03-01\r\n"
        "Bopha,Chan,8th Kyu,2024-09-01\r\n"
    )

    run = run_import(RankImporter(), text, RANK_MAP, actor, dojo)

    assert run.created_count == 2
    with allow_unscoped("test read"):
        track = StudentStyleTrack.objects.get()
        assert track.current_rank.name == "8th Kyu"
        assert RankAward.objects.count() == 2


def test_history_listed_newest_first_still_imports(org, dojo, actor, ladder):
    """⚠ The service is forward-only, and most systems export newest first. The
    importer sorts chronologically before applying — without that, every row
    after the first is rejected."""
    make_student(org, dojo, "Bopha")
    text = (
        "First name,Last name,Rank,Date\r\n"
        "Bopha,Chan,8th Kyu,2024-09-01\r\n"
        "Bopha,Chan,9th Kyu,2024-03-01\r\n"
    )

    run = run_import(RankImporter(), text, RANK_MAP, actor, dojo)

    assert run.created_count == 2, run.outcomes
    with allow_unscoped("test read"):
        assert StudentStyleTrack.objects.get().current_rank.name == "8th Kyu"


def test_the_report_stays_in_file_order_despite_the_sort(org, dojo, actor, ladder):
    """Reordering is internal; the operator reads row numbers against their file."""
    make_student(org, dojo, "Bopha")
    text = (
        "First name,Last name,Rank,Date\r\n"
        "Bopha,Chan,8th Kyu,2024-09-01\r\n"
        "Bopha,Chan,9th Kyu,2024-03-01\r\n"
    )

    run = run_import(RankImporter(), text, RANK_MAP, actor, dojo)

    assert [o["row_number"] for o in run.outcomes] == [2, 3]


def test_re_importing_rank_history_skips_rather_than_duplicating(org, dojo, actor, ladder):
    """⚠ Rank awards are append-only, so a re-import cannot rewrite one. Skipped
    is the honest answer: the row is already recorded."""
    make_student(org, dojo, "Bopha")
    text = "First name,Last name,Rank,Date\r\nBopha,Chan,9th Kyu,2024-03-01\r\n"

    run_import(RankImporter(), text, RANK_MAP, actor, dojo)
    second = run_import(RankImporter(), text, RANK_MAP, actor, dojo)

    assert second.skipped_count == 1
    assert second.created_count == 0
    with allow_unscoped("test read"):
        assert RankAward.objects.count() == 1


def test_a_missing_track_is_created_from_the_earliest_award(org, dojo, actor, ladder):
    make_student(org, dojo, "Bopha")
    text = "First name,Last name,Rank,Date\r\nBopha,Chan,9th Kyu,2024-03-01\r\n"

    run_import(RankImporter(), text, RANK_MAP, actor, dojo)

    with allow_unscoped("test read"):
        assert StudentStyleTrack.objects.get().started_on == datetime.date(2024, 3, 1)


def test_an_unknown_rank_name_is_an_error(org, dojo, actor, ladder):
    make_student(org, dojo, "Bopha")
    text = "First name,Last name,Rank,Date\r\nBopha,Chan,Purple Belt,2024-03-01\r\n"

    run = run_import(RankImporter(), text, RANK_MAP, actor, dojo)

    assert run.error_count == 1
    assert "No rank called" in run.outcomes[0]["detail"]


def test_an_examiner_ceiling_constrains_what_can_be_imported(org, dojo, actor, ladder):
    """⚠ The control working, not a bug. Somebody who may not award a rank must
    not be able to award it by uploading it."""
    make_student(org, dojo, "Bopha")
    with allow_unscoped("test setup"):
        lowest = Rank.objects.get(name="9th Kyu")
        InstructorProfile.objects.create(person_id=actor.person_id, max_grading_rank=lowest)
    text = "First name,Last name,Rank,Date\r\nBopha,Chan,7th Kyu,2024-03-01\r\n"

    run = run_import(RankImporter(), text, RANK_MAP, actor, dojo)

    assert run.error_count == 1
    assert "ceiling" in run.outcomes[0]["detail"].lower()


def test_two_styles_without_a_style_column_is_refused(org, dojo, actor, ladder):
    """⚠ Picking one would file a judo grading on the karate ladder."""
    with allow_unscoped("test setup"):
        Style.objects.create(organization=org, name="Judo")
    make_student(org, dojo, "Bopha")
    text = "First name,Last name,Rank,Date\r\nBopha,Chan,9th Kyu,2024-03-01\r\n"

    run = run_import(RankImporter(), text, RANK_MAP, actor, dojo)

    assert run.error_count == 1
    assert "more than one style" in run.outcomes[0]["detail"].lower()


def test_a_rank_dry_run_writes_nothing(org, dojo, actor, ladder):
    make_student(org, dojo, "Bopha")
    text = "First name,Last name,Rank,Date\r\nBopha,Chan,9th Kyu,2024-03-01\r\n"

    run = run_import(RankImporter(), text, RANK_MAP, actor, dojo, dry_run=True)

    assert run.created_count == 1
    with allow_unscoped("test read"):
        assert RankAward.objects.count() == 0
        assert StudentStyleTrack.objects.count() == 0


# -- presets ------------------------------------------------------------------


def test_a_preset_claims_a_file_matching_its_fingerprint():
    headers = ["Member ID", "First Name", "Last Name", "Birthday", "Parent Email"]

    detected = presets.detect(headers, "students")

    assert detected is not None
    assert detected.key == "gymdesk-students"
    mapping = detected.mapping_for(headers)
    assert mapping["Member ID"] == "external_id"
    assert mapping["Parent Email"] == "guardian_email"


def test_a_preset_does_not_claim_an_unrelated_file():
    assert presets.detect(["Name", "Age", "Colour"], "students") is None


def test_every_unverified_preset_says_so():
    """⚠ The point of a preset is that it is known-good. These were built from
    ordinary column names, not from a real export, and must not be presented as
    more than that until somebody runs a genuine file through them."""
    for preset in presets.PRESETS:
        if not preset.verified:
            assert preset.note, f"{preset.key} is unverified and says nothing about it"


def test_presets_are_scoped_to_their_kind():
    assert all(p.kind == "attendance" for p in presets.for_kind("attendance"))
    assert presets.detect(["Member ID", "First Name", "Last Name"], "ranks") is None
