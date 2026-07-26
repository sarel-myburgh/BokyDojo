"""StudentProfile — TODO 1.1.1, plan §4.2."""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo, Organization, Person, StudentProfile

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
def person(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(
            organization=org, given_name="Kenji", family_name="Sato"
        )


@pytest.fixture
def profile(dojo, person):
    with allow_unscoped("test setup"):
        return StudentProfile.objects.create(person=person, home_dojo=dojo)


# -- lifecycle status transitions --------------------------------------------


class TestStatusLifecycle:
    def test_default_status_is_prospect(self, person, dojo):
        with allow_unscoped("test setup"):
            profile = StudentProfile.objects.create(person=person, home_dojo=dojo)
        assert profile.status == StudentProfile.Status.PROSPECT

    def test_prospect_to_trial(self, profile):
        profile.status = StudentProfile.Status.TRIAL
        profile.save(update_fields=["status"])
        assert profile.status == StudentProfile.Status.TRIAL

    def test_trial_to_active(self, profile):
        profile.status = StudentProfile.Status.TRIAL
        profile.save(update_fields=["status"])
        profile.status = StudentProfile.Status.ACTIVE
        profile.save(update_fields=["status"])
        assert profile.status == StudentProfile.Status.ACTIVE

    def test_active_to_on_hold_with_reason(self, profile):
        profile.status = StudentProfile.Status.ACTIVE
        profile.save(update_fields=["status"])
        profile.status = StudentProfile.Status.ON_HOLD
        profile.hold_reason = "Knee injury"
        profile.save(update_fields=["status", "hold_reason"])
        assert profile.status == StudentProfile.Status.ON_HOLD
        assert profile.hold_reason == "Knee injury"

    def test_on_hold_to_active_clears_hold_reason(self, profile):
        profile.status = StudentProfile.Status.ON_HOLD
        profile.hold_reason = "Travel"
        profile.save(update_fields=["status", "hold_reason"])
        profile.status = StudentProfile.Status.ACTIVE
        profile.hold_reason = ""
        profile.save(update_fields=["status", "hold_reason"])
        assert profile.status == StudentProfile.Status.ACTIVE
        assert profile.hold_reason == ""

    def test_active_to_lapsed(self, profile):
        profile.status = StudentProfile.Status.ACTIVE
        profile.save(update_fields=["status"])
        profile.status = StudentProfile.Status.LAPSED
        profile.save(update_fields=["status"])
        assert profile.status == StudentProfile.Status.LAPSED

    def test_lapsed_to_alumni(self, profile):
        profile.status = StudentProfile.Status.LAPSED
        profile.save(update_fields=["status"])
        profile.status = StudentProfile.Status.ALUMNI
        profile.save(update_fields=["status"])
        assert profile.status == StudentProfile.Status.ALUMNI


# -- properties ---------------------------------------------------------------


class TestProperties:
    def test_is_active_true_only_for_active(self, profile):
        for status in StudentProfile.Status:
            profile.status = status
            profile.save(update_fields=["status"])
            assert profile.is_active is (status == StudentProfile.Status.ACTIVE)

    def test_is_training_true_for_active_and_trial(self, profile):
        for status in StudentProfile.Status:
            profile.status = status
            profile.save(update_fields=["status"])
            expected = status in (StudentProfile.Status.ACTIVE, StudentProfile.Status.TRIAL)
            assert profile.is_training is expected


# -- tenant isolation ---------------------------------------------------------


class TestTenantIsolation:
    def test_cross_org_sees_nothing(self, profile, other_org):
        outsider = Actor(user_id=None, person_id=None, organization_id=other_org.pk)
        assert StudentProfile.objects.for_actor(outsider).count() == 0

    def test_owning_org_sees_it(self, profile, org):
        actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
        assert StudentProfile.objects.for_actor(actor).count() == 1

    def test_for_organization_works(self, profile, org):
        assert StudentProfile.objects.for_organization(org.pk).count() == 1


# -- one-to-one constraint ----------------------------------------------------


class TestOneToOne:
    def test_cannot_create_two_profiles_for_same_person(self, person, dojo):
        with allow_unscoped("test setup"):
            StudentProfile.objects.create(person=person, home_dojo=dojo)
            with pytest.raises(IntegrityError):
                StudentProfile.objects.create(person=person)


# -- optional home_dojo -------------------------------------------------------


class TestHomeDojo:
    def test_home_dojo_is_optional(self, person):
        with allow_unscoped("test setup"):
            profile = StudentProfile.objects.create(person=person)
        assert profile.home_dojo is None
