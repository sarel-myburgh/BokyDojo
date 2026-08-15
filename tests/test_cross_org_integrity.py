"""Cross-organisation referential integrity — SEC 2.2.

The scoping layer decides *who can read* a row. It says nothing about whether a
row should have been creatable in the first place. A record that references two
different organisations is a tenant boundary violation baked into the data:
scoping will faithfully show it to one side and hide it from the other, and
neither view is correct.

These models are reached through an indirect tenant path, which is exactly where
the mismatch hides:

    StudentProfile.tenant_org_path = "person__organization_id"
                  .tenant_dojo_path = "home_dojo_id"      <- different org?
    GuardianLink.tenant_org_path   = "student__organization_id"
                 .guardian                                 <- different org?
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.core.scoping import allow_unscoped
from apps.identity.models import (
    Dojo,
    GuardianLink,
    Organization,
    Person,
    StudentProfile,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_orgs():
    with allow_unscoped("test setup"):
        a = Organization.objects.create(name="Org A", slug="org-a-xorg")
        b = Organization.objects.create(name="Org B", slug="org-b-xorg")
        return {
            "a": a,
            "b": b,
            "dojo_a": Dojo.objects.create(organization=a, name="A Dojo", slug="a-dojo"),
            "dojo_b": Dojo.objects.create(organization=b, name="B Dojo", slug="b-dojo"),
            "person_a": Person.objects.create(
                organization=a, given_name="Ana", family_name="Alpha"
            ),
            "person_b": Person.objects.create(organization=b, given_name="Ben", family_name="Beta"),
            "student_a": Person.objects.create(
                organization=a, given_name="Sam", family_name="Alpha"
            ),
        }


def test_guardian_must_belong_to_the_students_organisation(two_orgs):
    """A guardian from another organisation must not be linkable to our student.

    Without this the row is visible to the student's org (the tenant path runs
    through the student) while pointing at a person that org cannot otherwise
    see — a one-way window into another tenant's people.
    """
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        GuardianLink.objects.create(
            guardian=two_orgs["person_b"],
            student=two_orgs["student_a"],
            relationship=GuardianLink.Relationship.OTHER,
        )


def test_same_org_guardian_link_is_fine(two_orgs):
    with allow_unscoped("test setup"):
        link = GuardianLink.objects.create(
            guardian=two_orgs["person_a"],
            student=two_orgs["student_a"],
            relationship=GuardianLink.Relationship.MOTHER,
        )
    assert link.pk is not None


def test_home_dojo_must_belong_to_the_students_organisation(two_orgs):
    """A student's home dojo in another organisation would make the record
    visible under one tenant path and not the other."""
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        StudentProfile.objects.create(
            person=two_orgs["student_a"],
            home_dojo=two_orgs["dojo_b"],
        )


def test_same_org_home_dojo_is_fine(two_orgs):
    with allow_unscoped("test setup"):
        profile = StudentProfile.objects.create(
            person=two_orgs["student_a"],
            home_dojo=two_orgs["dojo_a"],
        )
    assert profile.pk is not None


def test_student_profile_without_a_home_dojo_is_allowed(two_orgs):
    """A prospect who has not picked a dojo yet."""
    with allow_unscoped("test setup"):
        profile = StudentProfile.objects.create(person=two_orgs["student_a"])
    assert profile.home_dojo_id is None


def test_instructor_must_belong_to_the_dojos_organisation(two_orgs):
    import datetime

    from apps.identity.models import InstructorAssignment

    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        InstructorAssignment.objects.create(
            dojo=two_orgs["dojo_a"],
            person=two_orgs["person_b"],
            started_on=datetime.date(2024, 1, 1),
        )


def test_same_org_instructor_assignment_is_fine(two_orgs):
    import datetime

    from apps.identity.models import InstructorAssignment

    with allow_unscoped("test setup"):
        assignment = InstructorAssignment.objects.create(
            dojo=two_orgs["dojo_a"],
            person=two_orgs["person_a"],
            started_on=datetime.date(2024, 1, 1),
        )
    assert assignment.pk is not None


def test_the_guard_is_declared_wherever_two_organisations_could_meet():
    """A model with more than one FK that carries an organisation needs the
    guard. This catches the next model added without it."""
    from django.apps import apps as django_apps

    from apps.core.models import TenantScopedModel

    # Provenance columns are set from the acting user, never from request data,
    # and constraining them would flag every model without protecting any
    # domain relationship.
    provenance = {"created_by", "deleted_by"}

    missing = []
    for model in django_apps.get_models():
        if not issubclass(model, TenantScopedModel) or model._meta.abstract:
            continue
        org_bearing = [
            field.name
            for field in model._meta.fields
            if field.is_relation
            and field.name not in provenance
            and field.related_model is not None
            and (
                field.related_model.__name__ == "Organization"
                or any(f.name == "organization" for f in field.related_model._meta.fields)
            )
        ]
        if len(org_bearing) > 1 and not model.same_organization_fields:
            missing.append(f"{model.__name__} ({', '.join(org_bearing)})")

    assert not missing, (
        "These models reference more than one organisation-bearing record but do "
        "not declare same_organization_fields, so a row could span two tenants: "
        + "; ".join(missing)
    )
