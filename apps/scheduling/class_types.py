"""Which classes count toward what — TODO 1.4.10, plan §2 item 23.

The market research called per-class-type weighting the sharpest specific
complaint it found, and no existing product models it. The rule a dojo wants to
write is "40 classes since the last grading, of which at least 10 must be kata".
This module is the tagging half of that; the eligibility engine that reads the
counts is `3.6.2`.

⚠ **The vocabulary is per organisation, not an enum in this file.** kata, kihon
and kumite are karate words; a BJJ club needs different ones and a Judo club
different again. It lives in the settings hierarchy at organisation scope — see
``scheduling.class_type_tags`` in ``apps/core/setting_registry.py`` for why a
dojo may not override it.

⚠ **Tags are validated on write and never normalised.** A template tagged
``Kata`` against a rule naming ``kata`` matches nothing, silently, and surfaces
months later as a student wrongly held back from a grading. Coercing the case
would make them agree by luck while hiding that somebody has two names for one
thing, so a case variant is refused with a message naming the tag that exists.
"""

from __future__ import annotations

from uuid import UUID

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.core.setting_registry import CLASS_TYPE_TAGS
from apps.core.setting_resolver import ScopeChain, resolve


def vocabulary(organization_id: UUID) -> tuple[str, ...]:
    """The tags this organisation allows, in the order it declared them."""
    return tuple(resolve(CLASS_TYPE_TAGS.key, ScopeChain(organization_id=organization_id)))


def validate_tags(tags, *, organization_id: UUID) -> list[str]:
    """Check a ``counts_toward`` value against the organisation's vocabulary.

    Returns the cleaned list. Raises ``ValidationError`` keyed on the field, so
    it renders correctly in a form and in the admin.
    """
    if tags is None:
        return []
    if not isinstance(tags, list | tuple):
        raise ValidationError(
            {"counts_toward": _("Class types must be a list of tags.")},
        )

    allowed = vocabulary(organization_id)
    cleaned: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            raise ValidationError(
                {"counts_toward": _("Class type %(tag)r is not text.") % {"tag": tag}},
            )
        if tag in cleaned:
            raise ValidationError(
                {"counts_toward": _("Class type '%(tag)s' is listed twice.") % {"tag": tag}},
            )
        if tag not in allowed:
            # ⚠ Name the near-miss explicitly. "kata is not valid" when the org
            # has `kata` and the template says `Kata` reads as a system fault;
            # saying which one exists makes it a typo the author can fix.
            near = next((option for option in allowed if option.lower() == tag.lower()), None)
            if near is not None:
                raise ValidationError(
                    {
                        "counts_toward": _(
                            "Class type '%(tag)s' is not recognised — did you mean "
                            "'%(near)s'? Tags are case-sensitive."
                        )
                        % {"tag": tag, "near": near},
                    },
                )
            raise ValidationError(
                {
                    "counts_toward": _(
                        "Class type '%(tag)s' is not one of this organisation's "
                        "class types (%(allowed)s)."
                    )
                    % {"tag": tag, "allowed": ", ".join(allowed) or _("none configured")},
                },
            )
        cleaned.append(tag)
    return cleaned


def templates_counting_toward(templates, tag: str) -> list[UUID]:
    """Ids of the templates in ``templates`` that count toward ``tag``.

    ⚠ **Filtered in Python, deliberately.** The obvious query is
    ``filter(counts_toward__contains=[tag])``, and it is a trap: Django's
    ``contains`` lookup on a JSONField is **unsupported on SQLite**, which is
    what dev and the whole test suite run on, while production is PostgreSQL. It
    would have passed review, worked in production, and raised
    ``NotSupportedError`` the moment anybody ran it locally. ``icontains``
    against the serialised JSON is worse — a substring match, so ``kata`` finds
    ``kata_advanced``.

    Templates are inherently few: one per weekly slot per dojo, so tens, not
    thousands, even for a large organisation. Sessions are the numerous side, and
    they are filtered by ``template_id__in`` against this list — an indexed
    integer-set match. If a deployment ever grows enough templates for this to
    matter, the fix is a join table, not a JSON lookup.
    """
    return [
        template.pk
        for template in templates
        if isinstance(template.counts_toward, list) and tag in template.counts_toward
    ]


def sessions_counting_toward(sessions, templates, tag: str):
    """Narrow a ClassSession queryset to classes counting toward ``tag``.

    ``templates`` is the ClassTemplate queryset to consider — pass one already
    scoped with ``for_actor``/``for_organization``, because this helper applies
    no tenant filter of its own and must not be the place that forgets to.

    ⚠ **A one-off session counts toward nothing.** ``ClassSession.template`` is
    null for ad-hoc classes, and the tags live on the template, so a one-off kata
    seminar contributes to no eligibility rule. That is a real gap `3.6.2` will
    meet: either such sessions are excluded by design, or ClassSession needs its
    own override. It is recorded here rather than discovered there.
    """
    return sessions.filter(template_id__in=templates_counting_toward(templates, tag))
