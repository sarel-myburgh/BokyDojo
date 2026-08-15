"""Rich, tenant-scoped student directory — TODO 1.1.9."""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import AttendanceRecord
from apps.core.models import AuditLog, Document
from apps.core.notes import Note
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.consent import record_consent
from apps.identity.models import (
    ConsentPolicy,
    ConsentRecord,
    Dojo,
    EmergencyContact,
    Enrollment,
    GovernanceModel,
    GuardianLink,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    StudentSegment,
    User,
)
from apps.ranks.models import Rank, RankAward, RankLadder, StudentStyleTrack, Style
from apps.scheduling.models import ClassSession

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


def _person_at_age(org, given, family, age):
    today = timezone.localdate()
    return Person.objects.create(
        organization=org,
        given_name=given,
        family_name=family,
        date_of_birth=today.replace(year=today.year - age),
    )


def _staff(org, role, *, email, dojo=None):
    person = Person.objects.create(organization=org, given_name=role.title(), family_name="Staff")
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
    now = timezone.now()
    with allow_unscoped("student list test setup"):
        org = Organization.objects.create(name="Directory Org", slug="directory-org")
        dojo_a = Dojo.objects.create(organization=org, name="Dojo A", slug="directory-a")
        dojo_b = Dojo.objects.create(organization=org, name="Dojo B", slug="directory-b")

        alice = _person_at_age(org, "Alice", "Active", 10)
        alice_profile = StudentProfile.objects.create(
            person=alice,
            home_dojo=dojo_a,
            status=StudentProfile.Status.ACTIVE,
            licence_expires_on=today + datetime.timedelta(days=30),
        )
        bob = _person_at_age(org, "Bob", "BackSoon", 20)
        bob_profile = StudentProfile.objects.create(
            person=bob,
            home_dojo=dojo_a,
            status=StudentProfile.Status.ON_HOLD,
            licence_expires_on=today - datetime.timedelta(days=1),
        )
        cara = _person_at_age(org, "Cara", "CrossDojo", 30)
        StudentProfile.objects.create(
            person=cara, home_dojo=dojo_b, status=StudentProfile.Status.ACTIVE
        )

        parent = Person.objects.create(organization=org, given_name="Paula", family_name="Parent")
        GuardianLink.objects.create(
            student=alice,
            guardian=parent,
            relationship=GuardianLink.Relationship.MOTHER,
            has_custody=True,
        )
        dojo_admin = _staff(org, Role.DOJO_ADMIN, email="admin-a@directory.test", dojo=dojo_a)
        front_desk = _staff(org, Role.FRONT_DESK, email="desk-a@directory.test", dojo=dojo_a)
        other_dojo_admin = _staff(org, Role.DOJO_ADMIN, email="admin-b@directory.test", dojo=dojo_b)
        org_admin = _staff(org, Role.ORG_ADMIN, email="org-admin@directory.test")
        guardian_user = _staff(org, Role.GUARDIAN, email="guardian@directory.test", dojo=dojo_a)

        style = Style.objects.create(organization=org, name="Shotokan")
        ladder = RankLadder.objects.create(
            style=style, name="Junior ladder", applies_to=RankLadder.AppliesTo.JUNIOR
        )
        white = Rank.objects.create(ladder=ladder, order=1, name="White belt")
        yellow = Rank.objects.create(ladder=ladder, order=2, name="Yellow belt")
        StudentStyleTrack.objects.create(
            student=alice,
            style=style,
            ladder=ladder,
            current_rank=white,
            started_on=today,
        )
        StudentStyleTrack.objects.create(
            student=bob,
            style=style,
            ladder=ladder,
            current_rank=yellow,
            started_on=today,
        )

        session = ClassSession.objects.create(
            dojo=dojo_a,
            starts_at=now - datetime.timedelta(days=2),
            ends_at=now - datetime.timedelta(days=2) + datetime.timedelta(hours=1),
        )
        AttendanceRecord.objects.create(
            session=session,
            student=alice,
            status=AttendanceRecord.Status.PRESENT,
            marked_by=dojo_admin.person,
        )
        medical_policy = ConsentPolicy.objects.create(
            organization=org,
            consent_type=ConsentRecord.Type.MEDICAL,
            version="medical-list-v1",
            title="Medical consent",
            body="Medical terms",
        )
        waiver_policy = ConsentPolicy.objects.create(
            organization=org,
            consent_type=ConsentRecord.Type.WAIVER,
            version="waiver-list-v1",
            title="Waiver",
            body="Waiver terms",
        )

        other_org = Organization.objects.create(name="Foreign", slug="directory-foreign")
        foreign_dojo = Dojo.objects.create(
            organization=other_org, name="Foreign Dojo", slug="directory-foreign-dojo"
        )
        foreign = _person_at_age(other_org, "Foreign", "Student", 18)
        StudentProfile.objects.create(person=foreign, home_dojo=foreign_dojo)

    actor = actor_for_user(dojo_admin)
    record_consent(
        person=alice,
        consent_type=ConsentRecord.Type.WAIVER,
        version=waiver_policy.version,
        granted=True,
        granted_by=parent,
        capacity=ConsentRecord.Capacity.PARENT,
        ip_address="203.0.113.55",
        actor=actor,
        minimum_self_consent_age=18,
        signature_name="Paula Parent",
        policy=waiver_policy,
    )
    return locals()


def _body(client, user, params=None):
    client.force_login(user)
    response = client.get(reverse("student-list"), params or {})
    return response, response.content.decode()


def test_dojo_scoped_directory_only_lists_visible_students(client, world):
    response, body = _body(client, world["dojo_admin"])

    assert response.status_code == 200
    assert "Alice Active" in body
    assert "Bob BackSoon" in body
    assert "Cara CrossDojo" not in body
    assert "Foreign Student" not in body
    assert reverse("waiver-consent", args=[world["alice"].pk]) in body
    assert reverse("medical-consent", args=[world["alice"].pk]) in body


@pytest.mark.parametrize(
    ("params", "included", "excluded"),
    [
        ({"q": "alice"}, "Alice Active", "Bob BackSoon"),
        ({"status": StudentProfile.Status.ON_HOLD}, "Bob BackSoon", "Alice Active"),
        ({"age_min": "18", "age_max": "25"}, "Bob BackSoon", "Alice Active"),
        ({"attendance_gap": "7"}, "Bob BackSoon", "Alice Active"),
        ({"unsigned_waiver": "on"}, "Bob BackSoon", "Alice Active"),
        ({"expired_licence": "on"}, "Bob BackSoon", "Alice Active"),
    ],
)
def test_attention_filters(client, world, params, included, excluded):
    _response, body = _body(client, world["dojo_admin"], params)

    assert included in body
    assert excluded not in body


def test_dojo_and_rank_filters_use_scoped_choice_ids(client, world):
    _response, dojo_body = _body(client, world["org_admin"], {"dojo": str(world["dojo_b"].pk)})
    _response, rank_body = _body(client, world["dojo_admin"], {"rank": str(world["yellow"].pk)})

    assert "Cara CrossDojo" in dojo_body
    assert "Alice Active" not in dojo_body
    assert "Bob BackSoon" in rank_body
    assert "Alice Active" not in rank_body


def test_latest_waiver_revocation_returns_student_to_unsigned_filter(client, world):
    record_consent(
        person=world["alice"],
        consent_type=ConsentRecord.Type.WAIVER,
        version=world["waiver_policy"].version,
        granted=False,
        granted_by=world["parent"],
        capacity=ConsentRecord.Capacity.PARENT,
        ip_address="203.0.113.55",
        actor=actor_for_user(world["dojo_admin"]),
        minimum_self_consent_age=18,
        signature_name="Paula Parent",
        policy=world["waiver_policy"],
    )

    _response, body = _body(client, world["dojo_admin"], {"unsigned_waiver": "on"})

    assert "Alice Active" in body


def test_front_desk_gets_waiver_action_but_not_medical_action(client, world):
    _response, body = _body(client, world["front_desk"])

    assert reverse("waiver-consent", args=[world["alice"].pk]) in body
    assert reverse("medical-consent", args=[world["alice"].pk]) not in body


def test_role_without_person_view_is_refused(client, world):
    response, _body_text = _body(client, world["guardian_user"])

    assert response.status_code == 403


def test_invalid_and_cross_tenant_filter_values_fail_closed(client, world):
    invalid, invalid_body = _body(client, world["dojo_admin"], {"age_min": "30", "age_max": "10"})
    foreign, foreign_body = _body(
        client, world["dojo_admin"], {"dojo": str(world["foreign_dojo"].pk)}
    )

    assert invalid.status_code == 200
    assert "Minimum age cannot exceed maximum age" in invalid_body
    assert "Alice Active" not in invalid_body
    assert foreign.status_code == 200
    assert "Select a valid choice" in foreign_body
    assert "Foreign Student" not in foreign_body


def test_student_names_are_escaped_and_navigation_links_to_directory(client, world):
    world["alice"].given_name = "<script>alert(1)</script>"
    world["alice"].save(update_fields=["given_name", "updated_at"])
    response, body = _body(client, world["dojo_admin"])

    assert response.status_code == 200
    assert "&lt;script&gt;" in body
    assert "<script>alert(1)</script>" not in body
    assert reverse("student-list") in client.get(reverse("today")).content.decode()


def test_federated_org_actor_cannot_search_or_render_private_person_fields(client, world):
    world["org"].governance_model = "federated"
    world["org"].save(update_fields=["governance_model", "updated_at"])
    world["alice"].email = "private-alice@example.test"
    world["alice"].save(update_fields=["email", "updated_at"])

    _response, body = _body(client, world["org_admin"])
    _response, searched = _body(client, world["org_admin"], {"q": "private-alice@example.test"})

    assert "Minimum age" not in body
    assert "Name, email, or phone" not in body
    assert "Age 10" not in body
    assert "Alice Active" not in searched


def test_saved_segment_is_personal_reusable_and_audited_without_filter_values(client, world):
    client.force_login(world["dojo_admin"])
    response = client.post(
        reverse("student-segment-create"),
        {
            "name": "Needs follow-up",
            "filter_query": "status=on_hold&q=Bob&unsigned_waiver=on",
        },
    )

    assert response.status_code == 302
    segment = StudentSegment.objects.for_organization(world["org"].pk).get()
    assert segment.owner_id == world["dojo_admin"].person_id
    assert segment.filters == {"q": "Bob", "status": "on_hold", "unsigned_waiver": "on"}

    filtered = client.get(reverse("student-list"), {"segment": segment.pk})
    body = filtered.content.decode()
    assert filtered.status_code == 200
    assert "Bob BackSoon" in body
    assert "Alice Active" not in body
    assert "Needs follow-up" in body

    entry = AuditLog.objects.filter(subject_id=str(segment.pk), action="create").get()
    assert entry.after == {
        "name": "Needs follow-up",
        "filter_keys": ["q", "status", "unsigned_waiver"],
    }
    assert "Bob" not in str(entry.after)


def test_saved_segments_are_not_visible_or_loadable_by_another_staff_member(client, world):
    client.force_login(world["dojo_admin"])
    client.post(
        reverse("student-segment-create"),
        {"name": "Private segment", "filter_query": "status=on_hold"},
    )
    segment = StudentSegment.objects.for_organization(world["org"].pk).get()

    client.force_login(world["org_admin"])
    directory = client.get(reverse("student-list"))
    attempted = client.get(reverse("student-list"), {"segment": segment.pk})

    assert "Private segment" not in directory.content.decode()
    assert attempted.status_code == 404


def test_segment_delete_is_owner_only_and_audited(client, world):
    client.force_login(world["dojo_admin"])
    client.post(
        reverse("student-segment-create"),
        {"name": "Delete me", "filter_query": "expired_licence=on"},
    )
    segment = StudentSegment.objects.for_organization(world["org"].pk).get()

    client.force_login(world["org_admin"])
    denied = client.post(reverse("student-segment-delete", args=[segment.pk]))
    assert denied.status_code == 404

    client.force_login(world["dojo_admin"])
    deleted = client.post(reverse("student-segment-delete", args=[segment.pk]))

    assert deleted.status_code == 302
    assert not StudentSegment.objects.for_organization(world["org"].pk).exists()
    assert AuditLog.objects.filter(subject_id=str(segment.pk), action="delete").exists()


def test_duplicate_empty_and_invalid_segments_fail_closed(client, world):
    client.force_login(world["dojo_admin"])
    url = reverse("student-segment-create")
    assert (
        client.post(url, {"name": "At risk", "filter_query": "attendance_gap=30"}).status_code
        == 302
    )
    assert client.post(url, {"name": "at RISK", "filter_query": "status=active"}).status_code == 302
    client.post(url, {"name": "Empty", "filter_query": ""})
    client.post(url, {"name": "Invalid", "filter_query": "status=not-a-status"})

    segments = StudentSegment.objects.for_organization(world["org"].pk)
    assert segments.count() == 1
    assert segments.get().name == "At risk"


def test_segment_model_rejects_cross_tenant_owner_and_unknown_filter(world):
    cross_tenant = StudentSegment(
        organization=world["org"],
        owner=world["foreign"],
        name="Cross tenant",
        filters={"status": "active"},
    )
    unknown = StudentSegment(
        organization=world["org"],
        owner=world["dojo_admin"].person,
        name="Unknown filter",
        filters={"sql": "DROP TABLE students"},
    )

    with pytest.raises(ValidationError, match="different organisation"):
        cross_tenant.save()
    with pytest.raises(ValidationError, match="unknown fields"):
        unknown.full_clean(validate_unique=False, validate_constraints=False)


def test_segment_writes_require_csrf(world):
    client = Client(enforce_csrf_checks=True)
    client.force_login(world["dojo_admin"])

    response = client.post(
        reverse("student-segment-create"),
        {"name": "No CSRF", "filter_query": "status=active"},
    )

    assert response.status_code == 403
    assert not StudentSegment.objects.for_organization(world["org"].pk).exists()


def test_segment_routes_deny_roles_without_person_view_and_malformed_ids(client, world):
    client.force_login(world["guardian_user"])
    denied = client.post(
        reverse("student-segment-create"),
        {"name": "Denied", "filter_query": "status=active"},
    )
    assert denied.status_code == 403

    client.force_login(world["dojo_admin"])
    malformed = client.get(reverse("student-list"), {"segment": "not-a-uuid"})
    assert malformed.status_code == 404
    assert not StudentSegment.objects.for_organization(world["org"].pk).exists()


def _detail(client, user, person, tab=None):
    client.force_login(user)
    params = {"tab": tab} if tab else {}
    response = client.get(reverse("student-detail", args=[person.pk]), params)
    return response, response.content.decode()


def test_student_detail_header_tabs_attendance_and_directory_link(client, world):
    list_response, list_body = _body(client, world["dojo_admin"])
    detail_url = reverse("student-detail", args=[world["alice"].pk])
    assert list_response.status_code == 200
    assert detail_url in list_body

    response, body = _detail(client, world["dojo_admin"], world["alice"])

    assert response.status_code == 200
    assert "Alice Active" in body
    assert "Dojo A" in body
    assert "Age 10" in body
    assert "White belt" in body
    for label in ("Attendance", "Rank history", "Notes", "Billing", "Documents", "Family"):
        assert label in body
    assert "Present" in body
    assert AuditLog.objects.filter(
        action="view_student",
        actor_person=world["dojo_admin"].person,
        subject_id=str(world["alice_profile"].pk),
        note="tab: attendance",
    ).exists()


@pytest.mark.parametrize(
    ("user_key", "expected_status"),
    [
        ("other_dojo_admin", 404),
        ("guardian_user", 403),
    ],
)
def test_student_detail_enforces_object_scope_and_permission(
    client, world, user_key, expected_status
):
    response, _body_text = _detail(client, world[user_key], world["alice"])
    assert response.status_code == expected_status


def test_student_detail_rejects_unknown_tab(client, world):
    response, _body_text = _detail(client, world["dojo_admin"], world["alice"], "not-a-real-tab")
    assert response.status_code == 404


def test_student_detail_rank_awards_and_billing_placeholder(client, world):
    track = StudentStyleTrack.objects.for_organization(world["org"].pk).get(student=world["alice"])
    RankAward.objects.create(
        track=track,
        rank=world["white"],
        awarded_on=timezone.localdate(),
        awarded_by=world["dojo_admin"].person,
    )

    rank_response, rank_body = _detail(client, world["dojo_admin"], world["alice"], "rank")
    billing_response, billing_body = _detail(client, world["dojo_admin"], world["alice"], "billing")

    assert rank_response.status_code == 200
    assert "White belt" in rank_body
    assert "Awarded here" in rank_body
    assert billing_response.status_code == 200
    assert "not enabled in this build yet" in billing_body


def test_student_detail_note_visibility_pinned_alert_and_escaping(client, world):
    instructor = _staff(
        world["org"],
        Role.INSTRUCTOR,
        email="instructor-a@directory.test",
        dojo=world["dojo_a"],
    )
    other_author = world["front_desk"].person
    Note.objects.create(
        organization=world["org"],
        subject_type=Note.SubjectType.STUDENT,
        subject_id=world["alice"].pk,
        author=world["dojo_admin"].person,
        body="<script>alert('pinned')</script>",
        visibility=Note.Visibility.INSTRUCTORS,
        pinned=True,
    )
    Note.objects.create(
        organization=world["org"],
        subject_type=Note.SubjectType.STUDENT,
        subject_id=world["alice"].pk,
        author=other_author,
        body="Admin-only secret",
        visibility=Note.Visibility.ADMINS,
        pinned=True,
    )
    Note.objects.create(
        organization=world["org"],
        subject_type=Note.SubjectType.STUDENT,
        subject_id=world["alice"].pk,
        author=other_author,
        body="Other author's private secret",
        visibility=Note.Visibility.PRIVATE,
        pinned=True,
    )

    response, body = _detail(client, instructor, world["alice"], "notes")

    assert response.status_code == 200
    assert "&lt;script&gt;alert(&#x27;pinned&#x27;)&lt;/script&gt;" in body
    assert "<script>" not in body
    assert "Admin-only secret" not in body
    assert "Other author&#x27;s private secret" not in body


def test_student_detail_medical_alert_is_permission_checked_and_audited(client, world):
    world["alice_profile"].do_not_spar = True
    world["alice_profile"].save(update_fields=["do_not_spar", "updated_at"])

    admin_response, admin_body = _detail(client, world["dojo_admin"], world["alice"])
    desk_response, desk_body = _detail(client, world["front_desk"], world["alice"])

    assert admin_response.status_code == 200
    assert "Do not spar" in admin_body
    assert desk_response.status_code == 200
    assert "Do not spar" not in desk_body
    assert AuditLog.objects.filter(
        action="view_medical",
        actor_person=world["dojo_admin"].person,
        subject_id=str(world["alice_profile"].pk),
    ).exists()
    assert not AuditLog.objects.filter(
        action="view_medical",
        actor_person=world["front_desk"].person,
        subject_id=str(world["alice_profile"].pk),
    ).exists()


def test_student_detail_documents_are_filtered_by_sensitivity(client, world):
    waiver = Document.objects.create(
        organization=world["org"],
        subject_person=world["alice"],
        uploaded_by=world["dojo_admin"].person,
        kind=Document.Kind.WAIVER,
        original_filename="signed-waiver.pdf",
        storage_key="documents/test/signed-waiver.pdf",
        content_type="application/pdf",
        byte_size=12,
        checksum="a" * 64,
    )
    medical = Document.objects.create(
        organization=world["org"],
        subject_person=world["alice"],
        uploaded_by=world["dojo_admin"].person,
        kind=Document.Kind.MEDICAL,
        original_filename="private-medical.pdf",
        storage_key="documents/test/private-medical.pdf",
        content_type="application/pdf",
        byte_size=12,
        checksum="b" * 64,
        is_sensitive=True,
    )

    desk_response, desk_body = _detail(client, world["front_desk"], world["alice"], "documents")
    admin_response, admin_body = _detail(client, world["dojo_admin"], world["alice"], "documents")

    assert desk_response.status_code == 200
    assert waiver.original_filename in desk_body
    assert medical.original_filename not in desk_body
    assert admin_response.status_code == 200
    assert waiver.original_filename in admin_body
    assert medical.original_filename in admin_body


def test_student_detail_family_tab_shows_independent_contact_flags(client, world):
    link = GuardianLink.objects.for_organization(world["org"].pk).get(
        student=world["alice"], guardian=world["parent"]
    )
    link.is_primary_contact = True
    link.is_financially_responsible = True
    link.save(update_fields=["is_primary_contact", "is_financially_responsible", "updated_at"])
    world["parent"].email = "paula@example.test"
    world["parent"].phone = "+855 12 345 678"
    world["parent"].save(update_fields=["email", "phone", "updated_at"])
    EmergencyContact.objects.create(
        person=world["alice"],
        name="Trusted Neighbour",
        phone="+855 10 000 001",
        relationship="Neighbour",
        priority=1,
    )
    Enrollment.objects.create(
        student=world["alice"],
        dojo=world["dojo_a"],
        is_primary=True,
        started_on=timezone.localdate(),
    )

    response, body = _detail(client, world["dojo_admin"], world["alice"], "family")

    assert response.status_code == 200
    assert "Paula Parent" in body
    assert "Primary contact" in body
    assert "Financially responsible" in body
    assert "Custody confirmed" in body
    assert "Trusted Neighbour" in body
    assert "Primary dojo" in body


def test_federated_org_actor_sees_rank_but_no_private_fields_family_or_documents(client, world):
    world["org"].governance_model = GovernanceModel.FEDERATED
    world["org"].save(update_fields=["governance_model", "updated_at"])
    world["alice"].email = "alice-private@example.test"
    world["alice"].phone = "+855 99 999 999"
    world["alice"].save(update_fields=["email", "phone", "updated_at"])
    Document.objects.create(
        organization=world["org"],
        subject_person=world["alice"],
        uploaded_by=world["dojo_admin"].person,
        kind=Document.Kind.CERTIFICATE,
        original_filename="private-certificate.pdf",
        storage_key="documents/test/private-certificate.pdf",
        content_type="application/pdf",
        byte_size=12,
        checksum="c" * 64,
    )

    family_response, family_body = _detail(client, world["org_admin"], world["alice"], "family")
    documents_response, documents_body = _detail(
        client, world["org_admin"], world["alice"], "documents"
    )

    assert family_response.status_code == 200
    assert "White belt" in family_body
    assert "Age 10" not in family_body
    assert "alice-private@example.test" not in family_body
    assert "Paula Parent" not in family_body
    assert "restricted by federation privacy rules" in family_body
    assert documents_response.status_code == 200
    assert "private-certificate.pdf" not in documents_body


def test_student_detail_status_form_only_offers_valid_next_states(client, world):
    response, body = _detail(client, world["dojo_admin"], world["alice"])

    assert response.status_code == 200
    assert reverse("student-status-transition", args=[world["alice"].pk]) in body
    choices = dict(response.context["status_form"].fields["to_status"].choices)
    assert set(choices) == {
        StudentProfile.Status.ON_HOLD,
        StudentProfile.Status.LAPSED,
        StudentProfile.Status.ALUMNI,
    }
    assert StudentProfile.Status.PROSPECT not in choices
    assert StudentProfile.Status.TRIAL not in choices


def test_student_status_transition_post_holds_and_resumes_without_leaking_reason(client, world):
    client.force_login(world["dojo_admin"])
    url = reverse("student-status-transition", args=[world["alice"].pk])

    held_response = client.post(
        url,
        {
            "to_status": StudentProfile.Status.ON_HOLD,
            "hold_reason": "Private injury detail",
        },
        follow=True,
    )

    assert held_response.status_code == 200
    world["alice_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ON_HOLD
    assert world["alice_profile"].hold_reason == "Private injury detail"
    assert "Private injury detail" not in held_response.content.decode()
    audit_entry = AuditLog.objects.get(
        action="update",
        subject_id=str(world["alice_profile"].pk),
        note__startswith="student lifecycle:",
    )
    assert "Private injury detail" not in str(audit_entry.before)
    assert "Private injury detail" not in str(audit_entry.after)
    assert "Private injury detail" not in audit_entry.note

    resumed_response = client.post(
        url,
        {"to_status": StudentProfile.Status.ACTIVE, "hold_reason": ""},
        follow=True,
    )
    assert resumed_response.status_code == 200
    world["alice_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ACTIVE
    assert world["alice_profile"].hold_reason == ""


@pytest.mark.parametrize(
    ("user_key", "expected_status"),
    [
        ("other_dojo_admin", 404),
        ("guardian_user", 403),
    ],
)
def test_student_status_transition_endpoint_enforces_scope_and_permission(
    client, world, user_key, expected_status
):
    client.force_login(world[user_key])
    response = client.post(
        reverse("student-status-transition", args=[world["alice"].pk]),
        {"to_status": StudentProfile.Status.LAPSED, "hold_reason": ""},
    )

    assert response.status_code == expected_status
    world["alice_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ACTIVE


def test_student_status_transition_rejects_tampered_or_incomplete_post(client, world):
    client.force_login(world["dojo_admin"])
    url = reverse("student-status-transition", args=[world["alice"].pk])

    tampered = client.post(
        url,
        {"to_status": StudentProfile.Status.TRIAL, "hold_reason": ""},
        follow=True,
    )
    missing_reason = client.post(
        url,
        {"to_status": StudentProfile.Status.ON_HOLD, "hold_reason": "   "},
        follow=True,
    )

    assert tampered.status_code == 200
    assert missing_reason.status_code == 200
    world["alice_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ACTIVE
    assert not AuditLog.objects.filter(
        action="update",
        subject_id=str(world["alice_profile"].pk),
        note__startswith="student lifecycle:",
    ).exists()


def test_student_status_transition_requires_csrf(world):
    client = Client(enforce_csrf_checks=True)
    client.force_login(world["dojo_admin"])
    response = client.post(
        reverse("student-status-transition", args=[world["alice"].pk]),
        {"to_status": StudentProfile.Status.LAPSED, "hold_reason": ""},
    )

    assert response.status_code == 403
    world["alice_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ACTIVE


def test_student_directory_shows_bulk_controls_only_for_editable_students(client, world):
    response, body = _body(client, world["dojo_admin"])

    assert response.status_code == 200
    assert reverse("student-bulk-status") in body
    assert f'value="{world["alice_profile"].pk}"' in body
    assert f'value="{world["bob_profile"].pk}"' in body

    guardian_response, guardian_body = _body(client, world["guardian_user"])
    assert guardian_response.status_code == 403
    assert reverse("student-bulk-status") not in guardian_body


def test_bulk_status_endpoint_holds_and_resumes_selected_students(client, world):
    client.force_login(world["dojo_admin"])
    url = reverse("student-bulk-status")

    held = client.post(
        url,
        {
            "student_ids": [str(world["alice_profile"].pk)],
            "action": "hold",
            "hold_reason": "School holidays",
        },
        follow=True,
    )
    assert held.status_code == 200
    world["alice_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ON_HOLD
    assert world["alice_profile"].hold_reason == "School holidays"
    assert "School holidays" not in held.content.decode()

    resumed = client.post(
        url,
        {
            "student_ids": [
                str(world["alice_profile"].pk),
                str(world["bob_profile"].pk),
            ],
            "action": "resume",
            "hold_reason": "",
        },
        follow=True,
    )
    assert resumed.status_code == 200
    world["alice_profile"].refresh_from_db()
    world["bob_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ACTIVE
    assert world["bob_profile"].status == StudentProfile.Status.ACTIVE
    assert world["alice_profile"].hold_reason == ""
    assert world["bob_profile"].hold_reason == ""


def test_bulk_status_mixed_states_roll_back_the_entire_batch(client, world):
    client.force_login(world["dojo_admin"])
    response = client.post(
        reverse("student-bulk-status"),
        {
            "student_ids": [
                str(world["alice_profile"].pk),
                str(world["bob_profile"].pk),
            ],
            "action": "hold",
            "hold_reason": "Summer",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert "No statuses were changed" in response.content.decode()
    world["alice_profile"].refresh_from_db()
    world["bob_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ACTIVE
    assert world["bob_profile"].status == StudentProfile.Status.ON_HOLD
    assert not AuditLog.objects.filter(
        action="update", note__startswith="student lifecycle:"
    ).exists()


@pytest.mark.parametrize("person_key", ["cara", "foreign"])
def test_bulk_status_rejects_tampered_out_of_scope_student_ids(client, world, person_key):
    client.force_login(world["dojo_admin"])
    target_profile = world[person_key].student_profile
    response = client.post(
        reverse("student-bulk-status"),
        {
            "student_ids": [str(target_profile.pk)],
            "action": "hold",
            "hold_reason": "Tampered",
        },
        follow=True,
    )

    assert response.status_code == 200
    target_profile.refresh_from_db()
    assert target_profile.status != StudentProfile.Status.ON_HOLD
    assert not AuditLog.objects.filter(
        action="update", note__startswith="student lifecycle:"
    ).exists()


def test_bulk_status_endpoint_rejects_role_without_person_edit(client, world):
    client.force_login(world["guardian_user"])
    response = client.post(
        reverse("student-bulk-status"),
        {
            "student_ids": [str(world["alice_profile"].pk)],
            "action": "hold",
            "hold_reason": "Unauthorized",
        },
    )

    assert response.status_code == 403
    world["alice_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ACTIVE


def test_bulk_status_endpoint_requires_csrf(world):
    client = Client(enforce_csrf_checks=True)
    client.force_login(world["dojo_admin"])
    response = client.post(
        reverse("student-bulk-status"),
        {
            "student_ids": [str(world["alice_profile"].pk)],
            "action": "hold",
            "hold_reason": "Summer",
        },
    )

    assert response.status_code == 403
    world["alice_profile"].refresh_from_db()
    assert world["alice_profile"].status == StudentProfile.Status.ACTIVE
