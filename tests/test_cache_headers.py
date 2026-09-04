"""Nothing signed-in may sit in a browser cache — plan §3, SEC §2.

⚠ Reported as "I updated the container and the browser still shows the old
version until I clear history". The staleness was the visible half; the other
half is that pages full of personal data were cacheable by the browser and by
anything between it and the server, with nothing saying otherwise.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.scoping import allow_unscoped
from apps.core.version import build_revision
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
    with allow_unscoped("cache header test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        person = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        return User.objects.create_user("mei@example.com", PASSWORD, person=person)


def test_a_signed_in_page_is_never_stored(client, user):
    """⚠ The reported bug. Without this the response carried only Vary, and a
    browser invents a freshness lifetime for a response that states none."""
    client.force_login(user)

    response = client.get(reverse("today"))

    assert "no-store" in response["Cache-Control"]


def test_every_signed_in_page_is_covered_not_just_the_one_i_thought_of(client, user):
    """⚠ Enumerated rather than spot-checked: the bug was a *missing default*,
    so testing one page would prove nothing about the rest."""
    client.force_login(user)

    for name in (
        "today",
        "calendar",
        "student-list",
        "student-qr-cards",
        "org-settings",
        "account",
        "help",
    ):
        response = client.get(reverse(name))
        assert response.status_code == 200, name
        assert "no-store" in response.get("Cache-Control", ""), name


def test_a_person_page_full_of_personal_details_is_not_stored(client, user):
    client.force_login(user)

    response = client.get(reverse("person-detail", args=[user.person_id]))

    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]


def test_the_login_page_keeps_its_own_stricter_header(client):
    """⚠ Views that set their own must win. never_cache already says no-store
    here, and a blanket overwrite would hide those decisions."""
    response = client.get(reverse("login"))

    assert "no-store" in response["Cache-Control"]


def test_a_view_that_sets_its_own_cache_control_is_left_alone(client, user):
    """The document and photograph endpoints send their own deliberately."""
    from django.http import HttpResponse

    from apps.core.cache_headers import NoStoreMiddleware

    def view(request):
        response = HttpResponse("x")
        response["Cache-Control"] = "public, max-age=31536000"
        return response

    middleware = NoStoreMiddleware(view)

    class FakeRequest:
        path = "/anything/"

    assert middleware(FakeRequest())["Cache-Control"] == "public, max-age=31536000"


def test_static_assets_are_left_for_the_web_server_to_handle():
    """⚠ Django is not what serves /static/ in production — Caddy is — and the
    header belongs where the file is served from."""
    from django.http import HttpResponse

    from apps.core.cache_headers import NoStoreMiddleware

    middleware = NoStoreMiddleware(lambda request: HttpResponse("x"))

    class FakeRequest:
        path = "/static/css/tailwind.css"

    assert not middleware(FakeRequest()).has_header("Cache-Control")


# -- the service worker -------------------------------------------------------


def test_the_service_worker_names_its_cache_after_the_build(client):
    """⚠ This is what makes a deploy reach an installed PWA.

    A new revision means a new cache name, so the worker's activate step deletes
    the old one and refetches. A hard-coded name only changes when somebody
    remembers to change it, and nobody remembers.
    """
    body = client.get(reverse("service-worker")).content.decode()

    assert f"bokydojo-shell-{build_revision()}" in body
    assert "__BUILD__" not in body, "the placeholder was left unsubstituted"


def test_the_service_worker_itself_is_never_cached(client):
    """If the worker were cached, nothing downstream of it could ever update."""
    response = client.get(reverse("service-worker"))

    assert "no-cache" in response["Cache-Control"]


def test_the_service_worker_still_serves_pages_from_the_network_first(client):
    """⚠ HTML must never come out of the shell cache. Belt and braces beside the
    no-store header: one is the browser's rule, this is the worker's."""
    body = client.get(reverse("service-worker")).content.decode()

    navigate = body[body.index('mode === "navigate"') :]
    assert "fetch(event.request)" in navigate
    assert navigate.index("fetch(event.request)") < navigate.index("caches.match")


def test_the_caddy_config_revalidates_static_files():
    """⚠ file_server sends only ETag and Last-Modified; a browser with no
    explicit freshness invents a lifetime and serves the old asset for it."""
    import pathlib

    caddyfile = pathlib.Path("Caddyfile").read_text(encoding="utf-8")
    static_block = caddyfile[caddyfile.index("handle /static/*") :]
    static_block = static_block[: static_block.index("}")]

    assert 'header Cache-Control "no-cache"' in static_block
