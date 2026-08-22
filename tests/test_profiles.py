"""Profile screens, staff detail editing, and profile pictures — plan §3.

⚠ Two things here are load-bearing and easy to break silently:

* A profile picture is **not** a student photograph. It uses its own document
  kind so that it is readable without a consent record — an administrator
  cannot consent on a colleague's behalf — and, just as importantly, so that
  nothing reading student photographs can ever pick one up.
* Dojos and grades are shown and never editable, on every one of these screens.
  A form field there would be a self-service promotion.
"""

from __future__ import annotations

import io
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.core.documents import may_read
from apps.core.models import Document
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)
from apps.identity.profiles import current_profile_photo, may_edit_person, upload_profile_photo

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"  # pragma: allowlist secret


def an_image(name="face.jpg"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "navy").save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


@pytest.fixture
def world(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    with allow_unscoped("profile test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        other_org = Organization.objects.create(name="Elsewhere", slug="elsewhere")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        far = Dojo.objects.create(
            organization=org, name="Toul Kork", slug="toul-kork", timezone="Asia/Phnom_Penh"
        )

        boss = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org, person=boss, role=Role.ORG_ADMIN, scope_type=ScopeType.ORG
        )
        boss_user = User.objects.create_user("ops@example.com", PASSWORD, person=boss)

        teacher = Person.objects.create(
            organization=org, given_name="Mei", family_name="Kato", email="mei@example.com"
        )
        RoleAssignment.objects.create(
            organization=org,
            person=teacher,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        teacher_user = User.objects.create_user("mei@example.com", PASSWORD, person=teacher)

        # A dojo administrator at `dojo`, and one at `far` who must not reach Mei.
        near_admin = Person.objects.create(organization=org, given_name="Near", family_name="Boss")
        RoleAssignment.objects.create(
            organization=org,
            person=near_admin,
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        far_admin = Person.objects.create(organization=org, given_name="Far", family_name="Boss")
        RoleAssignment.objects.create(
            organization=org,
            person=far_admin,
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=far,
        )
        outsider = Person.objects.create(
            organization=other_org, given_name="Nope", family_name="Person"
        )
        # ⚠ Front desk holds PERSON_EDIT but neither ROLE_ASSIGN nor
        # RANK_AWARD, so they can reach their own page and must be offered
        # neither the roles panel nor a way to grade. Nothing else in the
        # fixture separates those three powers.
        desk = Person.objects.create(organization=org, given_name="Dee", family_name="Desk")
        RoleAssignment.objects.create(
            organization=org,
            person=desk,
            role=Role.FRONT_DESK,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        desk_user = User.objects.create_user("desk@example.com", PASSWORD, person=desk)

        # Same organisation, no roles at all — a guardian, once 3.2 exists.
        bystander = Person.objects.create(organization=org, given_name="Pat", family_name="Parent")
        User.objects.create_user("pat@example.com", PASSWORD, person=bystander)

    return {
        "org": org,
        "other_org": other_org,
        "dojo": dojo,
        "far": far,
        "boss": boss,
        "boss_user": boss_user,
        "teacher": teacher,
        "teacher_user": teacher_user,
        "near_admin": near_admin,
        "far_admin": far_admin,
        "outsider": outsider,
        "bystander": bystander,
        "desk": desk,
        "desk_user": desk_user,
    }


def actor(person):
    with allow_unscoped("test"):
        user = User.objects.filter(person=person).first()
    if user is None:
        with allow_unscoped("test"):
            user = User.objects.create_user(f"{person.pk}@example.com", PASSWORD, person=person)
    return actor_for_user(user)


# -- who may edit whom --------------------------------------------------------


def test_an_org_admin_may_edit_anybody_in_the_organisation(world):
    assert may_edit_person(actor(world["boss"]), world["teacher"])


def test_everybody_may_edit_themselves(world):
    assert may_edit_person(actor(world["teacher"]), world["teacher"])


def test_an_instructor_may_not_edit_a_colleague(world):
    """⚠ Teaching somebody is not administering them."""
    assert not may_edit_person(actor(world["teacher"]), world["boss"])


def test_a_dojo_admin_may_edit_staff_at_their_own_dojo(world):
    """⚠ The reason may_edit_person exists at all.

    Person carries no dojo, so the object-level permission check grants only to
    organisation-scoped roles — a dojo administrator would be refused on their
    own instructor. The rule has to be written out against role assignments.
    """
    assert may_edit_person(actor(world["near_admin"]), world["teacher"])


def test_a_dojo_admin_may_not_edit_staff_at_a_dojo_that_is_not_theirs(world):
    assert not may_edit_person(actor(world["far_admin"]), world["teacher"])


def test_nobody_may_edit_across_organisations(world):
    assert not may_edit_person(actor(world["boss"]), world["outsider"])


# -- editing the details ------------------------------------------------------


def test_an_admin_can_change_a_staff_members_details(client, world):
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("person-edit", args=[world["teacher"].pk]),
        {
            "given_name": "Mei",
            "family_name": "Tanaka",
            "preferred_name": "",
            "email": "mei.tanaka@example.com",
            "phone": "+855 12 345 678",
            "locale": "en",
        },
    )

    assert response.status_code == 302
    world["teacher"].refresh_from_db()
    assert world["teacher"].family_name == "Tanaka"
    assert world["teacher"].phone == "+855 12 345 678"


def test_changing_the_email_also_changes_the_login(client, world):
    """⚠ Person.email and User.email are separate columns and you sign in with
    the second. Writing only the first leaves somebody signing in with an
    address that no screen still shows."""
    client.force_login(world["boss_user"])

    client.post(
        reverse("person-edit", args=[world["teacher"].pk]),
        {
            "given_name": "Mei",
            "family_name": "Kato",
            "preferred_name": "",
            "email": "new.address@example.com",
            "phone": "",
            "locale": "en",
        },
    )

    world["teacher_user"].refresh_from_db()
    assert world["teacher_user"].email == "new.address@example.com"


def test_an_email_already_used_by_another_login_is_refused(client, world):
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("person-edit", args=[world["teacher"].pk]),
        {
            "given_name": "Mei",
            "family_name": "Kato",
            "preferred_name": "",
            "email": "ops@example.com",  # the administrator's own login
            "phone": "",
            "locale": "en",
        },
    )

    assert response.status_code == 200
    assert "already uses this address" in response.content.decode()
    world["teacher_user"].refresh_from_db()
    assert world["teacher_user"].email == "mei@example.com"


def test_an_instructor_cannot_edit_a_colleague_through_the_view(client, world):
    client.force_login(world["teacher_user"])

    response = client.post(
        reverse("person-edit", args=[world["boss"].pk]),
        {
            "given_name": "Pwned",
            "family_name": "Admin",
            "preferred_name": "",
            "email": "attacker@example.com",
            "phone": "",
            "locale": "en",
        },
    )

    assert response.status_code == 403
    world["boss"].refresh_from_db()
    assert world["boss"].given_name == "Ops"


def test_somebody_can_edit_their_own_details(client, world):
    client.force_login(world["teacher_user"])

    response = client.post(
        reverse("account-edit"),
        {
            "given_name": "Mei",
            "family_name": "Kato",
            "preferred_name": "Mei-chan",
            "email": "mei@example.com",
            "phone": "+855 99 000 111",
            "locale": "en",
        },
    )

    assert response.status_code == 302
    world["teacher"].refresh_from_db()
    assert world["teacher"].preferred_name == "Mei-chan"


def test_the_profile_form_offers_no_way_to_change_dojos_or_grades(client, world):
    """⚠ The whole point of the read-only panels. If a field for either ever
    appears in this form, somebody can promote themselves."""
    from apps.identity.profile_forms import PersonDetailsForm

    fields = set(PersonDetailsForm().fields)

    assert not fields & {"dojo", "dojos", "rank", "ranks", "belt", "roles", "role", "style"}


# -- pictures -----------------------------------------------------------------


def test_staff_can_upload_their_own_picture(world):
    photo = upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image(), actor=actor(world["teacher"])
    )

    assert photo.kind == Document.Kind.PROFILE_PHOTO


def test_an_org_admin_can_upload_a_picture_for_staff(world):
    photo = upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image(), actor=actor(world["boss"])
    )

    assert photo.subject_person_id == world["teacher"].pk


def test_an_instructor_cannot_upload_a_picture_for_a_colleague(world):
    from apps.identity.permissions import PermissionDenied

    with pytest.raises(PermissionDenied):
        upload_profile_photo(
            person=world["boss"], uploaded_file=an_image(), actor=actor(world["teacher"])
        )


def test_a_profile_picture_is_readable_with_no_consent_record(world):
    """⚠ The reason PROFILE_PHOTO exists as a separate kind.

    may_read refuses a PHOTO document unless an active consent policy and a
    granted consent record both stand. There is neither here, and there cannot
    be for a picture an administrator uploaded on somebody's behalf — nobody
    can consent for another adult. Under the student kind this returns None and
    the feature simply does not work.
    """
    upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image(), actor=actor(world["boss"])
    )

    assert current_profile_photo(person=world["teacher"], actor=actor(world["boss"])) is not None


def test_staff_can_see_the_picture_they_uploaded_of_themselves(world):
    """⚠ Not as obvious as it sounds, and it was broken when first written.

    Person carries no dojo, so the PERSON_VIEW check behind may_read grants only
    to organisation-scoped roles. A dojo-scoped instructor could upload their
    own picture and then be refused it — an upload button that appears to do
    nothing.
    """
    upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image(), actor=actor(world["teacher"])
    )

    assert current_profile_photo(person=world["teacher"], actor=actor(world["teacher"])) is not None


def test_a_profile_picture_is_not_visible_from_another_organisation(world):
    upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image(), actor=actor(world["boss"])
    )
    with allow_unscoped("test"):
        photo = Document.objects.get(kind=Document.Kind.PROFILE_PHOTO)

    assert not may_read(actor(world["outsider"]), photo, governance_model="central")


def test_a_profile_picture_still_needs_permission_to_view_the_person(world):
    """⚠ Skipping the consent record does not mean skipping authorisation.

    The cross-organisation test above does not prove this: may_read rejects a
    foreign organisation before it ever reaches the profile-picture branch, so
    it would pass even if that branch returned True unconditionally. This one
    uses somebody inside the organisation who holds no role.
    """
    upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image(), actor=actor(world["boss"])
    )
    with allow_unscoped("test"):
        photo = Document.objects.get(kind=Document.Kind.PROFILE_PHOTO)

    assert not may_read(actor(world["bystander"]), photo, governance_model="central")


def test_a_staff_picture_is_never_returned_as_a_student_photograph(world):
    """⚠ The separation cuts both ways. The check-in grid queries kind=PHOTO;
    a staff picture appearing there would put an adult's face on the screen
    students tap."""
    upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image(), actor=actor(world["boss"])
    )

    with allow_unscoped("test"):
        assert not Document.objects.filter(
            subject_person=world["teacher"], kind=Document.Kind.PHOTO
        ).exists()


def test_a_non_image_upload_is_refused(world):
    from django.core.exceptions import ValidationError

    bad = SimpleUploadedFile("notes.pdf", b"%PDF-1.4 not an image", content_type="application/pdf")

    with pytest.raises(ValidationError):
        upload_profile_photo(
            person=world["teacher"], uploaded_file=bad, actor=actor(world["teacher"])
        )


def test_serving_a_picture_refuses_somebody_from_another_organisation(client, world):
    upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image(), actor=actor(world["boss"])
    )
    with allow_unscoped("test"):
        stranger = User.objects.create_user(
            "stranger@example.com", PASSWORD, person=world["outsider"]
        )
    client.force_login(stranger)

    response = client.get(reverse("profile-photo", args=[world["teacher"].pk]))

    assert response.status_code == 404


def test_the_latest_picture_wins(world):
    upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image("one.jpg"), actor=actor(world["teacher"])
    )
    second = upload_profile_photo(
        person=world["teacher"], uploaded_file=an_image("two.jpg"), actor=actor(world["teacher"])
    )

    current = current_profile_photo(person=world["teacher"], actor=actor(world["teacher"]))
    assert current.pk == second.pk


# -- the screens --------------------------------------------------------------


def test_the_account_page_renders_for_a_signed_in_member_of_staff(client, world):
    client.force_login(world["teacher_user"])

    body = client.get(reverse("account")).content.decode()

    assert "Mei" in body
    assert "{#" not in body, "leaked a template comment"


def test_the_person_page_renders_for_an_admin(client, world):
    client.force_login(world["boss_user"])

    body = client.get(reverse("person-detail", args=[world["teacher"].pk])).content.decode()

    assert "Mei" in body
    assert "{#" not in body, "leaked a template comment"


def test_the_person_page_is_refused_to_somebody_who_may_not_edit(client, world):
    client.force_login(world["teacher_user"])

    response = client.get(reverse("person-detail", args=[world["boss"].pk]))

    assert response.status_code == 403


def test_the_header_offers_profile_security_and_sign_out(client, world):
    client.force_login(world["teacher_user"])

    body = client.get(reverse("account")).content.decode()

    assert reverse("account") in body
    assert reverse("mfa-setup") in body
    assert reverse("logout") in body


def test_the_header_menu_uses_no_inline_script(client, world):
    """⚠ The CSP is strict-nonce. A scripted dropdown would silently not open."""
    client.force_login(world["teacher_user"])

    body = client.get(reverse("account")).content.decode()

    assert "onclick=" not in body
    assert "onerror=" not in body
    assert "<details" in body


# -- one page, not three ------------------------------------------------------


def test_the_person_page_carries_roles_grades_and_sign_in_together(client, world):
    """⚠ The point of the merge. There used to be a staff list and a roles
    screen beside this page, so the same person could be reached three ways and
    each way offered a different subset of what could be done to them."""
    client.force_login(world["boss_user"])

    body = client.get(reverse("person-detail", args=[world["teacher"].pk])).content.decode()

    assert "Roles" in body
    assert reverse("role-grant", args=[world["teacher"].pk]) in body
    assert reverse("temporary-password", args=[world["teacher"].pk]) in body
    assert "Grades" in body
    assert "picture/upload/" in body


def test_the_back_link_goes_to_organization_settings(client, world):
    """Not to a staff page of its own — there no longer is one."""
    client.force_login(world["boss_user"])

    body = client.get(reverse("person-detail", args=[world["teacher"].pk])).content.decode()

    assert reverse("org-settings") in body


def test_the_separate_staff_screens_are_gone(world):
    """⚠ Removed, not merely unlinked. A route left reachable is a second place
    the same person can be looked at, which is what this change was undoing."""
    from django.urls import NoReverseMatch

    for name in ("staff-list", "staff-roles"):
        with pytest.raises(NoReverseMatch):
            reverse(name, args=[world["teacher"].pk])


def test_a_role_can_be_granted_from_the_person_page(client, world):
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("role-grant", args=[world["teacher"].pk]),
        {"role": Role.DOJO_ADMIN, "scope": "dojo", "dojo": str(world["dojo"].pk)},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("person-detail", args=[world["teacher"].pk])
    with allow_unscoped("test"):
        assert RoleAssignment.objects.filter(
            person=world["teacher"], role=Role.DOJO_ADMIN, revoked_at__isnull=True
        ).exists()


def test_granting_a_role_is_refused_without_role_assign(client, world):
    client.force_login(world["teacher_user"])

    response = client.post(
        reverse("role-grant", args=[world["boss"].pk]),
        {"role": Role.ORG_ADMIN, "scope": "org"},
    )

    assert response.status_code == 403


def test_the_roles_panel_is_hidden_from_somebody_who_cannot_assign_them(client, world):
    """⚠ Front desk can edit details but not hand out roles.

    Checked on the person page, not the account page — the account page has no
    roles panel at all, so asserting its absence there proves nothing.
    """
    client.force_login(world["desk_user"])

    body = client.get(reverse("person-detail", args=[world["desk"].pk])).content.decode()

    assert "Details" in body, "expected to be on the person page"
    assert reverse("role-grant", args=[world["desk"].pk]) not in body


def test_the_grade_link_is_hidden_from_somebody_who_cannot_award_one(client, world):
    """⚠ Having a grade to show is not permission to change it. Front desk sees
    the grade and is offered no way to alter it."""
    from apps.identity.models import StudentProfile
    from apps.ranks.models import RankLadder, StudentStyleTrack, Style

    with allow_unscoped("test"):
        StudentProfile.objects.create(
            person=world["desk"], home_dojo=world["dojo"], status=StudentProfile.Status.ACTIVE
        )
        style = Style.objects.create(organization=world["org"], name="Boxing", is_ranked=True)
        ladder = RankLadder.objects.create(style=style, name="Adult")
        track = StudentStyleTrack.objects.create(
            student=world["desk"], style=style, ladder=ladder, started_on=date(2024, 1, 1)
        )

    client.force_login(world["desk_user"])
    body = client.get(reverse("person-detail", args=[world["desk"].pk])).content.decode()

    assert "Boxing" in body, "expected the track to be listed"
    assert reverse("student-promote", args=[world["desk"].pk, track.pk]) not in body


def test_an_admin_is_offered_a_way_to_award_a_grade(client, world):
    """⚠ The page used to tell an organisation administrator that grades were
    set by an administrator, which is both unhelpful and, to them, untrue."""
    from apps.identity.models import StudentProfile
    from apps.ranks.models import RankLadder, StudentStyleTrack, Style

    with allow_unscoped("test"):
        profile = StudentProfile.objects.create(
            person=world["teacher"],
            home_dojo=world["dojo"],
            status=StudentProfile.Status.ACTIVE,
        )
        style = Style.objects.create(organization=world["org"], name="Goju Ryu", is_ranked=True)
        ladder = RankLadder.objects.create(style=style, name="Adult")
        track = StudentStyleTrack.objects.create(
            student=world["teacher"],
            style=style,
            ladder=ladder,
            started_on=date(2024, 1, 1),
        )

    client.force_login(world["boss_user"])
    body = client.get(reverse("person-detail", args=[world["teacher"].pk])).content.decode()

    assert profile is not None
    assert reverse("student-promote", args=[world["teacher"].pk, track.pk]) in body


def test_somebody_with_no_grade_is_offered_a_way_to_record_one(client, world):
    """⚠ This used to say "not enrolled as a student, so there is no grade to
    award", which was true only while grades hung off a student record. Staff
    now hold their own grades, so the honest state is simply that none is on
    file — and an administrator is offered the form to add one."""
    client.force_login(world["boss_user"])

    body = client.get(reverse("person-detail", args=[world["teacher"].pk])).content.decode()

    assert "No grade recorded." in body
    assert reverse("staff-grade-add", args=[world["teacher"].pk]) in body


def test_the_settings_page_lists_every_member_of_staff(client, world):
    """⚠ It used to show the first eight and link to a staff page for the rest.
    That page is gone, so a truncation here would simply hide people."""
    with allow_unscoped("test"):
        for i in range(12):
            extra = Person.objects.create(
                organization=world["org"], given_name=f"Staff{i}", family_name="Extra"
            )
            RoleAssignment.objects.create(
                organization=world["org"],
                person=extra,
                role=Role.INSTRUCTOR,
                scope_type=ScopeType.DOJO,
                dojo=world["dojo"],
            )

    client.force_login(world["boss_user"])
    body = client.get(reverse("org-settings")).content.decode()

    for i in range(12):
        assert f"Staff{i}" in body, f"Staff{i} missing from the settings page"


def test_a_photo_larger_than_the_in_memory_threshold_is_accepted(world):
    """⚠ Nothing else here uses an image big enough to leave memory.

    Django hands anything above FILE_UPLOAD_MAX_MEMORY_SIZE (2.5MB by default)
    to the view as a TemporaryUploadedFile backed by a file on disk, and every
    other upload test in this suite uses a 40x40 thumbnail — so the whole
    temporary-file path was untested while most photographs taken on a phone go
    down it.
    """
    import io
    import os

    from django.conf import settings
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    # Comfortably over the threshold, comfortably under the multipart limit.
    side = 1000
    buffer = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(buffer, format="PNG")
    assert buffer.tell() > settings.FILE_UPLOAD_MAX_MEMORY_SIZE, (
        "the fixture is not big enough to become a temporary file"
    )
    big = SimpleUploadedFile("big.png", buffer.getvalue(), content_type="image/png")

    photo = upload_profile_photo(
        person=world["teacher"], uploaded_file=big, actor=actor(world["teacher"])
    )

    assert photo is not None
