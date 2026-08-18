"""Administrator-issued temporary passwords — TODO 0.6.8.

Not every organisation has working SMTP, and the reset flow is an email link — so
an administrator can hand a temporary password over in person.

⚠ This is an account-takeover primitive. The tests that matter are the ones
saying who may use it, that it does not bypass a second factor, and that the
account stops being usable with it the moment the holder has signed in.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)
from apps.identity.passwords import generate_temporary_password, set_temporary_password

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


def make_person(org, given, family="Staff"):
    with allow_unscoped("test setup"):
        return Person.objects.create(organization=org, given_name=given, family_name=family)


@pytest.fixture
def world():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        boss = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org, person=boss, role=Role.ORG_ADMIN, scope_type=ScopeType.ORG
        )
        admin_user = User.objects.create_user(
            email="ops@example.com", password=PASSWORD, person=boss
        )
        teacher = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=teacher,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        teacher_user = User.objects.create_user(
            email="mei@example.com", password="old-password-they-forgot", person=teacher
        )
    return {
        "org": org,
        "dojo": dojo,
        "admin": admin_user,
        "teacher": teacher,
        "teacher_user": teacher_user,
    }


# -- the password itself ------------------------------------------------------


def test_the_generated_password_is_a_typable_passphrase():
    """⚠ The whole reason it is words and not random characters.

    It gets read across a counter or down a phone by one person and typed by
    another, often onto a phone keyboard where copy and paste is not available.
    Letters and hyphens only — no digits, no symbols, nothing anybody has to ask
    "was that an l or a 1" about.
    """
    for _ in range(200):
        password = generate_temporary_password()
        words = password.split("-")

        assert len(words) == 4
        for word in words:
            assert word.isascii() and word.isalpha(), password
            assert word[0].isupper() and word[1:].islower(), password


def test_the_generated_password_always_clears_the_length_floor():
    """Four words is comfortably past twelve characters even at their shortest."""
    assert min(len(generate_temporary_password()) for _ in range(2000)) >= 12


def test_generated_passwords_do_not_repeat():
    assert len({generate_temporary_password() for _ in range(1000)}) == 1000


def test_the_generated_password_passes_the_policy_it_will_be_checked_against():
    """⚠ Otherwise an administrator issues one and the recipient is told, at the
    change screen, that the password they were just given is invalid."""
    from django.contrib.auth.password_validation import validate_password

    for _ in range(100):
        validate_password(generate_temporary_password())


def test_the_wordlist_is_big_enough_to_be_worth_four_words():
    """43 bits behind a five-attempt lockout. If the list ever shrinks, this says
    so rather than the entropy quietly dropping."""
    import math

    from apps.identity.wordlist import WORDS

    assert len(WORDS) >= 1500
    assert 4 * math.log2(len(WORDS)) >= 40


def test_the_password_is_never_stored_in_the_clear(world):
    """It exists once, in the response. Nothing keeps it."""
    from apps.core.scoping import Actor

    actor = Actor(
        user_id=world["admin"].pk,
        person_id=world["admin"].person_id,
        organization_id=world["org"].pk,
        dojo_ids=None,
        roles=frozenset({(Role.ORG_ADMIN, ScopeType.ORG, None)}),
    )
    password = set_temporary_password(user=world["teacher_user"], actor=actor)

    with allow_unscoped("test read"):
        world["teacher_user"].refresh_from_db()
        entries = " ".join(f"{a.note} {a.before} {a.after}" for a in AuditLog.objects.all())
    assert password not in world["teacher_user"].password  # hashed
    assert password not in entries, "the audit trail must not carry the password"


# -- who may issue one --------------------------------------------------------


def test_an_org_admin_can_issue_one(client, world):
    client.force_login(world["admin"])

    response = client.post(reverse("temporary-password", args=[world["teacher"].pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Temporary password" in body
    with allow_unscoped("test read"):
        world["teacher_user"].refresh_from_db()
    assert world["teacher_user"].must_change_password is True


def test_a_dojo_admin_cannot_issue_one(client, world):
    """⚠ Narrower than ROLE_ASSIGN on purpose. A dojo administrator may hand out
    roles; signing in as somebody else is a different power."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=world["org"], given_name="Dojo", family_name="Boss"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=person,
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=world["dojo"],
        )
        user = User.objects.create_user(
            email="dojoboss@example.com", password=PASSWORD, person=person
        )
    client.force_login(user)

    assert client.post(reverse("temporary-password", args=[world["teacher"].pk])).status_code == 403


def test_an_instructor_cannot_issue_one(client, world):
    client.force_login(world["teacher_user"])

    assert client.post(reverse("temporary-password", args=[world["teacher"].pk])).status_code == 403


def test_another_organisations_person_is_a_404(client, world):
    other = Organization.objects.create(name="Other", slug="other-org")
    outsider = make_person(other, "Outsider")
    client.force_login(world["admin"])

    assert client.post(reverse("temporary-password", args=[outsider.pk])).status_code == 404


def test_an_admin_cannot_issue_one_for_themselves(client, world):
    """Pointless, and it muddies what the audit entry means."""
    client.force_login(world["admin"])

    client.post(reverse("temporary-password", args=[world["admin"].person_id]))

    with allow_unscoped("test read"):
        world["admin"].refresh_from_db()
    assert world["admin"].must_change_password is False


def test_issuing_one_is_audited(client, world):
    client.force_login(world["admin"])

    client.post(reverse("temporary-password", args=[world["teacher"].pk]))

    with allow_unscoped("test read"):
        assert AuditLog.objects.filter(action="password_reset_by_admin").exists()


# -- using it -----------------------------------------------------------------


def _issue(client, world):
    client.force_login(world["admin"])
    body = client.post(reverse("temporary-password", args=[world["teacher"].pk])).content.decode()
    import re

    match = re.search(r"select-all font-mono[^>]*>([A-Za-z-]+)<", body)
    assert match, "the password was not rendered"
    client.logout()
    return match.group(1)


def test_the_temporary_password_signs_them_in(client, world):
    password = _issue(client, world)

    ok = client.login(email="mei@example.com", password=password)

    assert ok is True


def test_the_old_password_stops_working(client, world):
    _issue(client, world)

    assert client.login(email="mei@example.com", password="old-password-they-forgot") is False


def test_every_page_redirects_to_the_password_screen_until_it_is_changed(client, world):
    """⚠ Enforced on every request, not checked once at login.

    The administrator who issued it knows what it is, so it has to stop being a
    way in the moment the holder has used it — checking only at the login view
    would leave the account usable by two people for the life of the session.
    """
    password = _issue(client, world)
    client.login(email="mei@example.com", password=password)

    for route in ("today", "student-list", "calendar", "timesheet"):
        response = client.get(reverse(route))
        assert response.status_code == 302
        assert reverse("password-change") in response["Location"], route


def test_choosing_a_password_releases_the_session(client, world):
    password = _issue(client, world)
    client.login(email="mei@example.com", password=password)

    response = client.post(
        reverse("password-change"),
        {
            "old_password": password,
            "new_password1": "a-quite-long-chosen-passphrase",
            "new_password2": "a-quite-long-chosen-passphrase",
        },
    )

    assert response.status_code == 302
    with allow_unscoped("test read"):
        world["teacher_user"].refresh_from_db()
    assert world["teacher_user"].must_change_password is False
    # ⚠ Still signed in: update_session_auth_hash, or changing the password signs
    # them straight back out having just proved who they are.
    assert client.get(reverse("today")).status_code == 200


def test_the_change_screen_requires_the_current_password(client, world):
    """⚠ A session hijacked between sign-in and this screen must not be able to
    set a password of its own."""
    password = _issue(client, world)
    client.login(email="mei@example.com", password=password)

    client.post(
        reverse("password-change"),
        {
            "old_password": "not-the-temporary-one",
            "new_password1": "a-quite-long-chosen-passphrase",
            "new_password2": "a-quite-long-chosen-passphrase",
        },
    )

    with allow_unscoped("test read"):
        world["teacher_user"].refresh_from_db()
    assert world["teacher_user"].must_change_password is True


def test_logging_out_is_still_possible_while_held(client, world):
    password = _issue(client, world)
    client.login(email="mei@example.com", password=password)

    assert client.post(reverse("logout")).status_code in (302, 200)


def test_a_temporary_password_does_not_bypass_a_second_factor(client, world, settings):
    """⚠ It replaces the first factor and nothing else.

    Enforcement is switched on *after* issuing: with it on beforehand the
    administrator doing the issuing is themselves held at the 2FA screen, and the
    test would pass for entirely the wrong reason.
    """
    password = _issue(client, world)
    with allow_unscoped("test setup"):
        RoleAssignment.objects.create(
            organization=world["org"],
            person=world["teacher"],
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
    settings.MFA_ENFORCEMENT_ENABLED = True

    response = client.post(reverse("login"), {"email": "mei@example.com", "password": password})

    # Sent to the second factor, not to the application.
    assert response.status_code == 302
    assert "2fa" in response["Location"]


# -- the password policy -------------------------------------------------------


@pytest.mark.parametrize(
    "password,why",
    [
        ("correct horse battery staple", "a plain lower-case passphrase"),
        ("Pizza-Dense-While-Problem", "a generated passphrase"),
        ("the quick brown fox jumps", "spaces and nothing else"),
    ],
)
def test_a_passphrase_is_accepted_without_composition_rules(password, why):
    """⚠ Requiring a capital, a digit and a symbol produces "Password1!" and a
    sticky note. Requiring length produces a passphrase."""
    from django.contrib.auth.password_validation import validate_password

    validate_password(password)


def test_twelve_characters_is_the_floor():
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    validate_password("abcdefghijkl")  # exactly twelve
    with pytest.raises(ValidationError):
        validate_password("abcdefghijk")  # eleven


def test_a_known_bad_password_is_still_refused():
    """⚠ Not a composition rule — a blocklist. "qwertyuiop12" is long and
    lower-case and is in every cracking dictionary; without this the length floor
    is the only thing between an account and a ten-second guess.
    """
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        validate_password("qwertyuiop12")
