"""Seed the database with realistic demo data — TODO 0.7.1.

Creates:
  - 2 organisations (one central, one federated)
  - 3 dojos (2 under central, 1 under federated)
  - ~200 students with guardians, role assignments, and basic profiles
  - 2 organisations' worth of people and roles

Usage:
    python manage.py seed
    python manage.py seed --clear   # wipe and re-seed
"""

from __future__ import annotations

import random
import string
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.text import slugify

from apps.attendance.models import AttendanceRecord
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    EmergencyContact,
    Enrollment,
    GovernanceModel,
    GuardianLink,
    InstructorAssignment,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    TransferRecord,
    User,
)
from apps.ranks.models import Rank, RankAward, RankLadder, StudentStyleTrack, Style
from apps.ranks.seeding import create_shotokan_ladders
from apps.scheduling.materialise import materialise_sessions
from apps.scheduling.models import ClassSession, ClassTemplate, ClosurePeriod

#: How far back the demo generates classes and attendance. Long enough for the
#: reports and the drop-off list to be interesting, short enough to seed in
#: seconds on SQLite.
HISTORY_DAYS = 60

KHMER_GIVEN_NAMES = [
    "Sokha", "Sokhem", "Sokly", "Sophea", "Sokun", "Sovann", "Srey",
    "Chantrea", "Chandara", "Chamroeun", "Chantrea", "Chhay",
    "Dara", "Davin", "Dewin",
    "Kosal", "Koemhong", "Kong",
    "Makara", "Maly", "Monirith",
    "Nary", "Nita", "Navin",
    "Pheakdey", "Pich", "Piseth",
    "Ratanak", "Rotha",
    "Sakhorn", "Salin", "Saran", "Sary", "Seat", "Seng",
    "Tep", "Thida", "Thouch", "Tharith",
    "Vannak", "Vathanak", "Vicheka",
]

KHMER_FAMILY_NAMES = [
    "Chhorn", "Chhouen", "Doeum", "Heng", "Hun", "Keo", "Khem",
    "Kim", "Kong", "Lim", "Mao", "Mean", "Nguon", "Nov",
    "Ouk", "Phan", "Phirun", "Pol", "Rith", "San", "Sang",
    "Sem", "Seng", "Sok", "Sorn", "Sou", "Suon", "Thach",
    "Thorn", "Tith", "Touch", "Tum", "Uon", "Van", "Vann",
]

LATIN_GIVEN_NAMES = [
    "Alex", "Sam", "Jordan", "Casey", "Morgan", "Taylor", "Riley",
    "Jamie", "Drew", "Avery", "Quinn", "Reese", "Skyler", "Hayden",
    "Emery", "Finley", "Rowan", "Sage", "Blair", "Parker",
]

LATIN_FAMILY_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
    "Miller", "Davis", "Rodriguez", "Martinez", "Anderson", "Thomas",
    "Jackson", "White", "Harris", "Martin", "Thompson", "Moore",
]

JAPANESE_GIVEN_NAMES = [
    "Haruto", "Yuto", "Sota", "Riku", "Kaito", "Ren", "Haruki",
    "Yui", "Aoi", "Hinata", "Mei", "Mio", "Sakura", "Ichika",
]

JAPANESE_FAMILY_NAMES = [
    "Tanaka", "Yamamoto", "Suzuki", "Watanabe", "Itou", "Takahashi",
    "Sato", "Kobayashi", "Kato", "Yoshida", "Yamada", "Sasaki",
]


def _random_phone() -> str:
    prefix = random.choice(["012", "015", "016", "017", "069", "088", "097"])
    return prefix + "".join(random.choices(string.digits, k=7))


def _random_email(given: str, family: str, org_slug: str) -> str:
    base = slugify(f"{given}.{family}")[:20]
    return f"{base}@{org_slug}.example.com"


class Command(BaseCommand):
    help = "Seed the database with demo data for development and evaluation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear all existing data before seeding.",
        )

    def handle(self, *args, **options):
        with allow_unscoped("seed command deliberately creates cross-tenant data"):
            if options["clear"]:
                self.stdout.write("Clearing existing data...")
                self._clear()
            elif Person.objects.exists():
                # People are generated with random names, so a second pass would
                # either collide on a user email or silently double the roll.
                # Refusing is more useful than either.
                raise CommandError(
                    "This database already has people in it. Re-run with --clear to "
                    "wipe and re-seed, or use `manage.py reset_demo`."
                )

            self.stdout.write("Seeding organisations...")
            orgs = self._create_organizations()

            self.stdout.write("Seeding rank ladders...")
            for org in orgs:
                create_shotokan_ladders(org)

            self.stdout.write("Seeding dojos...")
            dojos = self._create_dojos(orgs)

            self.stdout.write("Seeding people and roles...")
            self._create_people(dojos)

            self.stdout.write("Seeding class templates...")
            self._create_class_templates(dojos)

            self.stdout.write("Materialising sessions...")
            sessions = self._materialise()

            self.stdout.write("Seeding attendance history...")
            self._create_attendance(sessions)

            self._report_logins()

        self.stdout.write(self.style.SUCCESS("Done! Seed data created."))

    def _report_logins(self) -> None:
        """Print one usable sign-in per role.

        Instructor emails carry a random suffix, so without this the demo data is
        unreachable without a database query — which is a silly place for a
        five-minute demo to stall.
        """
        self.stdout.write("\nSign in at /login/ with:")
        for role, password in (
            (Role.ORG_ADMIN, "admin123!"),
            (Role.DOJO_ADMIN, "instructor123!"),
            (Role.INSTRUCTOR, "instructor123!"),
            (Role.GUARDIAN, "parent123!"),
        ):
            assignment = (
                RoleAssignment.objects.filter(role=role, person__user__isnull=False)
                .select_related("person__user")
                .first()
            )
            if assignment is None:
                continue
            self.stdout.write(
                f"  {role:<22} {assignment.person.user.email:<48} {password}"
            )

    def _clear(self) -> None:
        """Delete seeded data, children first.

        ⚠ Soft-delete models refuse ``.delete()`` on the queryset by design, so
        they need ``hard_delete()``. Swallowing that exception (as an earlier
        version did) left every Person in place and quietly added another two
        hundred on each run.

        AuditLog is deliberately not cleared: it is append-only, and retention is
        a separate, deliberate command.
        """
        ordered_models = [
            AttendanceRecord,
            ClassSession,
            ClassTemplate,
            ClosurePeriod,
            TransferRecord,
            Enrollment,
            RankAward,
            StudentStyleTrack,
            StudentProfile,
            GuardianLink,
            EmergencyContact,
            InstructorAssignment,
            RoleAssignment,
            User,
            Person,
            Rank,
            RankLadder,
            Style,
            Dojo,
            Organization,
        ]
        for model in ordered_models:
            queryset = model.objects.all()
            hard_delete = getattr(queryset, "hard_delete", None)
            if hard_delete is not None:
                hard_delete()
            else:
                queryset.delete()

    def _create_organizations(self) -> list[Organization]:
        orgs = []
        for name, slug, governance in [
            ("Phnom Penh Karate Association", "ppka", GovernanceModel.CENTRAL),
            ("Cambodia Martial Arts Federation", "cmaf", GovernanceModel.FEDERATED),
        ]:
            org, _ = Organization.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "governance_model": governance,
                    "country": "KH",
                    "default_currency": "USD",
                },
            )
            orgs.append(org)
        return orgs

    def _create_dojos(self, orgs: list[Organization]) -> dict[Organization, list[Dojo]]:
        dojos: dict[Organization, list[Dojo]] = {}
        dojo_configs = [
            (orgs[0], "PPKA Central", "ppka-central", "Phnom Penh"),
            (orgs[0], "PPKA Sen Sok", "ppka-sensok", "Phnom Penh"),
            (orgs[1], "CMAF Siem Reap", "cmaf-siemreap", "Siem Reap"),
        ]
        for org, name, slug, city in dojo_configs:
            dojo, _ = Dojo.objects.get_or_create(
                organization=org,
                slug=slug,
                defaults={
                    "name": name,
                    "city": city,
                    "country": "KH",
                    "timezone": "Asia/Phnom_Penh",
                    "currency": "USD",
                },
            )
            dojos.setdefault(org, []).append(dojo)
        return dojos

    def _create_people(self, dojos: dict[Organization, list[Dojo]]):
        student_count = 0

        for org, org_dojos in dojos.items():
            # Create org admin
            admin_person = self._create_person(
                org, "Admin", "User", org_slug=org.slug, is_khmer=False
            )
            User.objects.create_user(
                email=_random_email("admin", "user", org.slug),
                password="admin123!",
                person=admin_person,
            )
            RoleAssignment.objects.create(
                organization=org,
                person=admin_person,
                role=Role.ORG_ADMIN,
                scope_type=ScopeType.ORG,
                can_view_financials=True,
                can_export_pii=True,
            )

            for dojo in org_dojos:
                # Create dojo admin / head instructor
                instructor_person = self._create_person(
                    org,
                    random.choice(JAPANESE_GIVEN_NAMES),
                    random.choice(JAPANESE_FAMILY_NAMES),
                    org_slug=org.slug,
                    is_khmer=False,
                )
                User.objects.create_user(
                    email=_random_email(instructor_person.given_name, "instructor", dojo.slug),
                    password="instructor123!",
                    person=instructor_person,
                )
                RoleAssignment.objects.create(
                    organization=org,
                    person=instructor_person,
                    role=Role.DOJO_ADMIN,
                    scope_type=ScopeType.DOJO,
                    dojo=dojo,
                    can_view_financials=True,
                )

                # Create 2 regular instructors per dojo
                for _ in range(2):
                    inst_person = self._create_person(
                        org,
                        random.choice(JAPANESE_GIVEN_NAMES),
                        random.choice(JAPANESE_FAMILY_NAMES),
                        org_slug=org.slug,
                        is_khmer=random.random() > 0.5,
                    )
                    User.objects.create_user(
                        email=_random_email(
                            inst_person.given_name, "inst", dojo.slug + str(random.randint(1, 99))
                        ),
                        password="instructor123!",
                        person=inst_person,
                    )
                    RoleAssignment.objects.create(
                        organization=org,
                        person=inst_person,
                        role=Role.INSTRUCTOR,
                        scope_type=ScopeType.DOJO,
                        dojo=dojo,
                    )

                # Create students
                num_students = 60 if len(org_dojos) > 1 else 80
                num_students = num_students // len(org_dojos)
                for _ in range(num_students):
                    is_khmer = random.random() > 0.3
                    given = random.choice(KHMER_GIVEN_NAMES if is_khmer else LATIN_GIVEN_NAMES)
                    family = random.choice(KHMER_FAMILY_NAMES if is_khmer else LATIN_FAMILY_NAMES)
                    # 70% of students are minors — plan §2: this is a children's
                    # business, so the demo should look like one.
                    is_minor = random.random() > 0.3
                    student_person = self._create_person(
                        org,
                        given,
                        family,
                        org_slug=org.slug,
                        is_khmer=is_khmer,
                        is_minor=is_minor,
                    )

                    if is_minor:
                        guardian_given = random.choice(
                            KHMER_GIVEN_NAMES if is_khmer else LATIN_GIVEN_NAMES
                        )
                        guardian_family = family
                        guardian = self._create_person(
                            org,
                            guardian_given,
                            guardian_family,
                            org_slug=org.slug,
                            is_khmer=is_khmer,
                        )
                        # Create guardian role
                        RoleAssignment.objects.create(
                            organization=org,
                            person=guardian,
                            role=Role.GUARDIAN,
                            scope_type=ScopeType.DOJO,
                            dojo=dojo,
                        )
                        GuardianLink.objects.create(
                            guardian=guardian,
                            student=student_person,
                            relationship=random.choice(
                                [
                                    GuardianLink.Relationship.MOTHER,
                                    GuardianLink.Relationship.FATHER,
                                ]
                            ),
                            is_primary_contact=True,
                            is_emergency_contact=True,
                            is_financially_responsible=True,
                            has_custody=True,
                        )
                        User.objects.create_user(
                            email=_random_email(
                                guardian_given, guardian_family, dojo.slug + str(random.randint(100, 999))
                            ),
                            password="parent123!",
                            person=guardian,
                        )

                    # Create student role
                    RoleAssignment.objects.create(
                        organization=org,
                        person=student_person,
                        role=Role.STUDENT,
                        scope_type=ScopeType.DOJO,
                        dojo=dojo,
                    )

                    # A student without a profile and an enrolment is invisible to
                    # every screen that matters, so the seed creates all three.
                    status = random.choices(
                        [
                            StudentProfile.Status.ACTIVE,
                            StudentProfile.Status.ON_HOLD,
                            StudentProfile.Status.LAPSED,
                            StudentProfile.Status.TRIAL,
                        ],
                        weights=[85, 5, 5, 5],
                    )[0]
                    joined = date.today() - timedelta(days=random.randint(30, 900))
                    StudentProfile.objects.create(
                        person=student_person,
                        home_dojo=dojo,
                        status=status,
                        joined_on=joined,
                    )
                    Enrollment.objects.create(
                        student=student_person,
                        dojo=dojo,
                        is_primary=True,
                        status=(
                            Enrollment.Status.ACTIVE
                            if status != StudentProfile.Status.ON_HOLD
                            else Enrollment.Status.ON_HOLD
                        ),
                        started_on=joined,
                    )
                    student_count += 1

        self.stdout.write(f"  Created {student_count} students across all dojos.")

    def _create_class_templates(self, dojos: dict) -> None:
        """A believable weekly timetable per dojo — TODO 1.4.1."""
        timetable = [
            ("Little Dragons (4-7)", "FREQ=WEEKLY;BYDAY=TU,TH", time(16, 0), 45),
            ("Juniors (8-13)", "FREQ=WEEKLY;BYDAY=MO,WE,FR", time(17, 0), 60),
            ("Adults", "FREQ=WEEKLY;BYDAY=MO,WE,FR", time(18, 30), 90),
            ("Saturday all grades", "FREQ=WEEKLY;BYDAY=SA", time(9, 0), 90),
        ]
        for org_dojos in dojos.values():
            for dojo in org_dojos:
                for name, rrule, start, duration in timetable:
                    ClassTemplate.objects.get_or_create(
                        dojo=dojo,
                        name=name,
                        defaults={
                            "rrule": rrule,
                            "start_time": start,
                            "duration_minutes": duration,
                            "room": "Main hall",
                            "capacity": 30,
                            # Backdated so the seed has history to report on.
                            "active_from": date.today() - timedelta(days=HISTORY_DAYS),
                        },
                    )

    def _materialise(self) -> list:
        """Generate sessions across the demo window — TODO 1.4.2.

        Starts ``HISTORY_DAYS`` in the past so the reports and the drop-off list
        have something to say on a fresh database. A demo where every screen is
        empty demonstrates nothing.
        """
        result = materialise_sessions(
            actor=Actor.system(),
            today=date.today() - timedelta(days=HISTORY_DAYS),
            horizon_days=HISTORY_DAYS + 90,
        )
        self.stdout.write(f"  {result}")
        return list(
            ClassSession.objects.unscoped("seeding attendance across all demo tenants")
            .select_related("dojo")
            .order_by("starts_at")
        )

    def _create_attendance(self, sessions: list) -> None:
        """Attendance for classes that have already happened — TODO 1.5.1.

        The last two days are left unmarked on purpose, so the "not yet marked"
        prompt on the Today screen has something in it: that nag is a real part
        of the product (plan §12.7) and an empty version of it teaches nobody
        anything.
        """
        now = timezone.now()
        cutoff = now - timedelta(days=2)

        students_by_dojo: dict = {}
        for enrollment in Enrollment.objects.unscoped(
            "seeding attendance across all demo tenants"
        ).filter(ended_on__isnull=True, status=Enrollment.Status.ACTIVE):
            students_by_dojo.setdefault(enrollment.dojo_id, []).append(enrollment.student_id)

        records = []
        completed_session_ids = []
        for session in sessions:
            if session.starts_at >= cutoff:
                continue
            roll = students_by_dojo.get(session.dojo_id, [])
            if not roll:
                continue

            completed_session_ids.append(session.pk)
            # Not everyone attends every class: a real roster is a subset.
            attending = random.sample(roll, k=max(1, int(len(roll) * random.uniform(0.35, 0.7))))
            for student_id in attending:
                status = random.choices(
                    [
                        AttendanceRecord.Status.PRESENT,
                        AttendanceRecord.Status.LATE,
                        AttendanceRecord.Status.ABSENT,
                        AttendanceRecord.Status.EXCUSED,
                    ],
                    weights=[82, 8, 6, 4],
                )[0]
                records.append(
                    AttendanceRecord(
                        session=session,
                        student_id=student_id,
                        status=status,
                        method=AttendanceRecord.Method.ROSTER,
                        marked_at=session.ends_at,
                    )
                )

        # bulk_create rather than the service: the service applies permission and
        # idempotency logic per row, which is right for a request and far too slow
        # for ten thousand seed rows.
        AttendanceRecord.objects.bulk_create(records, batch_size=500)
        ClassSession.objects.unscoped("seeding demo data").filter(
            pk__in=completed_session_ids
        ).update(status=ClassSession.Status.COMPLETED)
        self.stdout.write(
            f"  Created {len(records)} attendance records across "
            f"{len(completed_session_ids)} completed sessions."
        )

    def _create_person(
        self,
        org: Organization,
        given_name: str,
        family_name: str,
        *,
        org_slug: str,
        is_khmer: bool = True,
        is_minor: bool = False,
    ) -> Person:
        # Ages have to agree with the guardian links, or the demo shows
        # fifty-year-olds with a mother listed as their emergency contact.
        this_year = date.today().year
        if is_minor:
            dob_year = random.randint(this_year - 17, this_year - 5)
        else:
            dob_year = random.randint(this_year - 55, this_year - 19)
        dob_month = random.randint(1, 12)
        dob_day = random.randint(1, 28)
        locale = "km" if is_khmer else "en"

        return Person.objects.create(
            organization=org,
            given_name=given_name,
            family_name=family_name,
            preferred_name=given_name,
            email=_random_email(given_name, family_name, org_slug),
            phone=_random_phone(),
            date_of_birth=date(dob_year, dob_month, dob_day),
            country="KH",
            locale=locale,
        )
