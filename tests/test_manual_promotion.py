"""Manual promotion workflow - TODO 1.2.6."""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    User,
)
from apps.identity.permissions import PermissionDenied
from apps.ranks.models import Rank, RankAward, StudentStyleTrack
from apps.ranks.promotions import (
    BULK_PROMOTION_LIMIT,
    bulk_promote_students,
    promote_student,
    promotion_rank_choices,
)
from apps.ranks.seeding import create_shotokan_ladders
from apps.staffing.models import InstructorProfile

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(
        organization=org,
        given_name=role.title(),
        family_name="PromotionStaff",
    )
    RoleAssignment.objects.create(
        organization=org,
        person=person,
        role=role,
        scope_type=ScopeType.DOJO if dojo else ScopeType.ORG,
        dojo=dojo,
    )
    return User.objects.create_user(email=email, password=PASSWORD, person=person)


@pytest.fixture
def world():
    today = timezone.localdate()
    with allow_unscoped("manual promotion test setup"):
        org = Organization.objects.create(name="Promotion Org", slug="promotion-org")
        dojo_a = Dojo.objects.create(organization=org, name="Dojo A", slug="promotion-a")
        dojo_b = Dojo.objects.create(organization=org, name="Dojo B", slug="promotion-b")
        adult, junior = create_shotokan_ladders(org)
        student = Person.objects.create(
            organization=org,
            given_name="Ari",
            family_name="Student",
        )
        profile = StudentProfile.objects.create(person=student, home_dojo=dojo_a)
        other_student = Person.objects.create(
            organization=org,
            given_name="Bora",
            family_name="Student",
        )
        other_profile = StudentProfile.objects.create(person=other_student, home_dojo=dojo_b)
        track = StudentStyleTrack.objects.create(
            student=student,
            style=adult.style,
            ladder=adult,
            started_on=today - datetime.timedelta(days=365),
        )
        other_track = StudentStyleTrack.objects.create(
            student=other_student,
            style=adult.style,
            ladder=adult,
            started_on=today - datetime.timedelta(days=365),
        )
        ranks = list(Rank.objects.filter(ladder=adult).order_by("order"))
        junior_rank = Rank.objects.filter(ladder=junior).order_by("order").first()
        admin_a = _staff(org, Role.DOJO_ADMIN, "admin-a@promotion.test", dojo_a)
        admin_b = _staff(org, Role.DOJO_ADMIN, "admin-b@promotion.test", dojo_b)
        org_admin = _staff(org, Role.ORG_ADMIN, "org-admin@promotion.test")
        instructor = _staff(org, Role.INSTRUCTOR, "instructor@promotion.test", dojo_a)
    return locals()


def _bulk_profile(world, index, *, with_track=True):
    person = Person.objects.create(
        organization=world["org"],
        given_name=f"Bulk{index}",
        family_name="Student",
    )
    profile = StudentProfile.objects.create(person=person, home_dojo=world["dojo_a"])
    if with_track:
        StudentStyleTrack.objects.create(
            student=person,
            style=world["adult"].style,
            ladder=world["adult"],
            started_on=timezone.localdate() - datetime.timedelta(days=365),
        )
    return profile


def _promote(world, index=0, **overrides):
    values = {
        "profile": world["profile"],
        "track": world["track"],
        "rank": world["ranks"][index],
        "awarded_on": timezone.localdate(),
        "actor": actor_for_user(world["admin_a"]),
        "certificate_number": "CERT-001",
        "notes": "Private examiner note",
    }
    values.update(overrides)
    return promote_student(**values)


def test_manual_promotion_updates_rank_and_records_minimal_audit(world):
    award = _promote(world)
    world["track"].refresh_from_db()

    assert world["track"].current_rank_id == world["ranks"][0].pk
    assert award.awarded_by_id == world["admin_a"].person_id
    assert award.recognition == RankAward.Recognition.INTERNAL
    log = AuditLog.objects.get(action="rank_promote", subject_id=str(award.pk))
    assert log.after["rank_id"] == str(world["ranks"][0].pk)
    serialized = f"{log.before} {log.after} {log.note}"
    assert "Private examiner note" not in serialized
    assert "CERT-001" not in serialized


def test_rank_choices_only_offer_higher_ranks_on_the_same_ladder(world):
    _promote(world, 2)
    world["track"].refresh_from_db()
    choices = list(promotion_rank_choices(world["track"]))
    assert choices
    assert all(rank.ladder_id == world["track"].ladder_id for rank in choices)
    assert all(rank.order > world["ranks"][2].order for rank in choices)


def test_rejects_same_lower_cross_ladder_and_cross_student_targets(world):
    _promote(world, 2)
    for rank in (world["ranks"][0], world["ranks"][2], world["junior_rank"]):
        with pytest.raises(ValidationError):
            _promote(world, rank=rank)
    with pytest.raises(ValidationError, match="different student"):
        _promote(world, track=world["other_track"])


def test_rejects_dates_outside_valid_track_history(world):
    with pytest.raises(ValidationError, match="future"):
        _promote(world, awarded_on=timezone.localdate() + datetime.timedelta(days=1))
    with pytest.raises(ValidationError, match="before the track"):
        _promote(world, awarded_on=world["track"].started_on - datetime.timedelta(days=1))

    first_date = timezone.localdate() - datetime.timedelta(days=10)
    _promote(world, awarded_on=first_date)
    with pytest.raises(ValidationError, match="predate"):
        _promote(world, 1, awarded_on=first_date - datetime.timedelta(days=1))


def test_inactive_track_and_roles_without_award_permission_are_rejected(world):
    world["track"].close(
        status=StudentStyleTrack.Status.ENDED,
        on_date=timezone.localdate(),
    )
    with pytest.raises(ValidationError, match="active"):
        _promote(world)

    world["track"].status = StudentStyleTrack.Status.ACTIVE
    world["track"].ended_on = None
    world["track"].save(update_fields=["status", "ended_on", "updated_at"])
    for user in (world["instructor"], world["admin_b"]):
        with pytest.raises(PermissionDenied):
            _promote(world, actor=actor_for_user(user))


def test_examiner_ceiling_limits_choices_and_service_awards(world):
    actor = actor_for_user(world["admin_a"])
    InstructorProfile.objects.create(
        person=world["admin_a"].person,
        pay_type=InstructorProfile.PayType.VOLUNTEER,
        max_grading_rank=world["ranks"][2],
    )
    choices = list(promotion_rank_choices(world["track"], actor=actor))
    assert choices[-1] == world["ranks"][2]
    assert all(rank.order <= world["ranks"][2].order for rank in choices)

    with pytest.raises(ValidationError, match="grading ceiling"):
        _promote(world, 3, actor=actor)
    award = _promote(world, 2, actor=actor)
    assert award.rank_id == world["ranks"][2].pk


def test_ceiling_is_enforced_when_examiner_attempts_to_promote_themself(world):
    actor = actor_for_user(world["admin_a"])
    InstructorProfile.objects.create(
        person=world["admin_a"].person,
        pay_type=InstructorProfile.PayType.VOLUNTEER,
        max_grading_rank=world["ranks"][1],
    )
    examiner_profile = StudentProfile.objects.create(
        person=world["admin_a"].person,
        home_dojo=world["dojo_a"],
    )
    examiner_track = StudentStyleTrack.objects.create(
        student=world["admin_a"].person,
        style=world["adult"].style,
        ladder=world["adult"],
        started_on=timezone.localdate() - datetime.timedelta(days=365),
    )

    with pytest.raises(ValidationError, match="grading ceiling"):
        _promote(
            world,
            2,
            profile=examiner_profile,
            track=examiner_track,
            actor=actor,
        )


def test_ceiling_on_another_ladder_grants_no_cross_style_authority(world):
    actor = actor_for_user(world["admin_a"])
    InstructorProfile.objects.create(
        person=world["admin_a"].person,
        pay_type=InstructorProfile.PayType.VOLUNTEER,
        max_grading_rank=world["junior_rank"],
    )
    assert not promotion_rank_choices(world["track"], actor=actor).exists()
    with pytest.raises(ValidationError, match="grading ceiling"):
        _promote(world, actor=actor)


def test_admin_without_configured_ceiling_retains_organisation_authority(world):
    award = _promote(world, len(world["ranks"]) - 1, actor=actor_for_user(world["org_admin"]))
    assert award.rank_id == world["ranks"][-1].pk


def test_strict_audit_failure_rolls_back_award_and_current_rank(world):
    with patch("apps.ranks.promotions.audit.record", side_effect=RuntimeError("audit down")):
        with pytest.raises(RuntimeError, match="audit down"):
            _promote(world)

    assert not RankAward.objects.for_organization(world["org"].pk).exists()
    world["track"].refresh_from_db()
    assert world["track"].current_rank_id is None


def test_rank_award_history_is_append_only_but_explicit_revocation_still_works(world):
    award = _promote(world)
    award.notes = "rewritten"
    with pytest.raises(NotImplementedError, match="append-only"):
        award.save()
    with pytest.raises(NotImplementedError, match="append-only"):
        award.delete()
    queryset = RankAward.objects.for_organization(world["org"].pk).filter(pk=award.pk)
    with pytest.raises(NotImplementedError, match="append-only"):
        queryset.update(notes="rewritten")
    with pytest.raises(NotImplementedError, match="append-only"):
        queryset.delete()

    award.revoke(by=world["admin_a"].person, reason="Graded in error")
    world["track"].refresh_from_db()
    assert award.is_revoked
    assert world["track"].current_rank_id is None


def test_generic_admin_cannot_create_change_or_delete_award_fields():
    from apps.ranks.admin import RankAwardAdmin

    assert RankAwardAdmin.has_add_permission(None, None) is False
    assert RankAwardAdmin.has_delete_permission(None, None) is False
    assert {"track", "rank", "awarded_on", "notes", "revoked_at"}.issubset(
        RankAwardAdmin.readonly_fields
    )


def test_bulk_promotion_handles_exactly_thirty_students(world):
    profiles = [world["profile"]] + [
        _bulk_profile(world, index) for index in range(BULK_PROMOTION_LIMIT - 1)
    ]
    awards = bulk_promote_students(
        profiles=profiles,
        rank=world["ranks"][0],
        awarded_on=timezone.localdate(),
        actor=actor_for_user(world["admin_a"]),
        notes="Grading day",
    )
    assert len(awards) == BULK_PROMOTION_LIMIT
    assert RankAward.objects.for_organization(world["org"].pk).count() == BULK_PROMOTION_LIMIT
    assert AuditLog.objects.filter(action="rank_promote").count() == BULK_PROMOTION_LIMIT
    for profile in profiles:
        track = StudentStyleTrack.objects.for_organization(world["org"].pk).get(
            student=profile.person, ladder=world["adult"]
        )
        assert track.current_rank_id == world["ranks"][0].pk


def test_bulk_promotion_rejects_more_than_thirty_students(world):
    with pytest.raises(ValidationError, match="at most"):
        bulk_promote_students(
            profiles=[world["profile"]] * (BULK_PROMOTION_LIMIT + 1),
            rank=world["ranks"][0],
            awarded_on=timezone.localdate(),
            actor=actor_for_user(world["admin_a"]),
        )
    assert not RankAward.objects.for_organization(world["org"].pk).exists()


def test_bulk_promotion_is_atomic_when_one_student_has_no_matching_track(world):
    missing = _bulk_profile(world, "missing", with_track=False)
    with pytest.raises(ValidationError, match="no active track"):
        bulk_promote_students(
            profiles=[world["profile"], missing],
            rank=world["ranks"][0],
            awarded_on=timezone.localdate(),
            actor=actor_for_user(world["admin_a"]),
        )
    assert not RankAward.objects.for_organization(world["org"].pk).exists()
    world["track"].refresh_from_db()
    assert world["track"].current_rank_id is None


def test_bulk_promotion_rolls_back_every_student_on_second_audit_failure(world):
    second = _bulk_profile(world, "second")
    real_record = __import__("apps.core.audit", fromlist=["record"]).record
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second audit failed")
        return real_record(*args, **kwargs)

    with patch("apps.ranks.promotions.audit.record", side_effect=fail_second):
        with pytest.raises(RuntimeError, match="second audit failed"):
            bulk_promote_students(
                profiles=[world["profile"], second],
                rank=world["ranks"][0],
                awarded_on=timezone.localdate(),
                actor=actor_for_user(world["admin_a"]),
            )
    assert not RankAward.objects.for_organization(world["org"].pk).exists()
    assert not AuditLog.objects.filter(action="rank_promote").exists()


def test_bulk_promotion_page_is_dojo_scoped_and_posts_successfully(client, world):
    url = reverse("student-bulk-promote")
    client.force_login(world["admin_a"])
    page = client.get(url)
    body = page.content.decode()
    assert page.status_code == 200
    assert "Ari Student" in body
    assert "Bora Student" not in body

    response = client.post(
        url,
        {
            "student_ids": [str(world["profile"].pk)],
            "rank": str(world["ranks"][0].pk),
            "awarded_on": timezone.localdate().isoformat(),
            "notes": "Web batch",
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("student-list")
    assert RankAward.objects.for_organization(world["org"].pk).filter(track=world["track"]).exists()


def test_bulk_promotion_route_denies_non_awarder_and_requires_csrf(world):
    url = reverse("student-bulk-promote")
    denied = Client()
    denied.force_login(world["instructor"])
    assert denied.get(url).status_code == 403

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(world["admin_a"])
    response = csrf_client.post(
        url,
        {
            "student_ids": [str(world["profile"].pk)],
            "rank": str(world["ranks"][0].pk),
            "awarded_on": timezone.localdate().isoformat(),
        },
    )
    assert response.status_code == 403
    assert not RankAward.objects.for_organization(world["org"].pk).exists()


def test_student_rank_tab_links_to_promotion_and_http_post_succeeds(client, world):
    client.force_login(world["admin_a"])
    promotion_url = reverse("student-promote", args=[world["student"].pk, world["track"].pk])
    detail = client.get(reverse("student-detail", args=[world["student"].pk]), {"tab": "rank"})
    assert detail.status_code == 200
    assert promotion_url in detail.content.decode()

    response = client.post(
        promotion_url,
        {
            "rank": str(world["ranks"][0].pk),
            "awarded_on": timezone.localdate().isoformat(),
            "certificate_number": "WEB-001",
            "notes": "Recorded at grading",
        },
    )
    assert response.status_code == 302
    assert response.url.endswith("?tab=rank")
    award = RankAward.objects.for_organization(world["org"].pk).get(track=world["track"])
    assert award.certificate_number == "WEB-001"


def test_http_form_rejects_cross_ladder_rank_without_creating_award(client, world):
    client.force_login(world["admin_a"])
    response = client.post(
        reverse("student-promote", args=[world["student"].pk, world["track"].pk]),
        {
            "rank": str(world["junior_rank"].pk),
            "awarded_on": timezone.localdate().isoformat(),
        },
    )
    assert response.status_code == 200
    assert "valid choice" in response.content.decode()
    assert not RankAward.objects.for_organization(world["org"].pk).exists()


@pytest.mark.parametrize("user_key", ["admin_b", "instructor"])
def test_promotion_route_enforces_scope_and_permission(client, world, user_key):
    client.force_login(world[user_key])
    response = client.get(reverse("student-promote", args=[world["student"].pk, world["track"].pk]))
    assert response.status_code == (404 if user_key == "admin_b" else 403)


def test_promotion_post_requires_csrf(world):
    client = Client(enforce_csrf_checks=True)
    client.force_login(world["admin_a"])
    response = client.post(
        reverse("student-promote", args=[world["student"].pk, world["track"].pk]),
        {
            "rank": str(world["ranks"][0].pk),
            "awarded_on": timezone.localdate().isoformat(),
        },
    )
    assert response.status_code == 403
    assert not RankAward.objects.for_organization(world["org"].pk).exists()
