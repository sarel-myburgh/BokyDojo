"""The build badge — plan §3.

⚠ It exists to answer one question from the screen: which build is this? Pulling
an image does not restart a running container, so a stale container looks
exactly like a bad deploy. Everything here is about that question staying
answerable.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from django.urls import reverse

from apps.core.scoping import allow_unscoped
from apps.core.version import VERSION, build_revision, display_version
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
def user():
    with allow_unscoped("version test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        person = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        return User.objects.create_user("mei@example.com", PASSWORD, person=person)


def test_the_version_is_three_numbers():
    """⚠ MAJOR.MINOR.PATCH, checked so a hand-edit cannot leave "0.1" or
    "0.1.0-rc1" behind — the bump script matches this shape exactly and silently
    does nothing if it does not find it."""
    import re

    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), VERSION


def test_the_bump_script_moves_the_patch_number(tmp_path, monkeypatch):
    """⚠ The command that has to be run before every push. If it stops working,
    the badge silently stops moving and stale containers become invisible
    again."""
    import subprocess
    import sys

    source = pathlib.Path("apps/core/version.py").read_text(encoding="utf-8")
    try:
        for expected in ("0.0.1", "0.0.2"):
            pathlib.Path("apps/core/version.py").write_text(
                re.sub(r'VERSION = "[^"]+"', f'VERSION = "{_previous(expected)}"', source),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, "scripts/bump-version.py"],
                capture_output=True,
                text=True,
                check=True,
            )
            assert result.stdout.strip() == expected
    finally:
        pathlib.Path("apps/core/version.py").write_text(source, encoding="utf-8")


def _previous(version: str) -> str:
    major, minor, patch = (int(p) for p in version.split("."))
    return f"{major}.{minor}.{patch - 1}"


def test_the_bump_script_can_raise_minor_and_major(tmp_path):
    """⚠ Only ever on request — never a judgement call made here."""
    import subprocess
    import sys

    source = pathlib.Path("apps/core/version.py").read_text(encoding="utf-8")
    try:
        for flag, expected in (("--minor", "0.4.0"), ("--major", "1.0.0")):
            pathlib.Path("apps/core/version.py").write_text(
                re.sub(r'VERSION = "[^"]+"', 'VERSION = "0.3.7"', source), encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, "scripts/bump-version.py", flag],
                capture_output=True,
                text=True,
                check=True,
            )
            assert result.stdout.strip() == expected
    finally:
        pathlib.Path("apps/core/version.py").write_text(source, encoding="utf-8")


def test_the_badge_shows_all_three_numbers_and_the_commit(monkeypatch):
    """⚠ The commit is the load-bearing half. A version number that only moves on
    a release cannot tell you whether the container restarted."""
    build_revision.cache_clear()
    monkeypatch.setenv("BOKYDOJO_REVISION", "abcdef1234567890")  # pragma: allowlist secret

    try:
        assert display_version() == f"v{VERSION} (abcdef1)"
        assert display_version().count(".") == 2
    finally:
        build_revision.cache_clear()


def test_a_baked_revision_is_shortened(monkeypatch):
    build_revision.cache_clear()
    monkeypatch.setenv(
        "BOKYDOJO_REVISION",
        "9ef17cc8f583b1044d03a94cb481b39b1b35b490",  # pragma: allowlist secret
    )

    try:
        assert build_revision() == "9ef17cc"
    finally:
        build_revision.cache_clear()


def test_an_unknown_revision_says_dev_rather_than_lying(monkeypatch, tmp_path):
    """⚠ Never a blank or a plausible-looking wrong value. "dev" is the honest
    answer when nothing was baked in and there is no git to ask."""
    build_revision.cache_clear()
    monkeypatch.setenv("BOKYDOJO_REVISION", "")
    monkeypatch.setattr("apps.core.version.subprocess.run", _raise_oserror)

    try:
        assert build_revision() == "dev"
    finally:
        build_revision.cache_clear()


def _raise_oserror(*args, **kwargs):
    raise OSError("no git here")


def test_the_badge_is_on_a_signed_in_page(client, user):
    client.force_login(user)

    body = client.get(reverse("today")).content.decode()

    assert display_version() in body


def test_the_badge_is_on_the_login_page_too(client):
    """⚠ Before signing in as well: somebody diagnosing a stale container often
    cannot get past the login screen, which is the whole reason they are asking."""
    body = client.get(reverse("login")).content.decode()

    assert display_version() in body


def test_the_badge_cannot_swallow_a_tap(client, user):
    """⚠ It is fixed over the bottom-left corner, where buttons live. Without
    pointer-events-none it would intercept taps on whatever is beneath it."""
    client.force_login(user)

    body = client.get(reverse("today")).content.decode()

    marker = body.index(display_version())
    container = body.rfind("<div", 0, marker)
    assert "pointer-events-none" in body[container:marker]


def test_the_dockerfile_bakes_the_revision_in():
    """⚠ Without the build arg every container reports "dev" and the badge
    answers nothing — which is the one job it has."""
    import pathlib

    dockerfile = pathlib.Path("Dockerfile").read_text(encoding="utf-8")
    workflow = pathlib.Path(".github/workflows/publish.yml").read_text(encoding="utf-8")

    assert "ARG REVISION" in dockerfile
    assert "BOKYDOJO_REVISION=${REVISION}" in dockerfile
    assert "REVISION=${{ github.sha }}" in workflow
