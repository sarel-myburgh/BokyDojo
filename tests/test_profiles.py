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
