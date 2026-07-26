"""Structural invariants of the tenancy layer — TODO 0.3.3 / 0.3.4, SEC 2.2.

Complements tests/test_unscoped_guard.py, which scans for escape-hatch usage.
This file asserts the contract every tenant-scoped model must satisfy, so a new
model cannot silently join the schema without declaring how it is scoped.
"""

from __future__ import annotations

import re

import pytest
from django.apps import apps as django_apps

from apps.core.models import TenantScopedModel
from apps.core.scoping import Actor

from .test_unscoped_guard import ALLOWED_FILES, ROOT

PATTERN = re.compile(r"allow_unscoped\b|\.unscoped\(")


def _concrete_tenant_models():
    return [
        model
        for model in django_apps.get_models()
        if issubclass(model, TenantScopedModel) and not model._meta.abstract
    ]


def test_there_are_tenant_models_to_check():
    """Guards against this suite silently passing because it found nothing."""
    assert _concrete_tenant_models(), "No TenantScopedModel subclasses discovered"


@pytest.mark.parametrize(
    "model", _concrete_tenant_models(), ids=lambda m: m.__name__
)
def test_tenant_model_declares_org_path(model):
    """Every tenant model must state how to reach its owning organisation."""
    path = getattr(model, "tenant_org_path", None)
    assert path, f"{model.__name__} does not declare tenant_org_path"


@pytest.mark.parametrize(
    "model", _concrete_tenant_models(), ids=lambda m: m.__name__
)
def test_tenant_scope_q_is_usable(model):
    """tenant_scope_q() must build a filter the ORM accepts for this model."""
    actor = Actor(
        user_id=None,
        person_id=None,
        organization_id="00000000-0000-7000-8000-000000000001",
    )
    q = model.tenant_scope_q(actor)
    # Raises FieldError if a declared path does not resolve.
    str(model.objects.unscoped("contract test").filter(q).query)


@pytest.mark.parametrize(
    "model", _concrete_tenant_models(), ids=lambda m: m.__name__
)
def test_tenant_model_default_manager_is_scoped(model):
    """A tenant model whose default manager is not scoped is a leak waiting to happen."""
    manager = model._default_manager
    assert hasattr(manager, "for_actor"), (
        f"{model.__name__}._default_manager has no for_actor(); it is not a "
        f"ScopedManager and will not enforce tenancy."
    )


def test_unscoped_allowlist_has_no_stale_entries():
    """Remove an allow-list entry once the file no longer needs the escape hatch.

    A stale entry is a standing permission nobody is watching.
    """
    stale = []
    for relative in ALLOWED_FILES:
        path = ROOT / relative
        if not path.exists():
            stale.append(f"{relative} (file no longer exists)")
            continue
        source = path.read_text(encoding="utf-8")
        code_lines = [
            line
            for line in source.splitlines()
            if not line.lstrip().startswith("#")
        ]
        if not PATTERN.search("\n".join(code_lines)):
            stale.append(f"{relative} (no longer uses unscoped access)")

    assert not stale, (
        "Stale entries in ALLOWED_FILES (tests/test_unscoped_guard.py): "
        + ", ".join(stale)
    )
