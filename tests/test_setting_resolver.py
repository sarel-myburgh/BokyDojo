"""Setting hierarchy resolution — TODO 0.3.7, plan §13.2."""

from __future__ import annotations

import uuid

import pytest

from apps.core.scoping import allow_unscoped
from apps.core.setting_registry import (
    InvalidSettingValue,
    Scope,
    UnknownSetting,
    get_definition,
)
from apps.core.setting_resolver import (
    ScopeChain,
    clear_value,
    resolve,
    resolve_many,
    set_value,
)
from apps.identity.models import Dojo, Organization

pytestmark = pytest.mark.django_db

KIOSK = "attendance.kiosk_display_mode"
PIN = "attendance.pin_policy"

TEMPLATE_ID = uuid.UUID("00000000-0000-7000-8000-0000000000c1")
SESSION_ID = uuid.UUID("00000000-0000-7000-8000-0000000000c2")
STUDENT_ID = uuid.UUID("00000000-0000-7000-8000-0000000000c3")


@pytest.fixture
def org_and_dojo():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Setting Org", slug="setting-org")
        dojo = Dojo.objects.create(organization=org, name="Main", slug="main")
    return org, dojo


@pytest.fixture
def chain(org_and_dojo):
    org, dojo = org_and_dojo
    return ScopeChain(
        organization_id=org.pk,
        dojo_id=dojo.pk,
        class_template_id=TEMPLATE_ID,
        class_session_id=SESSION_ID,
        student_id=STUDENT_ID,
    )


# -- defaults and inheritance -------------------------------------------------


def test_unset_setting_returns_declared_default(chain):
    assert resolve(KIOSK, chain) == "photo_grid"


def test_org_value_is_inherited_by_everything_below(org_and_dojo, chain):
    org, _dojo = org_and_dojo
    set_value(KIOSK, "name_list", organization_id=org.pk, scope_type=Scope.ORG)
    assert resolve(KIOSK, chain) == "name_list"


def test_more_specific_scope_overrides_less_specific(org_and_dojo, chain):
    org, dojo = org_and_dojo
    set_value(KIOSK, "name_list", organization_id=org.pk, scope_type=Scope.ORG)
    set_value(KIOSK, "photo_grid", organization_id=org.pk, scope_type=Scope.DOJO, scope_id=dojo.pk)
    assert resolve(KIOSK, chain) == "photo_grid"


def test_most_specific_level_wins_across_the_whole_chain(org_and_dojo, chain):
    org, dojo = org_and_dojo
    set_value(KIOSK, "name_list", organization_id=org.pk, scope_type=Scope.ORG)
    set_value(KIOSK, "both", organization_id=org.pk, scope_type=Scope.DOJO, scope_id=dojo.pk)
    set_value(
        KIOSK,
        "photo_grid",
        organization_id=org.pk,
        scope_type=Scope.CLASS_SESSION,
        scope_id=SESSION_ID,
    )
    assert resolve(KIOSK, chain) == "photo_grid"


def test_levels_absent_from_the_chain_are_skipped(org_and_dojo):
    """A dojo-level override must not leak into a chain with no dojo."""
    org, dojo = org_and_dojo
    set_value(KIOSK, "name_list", organization_id=org.pk, scope_type=Scope.DOJO, scope_id=dojo.pk)
    assert resolve(KIOSK, ScopeChain(organization_id=org.pk)) == "photo_grid"


def test_clearing_an_override_restores_inheritance(org_and_dojo, chain):
    org, dojo = org_and_dojo
    set_value(KIOSK, "name_list", organization_id=org.pk, scope_type=Scope.ORG)
    set_value(KIOSK, "both", organization_id=org.pk, scope_type=Scope.DOJO, scope_id=dojo.pk)
    assert resolve(KIOSK, chain) == "both"

    clear_value(KIOSK, organization_id=org.pk, scope_type=Scope.DOJO, scope_id=dojo.pk)
    assert resolve(KIOSK, chain) == "name_list"


# -- strictest-wins resolution (plan §13.2) -----------------------------------


def test_student_required_beats_class_off(org_and_dojo, chain):
    """The worked example from the plan: a class set to 'off' must not be able to
    downgrade a student individually marked 'required'."""
    org, _dojo = org_and_dojo
    set_value(
        PIN, "off", organization_id=org.pk, scope_type=Scope.CLASS_TEMPLATE, scope_id=TEMPLATE_ID
    )
    set_value(
        PIN, "required", organization_id=org.pk, scope_type=Scope.STUDENT, scope_id=STUDENT_ID
    )
    assert resolve(PIN, chain) == "required"


def test_class_required_beats_student_off(org_and_dojo, chain):
    """Strictness wins in both directions — a student cannot opt out of a
    class-wide requirement either."""
    org, _dojo = org_and_dojo
    set_value(
        PIN,
        "required",
        organization_id=org.pk,
        scope_type=Scope.CLASS_TEMPLATE,
        scope_id=TEMPLATE_ID,
    )
    set_value(PIN, "off", organization_id=org.pk, scope_type=Scope.STUDENT, scope_id=STUDENT_ID)
    assert resolve(PIN, chain) == "required"


def test_strictest_picks_the_highest_anywhere_in_the_chain(org_and_dojo, chain):
    org, dojo = org_and_dojo
    set_value(PIN, "off", organization_id=org.pk, scope_type=Scope.ORG)
    set_value(PIN, "optional", organization_id=org.pk, scope_type=Scope.DOJO, scope_id=dojo.pk)
    set_value(PIN, "off", organization_id=org.pk, scope_type=Scope.STUDENT, scope_id=STUDENT_ID)
    assert resolve(PIN, chain) == "optional"


def test_strictest_never_falls_below_the_default(org_and_dojo, chain):
    assert resolve(PIN, chain) == "off"


# -- validation ---------------------------------------------------------------


def test_unknown_key_raises(chain):
    with pytest.raises(UnknownSetting):
        resolve("attendance.not_a_real_setting", chain)


def test_value_outside_choices_is_rejected(org_and_dojo):
    org, _dojo = org_and_dojo
    with pytest.raises(InvalidSettingValue):
        set_value(KIOSK, "hologram", organization_id=org.pk, scope_type=Scope.ORG)


def test_setting_at_a_disallowed_scope_is_rejected(org_and_dojo):
    """kiosk_display_mode is a class-level concern; it has no per-student meaning."""
    org, _dojo = org_and_dojo
    with pytest.raises(InvalidSettingValue):
        set_value(
            KIOSK,
            "name_list",
            organization_id=org.pk,
            scope_type=Scope.STUDENT,
            scope_id=STUDENT_ID,
        )


def test_org_scope_rejects_a_scope_id(org_and_dojo):
    org, _dojo = org_and_dojo
    with pytest.raises(ValueError):
        set_value(KIOSK, "name_list", organization_id=org.pk, scope_type=Scope.ORG, scope_id=org.pk)


def test_non_org_scope_requires_a_scope_id(org_and_dojo):
    org, _dojo = org_and_dojo
    with pytest.raises(ValueError):
        set_value(KIOSK, "name_list", organization_id=org.pk, scope_type=Scope.DOJO)


# -- tenancy ------------------------------------------------------------------


def test_another_organisations_settings_are_invisible(org_and_dojo, chain):
    """Settings are resolved without an actor, so the org filter is the only
    thing standing between tenants. Assert it holds."""
    org, _dojo = org_and_dojo
    with allow_unscoped("test setup"):
        other = Organization.objects.create(name="Other", slug="other-org")

    set_value(KIOSK, "name_list", organization_id=other.pk, scope_type=Scope.ORG)
    assert resolve(KIOSK, chain) == "photo_grid"
    assert resolve(KIOSK, ScopeChain(organization_id=other.pk)) == "name_list"


# -- batching -----------------------------------------------------------------


def test_resolve_many_returns_all_keys(org_and_dojo, chain):
    org, _dojo = org_and_dojo
    set_value(KIOSK, "both", organization_id=org.pk, scope_type=Scope.ORG)
    result = resolve_many([KIOSK, PIN], chain)
    assert result == {KIOSK: "both", PIN: "off"}


def test_resolve_many_uses_a_single_query(django_assert_num_queries, chain):
    with django_assert_num_queries(1):
        resolve_many([KIOSK, PIN, "attendance.catchup_window_days"], chain)


# -- registry hygiene ---------------------------------------------------------


def test_every_declared_setting_has_a_valid_default():
    from apps.core.setting_registry import REGISTRY

    for key, definition in REGISTRY.items():
        assert definition.key == key
        if definition.choices:
            assert definition.default in definition.choices
        if definition.strictness:
            definition.strictness_of(definition.default)


def test_strictest_settings_declare_a_strictness_order():
    from apps.core.setting_registry import REGISTRY, Resolution

    for definition in REGISTRY.values():
        if definition.resolution == Resolution.STRICTEST:
            assert definition.strictness, f"{definition.key} has no strictness order"


def test_get_definition_round_trips():
    assert get_definition(PIN).key == PIN
