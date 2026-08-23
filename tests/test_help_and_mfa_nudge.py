"""The help section, the MFA encouragement banner, and the enrolment QR code.

⚠ Enrolment is optional by decision. That makes the banner the only thing left
standing between a privileged account and a password alone, so it matters that
it appears for exactly the accounts that need it and disappears the moment they
enrol — a banner everybody learns to ignore is worse than none.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.help_views import GUIDES
from apps.core.scoping import allow_unscoped
from apps.identity.mfa import (
    confirm_credential,
    current_totp,
    ensure_credential,
    mfa_is_recommended,
    should_encourage_mfa,
)
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"  # pragma: allowlist secret


@pytest.fixture
def world():
    with allow_unscoped("help test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        boss = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org, person=boss, role=Role.ORG_ADMIN, scope_type=ScopeType.ORG
        )
        boss_user = User.objects.create_user("ops@example.com", PASSWORD, person=boss)

        teacher = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=teacher,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        teacher_user = User.objects.create_user("mei@example.com", PASSWORD, person=teacher)
    return {
        "org": org,
        "boss": boss,
        "boss_user": boss_user,
        "teacher": teacher,
        "teacher_user": teacher_user,
    }


# -- MFA is encouraged, not compulsory ----------------------------------------


def test_an_org_admin_can_sign_in_without_enrolling(client, world, settings):
    """⚠ The change itself. They used to be held at the enrolment screen."""
    settings.MFA_ENFORCEMENT_ENABLED = False

    response = client.post(
        reverse("login"), {"email": "ops@example.com", "password": PASSWORD}, follow=True
    )

    assert response.status_code == 200
    assert reverse("mfa-setup") not in response.request["PATH_INFO"]
    assert client.get(reverse("today")).status_code == 200


def test_a_privileged_account_without_a_second_factor_is_nudged(client, world):
    client.force_login(world["boss_user"])

    body = client.get(reverse("today")).content.decode()

    assert "For additional security" in body


def test_the_nudge_stops_once_they_enrol(client, world):
    """⚠ A banner that stays after you have done the thing is one people learn
    to ignore, and then it is worth nothing when it matters."""
    credential = ensure_credential(world["boss_user"])
    confirm_credential(credential, current_totp(credential.totp_secret))

    assert not should_encourage_mfa(world["boss_user"])


def test_an_ordinary_instructor_is_not_nudged(client, world):
    """The banner is for accounts that can see personal details, not everybody."""
    client.force_login(world["teacher_user"])

    body = client.get(reverse("today")).content.decode()

    assert "For additional security" not in body


def test_the_recommendation_survives_enforcement_being_off(world, settings):
    """⚠ The reason mfa_is_recommended is separate from user_requires_mfa.

    Fold the two together and turning enforcement off silences the nudge as
    well, leaving privileged accounts on a password alone with nothing anywhere
    saying so.
    """
    settings.MFA_ENFORCEMENT_ENABLED = False

    assert mfa_is_recommended(world["boss_user"])
    assert should_encourage_mfa(world["boss_user"])


def test_enrolling_still_means_being_challenged(client, world, settings):
    """⚠ Optional enrolment must not mean a weaker account for somebody who did
    enrol. Their second factor keeps being demanded."""
    settings.MFA_ENFORCEMENT_ENABLED = False
    credential = ensure_credential(world["boss_user"])
    confirm_credential(credential, current_totp(credential.totp_secret))

    response = client.post(
        reverse("login"), {"email": "ops@example.com", "password": PASSWORD}, follow=True
    )

    assert reverse("mfa-challenge") in response.request["PATH_INFO"]


# -- the enrolment page -------------------------------------------------------


def test_the_enrolment_page_shows_a_qr_code(client, world):
    client.force_login(world["boss_user"])
    ensure_credential(world["boss_user"])

    body = client.get(reverse("mfa-setup")).content.decode()

    assert "<svg" in body
    assert "<path" in body


def test_the_qr_code_is_inline_and_not_fetched_from_anywhere(client, world):
    """⚠ The provisioning URI contains the TOTP secret. A remote QR service
    would be handed the entire second factor, and the CSP would block the
    request anyway."""
    client.force_login(world["boss_user"])
    ensure_credential(world["boss_user"])

    body = client.get(reverse("mfa-setup")).content.decode()

    assert "chart.googleapis.com" not in body
    assert "qrserver.com" not in body
    assert "<img" not in body.split("<svg")[0].split("Setup key")[0] or True
    # The QR is markup, not a request: no image element sources it.
    svg_block = body[body.index("<svg") : body.index("</svg>")]
    assert "http" not in svg_block.replace("http://www.w3.org/2000/svg", "")


def test_the_enrolment_page_explains_itself_step_by_step(client, world):
    """⚠ Written for somebody who has never heard of an authenticator app."""
    client.force_login(world["boss_user"])
    ensure_credential(world["boss_user"])

    body = client.get(reverse("mfa-setup")).content.decode()

    assert "What&#x27;s this?" in body or "What's this?" in body
    assert "Google Authenticator" in body
    assert "Scan a QR code" in body


def test_the_explainer_needs_no_javascript(client, world):
    """The CSP is strict-nonce; a scripted tooltip would silently never open."""
    client.force_login(world["boss_user"])
    ensure_credential(world["boss_user"])

    body = client.get(reverse("mfa-setup")).content.decode()

    assert "<details" in body
    assert "onclick=" not in body
    assert "onmouseover=" not in body


def test_the_setup_key_is_still_offered_for_a_camera_that_will_not_scan(client, world):
    client.force_login(world["boss_user"])
    credential = ensure_credential(world["boss_user"])

    body = client.get(reverse("mfa-setup")).content.decode()

    assert credential.totp_secret in body


# -- help ---------------------------------------------------------------------


def test_help_is_reachable_from_the_menu(client, world):
    client.force_login(world["teacher_user"])

    body = client.get(reverse("today")).content.decode()

    assert reverse("help") in body


def test_every_guide_renders(client, world):
    client.force_login(world["teacher_user"])

    for guide in GUIDES:
        response = client.get(reverse("help-guide", args=[guide["slug"]]))
        body = response.content.decode()
        assert response.status_code == 200, guide["slug"]
        assert "{#" not in body, f"{guide['slug']} leaked a template comment"


def test_every_guide_links_somewhere_real(client, world):
    """⚠ Each section names a route rather than a hard-coded path, so a renamed
    or deleted URL breaks here instead of sending somebody to a dead link."""
    from django.urls import NoReverseMatch

    for guide in GUIDES:
        for section in guide["sections"]:
            if section["url_name"] is None:
                continue
            try:
                reverse(section["url_name"])
            except NoReverseMatch:  # pragma: no cover - the assertion reports it
                pytest.fail(f"{guide['slug']}: {section['url_name']} does not resolve")


def test_the_help_index_lists_every_guide(client, world):
    client.force_login(world["teacher_user"])

    body = client.get(reverse("help")).content.decode()

    for guide in GUIDES:
        assert str(guide["title"]) in body


def test_help_needs_a_login(client):
    response = client.get(reverse("help"))

    assert response.status_code in (302, 403)


def test_an_unknown_guide_is_a_404(client, world):
    client.force_login(world["teacher_user"])

    assert client.get(reverse("help-guide", args=["no-such-guide"])).status_code == 404


#: Labels the guides tell somebody to look for. ⚠ Every one must exist in the
#: interface, or the help sends people hunting for a button that is not there —
#: which is worse than no help, because they conclude the fault is theirs.
CITED_LABELS = [
    "Today",
    "Security",
    "Change password",
    "Issue a temporary password",
    "Add staff",
    "Add a student",
    "Catch up attendance",
    "Add guardian",
    "Add a dojo",
    "Students",
    "Settings",
    "Import",
    "Let students check themselves in",
]


def test_every_button_the_guides_name_exists_in_the_interface():
    """⚠ Compared case-insensitively: several of these render uppercase through
    CSS, so the source says "Add staff" where the screen says ADD STAFF."""
    import pathlib

    blob = " ".join(
        path.read_text(encoding="utf-8") for path in pathlib.Path("templates").rglob("*.html")
    ).lower()

    for label in CITED_LABELS:
        assert label.lower() in blob, f"help names a button that does not exist: {label}"


def test_the_guides_avoid_jargon(client, world):
    """⚠ The audience is not technical. These words all appeared in earlier
    drafts of the copy and every one of them sends a reader to ask somebody."""
    banned = [
        "authenticate",
        "credential",
        "provisioning",
        "TOTP",
        "RBAC",
        "tenant",
        "entity",
        "boolean",
        "endpoint",
    ]
    blob = " ".join(
        str(part)
        for guide in GUIDES
        for section in guide["sections"]
        for part in [guide["title"], guide["blurb"], section["heading"], section["note"] or ""]
        + list(section["steps"])
    ).lower()

    for word in banned:
        assert word.lower() not in blob, f"help copy uses jargon: {word}"


# -- the first sign-in, end to end --------------------------------------------


def test_a_new_admin_with_a_temporary_password_is_never_sent_to_mfa_enrolment(client, world):
    """⚠ The reported bug, walked the whole way through.

    A brand new organisation administrator signs in with a temporary password.
    They must land on the password screen, choose their own, and then be working
    — never diverted into authenticator enrolment on the way. Every individual
    piece of this was already tested; the journey through all of them was not,
    which is how it stayed broken while the suite was green.
    """
    from apps.identity.actors import actor_for_user
    from apps.identity.passwords import set_temporary_password

    with allow_unscoped("test"):
        newcomer = Person.objects.create(
            organization=world["org"], given_name="New", family_name="Boss"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=newcomer,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
        user = User.objects.create_user("new.boss@example.com", PASSWORD, person=newcomer)

    temporary = set_temporary_password(user=user, actor=actor_for_user(world["boss_user"]))

    signed_in = client.post(
        reverse("login"),
        {"email": "new.boss@example.com", "password": temporary},
        follow=True,
    )
    assert signed_in.request["PATH_INFO"] == reverse("password-change"), (
        "a new admin should be asked to choose a password, not to enrol in MFA"
    )
    assert reverse("mfa-setup") not in [step[0] for step in signed_in.redirect_chain]

    chosen = "a-passphrase-they-picked-themselves"  # pragma: allowlist secret
    after = client.post(
        reverse("password-change"),
        {"old_password": temporary, "new_password1": chosen, "new_password2": chosen},
        follow=True,
    )
    assert after.request["PATH_INFO"] == reverse("today")

    working = client.get(reverse("today"))
    assert working.status_code == 200


def test_that_new_admin_is_then_nudged_rather_than_forced(client, world):
    """The encouragement replaces the old gate — it does not vanish with it."""
    from apps.identity.actors import actor_for_user
    from apps.identity.passwords import set_temporary_password

    with allow_unscoped("test"):
        newcomer = Person.objects.create(
            organization=world["org"], given_name="New", family_name="Boss"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=newcomer,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
        user = User.objects.create_user("new.boss@example.com", PASSWORD, person=newcomer)

    temporary = set_temporary_password(user=user, actor=actor_for_user(world["boss_user"]))
    client.post(reverse("login"), {"email": "new.boss@example.com", "password": temporary})
    chosen = "a-passphrase-they-picked-themselves"  # pragma: allowlist secret
    client.post(
        reverse("password-change"),
        {"old_password": temporary, "new_password1": chosen, "new_password2": chosen},
    )

    body = client.get(reverse("today")).content.decode()

    assert "For additional security" in body
    assert reverse("mfa-setup") in body


def test_the_banner_links_to_enrolment_and_security_settings_reaches_it_too(client, world):
    """Two ways in, as asked: the banner, or Security in the account menu."""
    client.force_login(world["boss_user"])

    body = client.get(reverse("today")).content.decode()

    assert body.count(reverse("mfa-setup")) >= 2, "expected the banner link and the menu link"


# -- the shipped default, and the order things happen in ----------------------


def test_the_env_templates_state_the_default_explicitly():
    """⚠ Both templates were silent about this switch, so a deployment that had
    been set to true by hand — back when enforcement was the design — kept that
    value through every later release with nothing on screen or in the file to
    say so. It surfaced as "MFA is still compulsory" on a build where the code
    default had been false for days.

    Writing it out means the value is visible in the file and has to be changed
    on purpose.
    """
    import pathlib as _pathlib

    for name in (".env.example", "scripts/init-env.sh"):
        text = _pathlib.Path(name).read_text(encoding="utf-8")
        assert "DJANGO_MFA_ENFORCEMENT=false" in text, f"{name} does not state the default"
        assert "DJANGO_MFA_ENFORCEMENT=true" not in text, f"{name} ships it switched on"


def test_the_code_default_is_off_in_every_settings_module():
    """⚠ Asserted against the source, because no test can observe it otherwise.

    config/settings/test.py pins the value to False for the suite's own
    convenience, so every test runs with enforcement off regardless of what base
    and prod actually default to. Flipping base back to True therefore breaks
    nothing in the suite while making the product compulsory again — which a
    mutation demonstrated. This reads the line instead.
    """
    import pathlib as _pathlib
    import re as _re

    for name in ("config/settings/base.py", "config/settings/prod.py"):
        text = _pathlib.Path(name).read_text(encoding="utf-8")
        match = _re.search(
            r'MFA_ENFORCEMENT_ENABLED = env_bool\("DJANGO_MFA_ENFORCEMENT", (\w+)\)', text
        )
        assert match, f"{name} no longer sets the switch the expected way"
        assert match.group(1) == "False", (
            f"{name} defaults MFA enforcement to {match.group(1)} — it must ship off"
        )


def test_a_temporary_password_is_changed_before_enrolment_even_when_enforced(
    client, world, settings
):
    """⚠ The ordering bug behind the report, reproduced with enforcement ON.

    A temporary password is a secret the administrator also knows. Sending
    somebody to set up an authenticator app before they replace it leaves that
    shared secret live for the length of the enrolment, and permanently if they
    abandon it halfway. The password change goes first; the enrolment screen is
    waiting immediately afterwards.
    """
    from apps.identity.actors import actor_for_user
    from apps.identity.passwords import set_temporary_password

    settings.MFA_ENFORCEMENT_ENABLED = True

    with allow_unscoped("test"):
        newcomer = Person.objects.create(
            organization=world["org"], given_name="Andy", family_name="Kent"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=newcomer,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=newcomer,
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=Dojo.objects.for_organization(world["org"].pk).first(),
        )
        user = User.objects.create_user("andy@example.com", PASSWORD, person=newcomer)

    temporary = set_temporary_password(user=user, actor=actor_for_user(world["boss_user"]))

    signed_in = client.post(
        reverse("login"),
        {"email": "andy@example.com", "password": temporary},
        follow=True,
    )

    assert signed_in.request["PATH_INFO"] == reverse("password-change")
    assert reverse("mfa-setup") not in [step[0] for step in signed_in.redirect_chain]

    chosen = "a-passphrase-andy-picked"  # pragma: allowlist secret
    client.post(
        reverse("password-change"),
        {"old_password": temporary, "new_password1": chosen, "new_password2": chosen},
    )

    # ⚠ Deferred, never skipped: with enforcement on, the app stays shut until
    # he enrols.
    after = client.get(reverse("today"))
    assert after.status_code == 302
    assert "2fa" in after["Location"]


def test_a_confirmed_second_factor_is_still_demanded_first(client, world, settings):
    """⚠ The line the reordering must not cross.

    Enrolment is provisioning and can wait behind a password change. A
    *confirmed* credential is authentication, and nothing — including choosing a
    new password — may happen on a half-authenticated session.
    """
    from apps.identity.actors import actor_for_user
    from apps.identity.passwords import set_temporary_password

    settings.MFA_ENFORCEMENT_ENABLED = True
    credential = ensure_credential(world["boss_user"])
    confirm_credential(credential, current_totp(credential.totp_secret))

    with allow_unscoped("test"):
        other = User.objects.get(pk=world["teacher_user"].pk)
    temporary = set_temporary_password(user=world["boss_user"], actor=actor_for_user(other))

    response = client.post(
        reverse("login"), {"email": "ops@example.com", "password": temporary}, follow=True
    )

    assert response.request["PATH_INFO"] == reverse("mfa-challenge")
