"""The deployment files are code too — TODO 0.2.3, SEC §2.7.

The application suite cannot reach these: `docker-compose.yml` and the `Caddyfile`
decide what the world can fetch, and nothing else in this repository asserts
anything about them. Both bugs guarded here were live and neither was visible
from Python.

⚠ These are text assertions over config, which is a weak form of test. They exist
because the alternative was no test at all, and because both failures are silent:
one ships an unstyled app with a dead service worker, the other publishes
children's photographs.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CADDYFILE = ROOT / "Caddyfile"
COMPOSE = ROOT / "docker-compose.yml"
WORKFLOW = ROOT / ".github/workflows/ci.yml"
DOCKERIGNORE = ROOT / ".dockerignore"


@pytest.fixture(scope="module")
def caddyfile() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _caddy_service(compose: str) -> str:
    """The caddy service block, up to the next top-level key."""
    match = re.search(r"\n  caddy:\n(.*?)(?=\n  \w|\nvolumes:)", compose, re.S)
    assert match, "no caddy service in docker-compose.yml"
    return match.group(1)


def test_caddy_can_reach_the_static_files_it_serves(compose, caddyfile):
    """⚠ The bug: Caddy rooted /static/* at /app/staticfiles and never mounted it.

    Django does not serve static with DEBUG=False and there is no WhiteNoise, so
    every stylesheet and every script 404s in production — and the service
    worker's cache.addAll rejects on install, silently killing the 1.6.x offline
    queue. The same failure is already recorded once in TODO.md at the Django
    layer; this is its deployment twin.
    """
    if "handle /static/*" not in caddyfile:
        pytest.skip("Caddy no longer serves static directly")

    service = _caddy_service(compose)
    assert "static:/app/staticfiles" in service, (
        "Caddy serves /static/* from /app/staticfiles but does not mount it — "
        "every asset will 404 in production"
    )


def test_caddy_does_not_publish_the_media_directory(caddyfile):
    """⚠ MEDIA_ROOT is consent documents, medical attachments and photographs of
    children. Django serves every one of them through permission-checked, audited
    views that also enforce current consent. A file_server over that directory
    bypasses all of it, permanently, for anyone who ever learns a UUID — and
    makes 1.1.14's "revocation immediately blocks direct document reads" untrue.
    """
    media_handler = re.search(r"handle\s+/media/\*\s*\{(.*?)\}", caddyfile, re.S)
    assert media_handler is None, (
        "Caddyfile serves /media/* directly; personal data must go through "
        "Django's permission-checked document views"
    )


def test_the_media_volume_is_not_mounted_into_the_edge_proxy(compose):
    """Belt and braces: even with no handler today, mounting the volume invites
    somebody to add one back while debugging a 404."""
    service = _caddy_service(compose)
    assert not re.search(r"^\s*-\s*media:", service, re.M), (
        "the caddy service mounts the media volume; it has no business reading "
        "uploaded personal data"
    )


def test_the_application_still_owns_media_because_the_worker_and_web_need_it(compose):
    """The volume itself is legitimate — this pins *who* may see it."""
    assert compose.count("media:/app/media") >= 2, (
        "web and worker both need MEDIA_ROOT; if this changed, check nothing "
        "silently lost access to uploaded documents"
    )


# -- CI ------------------------------------------------------------------------


def test_the_ci_workflow_is_valid_yaml():
    """⚠ The check that would have saved eight failed runs.

    The workflow embedded a Python one-liner as an unquoted multi-line YAML
    scalar. ``if results:`` made the parser try to read a mapping, the file was
    invalid, and every run from the moment the repository gained a remote failed
    in 0 seconds without executing a single step. Nothing in the repository
    noticed — the suite was green throughout, because the suite never looked at
    the file that decides whether the suite runs at all.
    """
    import yaml

    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    assert isinstance(parsed, dict), "the workflow must parse to a mapping"
    assert parsed.get("jobs"), "the workflow declares no jobs"
    for name, job in parsed["jobs"].items():
        assert job.get("steps"), f"job {name!r} has no steps"


def test_no_ci_step_tests_the_exit_status_of_a_pipe_into_head():
    """⚠ The other bug, and it could never pass.

    ``if find ... | head -1; then`` tests the *pipeline's* status, which is
    head's, and head returns 0 on empty input. The condition was always true, so
    the step always failed — while finding nothing. A check that cannot pass is
    worse than no check: it trains people to ignore the failure.
    """
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "| head -1; then" not in source
    assert "| head; then" not in source


def test_the_docker_build_context_excludes_secrets_and_local_state():
    """⚠ The Dockerfile does `COPY . .`.

    Without a .dockerignore that copies .env — DJANGO_SECRET_KEY, the database
    password, and the field-encryption keys protecting medical and safeguarding
    data — into the image, along with a 7 MB dev database. A fresh CI checkout
    has no .env, so CI would never catch it; it bites whoever builds locally.
    """
    assert DOCKERIGNORE.exists(), "the Dockerfile COPYs everything and needs a .dockerignore"
    patterns = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for required in (".env", "*.sqlite3", ".venv/", "node_modules/", ".git/"):
        assert required in patterns, f".dockerignore must exclude {required}"
