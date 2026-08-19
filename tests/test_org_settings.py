"""Styles, dojos, and the people screens that were missing.

The worked example throughout is the one that prompted this: Emma enrols at Sen
Sok, which teaches Goju Ryu, and at Urban Village, which teaches boxing. She ends
up with two style tracks — a graded one and an unranked one — without anybody
creating either by hand.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.enrolment import enrol_student
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
from apps.ranks.enrolment_tracks import choose_ladder, sync_tracks_for_enrolment
from apps.ranks.models import Rank, RankLadder, StudentStyleTrack, Style

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Shimbukai", slug="shimbukai")


@pytest.fixture
def goju(org):
    with allow_unscoped("test setup"):
        style = Style.objects.create(organization=org, name="Goju Ryu", is_ranked=True)
        ladder = RankLadder.objects.create(
            style=style, name="Adult", applies_to=RankLadder.AppliesTo.ADULT
        )
        for order, name in enumerate(["10th Kyu", "9th Kyu"], start=1):
            Rank.objects.create(ladder=ladder, name=name, order=order)
        return style


@pytest.fixture
def boxing(org):
    with allow_unscoped("test setup"):
        return Style.objects.create(organization=org, name="Boxing", is_ranked=False)


@pytest.fixture
def dojos(org, goju, boxing):
    with allow_unscoped("test setup"):
        sen_sok = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        sen_sok.styles.set([goju])
        urban = Dojo.objects.create(
            organization=org, name="Urban Village", slug="urban", timezone="Asia/Phnom_Penh"
        )
        urban.styles.set([boxing])
        return {"sen_sok": sen_sok, "urban": urban}


@pytest.fixture
def admin(org, dojos):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
        user = User.objects.create_user(email="ops@example.com", password=PASSWORD, person=person)
    return user


@pytest.fixture
def actor(org, admin):
    return Actor(
        user_id=admin.pk,
        person_id=admin.person_id,
        organization_id=org.pk,
        dojo_ids=None,
        roles=frozenset({(Role.ORG_ADMIN, ScopeType.ORG, None)}),
    )


def make_student(org, given="Emma", dob=None):
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=org, given_name=given, family_name="Roux", date_of_birth=dob
        )
        StudentProfile.objects.create(person=person, status=StudentProfile.Status.ACTIVE)
        return person


def tracks_of(person):
    with allow_unscoped("test read"):
        return list(
            StudentStyleTrack.objects.filter(student=person).select_related("style", "ladder")
        )


# -- the worked example -------------------------------------------------------


def test_enrolling_at_two_dojos_gives_a_track_per_style(org, dojos, actor):
    """Emma at Sen Sok (Goju Ryu) and Urban Village (boxing) → two tracks."""
    emma = make_student(org)

    enrol_student(
        student=emma, dojo=dojos["sen_sok"], started_on=datetime.date(2026, 1, 5), actor=actor
    )
    enrol_student(
        student=emma, dojo=dojos["urban"], started_on=datetime.date(2026, 2, 1), actor=actor
    )

    by_style = {track.style.name: track for track in tracks_of(emma)}
    assert set(by_style) == {"Goju Ryu", "Boxing"}
    assert by_style["Goju Ryu"].ladder is not None
    # ⚠ An unranked style gets a track and no ladder. The track records that she
    # trains boxing; there is simply no belt to hold.
    assert by_style["Boxing"].ladder is None


def test_the_ranked_track_can_be_promoted_and_the_unranked_one_has_no_ladder(org, dojos, actor):
    emma = make_student(org)
    enrol_student(
        student=emma, dojo=dojos["sen_sok"], started_on=datetime.date(2026, 1, 5), actor=actor
    )
    enrol_student(
        student=emma, dojo=dojos["urban"], started_on=datetime.date(2026, 2, 1), actor=actor
    )
    goju_track = next(t for t in tracks_of(emma) if t.style.name == "Goju Ryu")

    from apps.ranks.promotions import promote_student

    with allow_unscoped("test read"):
        profile = StudentProfile.objects.get(person=emma)
        rank = Rank.objects.get(name="10th Kyu")
    promote_student(
        profile=profile,
        track=goju_track,
        rank=rank,
        awarded_on=datetime.date(2026, 3, 1),
        actor=actor,
    )

    by_style = {t.style.name: t for t in tracks_of(emma)}
    assert by_style["Goju Ryu"].current_rank.name == "10th Kyu"
    assert by_style["Boxing"].current_rank is None


def test_enrolling_twice_does_not_duplicate_a_track(org, dojos, actor):
    emma = make_student(org)
    enrol_student(
        student=emma, dojo=dojos["sen_sok"], started_on=datetime.date(2026, 1, 5), actor=actor
    )
    with allow_unscoped("test setup"):
        enrollment = emma.enrollments.first()

    sync_tracks_for_enrolment(enrollment, actor=actor)

    assert len(tracks_of(emma)) == 1


def test_a_dojo_with_no_styles_creates_no_tracks(org, actor):
    with allow_unscoped("test setup"):
        bare = Dojo.objects.create(organization=org, name="Bare", slug="bare", timezone="UTC")
    emma = make_student(org)

    enrol_student(student=emma, dojo=bare, started_on=datetime.date(2026, 1, 5), actor=actor)

    assert tracks_of(emma) == []


# -- choosing a ladder --------------------------------------------------------


def test_a_style_with_one_ladder_uses_it_regardless_of_age(org, goju):
    child = make_student(org, given="Kid", dob=datetime.date(2020, 1, 1))

    ladder = choose_ladder(goju, student=child, organization_id=org.pk)

    assert ladder is not None


def test_age_decides_between_a_junior_and_an_adult_ladder(org, goju):
    with allow_unscoped("test setup"):
        RankLadder.objects.create(style=goju, name="Junior", applies_to=RankLadder.AppliesTo.JUNIOR)
    child = make_student(
        org, given="Kid", dob=datetime.date.today() - datetime.timedelta(days=8 * 365)
    )
    adult = make_student(
        org, given="Grown", dob=datetime.date.today() - datetime.timedelta(days=30 * 365)
    )

    assert choose_ladder(goju, student=child, organization_id=org.pk).applies_to == "junior"
    assert choose_ladder(goju, student=adult, organization_id=org.pk).applies_to == "adult"


def test_an_unknown_birthday_leaves_the_ladder_unset_rather_than_guessing(org, goju):
    """⚠ Putting an eight-year-old on the adult ladder is invisible until a
    grading. No ladder is the honest answer."""
    with allow_unscoped("test setup"):
        RankLadder.objects.create(style=goju, name="Junior", applies_to=RankLadder.AppliesTo.JUNIOR)
    unknown = make_student(org, given="Nobirthday", dob=None)

    assert choose_ladder(goju, student=unknown, organization_id=org.pk) is None


def test_a_ladder_is_filled_in_once_the_birthday_is_known(org, goju, dojos, actor):
    """A gap being closed, not a decision reversed."""
    with allow_unscoped("test setup"):
        RankLadder.objects.create(style=goju, name="Junior", applies_to=RankLadder.AppliesTo.JUNIOR)
    emma = make_student(org, dob=None)
    enrol_student(
        student=emma, dojo=dojos["sen_sok"], started_on=datetime.date(2026, 1, 5), actor=actor
    )
    assert tracks_of(emma)[0].ladder is None

    with allow_unscoped("test setup"):
        emma.date_of_birth = datetime.date.today() - datetime.timedelta(days=9 * 365)
        emma.save(update_fields=["date_of_birth"])
        enrollment = emma.enrollments.first()
    sync_tracks_for_enrolment(enrollment, actor=actor)

    assert tracks_of(emma)[0].ladder.applies_to == "junior"


def test_an_unranked_style_never_gets_a_ladder(org, boxing):
    emma = make_student(org, dob=datetime.date(2000, 1, 1))

    assert choose_ladder(boxing, student=emma, organization_id=org.pk) is None


# -- the settings screen ------------------------------------------------------


def test_the_settings_page_lists_styles_and_dojos(client, admin, dojos):
    client.force_login(admin)

    body = client.get(reverse("org-settings")).content.decode()

    assert "Goju Ryu" in body
    assert "Boxing" in body
    assert "Sen Sok" in body
    assert "unranked" in body


def test_a_style_can_be_added(client, admin, org):
    client.force_login(admin)

    client.post(reverse("style-create"), {"name": "Shotokan", "is_ranked": "on"})

    with allow_unscoped("test read"):
        assert Style.objects.filter(organization=org, name="Shotokan", is_ranked=True).exists()


def test_an_unranked_style_can_be_added(client, admin, org):
    client.force_login(admin)

    client.post(reverse("style-create"), {"name": "Conditioning"})

    with allow_unscoped("test read"):
        assert Style.objects.get(name="Conditioning").is_ranked is False


def test_a_duplicate_style_name_is_refused(client, admin, org, goju):
    client.force_login(admin)

    client.post(reverse("style-create"), {"name": "goju ryu", "is_ranked": "on"})

    with allow_unscoped("test read"):
        assert Style.objects.filter(organization=org).count() == 2  # goju + boxing


def test_a_graded_style_with_ladders_cannot_be_marked_unranked(client, admin, goju):
    """⚠ It would orphan the ladders and leave awards pointing at a style that
    claims not to grade."""
    client.force_login(admin)

    client.post(reverse("style-toggle-ranked", args=[goju.pk]))

    with allow_unscoped("test read"):
        goju.refresh_from_db()
    assert goju.is_ranked is True


def test_an_unranked_style_can_be_marked_graded(client, admin, boxing):
    client.force_login(admin)

    client.post(reverse("style-toggle-ranked", args=[boxing.pk]))

    with allow_unscoped("test read"):
        boxing.refresh_from_db()
    assert boxing.is_ranked is True


def test_an_instructor_cannot_reach_organisation_settings(client, org, dojos):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Sen", family_name="Sei")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojos["sen_sok"],
        )
        user = User.objects.create_user(
            email="sensei@example.com", password=PASSWORD, person=person
        )
    client.force_login(user)

    assert client.get(reverse("org-settings")).status_code == 403


# -- dojos --------------------------------------------------------------------


def test_a_dojo_can_be_created_with_styles(client, admin, org, goju):
    client.force_login(admin)

    client.post(
        reverse("dojo-create"),
        {
            "name": "Toul Kork",
            "slug": "",
            "timezone": "Asia/Phnom_Penh",
            "currency": "USD",
            "city": "",
            "country": "",
            "contact_email": "",
            "contact_phone": "",
            "styles": [str(goju.pk)],
        },
    )

    with allow_unscoped("test read"):
        dojo = Dojo.objects.get(name="Toul Kork")
        assert dojo.slug == "toul-kork"
        assert list(dojo.styles.all()) == [goju]


def test_a_bad_timezone_is_refused(client, admin):
    """⚠ Otherwise every class time at that dojo silently renders in UTC."""
    client.force_login(admin)

    client.post(
        reverse("dojo-create"),
        {"name": "Nowhere", "slug": "nowhere", "timezone": "Mars/Olympus", "currency": "USD"},
    )

    with allow_unscoped("test read"):
        assert not Dojo.objects.filter(name="Nowhere").exists()


def test_adding_a_style_to_a_dojo_backfills_existing_members(
    client, admin, actor, org, dojos, boxing
):
    """⚠ Otherwise adding boxing to a dojo applies only to people who join later."""
    emma = make_student(org)
    enrol_student(
        student=emma, dojo=dojos["sen_sok"], started_on=datetime.date(2026, 1, 5), actor=actor
    )
    assert len(tracks_of(emma)) == 1
    client.force_login(admin)

    client.post(
        reverse("dojo-edit", args=[dojos["sen_sok"].pk]),
        {
            "name": "Sen Sok",
            "slug": "sen-sok",
            "timezone": "Asia/Phnom_Penh",
            "currency": "USD",
            "city": "",
            "country": "",
            "contact_email": "",
            "contact_phone": "",
            "styles": [str(s.pk) for s in [boxing]] + [str(t.style.pk) for t in tracks_of(emma)],
        },
    )

    assert {t.style.name for t in tracks_of(emma)} == {"Goju Ryu", "Boxing"}


def test_another_organisations_style_cannot_be_attached(client, admin, org):
    """⚠ The queryset is scoped, so a crafted POST is refused rather than
    silently attaching another tenant's style."""
    other = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        foreign = Style.objects.create(organization=other, name="Foreign")
    client.force_login(admin)

    client.post(
        reverse("dojo-create"),
        {
            "name": "Sneaky",
            "slug": "sneaky",
            "timezone": "UTC",
            "currency": "USD",
            "styles": [str(foreign.pk)],
        },
    )

    with allow_unscoped("test read"):
        assert not Dojo.objects.filter(name="Sneaky").exists()


# -- adding people ------------------------------------------------------------


def test_a_student_can_be_added_and_gets_their_style_tracks(client, admin, org, dojos):
    client.force_login(admin)

    client.post(
        reverse("student-create"),
        {
            "given_name": "Emma",
            "family_name": "Roux",
            "date_of_birth": "2015-04-02",
            "email": "",
            "phone": "",
            "dojo": str(dojos["sen_sok"].pk),
            "started_on": "2026-01-05",
        },
    )

    with allow_unscoped("test read"):
        emma = Person.objects.get(given_name="Emma", family_name="Roux")
        assert StudentProfile.objects.filter(person=emma).exists()
        assert emma.enrollments.filter(dojo=dojos["sen_sok"]).exists()
    assert {t.style.name for t in tracks_of(emma)} == {"Goju Ryu"}


def test_a_staff_member_can_hold_several_roles_at_once(client, admin, org, dojos, goju):
    """⚠ The point of the change: an administrator who also teaches.

    RoleAssignment was always unique on (person, role, scope, dojo) precisely so
    somebody could hold several, and can() has always walked the whole set. Only
    the form pretended a person had one job.
    """
    from apps.identity.models import InstructorAssignment
    from apps.staffing.models import InstructorProfile

    client.force_login(admin)

    client.post(
        reverse("staff-create"),
        {
            "given_name": "Dara",
            "family_name": "Sok",
            "email": "dara@example.com",
            "roles": [Role.DOJO_ADMIN, Role.INSTRUCTOR],
            "scope": "dojo",
            "dojo": str(dojos["sen_sok"].pk),
            "styles": [str(goju.pk)],
            "pay_type": InstructorProfile.PayType.PER_CLASS,
            "pay_rate": "15.00",
        },
    )

    with allow_unscoped("test read"):
        person = Person.objects.get(given_name="Dara")
        roles = set(
            RoleAssignment.objects.filter(person=person, revoked_at__isnull=True).values_list(
                "role", flat=True
            )
        )
        assert roles == {Role.DOJO_ADMIN, Role.INSTRUCTOR}
        # Teaching roles still get the records that let them be put on a class
        # and paid for it.
        assert InstructorAssignment.objects.filter(person=person).exists()
        assert InstructorProfile.objects.filter(person=person).exists()
        assert not User.objects.get(email="dara@example.com").has_usable_password()


def test_an_admin_can_be_added_without_teaching(client, admin, org, dojos):
    """A pure administrator needs no pay details or dojo assignment."""
    from apps.identity.models import InstructorAssignment

    client.force_login(admin)

    client.post(
        reverse("staff-create"),
        {
            "given_name": "Mala",
            "family_name": "Chan",
            "email": "mala@example.com",
            "roles": [Role.ORG_ADMIN],
            "scope": "org",
        },
    )

    with allow_unscoped("test read"):
        person = Person.objects.get(given_name="Mala")
        assignment = RoleAssignment.objects.get(person=person)
        assert assignment.role == Role.ORG_ADMIN
        assert assignment.scope_type == ScopeType.ORG
        assert assignment.dojo_id is None
        assert not InstructorAssignment.objects.filter(person=person).exists()


def test_an_org_admin_cannot_be_scoped_to_one_dojo(client, admin, dojos):
    """⚠ A dojo-scoped org admin holds none of the powers the role implies —
    can() only grants a dojo-scoped role over that dojo's own objects."""
    client.force_login(admin)

    client.post(
        reverse("staff-create"),
        {
            "given_name": "Wrong",
            "family_name": "Scope",
            "email": "wrong@example.com",
            "roles": [Role.ORG_ADMIN],
            "scope": "dojo",
            "dojo": str(dojos["sen_sok"].pk),
        },
    )

    with allow_unscoped("test read"):
        assert not Person.objects.filter(given_name="Wrong").exists()


def test_a_teaching_role_requires_a_dojo(client, admin):
    """⚠ InstructorAssignment is per dojo; without one every substitution is
    refused."""
    from apps.staffing.models import InstructorProfile

    client.force_login(admin)

    client.post(
        reverse("staff-create"),
        {
            "given_name": "NoDojo",
            "family_name": "Teacher",
            "email": "nodojo@example.com",
            "roles": [Role.INSTRUCTOR],
            "scope": "org",
            "pay_type": InstructorProfile.PayType.VOLUNTEER,
            "pay_rate": "0",
        },
    )

    with allow_unscoped("test read"):
        assert not Person.objects.filter(given_name="NoDojo").exists()


def test_an_existing_person_can_be_granted_another_role(client, admin, org, dojos):
    """⚠ The screen that makes this RBAC rather than a job title: without it an
    instructor could only become an admin by being created twice."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojos["sen_sok"],
        )
    client.force_login(admin)

    client.post(
        reverse("role-grant", args=[person.pk]),
        {"role": Role.ORG_ADMIN, "scope": "org"},
    )

    with allow_unscoped("test read"):
        roles = set(
            RoleAssignment.objects.filter(person=person, revoked_at__isnull=True).values_list(
                "role", flat=True
            )
        )
    assert roles == {Role.INSTRUCTOR, Role.ORG_ADMIN}


def test_revoking_a_role_keeps_the_record(client, admin, org, dojos):
    """⚠ Revoked, not deleted. Who held what and until when is the question an
    investigation asks months later."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Gone", family_name="Away")
        assignment = RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojos["sen_sok"],
        )
    client.force_login(admin)

    client.post(reverse("role-revoke", args=[person.pk, assignment.pk]))

    with allow_unscoped("test read"):
        assignment.refresh_from_db()
        assert assignment.revoked_at is not None
        assert RoleAssignment.objects.filter(pk=assignment.pk).exists()


def test_the_last_organisation_administrator_cannot_be_revoked(client, admin, org):
    """⚠ Otherwise the tenant locks itself out and needs database access back."""
    with allow_unscoped("test read"):
        assignment = RoleAssignment.objects.get(person=admin.person, role=Role.ORG_ADMIN)
    client.force_login(admin)

    client.post(reverse("role-revoke", args=[admin.person_id, assignment.pk]))

    with allow_unscoped("test read"):
        assignment.refresh_from_db()
    assert assignment.revoked_at is None


def test_a_duplicate_staff_email_is_refused(client, admin, dojos):
    client.force_login(admin)

    client.post(
        reverse("staff-create"),
        {
            "given_name": "Dara",
            "family_name": "Sok",
            "email": "ops@example.com",  # the admin's own address
            "roles": [Role.FRONT_DESK],
            "scope": "dojo",
            "dojo": str(dojos["sen_sok"].pk),
        },
    )

    with allow_unscoped("test read"):
        assert not Person.objects.filter(given_name="Dara").exists()


def test_an_instructor_cannot_manage_roles(client, org, dojos):
    """⚠ ROLE_ASSIGN, not merely being staff."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Sen", family_name="Sei")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojos["sen_sok"],
        )
        user = User.objects.create_user(
            email="sensei2@example.com", password=PASSWORD, person=person
        )
    client.force_login(user)

    assert client.post(reverse("role-grant", args=[person.pk])).status_code == 403
    assert client.get(reverse("staff-create")).status_code == 403


# -- the settings page's shape ------------------------------------------------


def test_each_settings_section_offers_its_own_add_button(client, admin, dojos):
    """⚠ Three sections that behave the same way.

    The styles section used to carry an inline form while dojos carried a link,
    which is the sort of inconsistency that has people hunting for a button that
    is not there.
    """
    client.force_login(admin)

    body = client.get(reverse("org-settings")).content.decode()

    for target in ("style-create", "dojo-create", "staff-create"):
        assert reverse(target) in body, f"no add button for {target}"


def test_the_settings_page_no_longer_takes_a_post(client, admin):
    """Adding happens on its own screen now."""
    client.force_login(admin)

    assert client.post(reverse("org-settings"), {"name": "Nope"}).status_code == 405


def test_staff_appear_on_the_settings_page(client, admin, org, dojos):
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=org, given_name="Visible", family_name="Staffer"
        )
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.FRONT_DESK,
            scope_type=ScopeType.DOJO,
            dojo=dojos["sen_sok"],
        )
    client.force_login(admin)

    body = client.get(reverse("org-settings")).content.decode()

    assert "Visible Staffer" in body


def test_adding_a_student_is_offered_with_the_students_not_in_settings(client, admin):
    """It belongs where students live."""
    client.force_login(admin)

    students = client.get(reverse("student-list")).content.decode()
    settings_page = client.get(reverse("org-settings")).content.decode()

    assert reverse("student-create") in students
    assert reverse("student-create") not in settings_page


def test_the_add_student_button_is_hidden_from_somebody_who_cannot(client, org, dojos):
    """⚠ Menu visibility is not a control — student_create_view checks the same
    action. This only stops offering a button that would refuse."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Sen", family_name="Sei")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.ASSISTANT_INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojos["sen_sok"],
        )
        user = User.objects.create_user(
            email="assistant@example.com", password=PASSWORD, person=person
        )
    client.force_login(user)

    body = client.get(reverse("student-list")).content.decode()

    assert reverse("student-create") not in body
    assert client.get(reverse("student-create")).status_code == 403
