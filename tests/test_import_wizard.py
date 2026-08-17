"""The import wizard — TODO 1.10.1, 1.10.7.

⚠ The load-bearing test in this file is
``test_there_is_no_path_from_upload_straight_to_writing``. The preview's whole
promise is that it ran the real code and rolled it back; a route that let an
operator skip it would make that promise worthless, and it is the kind of thing
a later refactor removes without noticing.
"""

from __future__ import annotations

import pytest
from django.core.files.storage import default_storage
from django.urls import reverse

from apps.core.scoping import allow_unscoped
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
from apps.imports import guessing, staging
from apps.imports.students import StudentImporter

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"

CSV_BYTES = (
    b"Student ID,First name,Last name,DOB,Parent email\r\n"
    b"S1,Bopha,Chan,2015-03-04,dara@example.com\r\n"
    b"S2,Sokha,Chan,2017-08-11,dara@example.com\r\n"
)


def upload(name="roster.csv", content=CSV_BYTES):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content, content_type="text/csv")


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


def make_user(org, dojo, role, email):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Ops", family_name="User")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=role,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        return User.objects.create_user(email=email, password=PASSWORD, person=person)


@pytest.fixture
def admin_user(org, dojo):
    return make_user(org, dojo, Role.DOJO_ADMIN, "ops@example.com")


def student_count(org):
    with allow_unscoped("test read"):
        return StudentProfile.objects.filter(person__organization=org).count()


def mapping_post(dojo, action):
    return {
        "action": action,
        "dojo": str(dojo.pk),
        "map:Student ID": "external_id",
        "map:First name": "given_name",
        "map:Last name": "family_name",
        "map:DOB": "date_of_birth",
        "map:Parent email": "guardian_email",
    }


# -- column guessing ----------------------------------------------------------


def test_common_competitor_headers_are_guessed():
    fields = StudentImporter().fields
    guessed = guessing.guess(
        ["Member ID", "First Name", "Surname", "D.O.B.", "Parent Email"], fields
    )

    assert guessed["First Name"] == "given_name"
    assert guessed["Surname"] == "family_name"
    assert guessed["D.O.B."] == "date_of_birth"
    assert guessed["Parent Email"] == "guardian_email"


def test_a_field_is_never_guessed_twice():
    """⚠ The mapping validator refuses two columns claiming one field, so a
    greedy guesser would hand the operator an error before they touched it."""
    fields = StudentImporter().fields
    guessed = guessing.guess(["Email", "Parent email", "Contact email"], fields)

    assert sorted(guessed.values()) == sorted(set(guessed.values()))


def test_an_exact_match_beats_a_looser_one():
    fields = StudentImporter().fields
    guessed = guessing.guess(["Parent email", "Email"], fields)

    assert guessed["Email"] == "email"
    assert guessed["Parent email"] == "guardian_email"


# -- the wizard ---------------------------------------------------------------


def test_the_upload_step_offers_only_dojos_you_may_import_into(client, org, dojo, admin_user):
    with allow_unscoped("test setup"):
        Dojo.objects.create(
            organization=org, name="Dojo B", slug="dojo-b", timezone="Asia/Phnom_Penh"
        )
    client.force_login(admin_user)

    body = client.get(reverse("import-wizard")).content.decode()

    assert "Dojo A" in body
    assert "Dojo B" not in body


def test_uploading_shows_the_mapping_step_with_guesses(client, dojo, admin_user):
    client.force_login(admin_user)

    response = client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "Student ID" in body
    assert "given_name" in body
    assert "Bopha" in body  # the operator's own rows, to check the guess against


def test_an_unreadable_file_is_reported_not_crashed(client, dojo, admin_user):
    """⚠ Bytes that defeat *both* encodings.

    b"\\x00\\x01\\x02" would not do — those are perfectly valid UTF-8 control
    characters, and cp1252 takes them too, so the file gets as far as "header row
    but no data". 0x81 and 0x8d are undefined in cp1252 and invalid as a UTF-8
    lead byte, which is what actually exercises the decode failure.
    """
    client.force_login(admin_user)

    response = client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload(content=b"\x81\x8d\x8f\x90")},
    )

    assert response.status_code == 200
    assert "not readable as text" in response.content.decode().lower()


def test_a_file_that_decodes_but_has_no_rows_is_reported(client, dojo, admin_user):
    client.force_login(admin_user)

    response = client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload(content=b"Just a header\r\n")},
    )

    assert response.status_code == 200
    assert "no data" in response.content.decode().lower()


def test_preview_writes_nothing(client, org, dojo, admin_user):
    client.force_login(admin_user)
    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )

    response = client.post(reverse("import-wizard"), mapping_post(dojo, "preview"))

    assert response.status_code == 200
    assert "Nothing has been written" in response.content.decode()
    assert student_count(org) == 0


def test_commit_writes(client, org, dojo, admin_user):
    client.force_login(admin_user)
    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )
    client.post(reverse("import-wizard"), mapping_post(dojo, "preview"))

    response = client.post(reverse("import-wizard"), mapping_post(dojo, "commit"))

    assert response.status_code == 200
    assert student_count(org) == 2


def test_there_is_no_path_from_upload_straight_to_writing(client, org, dojo, admin_user):
    """⚠ Commit requires a staged file that the operator uploaded, and the only
    screen offering the button is the dry run's result. An upload alone must
    never write."""
    client.force_login(admin_user)

    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )

    assert student_count(org) == 0


def test_the_commit_button_is_absent_once_the_import_is_real(client, dojo, admin_user):
    client.force_login(admin_user)
    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )
    client.post(reverse("import-wizard"), mapping_post(dojo, "preview"))

    body = client.post(reverse("import-wizard"), mapping_post(dojo, "commit")).content.decode()

    assert 'value="commit"' not in body


def test_an_invalid_mapping_returns_to_the_mapping_step(client, dojo, admin_user):
    client.force_login(admin_user)
    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )

    response = client.post(
        reverse("import-wizard"),
        {"action": "preview", "dojo": str(dojo.pk), "map:Last name": "family_name"},
    )

    body = response.content.decode()
    assert "given_name" in body  # the required field it complained about
    assert "Student ID" in body  # and we are back on the mapping table


def test_committing_discards_the_staged_file(client, dojo, admin_user):
    """⚠ A roster on disk after the import is finished buys nothing."""
    client.force_login(admin_user)
    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )
    token = client.session[staging.SESSION_KEY]["token"]
    assert default_storage.exists(f"{staging.PREFIX}/{token}.csv")

    client.post(reverse("import-wizard"), mapping_post(dojo, "preview"))
    client.post(reverse("import-wizard"), mapping_post(dojo, "commit"))

    assert not default_storage.exists(f"{staging.PREFIX}/{token}.csv")
    assert staging.SESSION_KEY not in client.session


def test_a_staged_file_is_not_readable_by_another_organisation(client, org, dojo, admin_user):
    """The staged bytes are a whole roster; the session that staged them is the
    only thing that may read them back."""
    client.force_login(admin_user)
    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )

    other_org = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        other_dojo = Dojo.objects.create(
            organization=other_org, name="Other", slug="other-dojo", timezone="UTC"
        )
    other = make_user(other_org, other_dojo, Role.DOJO_ADMIN, "other@example.com")

    client.force_login(other)
    response = client.post(reverse("import-wizard"), mapping_post(other_dojo, "preview"))

    assert "expired" in response.content.decode().lower()


# -- permission ---------------------------------------------------------------


def test_an_instructor_cannot_reach_the_wizard(client, org, dojo):
    instructor = make_user(org, dojo, Role.INSTRUCTOR, "sensei@example.com")
    client.force_login(instructor)

    assert client.get(reverse("import-wizard")).status_code == 403


def test_anonymous_is_redirected(client):
    response = client.get(reverse("import-wizard"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_importing_into_another_tenants_dojo_is_a_404(client, org, dojo, admin_user):
    other_org = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        other_dojo = Dojo.objects.create(
            organization=other_org, name="Other", slug="other-dojo", timezone="UTC"
        )
    client.force_login(admin_user)

    response = client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(other_dojo.pk), "file": upload()},
    )

    assert response.status_code == 404


# -- the report ---------------------------------------------------------------


def test_the_report_downloads_as_csv(client, dojo, admin_user):
    client.force_login(admin_user)
    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )
    client.post(reverse("import-wizard"), mapping_post(dojo, "preview"))

    from apps.imports.models import ImportRun

    with allow_unscoped("test read"):
        run = ImportRun.objects.latest("created_at")

    response = client.get(reverse("import-report", args=[run.pk]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    body = response.content.decode()
    assert "Row,Outcome,Source key,Detail" in body
    assert "created" in body


def test_another_tenants_report_is_a_404(client, org, dojo, admin_user):
    client.force_login(admin_user)
    client.post(
        reverse("import-wizard"),
        {"action": "upload", "dojo": str(dojo.pk), "file": upload()},
    )
    client.post(reverse("import-wizard"), mapping_post(dojo, "preview"))

    from apps.imports.models import ImportRun

    with allow_unscoped("test read"):
        run = ImportRun.objects.latest("created_at")

    other_org = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        other_dojo = Dojo.objects.create(
            organization=other_org, name="Other", slug="other-dojo", timezone="UTC"
        )
    other = make_user(other_org, other_dojo, Role.DOJO_ADMIN, "other@example.com")
    client.force_login(other)

    assert client.get(reverse("import-report", args=[run.pk])).status_code == 404
