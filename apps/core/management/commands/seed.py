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
from datetime import date

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.core.scoping import allow_unscoped
from apps.identity.models import (
    Dojo,
    GovernanceModel,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)

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
                for model in [RoleAssignment, User, Person, Dojo, Organization]:
                    try:
                        model.objects.all().delete()
                    except Exception:
                        pass  # table may not exist yet

            self.stdout.write("Seeding organisations...")
            orgs = self._create_organizations()

            self.stdout.write("Seeding dojos...")
            dojos = self._create_dojos(orgs)

            self.stdout.write("Seeding people and roles...")
            self._create_people(dojos)

        self.stdout.write(self.style.SUCCESS("Done! Seed data created."))

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
                    student_person = self._create_person(
                        org, given, family, org_slug=org.slug, is_khmer=is_khmer
                    )

                    # 70% of students are minors — create a guardian
                    is_minor = random.random() > 0.3
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
                    student_count += 1

        self.stdout.write(f"  Created {student_count} students across all dojos.")

    def _create_person(
        self,
        org: Organization,
        given_name: str,
        family_name: str,
        *,
        org_slug: str,
        is_khmer: bool = True,
    ) -> Person:
        dob_year = random.randint(1970, 2020)
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
