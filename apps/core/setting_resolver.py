"""Setting resolution down the scope hierarchy — TODO 0.3.7, plan §13.2.

One resolver, consulted everywhere. Reimplementing "check the class, then the
dojo, then the org" at a call site is how the kiosk ends up disagreeing with the
admin screen about whether a student needs a PIN.

Usage::

    chain = ScopeChain(
        organization_id=org.pk,
        dojo_id=dojo.pk,
        class_template_id=template.pk,
        student_id=student.pk,
    )
    mode = resolve("attendance.kiosk_display_mode", chain)

Resolution is a single query per chain, so callers may resolve several keys at
once with ``resolve_many`` rather than issuing one query per setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .setting_registry import (
    Resolution,
    Scope,
    SettingDefinition,
    get_definition,
)


@dataclass(frozen=True)
class ScopeChain:
    """The subject a setting is being resolved for.

    Only ``organization_id`` is required. Supply as much of the rest as the
    calling context knows; missing levels are simply skipped.
    """

    organization_id: UUID
    dojo_id: UUID | None = None
    class_template_id: UUID | None = None
    class_session_id: UUID | None = None
    student_id: UUID | None = None

    def levels(self) -> list[tuple[str, UUID | None]]:
        """Least specific → most specific, omitting levels not supplied."""
        candidates = [
            (Scope.ORG, None),
            (Scope.DOJO, self.dojo_id),
            (Scope.CLASS_TEMPLATE, self.class_template_id),
            (Scope.CLASS_SESSION, self.class_session_id),
            (Scope.STUDENT, self.student_id),
        ]
        return [
            (scope, scope_id)
            for scope, scope_id in candidates
            if scope == Scope.ORG or scope_id is not None
        ]


def _matching_rows(keys: list[str], chain: ScopeChain):
    from django.db.models import Q

    from .models import Setting

    predicate = Q()
    for scope_type, scope_id in chain.levels():
        if scope_type == Scope.ORG:
            predicate |= Q(scope_type=Scope.ORG, scope_id__isnull=True)
        else:
            predicate |= Q(scope_type=scope_type, scope_id=scope_id)

    # Settings are read on behalf of the subject, not a viewer — the kiosk
    # resolving a student's PIN policy has no logged-in actor. for_organization()
    # is the sanctioned actorless entry point: it still applies the tenant filter.
    return list(
        Setting.objects.for_organization(chain.organization_id)
        .filter(key__in=keys)
        .filter(predicate)
        .values("key", "scope_type", "value")
    )


def _reduce(definition: SettingDefinition, rows: list[dict]) -> Any:
    if not rows:
        return definition.default

    if definition.resolution == Resolution.STRICTEST:
        # Every level competes on strictness, including the default. The most
        # restrictive value anywhere in the chain wins.
        candidates = [definition.default, *(row["value"] for row in rows)]
        return max(candidates, key=definition.strictness_of)

    # MOST_SPECIFIC: the value set closest to the subject.
    return max(rows, key=lambda row: Scope.rank(row["scope_type"]))["value"]


def resolve_many(keys: list[str], chain: ScopeChain) -> dict[str, Any]:
    """Resolve several settings in one query."""
    definitions = {key: get_definition(key) for key in keys}
    rows = _matching_rows(keys, chain)

    grouped: dict[str, list[dict]] = {key: [] for key in keys}
    for row in rows:
        grouped[row["key"]].append(row)

    return {key: _reduce(definitions[key], grouped[key]) for key in keys}


def resolve(key: str, chain: ScopeChain) -> Any:
    """Resolve one setting for this subject."""
    return resolve_many([key], chain)[key]


def set_value(
    key: str,
    value: Any,
    *,
    organization_id: UUID,
    scope_type: str,
    scope_id: UUID | None = None,
):
    """Write an override, validating the key, the value and the scope."""
    from .models import Setting

    definition = get_definition(key)
    definition.validate(value, scope_type)

    if scope_type == Scope.ORG and scope_id is not None:
        raise ValueError("Organisation-scoped settings must not carry a scope_id")
    if scope_type != Scope.ORG and scope_id is None:
        raise ValueError(f"{scope_type} settings require a scope_id")

    obj, _created = Setting.objects.for_organization(organization_id).update_or_create(
        organization_id=organization_id,
        scope_type=scope_type,
        scope_id=scope_id,
        key=key,
        defaults={"value": value},
    )
    return obj


def clear_value(
    key: str,
    *,
    organization_id: UUID,
    scope_type: str,
    scope_id: UUID | None = None,
) -> int:
    """Remove an override so the level inherits again."""
    from .models import Setting

    get_definition(key)
    deleted, _ = (
        Setting.objects.for_organization(organization_id)
        .filter(scope_type=scope_type, scope_id=scope_id, key=key)
        .delete()
    )
    return deleted
