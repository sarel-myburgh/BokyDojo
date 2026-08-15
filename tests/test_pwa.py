from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse


def test_every_shell_asset_actually_resolves():
    """Every asset the shell names must be findable by the staticfiles finders.

    ⚠ This is the regression test for a bug that 981 other tests did not catch.
    No app in this project ships its own ``static/`` directory — everything lives
    in the project-level ``static/`` tree, which the finders only search if
    ``STATICFILES_DIRS`` names it. That setting was missing, so *every* asset
    404ed: both stylesheets, and all of the offline attendance JavaScript.

    Nothing failed loudly. Templates render fine without their CSS, and the test
    client never fetches a stylesheet, so the suite stayed green while the app
    was unstyled and the service worker's ``cache.addAll`` rejected on install —
    silently disabling offline attendance, the one feature that cannot degrade.

    Asserting on the paths the service worker declares keeps this honest: the
    shell list is the contract, so a new entry is covered the day it is added.
    """
    worker = Path(settings.BASE_DIR, "static/js/service-worker.js").read_text(encoding="utf-8")
    shell = re.findall(r'"(/static/[^"]+)"', worker)
    # Guard the guard: a refactor that renames the SHELL list must not turn this
    # test into a no-op that passes by finding nothing.
    assert len(shell) >= 8, f"expected the shell asset list, found {shell!r}"

    missing = [path for path in shell if finders.find(path.removeprefix("/static/")) is None]
    assert not missing, f"declared in the service worker shell but unservable: {missing}"


def test_stylesheets_referenced_by_the_base_template_resolve():
    """The two stylesheets are named in base.html, not in the worker's shell."""
    base = Path(settings.BASE_DIR, "templates/base.html").read_text(encoding="utf-8")
    referenced = re.findall(r"{% static '([^']+)' %}", base)

    assert "css/tailwind.css" in referenced
    missing = [path for path in referenced if finders.find(path) is None]
    assert not missing, f"referenced by base.html but unservable: {missing}"


def test_pwa_shell_is_public_and_installable(client):
    offline = client.get(reverse("offline"))
    worker = client.get(reverse("service-worker"))
    manifest_path = Path(settings.BASE_DIR, "static/manifest.webmanifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert offline.status_code == 200
    assert "You are offline" in offline.content.decode()
    assert worker.status_code == 200
    assert worker.headers["Service-Worker-Allowed"] == "/"
    assert (
        worker.headers["Cache-Control"] == "max-age=0, no-cache, no-store, must-revalidate, private"
    )
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/today/"
    assert manifest["scope"] == "/"
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}
    for icon in manifest["icons"]:
        assert Path(settings.BASE_DIR, icon["src"].removeprefix("/")).is_file()


def test_service_worker_does_not_cache_authenticated_html():
    """The worker may cache /static/, and nothing else.

    This used to assert ``"cache.put" not in source`` — a blanket ban standing in
    for the real rule. That also forbade revalidating static assets, which left
    an installed PWA pinned to the first CSS and JS it ever saw. The invariant
    that actually matters is *where* the writes happen, so assert that instead.
    """
    source = Path(settings.BASE_DIR, "static/js/service-worker.js").read_text(encoding="utf-8")
    static_branch, _, navigate_branch = source.partition('if (event.request.mode === "navigate")')

    assert 'caches.match("/offline/")' in source
    assert 'url.pathname.startsWith("/static/")' in source
    # Writes are confined to the static branch, which returns before this point.
    assert "cache.put" not in navigate_branch
    assert "caches.match(event.request)" not in navigate_branch
    # And the static branch really is guarded by the path check, not open to all.
    assert static_branch.index('url.pathname.startsWith("/static/")') < static_branch.index(
        "cache.put"
    )
    roster = Path(settings.BASE_DIR, "static/js/roster.js").read_text(encoding="utf-8")
    assert "scope: form.dataset.syncOwner" in roster
    assert "fetch(payload.endpoint" in roster


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_offline_queue_javascript():
    result = subprocess.run(
        ["node", "--test", "tests/js/attendance-sync.test.mjs"],
        cwd=settings.BASE_DIR,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
