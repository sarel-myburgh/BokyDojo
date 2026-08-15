"""Strict CSP and self-hosted asset regression tests — TODO 0.6.7."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)", re.IGNORECASE)
INLINE_STYLE = re.compile(r"<style\b|\sstyle=", re.IGNORECASE)
EVENT_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


@pytest.mark.django_db
def test_csp_is_strict_and_uses_a_fresh_nonce(client):
    first = client.get(reverse("login"))
    second = client.get(reverse("login"))

    first_policy = first.headers["Content-Security-Policy"]
    second_policy = second.headers["Content-Security-Policy"]
    first_nonce = re.search(r"script-src 'self' 'nonce-([^']+)'", first_policy).group(1)
    second_nonce = re.search(r"script-src 'self' 'nonce-([^']+)'", second_policy).group(1)

    assert "'unsafe-inline'" not in first_policy
    assert "'unsafe-eval'" not in first_policy
    assert "object-src 'none'" in first_policy
    assert "frame-ancestors 'none'" in first_policy
    assert first_nonce != second_nonce
    assert first.context["csp_nonce"] == first_nonce


@pytest.mark.django_db
def test_json_health_response_also_gets_security_policy(client):
    response = client.get(reverse("healthz"))
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")


def test_project_templates_have_no_inline_executable_content():
    violations = []
    for template in Path(settings.BASE_DIR, "templates").rglob("*.html"):
        source = template.read_text(encoding="utf-8")
        if (
            INLINE_SCRIPT.search(source)
            or INLINE_STYLE.search(source)
            or EVENT_HANDLER.search(source)
        ):
            violations.append(str(template.relative_to(settings.BASE_DIR)))

    assert violations == []


@pytest.mark.django_db
def test_roster_assets_are_self_hosted(client):
    response = client.get(reverse("login"))
    body = response.content.decode()

    assert "cdn.tailwindcss.com" not in body
    assert "unpkg.com" not in body
    assert "cdn.jsdelivr.net" not in body
    assert "/static/css/tailwind.css" in body


def test_compiled_css_and_external_roster_script_exist():
    css = Path(settings.BASE_DIR, "static/css/tailwind.css").read_text(encoding="utf-8")
    script = Path(settings.BASE_DIR, "static/js/roster.js").read_text(encoding="utf-8")

    assert ".bg-gray-900" in css
    assert "data-mark-all-status" in script
    assert "addEventListener" in script
