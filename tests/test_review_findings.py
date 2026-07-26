"""Regressions for issues found in adversarial review — SEC 2.1, 2.2.

Found by Codex (gpt-5.6-sol) reviewing the tenancy and authorisation layer.
Each test here corresponds to a confirmed finding.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError

from apps.core.scoping import allow_unscoped
from apps.core.throttle import LOGIN_POLICY, enforce, peek, register_failure
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    StudentProfile,
    User,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_orgs():
    with allow_unscoped("test setup"):
        a = Organization.objects.create(name="A", slug="rf-a")
        b = Organization.objects.create(name="B", slug="rf-b")
        return {
            "a": a,
            "b": b,
            "dojo_b": Dojo.objects.create(organization=b, name="B Dojo", slug="rf-b-dojo"),
            "person_a": Person.objects.create(
                organization=a, given_name="Ana", family_name="A"
            ),
        }


# -- Finding: bulk writes bypass the same-organisation guard -------------------


def test_bulk_create_cannot_bypass_the_cross_organisation_guard(two_orgs):
    """`save()` enforces the invariant; `bulk_create()` never calls it.

    Without this, one queryset call plants a row spanning two tenants.
    """
    profile = StudentProfile(
        person=two_orgs["person_a"],
        home_dojo=two_orgs["dojo_b"],
    )
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        StudentProfile.objects.bulk_create([profile])


def test_bulk_create_still_works_for_valid_rows(two_orgs):
    with allow_unscoped("test setup"):
        created = StudentProfile.objects.bulk_create(
            [StudentProfile(person=two_orgs["person_a"])]
        )
    assert len(created) == 1


def test_queryset_update_cannot_repoint_a_guarded_field(two_orgs):
    """`QuerySet.update()` issues raw SQL and never touches `save()`, so it
    could otherwise move a row into another organisation after the fact."""
    with allow_unscoped("test setup"):
        StudentProfile.objects.create(person=two_orgs["person_a"])

    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        StudentProfile.objects.for_organization(two_orgs["a"].pk).update(
            home_dojo=two_orgs["dojo_b"]
        )


def test_queryset_update_of_unguarded_fields_still_works(two_orgs):
    with allow_unscoped("test setup"):
        StudentProfile.objects.create(person=two_orgs["person_a"])
        updated = StudentProfile.objects.for_organization(two_orgs["a"].pk).update(
            hold_reason="injury"
        )
    assert updated == 1


# -- Finding: deactivated people retain a working Actor -----------------------


def test_a_soft_deleted_person_gets_no_scope(two_orgs):
    """Soft-deleting a person must revoke their access, not merely hide them
    from lists. Otherwise a removed instructor keeps a live session."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=two_orgs["a"], given_name="Gone", family_name="Away"
        )
        user = User.objects.create_user("gone@example.com", "pw", person=person)
        person.soft_delete()

    actor = actor_for_user(user)
    assert actor.organization_id is None
    assert actor.is_anonymous


def test_a_deactivated_person_gets_no_scope(two_orgs):
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=two_orgs["a"], given_name="Sus", family_name="Pended",
            is_active=False,
        )
        user = User.objects.create_user("sus@example.com", "pw", person=person)

    actor = actor_for_user(user)
    assert actor.organization_id is None


def test_an_active_person_still_gets_their_scope(two_orgs):
    with allow_unscoped("test setup"):
        user = User.objects.create_user(
            "fine@example.com", "pw", person=two_orgs["person_a"]
        )
    actor = actor_for_user(user)
    assert actor.organization_id == two_orgs["a"].pk


def test_an_inactive_user_gets_no_scope(two_orgs):
    with allow_unscoped("test setup"):
        user = User.objects.create_user(
            "off@example.com", "pw", person=two_orgs["person_a"], is_active=False
        )
    assert actor_for_user(user).organization_id is None


# -- Finding: lockout can be weaponised against a known account ---------------


def test_lockout_of_one_identifier_does_not_block_a_clean_source(two_orgs):
    """An attacker who knows a parent's email could otherwise lock them out of
    their own invoices at will, indefinitely, by failing logins on purpose.

    Failures are counted per (identifier, source) as well as per identifier, so
    a single hostile source cannot deny service to the account holder.
    """
    victim = "victim@example.com"
    attacker_source = "203.0.113.9"
    victim_source = "198.51.100.4"

    for _ in range(LOGIN_POLICY.max_attempts + 2):
        register_failure("login", victim, LOGIN_POLICY, source=attacker_source)

    assert peek("login", victim, LOGIN_POLICY, source=attacker_source).locked is True
    # The account holder, arriving from their own address, is not locked out.
    assert peek("login", victim, LOGIN_POLICY, source=victim_source).locked is False
    enforce("login", victim, LOGIN_POLICY, source=victim_source)


def test_a_single_source_is_still_stopped_after_enough_failures(two_orgs):
    """The DoS fix must not weaken brute-force protection."""
    for _ in range(LOGIN_POLICY.max_attempts):
        register_failure("login", "target@example.com", LOGIN_POLICY, source="203.0.113.9")
    assert peek("login", "target@example.com", LOGIN_POLICY, source="203.0.113.9").locked


def test_distributed_failures_still_lock_the_account(two_orgs):
    """Per-source counting must not let a botnet grind an account down for
    free — the account-wide counter still exists, just at a higher threshold."""
    for index in range(LOGIN_POLICY.max_attempts * 4):
        register_failure(
            "login", "spread@example.com", LOGIN_POLICY, source=f"203.0.113.{index}"
        )
    assert peek("login", "spread@example.com", LOGIN_POLICY, source="198.51.100.1").locked


def test_source_is_optional_for_non_network_scopes(two_orgs):
    """Kiosk PIN entry has no meaningful client IP — the device is the source."""
    for _ in range(LOGIN_POLICY.max_attempts):
        register_failure("pin", "student-1", LOGIN_POLICY)
    assert peek("pin", "student-1", LOGIN_POLICY).locked is True


_ = datetime  # keep the import meaningful if tests are extended


# -- Finding: ORM paths that bypassed the scope guard -------------------------


def test_iterator_is_guarded(two_orgs):
    """iterator() talks to the SQL compiler directly and never fills the result
    cache, so _fetch_all() never ran."""
    from apps.core.scoping import UnscopedAccessError

    with pytest.raises(UnscopedAccessError):
        list(Person.objects.all().iterator())


def test_raw_sql_is_refused_on_a_tenant_model(two_orgs):
    """RawQuerySet is not a ScopedQuerySet, so no guard can run on it."""
    from apps.core.scoping import UnscopedAccessError

    with pytest.raises(UnscopedAccessError):
        list(Person.objects.raw(f"SELECT * FROM {Person._meta.db_table}"))


def test_raw_sql_is_permitted_inside_an_explicit_escape_hatch(two_orgs):
    with allow_unscoped("deliberate cross-tenant maintenance query"):
        rows = list(Person.objects.raw(f"SELECT * FROM {Person._meta.db_table}"))
    assert rows


def test_union_with_an_unscoped_operand_is_refused(two_orgs):
    """The combined queryset is cloned from the left operand, so it inherited a
    valid scope flag while absorbing the right operand's rows."""
    from apps.core.scoping import Actor, UnscopedAccessError

    actor = Actor(user_id=None, person_id=None, organization_id=two_orgs["a"].pk)
    scoped = Person.objects.for_actor(actor)
    unscoped_foreign = Person.objects.filter(organization_id=two_orgs["b"].pk)

    with pytest.raises(UnscopedAccessError):
        scoped.union(unscoped_foreign)
    with pytest.raises(UnscopedAccessError):
        scoped | unscoped_foreign


def test_union_of_two_scoped_querysets_is_allowed(two_orgs):
    from apps.core.scoping import Actor

    actor = Actor(user_id=None, person_id=None, organization_id=two_orgs["a"].pk)
    # order_by() cleared: SQLite refuses ORDER BY inside a compound statement,
    # and Person carries a default ordering.
    scoped = Person.objects.for_actor(actor).order_by()
    assert list(scoped.union(scoped))


# -- Finding: soft-deleted rows resurfaced via the actorless path -------------


def test_for_organization_excludes_soft_deleted_rows(two_orgs):
    with allow_unscoped("test setup"):
        gone = Person.objects.create(
            organization=two_orgs["a"], given_name="Gone", family_name="Away"
        )
        gone.soft_delete()

    visible = Person.objects.for_organization(two_orgs["a"].pk).filter(pk=gone.pk)
    assert list(visible) == []


# -- Finding: audit log was only immutable at the instance level --------------


def test_audit_entries_cannot_be_deleted_by_queryset(two_orgs):
    """An attacker reaching the ORM could otherwise erase the evidence."""
    from apps.core.audit import record
    from apps.core.models import AuditLog

    entry = record(AuditLog.Action.PERMISSION_CHANGE, subject_type="x", subject_id="y")
    with pytest.raises(NotImplementedError):
        AuditLog.objects.filter(pk=entry.pk).delete()


def test_audit_entries_cannot_be_rewritten_by_queryset(two_orgs):
    from apps.core.audit import record
    from apps.core.models import AuditLog

    entry = record(AuditLog.Action.EXPORT, subject_type="x", subject_id="y")
    with pytest.raises(NotImplementedError):
        AuditLog.objects.filter(pk=entry.pk).update(action=AuditLog.Action.VIEW)


def test_retention_purge_is_the_one_sanctioned_deletion_path(two_orgs):
    import datetime as _dt

    from django.utils import timezone

    from apps.core.audit import record
    from apps.core.models import AuditLog

    record(AuditLog.Action.VIEW, subject_type="x", subject_id="old")
    future = timezone.now() + _dt.timedelta(days=1)
    deleted, _detail = AuditLog.objects.purge_before(future)
    assert deleted >= 1
