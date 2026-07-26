"""GuardianLink and EmergencyContact — TODO 1.1.3, 1.1.5."""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    EmergencyContact,
    GuardianLink,
    Organization,
    Person,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def other_org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Other Org", slug="other-org")


@pytest.fixture
def dojo(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo A", slug="dojo-a")


@pytest.fixture
def guardian_person(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(
            organization=org, given_name="Yuki", family_name="Tanaka"
        )


@pytest.fixture
def student_person(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(
            organization=org, given_name="Hiro", family_name="Tanaka"
        )


@pytest.fixture
def link(guardian_person, student_person):
    with allow_unscoped("test setup"):
        return GuardianLink.objects.create(
            guardian=guardian_person,
            student=student_person,
            relationship=GuardianLink.Relationship.MOTHER,
        )


# -- self-guardian check constraint -------------------------------------------


class TestSelfGuardianConstraint:
    def test_guardian_may_not_be_their_own_student(self, guardian_person):
        with allow_unscoped("test setup"):
            with pytest.raises(IntegrityError):
                GuardianLink.objects.create(
                    guardian=guardian_person,
                    student=guardian_person,
                    relationship=GuardianLink.Relationship.OTHER,
                )


# -- unique (guardian, student) constraint ------------------------------------


class TestUniqueConstraint:
    def test_duplicate_guardian_student_pair_rejected(
        self, guardian_person, student_person
    ):
        with allow_unscoped("test setup"):
            GuardianLink.objects.create(
                guardian=guardian_person,
                student=student_person,
                relationship=GuardianLink.Relationship.MOTHER,
            )
            with pytest.raises(IntegrityError):
                GuardianLink.objects.create(
                    guardian=guardian_person,
                    student=student_person,
                    relationship=GuardianLink.Relationship.FATHER,
                )

    def test_same_guardian_can_link_to_different_students(self, org, guardian_person):
        with allow_unscoped("test setup"):
            s1 = Person.objects.create(
                organization=org, given_name="Child1", family_name="Tanaka"
            )
            s2 = Person.objects.create(
                organization=org, given_name="Child2", family_name="Tanaka"
            )
            GuardianLink.objects.create(
                guardian=guardian_person,
                student=s1,
                relationship=GuardianLink.Relationship.MOTHER,
            )
            GuardianLink.objects.create(
                guardian=guardian_person,
                student=s2,
                relationship=GuardianLink.Relationship.MOTHER,
            )
        assert GuardianLink.objects.for_actor(
            Actor(user_id=None, person_id=None, organization_id=org.pk)
        ).count() == 2

    def test_different_guardians_can_link_to_same_student(
        self, org, student_person
    ):
        with allow_unscoped("test setup"):
            g1 = Person.objects.create(
                organization=org, given_name="Mom", family_name="Tanaka"
            )
            g2 = Person.objects.create(
                organization=org, given_name="Dad", family_name="Tanaka"
            )
            GuardianLink.objects.create(
                guardian=g1,
                student=student_person,
                relationship=GuardianLink.Relationship.MOTHER,
            )
            GuardianLink.objects.create(
                guardian=g2,
                student=student_person,
                relationship=GuardianLink.Relationship.FATHER,
            )
        assert GuardianLink.objects.for_actor(
            Actor(user_id=None, person_id=None, organization_id=org.pk)
        ).count() == 2


# -- four booleans are independent -------------------------------------------


class TestBooleanIndependence:
    def test_all_booleans_default_false(self, link):
        assert link.is_primary_contact is False
        assert link.is_emergency_contact is False
        assert link.is_financially_responsible is False
        assert link.has_custody is False

    def test_set_only_primary_contact(self, link):
        link.is_primary_contact = True
        link.save(update_fields=["is_primary_contact"])
        link.refresh_from_db()
        assert link.is_primary_contact is True
        assert link.is_emergency_contact is False
        assert link.is_financially_responsible is False
        assert link.has_custody is False

    def test_set_only_emergency_contact(self, link):
        link.is_emergency_contact = True
        link.save(update_fields=["is_emergency_contact"])
        link.refresh_from_db()
        assert link.is_primary_contact is False
        assert link.is_emergency_contact is True
        assert link.is_financially_responsible is False
        assert link.has_custody is False

    def test_set_only_financially_responsible(self, link):
        link.is_financially_responsible = True
        link.save(update_fields=["is_financially_responsible"])
        link.refresh_from_db()
        assert link.is_primary_contact is False
        assert link.is_emergency_contact is False
        assert link.is_financially_responsible is True
        assert link.has_custody is False

    def test_set_only_custody(self, link):
        link.has_custody = True
        link.save(update_fields=["has_custody"])
        link.refresh_from_db()
        assert link.is_primary_contact is False
        assert link.is_emergency_contact is False
        assert link.is_financially_responsible is False
        assert link.has_custody is True

    def test_divorced_parent_scenario(self, org, student_person):
        """The paying parent is not the emergency contact and does not have custody."""
        with allow_unscoped("test setup"):
            mom = Person.objects.create(
                organization=org, given_name="Mom", family_name="Tanaka"
            )
            dad = Person.objects.create(
                organization=org, given_name="Dad", family_name="Tanaka"
            )
            mom_link = GuardianLink.objects.create(
                guardian=mom,
                student=student_person,
                relationship=GuardianLink.Relationship.MOTHER,
                is_primary_contact=True,
                is_emergency_contact=True,
                has_custody=True,
            )
            dad_link = GuardianLink.objects.create(
                guardian=dad,
                student=student_person,
                relationship=GuardianLink.Relationship.FATHER,
                is_financially_responsible=True,
                has_custody=False,
                is_emergency_contact=False,
            )

        assert mom_link.is_primary_contact is True
        assert mom_link.has_custody is True
        assert dad_link.is_financially_responsible is True
        assert dad_link.has_custody is False
        assert dad_link.is_emergency_contact is False


# -- tenant isolation ---------------------------------------------------------


class TestTenantIsolation:
    def test_cross_org_sees_nothing(self, link, other_org):
        outsider = Actor(user_id=None, person_id=None, organization_id=other_org.pk)
        assert GuardianLink.objects.for_actor(outsider).count() == 0

    def test_owning_org_sees_it(self, link, org):
        actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
        assert GuardianLink.objects.for_actor(actor).count() == 1


# -- str ----------------------------------------------------------------------


class TestStr:
    def test_str_is_readable(self, link):
        rendered = str(link)
        assert "Yuki" in rendered
        assert "Hiro" in rendered
        assert "Mother" in rendered


# -- EmergencyContact ---------------------------------------------------------


class TestEmergencyContact:
    @pytest.fixture
    def person(self, org):
        with allow_unscoped("test setup"):
            return Person.objects.create(
                organization=org, given_name="Kenji", family_name="Sato"
            )

    @pytest.fixture
    def contact(self, person):
        with allow_unscoped("test setup"):
            return EmergencyContact.objects.create(
                person=person,
                name="Aunt Srey",
                phone="+85512345678",
                relationship="Aunt",
                priority=2,
            )

    def test_creation(self, contact):
        assert contact.name == "Aunt Srey"
        assert contact.phone == "+85512345678"
        assert contact.priority == 2

    def test_str(self, contact):
        rendered = str(contact)
        assert "Aunt Srey" in rendered
        assert "+85512345678" in rendered

    def test_ordering_by_priority(self, org, person):
        with allow_unscoped("test setup"):
            EmergencyContact.objects.create(
                person=person, name="Low", phone="111", priority=3
            )
            EmergencyContact.objects.create(
                person=person, name="High", phone="222", priority=1
            )
            EmergencyContact.objects.create(
                person=person, name="Mid", phone="333", priority=2
            )
        contacts = list(
            EmergencyContact.objects.for_actor(
                Actor(user_id=None, person_id=None, organization_id=org.pk)
            )
        )
        assert [c.priority for c in contacts] == [1, 2, 3]

    def test_default_priority_is_one(self, person):
        with allow_unscoped("test setup"):
            contact = EmergencyContact.objects.create(
                person=person, name="Default", phone="999"
            )
        assert contact.priority == 1

    def test_relationship_is_optional(self, person):
        with allow_unscoped("test setup"):
            contact = EmergencyContact.objects.create(
                person=person, name="No Rel", phone="888"
            )
        assert contact.relationship == ""

    def test_tenant_isolation(self, contact, other_org):
        outsider = Actor(user_id=None, person_id=None, organization_id=other_org.pk)
        assert EmergencyContact.objects.for_actor(outsider).count() == 0

    def test_for_organization_works(self, contact, org):
        assert EmergencyContact.objects.for_organization(org.pk).count() == 1
