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

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.text import slugify

from apps.attendance.models import AttendanceRecord
from apps.core.notes import Note
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    ConsentPolicy,
    ConsentRecord,
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
    "Sokha",
    "Sokhem",
    "Sokly",
    "Sophea",
    "Sokun",
    "Sovann",
    "Srey",
    "Chantrea",
    "Chandara",
    "Chamroeun",
    "Chantrea",
    "Chhay",
    "Dara",
    "Davin",
    "Dewin",
    "Kosal",
    "Koemhong",
    "Kong",
    "Makara",
    "Maly",
    "Monirith",
    "Nary",
    "Nita",
    "Navin",
    "Pheakdey",
    "Pich",
    "Piseth",
    "Ratanak",
    "Rotha",
    "Sakhorn",
    "Salin",
    "Saran",
    "Sary",
    "Seat",
    "Seng",
    "Tep",
    "Thida",
    "Thouch",
    "Tharith",
    "Vannak",
    "Vathanak",
    "Vicheka",
]

KHMER_FAMILY_NAMES = [
    "Chhorn",
    "Chhouen",
    "Doeum",
    "Heng",
    "Hun",
    "Keo",
    "Khem",
    "Kim",
    "Kong",
    "Lim",
    "Mao",
    "Mean",
    "Nguon",
    "Nov",
    "Ouk",
    "Phan",
    "Phirun",
    "Pol",
    "Rith",
    "San",
    "Sang",
    "Sem",
    "Seng",
    "Sok",
    "Sorn",
    "Sou",
    "Suon",
    "Thach",
    "Thorn",
    "Tith",
    "Touch",
    "Tum",
    "Uon",
    "Van",
    "Vann",
]

LATIN_GIVEN_NAMES = [
    "Alex",
    "Sam",
    "Jordan",
    "Casey",
    "Morgan",
    "Taylor",
    "Riley",
    "Jamie",
    "Drew",
    "Avery",
    "Quinn",
    "Reese",
    "Skyler",
    "Hayden",
    "Emery",
    "Finley",
    "Rowan",
    "Sage",
    "Blair",
    "Parker",
]

LATIN_FAMILY_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Anderson",
    "Thomas",
    "Jackson",
    "White",
    "Harris",
    "Martin",
    "Thompson",
    "Moore",
]

JAPANESE_GIVEN_NAMES = [
    "Haruto",
    "Yuto",
    "Sota",
    "Riku",
    "Kaito",
    "Ren",
    "Haruki",
    "Yui",
    "Aoi",
    "Hinata",
    "Mei",
    "Mio",
    "Sakura",
    "Ichika",
]

JAPANESE_FAMILY_NAMES = [
    "Tanaka",
    "Yamamoto",
    "Suzuki",
    "Watanabe",
    "Itou",
    "Takahashi",
    "Sato",
    "Kobayashi",
    "Kato",
    "Yoshida",
    "Yamada",
    "Sasaki",
]


def _random_phone() -> str:
    prefix = random.choice(["012", "015", "016", "017", "069", "088", "097"])
    return prefix + "".join(random.choices(string.digits, k=7))


def _random_email(given: str, family: str, org_slug: str) -> str:
    base = slugify(f"{given}.{family}")[:20]
    return f"{base}@{org_slug}.example.com"


# Demo sign-ins are typed by hand, over and over, on a phone. Generated
# addresses like `kenji.instructor@phnom-penh-central.example.com` are correct
# and unusable. Role-holders get `<role>@karate.test` instead, numbered from the
# second onwards because there are several dojos and admins per seed run.
#
# `.test` is reserved by RFC 6761 and can never resolve, so a misconfigured demo
# cannot mail a real person. It is exactly as short to type as a real domain.
DEMO_EMAIL_DOMAIN = "karate.test"


class _DemoLogins:
    """Hands out short, predictable demo addresses and remembers the first of each."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self.canonical: dict[str, str] = {}

    def allocate(self, label: str) -> str:
        seen = self._counts.get(label, 0) + 1
        self._counts[label] = seen
        suffix = "" if seen == 1 else str(seen)
        email = f"{label}{suffix}@{DEMO_EMAIL_DOMAIN}"
        self.canonical.setdefault(label, email)
        return email


class Command(BaseCommand):
    help = "Seed the database with demo data for development and evaluation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear all existing data before seeding.",
        )

    def handle(self, *args, **options):
        if not settings.DEMO_SEED_ENABLED:
            raise CommandError("Demo seeding is disabled in this environment.")
        self.logins = _DemoLogins()
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

            self.stdout.write("Seeding demo consent policies...")
            self._create_consent_policies(orgs)

            self.stdout.write("Seeding rank ladders...")
            ladders = {org: create_shotokan_ladders(org) for org in orgs}

            self.stdout.write("Seeding dojos...")
            dojos = self._create_dojos(orgs)

            self.stdout.write("Seeding people and roles...")
            self._create_people(dojos)

            self.stdout.write("Seeding rank tracks and grading history...")
            self._create_rank_tracks(ladders)

            self.stdout.write("Seeding notes...")
            self._create_notes(dojos)

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

        Every address is confirmed against the database before it is printed —
        an invented sign-in that does not work is worse than no hint at all.
        """
        self.stdout.write("\nSign in at /login/ with:")
        for role, label, password in (
            (Role.ORG_ADMIN, "admin", "admin123!"),
            (Role.DOJO_ADMIN, "dojoadmin", "instructor123!"),
            (Role.INSTRUCTOR, "instructor", "instructor123!"),
            (Role.GUARDIAN, "parent", "parent123!"),
        ):
            email = self.logins.canonical.get(label)
            if email is None or not User.objects.filter(email=email).exists():
                continue
            self.stdout.write(f"  {role:<22} {email:<28} {password}")

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
            Note,
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
            if model is RankAward:
                # The disposable demo reset is the only sanctioned hard-delete
                # path for append-only award history.
                queryset._raw_delete(queryset.db)
                continue
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

    def _create_consent_policies(self, orgs: list[Organization]) -> None:
        """Publish clearly labelled demo wording so consent screens are testable."""
        policies = (
            (
                ConsentRecord.Type.MEDICAL,
                "demo-medical-2026-01",
                "Demo medical information consent",
                "DEMO ONLY — replace before real use.\n\n"
                "I consent to this dojo collecting and using the student's medical "
                "information for safe training, emergency response, and reasonable "
                "training adjustments.",
            ),
            (
                ConsentRecord.Type.PHOTO,
                "demo-photo-2026-01",
                "Demo photo and video consent",
                "DEMO ONLY — replace before real use.\n\n"
                "I consent to photographs and video of the student being used for "
                "internal attendance identification and the specific dojo purposes "
                "described here. Public or marketing use requires wording that says so.",
            ),
            (
                ConsentRecord.Type.WAIVER,
                "demo-waiver-2026-01",
                "Demo training waiver",
                "DEMO ONLY — not legal advice; replace with locally reviewed wording "
                "before real use.\n\nI acknowledge that martial-arts training involves "
                "physical activity and risk of injury, and I agree to follow the "
                "instructor's safety directions.",
            ),
        )
        for org in orgs:
            for consent_type, version, title, body in policies:
                ConsentPolicy.objects.get_or_create(
                    organization=org,
                    consent_type=consent_type,
                    version=version,
                    defaults={"title": title, "body": body, "is_active": True},
                )

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
                email=self.logins.allocate("admin"),
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
                    email=self.logins.allocate("dojoadmin"),
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
                        email=self.logins.allocate("instructor"),
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
                            email=self.logins.allocate("parent"),
                            password="parent123!",
                            person=guardian,
                        )

                        # Keep enough two-guardian households in the demo to make
                        # the independent contact/custody flags easy to test by hand.
                        if random.random() < 0.35:
                            second_guardian = self._create_person(
                                org,
                                random.choice(KHMER_GIVEN_NAMES if is_khmer else LATIN_GIVEN_NAMES),
                                guardian_family,
                                org_slug=org.slug,
                                is_khmer=is_khmer,
                            )
                            GuardianLink.objects.create(
                                guardian=second_guardian,
                                student=student_person,
                                relationship=random.choice(
                                    [
                                        GuardianLink.Relationship.MOTHER,
                                        GuardianLink.Relationship.FATHER,
                                        GuardianLink.Relationship.GUARDIAN,
                                    ]
                                ),
                                is_primary_contact=False,
                                is_emergency_contact=False,
                                is_financially_responsible=True,
                                has_custody=False,
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

    def _create_rank_tracks(self, ladders: dict) -> None:
        """Put every student on a ladder and give them a grading history.

        ⚠ Without this the demo has ranks but nobody holding one: the seed built
        the ladders (task 1.2.11) and then never enrolled a student on one, so
        `1.11.2`'s report, the student rank tab and every promotion screen all
        rendered a single "no active rank track" bucket. Found by opening the
        report against seeded data, not by any test — TODO 1.2.4/1.2.5.

        Which ladder a student lands on is decided by age, the same rule the
        junior-to-adult transition uses: under 14 grades on mon, everyone else
        on kyu/dan.
        """
        today = date.today()
        track_count = 0
        award_count = 0

        for org, (adult, junior) in ladders.items():
            adult_ranks = list(Rank.objects.filter(ladder=adult).order_by("order"))
            junior_ranks = list(Rank.objects.filter(ladder=junior).order_by("order"))

            profiles = StudentProfile.objects.filter(person__organization=org).select_related(
                "person"
            )
            for profile in profiles:
                person = profile.person
                dob = person.date_of_birth
                age = (today - dob).days // 365 if dob else 20
                ladder, ranks = (junior, junior_ranks) if age < 14 else (adult, adult_ranks)
                if not ranks:
                    continue

                started = profile.joined_on or today - timedelta(days=180)
                track = StudentStyleTrack.objects.create(
                    student=person,
                    style=ladder.style,
                    ladder=ladder,
                    started_on=started,
                )
                track_count += 1

                # Roughly one in six has joined but never been graded. Ungraded
                # is a real state every rank screen has to render, so the demo
                # must contain some.
                if random.randint(1, 6) == 1:
                    continue

                # A grading roughly every four months, capped by ladder length.
                months = max(0, (today - started).days // 30)
                steps = min(len(ranks), 1 + months // 4)
                span = max(0, (today - started).days)
                for index, rank in enumerate(ranks[:steps]):
                    RankAward.objects.create(
                        track=track,
                        rank=rank,
                        awarded_on=started + timedelta(days=span * index // max(1, steps)),
                        recognition=RankAward.Recognition.INTERNAL,
                    )
                    award_count += 1

        self.stdout.write(f"  Created {track_count} rank tracks and {award_count} awards.")

    def _create_notes(self, dojos: dict) -> None:
        """Notes across all four visibility levels — TODO 1.8.1/1.8.2/1.8.3.

        ⚠ The same gap the rank tracks had: the levels were implemented and the
        demo used none of them, so the notes tab, the pinned header alerts and
        the whole visibility filter were invisible to anyone clicking through.
        An empty tab teaches the reader that the feature is missing.

        Every level appears, including `private`, precisely so that signing in as
        one instructor and then another shows a *different* set of notes on the
        same student. That difference is the feature.
        """
        # Demo wording. ⚠ Replace before showing this to a real dojo.
        by_level = {
            Note.Visibility.INSTRUCTORS: [
                "Struggling with the turn in heian nidan — worth five minutes at the start.",
                "Grading-ready on kata, still hesitant in kumite.",
                "Left knee strapped this month; keep an eye on the stances.",
            ],
            Note.Visibility.PARENT_VISIBLE: [
                "Excellent focus this term. Ready to be pushed a little harder.",
                "Has grown out of the current gi — a size up before the next grading.",
            ],
            Note.Visibility.ADMINS: [
                "Fees discussed with the family; office to follow up next month.",
                "Sibling discount to be applied from the next billing run.",
            ],
            Note.Visibility.PRIVATE: [
                "My own reminder: pair with a calmer partner next session.",
            ],
        }
        levels = list(by_level)
        weights = [60, 20, 15, 5]

        note_count = 0
        pinned_count = 0
        for org, org_dojos in dojos.items():
            for dojo in org_dojos:
                instructors = list(
                    Person.objects.filter(
                        organization=org,
                        role_assignments__dojo=dojo,
                        role_assignments__role__in=[Role.INSTRUCTOR, Role.DOJO_ADMIN],
                    ).distinct()
                )
                students = list(StudentProfile.objects.filter(home_dojo=dojo))
                if not instructors or not students:
                    continue

                for profile in students:
                    # Not every student has been written about. A file that is
                    # empty for most people is the honest shape of this feature.
                    if random.random() > 0.45:
                        continue

                    # ⚠ Several notes at *different* levels on the same student,
                    # written by *different* instructors. One note per student
                    # cannot show the visibility rules working: the demo has to
                    # be able to sign in as two people and get two answers about
                    # the same child.
                    chosen = set()
                    for _ in range(random.randint(1, 3)):
                        chosen.add(random.choices(levels, weights=weights)[0])
                    for level in chosen:
                        # One note in eight is pinned, so the student header's
                        # alert strip (1.8.3) has something to surface.
                        pinned = random.randint(1, 8) == 1
                        Note.objects.create(
                            organization=org,
                            author=random.choice(instructors),
                            subject_type=Note.SubjectType.STUDENT,
                            subject_id=profile.person_id,
                            body=random.choice(by_level[level]),
                            visibility=level,
                            pinned=pinned,
                        )
                        note_count += 1
                        pinned_count += 1 if pinned else 0

        self.stdout.write(f"  Created {note_count} notes ({pinned_count} pinned).")

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
