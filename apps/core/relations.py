"""Writing a many-to-many whose target is tenant-scoped.

⚠ ``instance.styles.set([...])`` does not work on this codebase's models, and the
failure is loud rather than silent: Django's ``set()`` first *reads* the current
values through the target's default manager, which is a ``ScopedManager`` and
refuses to evaluate without an actor. ``form.save_m2m()`` hits the same wall,
which is why a plain ``ModelForm`` with an M2M field cannot simply be saved.

Going through the auto-created through model avoids it. The through model is a
plain Django model with no tenant scoping of its own, so the read is allowed —
and this is the right place to enforce what ``same_organization_fields`` cannot,
because that mechanism understands foreign keys and an M2M is neither side's
column.

⚠ **The cross-organisation check here is the only one there is.** Nothing at the
database level stops a row pairing this organisation's dojo with another's style;
scoping decides who may *read* a row, not what may be written. Every caller must
come through here.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


def set_scoped_m2m(instance, field_name: str, objects, *, organization_id) -> None:
    """Replace ``instance.<field_name>`` with ``objects``.

    Raises ``ValidationError`` if any object belongs to another organisation.
    """
    wanted = list(objects)
    for obj in wanted:
        owner = getattr(obj, "organization_id", None)
        if owner is not None and owner != organization_id:
            raise ValidationError(
                {field_name: _("'%(name)s' belongs to another organisation.") % {"name": obj}}
            )

    field = instance._meta.get_field(field_name)
    through = field.remote_field.through
    source = field.m2m_field_name()
    target = field.m2m_reverse_field_name()

    through.objects.filter(**{source: instance}).delete()
    if wanted:
        through.objects.bulk_create([through(**{source: instance, target: obj}) for obj in wanted])


def scoped_m2m_ids(instance, field_name: str) -> list:
    """The ids currently linked, read through the through model.

    Same reason as above: the descriptor's own queryset is scoped and refuses.
    """
    field = instance._meta.get_field(field_name)
    through = field.remote_field.through
    source = field.m2m_field_name()
    target = field.m2m_reverse_field_name()
    return list(through.objects.filter(**{source: instance}).values_list(f"{target}_id", flat=True))
