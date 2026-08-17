"""Class-type tags and the vocabulary behind them — TODO 1.4.10, plan §2 item 23.

The rule these exist to serve is "40 classes since the last grading, of which at
least 10 kata". Everything here protects the one failure mode that matters: a tag
that no rule will ever match, stored without complaint, discovered months later
when a student is wrongly held back from a grading.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError

from apps.core.scoping import allow_unscoped
from apps.core.setting_registry import (
    CLASS_TYPE_TAGS,
    InvalidSettingValue,
    Scope,
    SettingDefinition,
    validate_class_type_vocabulary,
)
from apps.core.setting_resolver import set_value
from apps.identity.models import Dojo, Organization
from apps.scheduling import class_types
from apps.scheduling.models import ClassSession, ClassTemplate

pytestmark = pytest.mark.django_db


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


def make_template(dojo, name="Adults", counts_toward=None):
    with allow_unscoped("test setup"):
        return ClassTemplate.objects.create(
            dojo=dojo,
            name=name,
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(18, 30),
            duration_minutes=90,
            active_from=datetime.date(2026, 1, 1),
            counts_toward=counts_toward if counts_toward is not None else [],
        )


# -- the vocabulary itself ----------------------------------------------------


def test_the_default_vocabulary_is_karate_shaped(org):
    assert class_types.vocabulary(org.pk) == (
        "kata",
        "kihon",
        "kumite",
        "conditioning",
        "grading_preparation",
    )


def test_an_organisation_can_replace_the_vocabulary_wholesale(org):
    """A BJJ club has no kata. The default is a starting point, not a schema."""
    set_value(
        CLASS_TYPE_TAGS.key,
        ["guard", "passing", "takedowns", "rolling"],
        organization_id=org.pk,
        scope_type=Scope.ORG,
    )

    assert class_types.vocabulary(org.pk) == ("guard", "passing", "takedowns", "rolling")


def test_the_vocabulary_cannot_be_set_at_dojo_scope(org, dojo):
    """⚠ Eligibility rules are written against these words.

    A dojo-level vocabulary would make "≥10 kata" mean different things at two
    dojos in one organisation, and a transferring student would gain or lose
    progress with no record of why.
    """
    with pytest.raises(InvalidSettingValue):
        set_value(
            CLASS_TYPE_TAGS.key,
            ["kata"],
            organization_id=org.pk,
            scope_type=Scope.DOJO,
            scope_id=dojo.pk,
        )


@pytest.mark.parametrize(
    "bad",
    [
        "kata",  # a bare string, not a list — would substring-match otherwise
        ["Kata"],  # upper case
        [" kata"],  # leading whitespace
        ["kata "],  # trailing whitespace
        ["grading prep"],  # internal whitespace
        ["kata", "kata"],  # duplicate
        [""],  # empty
        [123],  # not text
        ["x" * 51],  # too long
    ],
)
def test_a_malformed_vocabulary_is_refused(bad):
    with pytest.raises(InvalidSettingValue):
        validate_class_type_vocabulary(bad)


def test_a_setting_whose_default_fails_its_own_validator_dies_at_import():
    """The validator runs in __post_init__, so a bad default cannot ship."""
    with pytest.raises(InvalidSettingValue):
        SettingDefinition(
            key="scheduling.bogus",
            default=["Kata"],
            scopes=(Scope.ORG,),
            validator=validate_class_type_vocabulary,
        )


# -- tagging a template -------------------------------------------------------


def test_a_template_accepts_tags_from_the_vocabulary(dojo):
    template = make_template(dojo, counts_toward=["kata", "kihon"])
    template.refresh_from_db()

    assert template.counts_toward == ["kata", "kihon"]


def test_an_untagged_template_is_the_ordinary_case(dojo):
    assert make_template(dojo).counts_toward == []


def test_a_tag_outside_the_vocabulary_is_refused(dojo):
    with pytest.raises(ValidationError) as excinfo:
        make_template(dojo, counts_toward=["sparring"])

    assert "sparring" in str(excinfo.value)


def test_a_case_variant_is_refused_and_names_the_tag_that_exists(dojo):
    """⚠ The whole point. 'Kata' is never silently coerced to 'kata'.

    Coercing would make the rule and the tag agree by luck while hiding that
    somebody has two names for one thing; storing it as-is would make the rule
    match nothing, silently. So it is refused, and the message says which tag is
    real so the author can see it is a typo rather than a system fault.
    """
    with pytest.raises(ValidationError) as excinfo:
        make_template(dojo, counts_toward=["Kata"])

    message = str(excinfo.value)
    assert "Kata" in message
    assert "kata" in message
    assert "case-sensitive" in message


def test_a_duplicate_tag_is_refused(dojo):
    with pytest.raises(ValidationError):
        make_template(dojo, counts_toward=["kata", "kata"])


def test_a_non_list_value_is_refused(dojo):
    with pytest.raises(ValidationError):
        make_template(dojo, counts_toward="kata")


def test_a_non_text_tag_is_refused(dojo):
    with pytest.raises(ValidationError):
        make_template(dojo, counts_toward=["kata", 7])


def test_validation_happens_on_save_not_only_full_clean(dojo):
    """⚠ The seed, fixtures and every service write go through save().

    full_clean() on a tenant-scoped model raises UnscopedAccessError anyway, so
    it is not available as the enforcement point even if it were wanted.
    """
    template = make_template(dojo, counts_toward=["kata"])
    template.counts_toward = ["nonsense"]

    with pytest.raises(ValidationError):
        template.save()


def test_tags_follow_the_organisations_own_vocabulary(org, dojo):
    """A tag valid for one organisation is not valid for another."""
    set_value(
        CLASS_TYPE_TAGS.key,
        ["guard", "passing"],
        organization_id=org.pk,
        scope_type=Scope.ORG,
    )

    make_template(dojo, name="BJJ basics", counts_toward=["guard"])
    with pytest.raises(ValidationError):
        make_template(dojo, name="Karate", counts_toward=["kata"])


# -- reading them back --------------------------------------------------------


def _session(dojo, template, day):
    starts = datetime.datetime(2026, 6, day, 11, 0, tzinfo=datetime.UTC)
    with allow_unscoped("test setup"):
        return ClassSession.objects.create(
            dojo=dojo,
            template=template,
            starts_at=starts,
            ends_at=starts + datetime.timedelta(hours=1),
        )


def test_sessions_counting_toward_a_tag(dojo):
    kata = make_template(dojo, name="Saturday", counts_toward=["kata", "grading_preparation"])
    kihon_only = make_template(dojo, name="Little Dragons", counts_toward=["kihon"])
    counted = _session(dojo, kata, 10)
    ignored = _session(dojo, kihon_only, 11)

    with allow_unscoped("test read"):
        found = class_types.sessions_counting_toward(
            ClassSession.objects.all(), ClassTemplate.objects.all(), "kata"
        )
        found_ids = set(found.values_list("pk", flat=True))

    assert counted.pk in found_ids
    assert ignored.pk not in found_ids


def test_a_tag_is_matched_whole_not_as_a_substring(dojo):
    """⚠ Rules out the `icontains`-against-serialised-JSON shortcut.

    With the org vocabulary extended, `grading_preparation` must not be found by
    a search for `grading`, and `kata` must not match `kata_advanced`.
    """
    set_value(
        CLASS_TYPE_TAGS.key,
        ["kata", "kata_advanced", "grading_preparation"],
        organization_id=dojo.organization_id,
        scope_type=Scope.ORG,
    )
    advanced = make_template(dojo, name="Advanced", counts_toward=["kata_advanced"])
    _session(dojo, advanced, 10)

    with allow_unscoped("test read"):
        found = class_types.sessions_counting_toward(
            ClassSession.objects.all(), ClassTemplate.objects.all(), "kata"
        )

        assert found.count() == 0
        assert (
            class_types.sessions_counting_toward(
                ClassSession.objects.all(), ClassTemplate.objects.all(), "kata_advanced"
            ).count()
            == 1
        )


def test_a_one_off_session_counts_toward_nothing(dojo):
    """⚠ Recorded, not fixed — 3.6.2 has to decide what to do about it.

    Tags live on the template and a one-off session has none, so an ad-hoc kata
    seminar contributes to no eligibility rule.
    """
    kata = make_template(dojo, counts_toward=["kata"])
    _session(dojo, kata, 10)
    one_off = _session(dojo, None, 11)

    with allow_unscoped("test read"):
        found = class_types.sessions_counting_toward(
            ClassSession.objects.all(), ClassTemplate.objects.all(), "kata"
        )
        found_ids = set(found.values_list("pk", flat=True))

    assert one_off.pk not in found_ids


def test_the_query_helper_applies_no_tenant_filter_of_its_own(org, dojo):
    """It narrows what it is given. Passing an unscoped queryset is the caller's
    bug, and the docstring says so — this pins the contract."""
    other_org = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        other_dojo = Dojo.objects.create(
            organization=other_org, name="Other", slug="other-dojo", timezone="UTC"
        )
    mine = make_template(dojo, counts_toward=["kata"])
    theirs = make_template(other_dojo, name="Theirs", counts_toward=["kata"])

    with allow_unscoped("test read"):
        scoped_templates = ClassTemplate.objects.for_organization(org.pk)
        ids = class_types.templates_counting_toward(scoped_templates, "kata")

    assert mine.pk in ids
    assert theirs.pk not in ids
