"""Security gates for destructive/demo management commands."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.management.commands.seed import Command
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import ConsentPolicy, ConsentRecord, Organization

pytestmark = pytest.mark.django_db


def test_demo_seed_is_refused_when_environment_does_not_enable_it(settings):
    settings.DEMO_SEED_ENABLED = False

    with pytest.raises(CommandError, match="disabled"):
        call_command("seed")


def test_demo_seed_publishes_separate_clearly_labelled_consent_policies():
    with allow_unscoped("demo seed test"):
        org = Organization.objects.create(name="Demo Org", slug="demo-policy-org")
        Command()._create_consent_policies([org])

        policies = ConsentPolicy.objects.for_organization(org.pk)
        medical = policies.get(consent_type=ConsentRecord.Type.MEDICAL)
        waiver = policies.get(consent_type=ConsentRecord.Type.WAIVER)

    assert medical.version != waiver.version
    assert medical.body.startswith("DEMO ONLY")
    assert waiver.body.startswith("DEMO ONLY")
    assert medical.is_active and waiver.is_active


def test_demo_clear_can_replace_immutable_demo_policies():
    with allow_unscoped("demo clear test"):
        org = Organization.objects.create(name="Old Demo", slug="old-demo-policy")
        ConsentPolicy.objects.create(
            organization=org,
            consent_type=ConsentRecord.Type.WAIVER,
            version="old-demo-v1",
            title="Old demo waiver",
            body="DEMO ONLY",
        )
        Command()._clear()

    assert Organization.objects.count() == 0
    assert ConsentPolicy.objects.for_actor(Actor.system()).count() == 0
