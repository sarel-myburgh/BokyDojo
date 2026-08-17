"""Finding the student a row is about — TODO 1.10.4, 1.10.5.

Attendance and rank history both arrive as "this person, that fact", and both are
useless unless "this person" resolves to exactly one student who already exists.
Neither importer creates people: a rank award for somebody not on the roll is a
mistake in the file, not an instruction to invent a member.

Three ways to name a student, in descending order of reliability:

1. ``student_external_id`` — the id from the old system, matched through the
   ``ImportedRecord`` map that `1.10.3` wrote. This is why the student import runs
   first and why the map is a table rather than a column.
2. Name plus date of birth, matched through the same map's derived key.
3. Name alone, matched against the roll.

⚠ **Ambiguity is an error, never a guess.** Two students called Sokha Chan is an
ordinary thing in a dojo of two hundred. Picking either would attach a grading to
the wrong child, and nothing downstream would ever notice.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.core.scoping import Actor
from apps.identity.models import Person, StudentProfile

from .engine import _existing_object_id
from .students import STUDENT_ENTITY


class SubjectNotFound(ValidationError):
    """No student matched, or more than one did."""


def _by_import_key(*, organization_id, key: str):
    return _existing_object_id(
        organization_id=organization_id,
        entity_type=STUDENT_ENTITY,
        source_key=key,
    )


def resolve_student(row: dict[str, str], *, actor: Actor, organization_id) -> Person:
    """The one student this row is about, or raise.

    ``row`` has already been re-keyed to importer field names, so it may carry
    ``student_external_id``, ``given_name``, ``family_name`` and
    ``date_of_birth``.
    """
    external = (row.get("student_external_id") or "").strip()
    given = (row.get("given_name") or "").strip()
    family = (row.get("family_name") or "").strip()
    dob = (row.get("date_of_birth") or "").strip()

    object_id = None
    if external:
        object_id = _by_import_key(organization_id=organization_id, key=f"ext:{external}")
        if object_id is None:
            raise SubjectNotFound(
                {
                    "student": _(
                        "No student was imported with id '%(id)s'. Import the students "
                        "file first, or correct the id."
                    )
                    % {"id": external}
                }
            )
    elif given:
        # The same derived key the student importer wrote, so a roster imported
        # without ids still matches on a second file.
        key = f"nat:{given.casefold()}|{family.casefold()}|{dob}"
        object_id = _by_import_key(organization_id=organization_id, key=key)

    if object_id is not None:
        person = Person.objects.for_actor(actor).filter(pk=object_id).first()
        if person is not None:
            return person

    if not given:
        raise SubjectNotFound({"student": _("This row does not say which student it is about.")})

    # Last resort: the roll itself, for students who were never imported — added
    # by hand, or created before the importer existed.
    candidates = Person.objects.for_actor(actor).filter(given_name__iexact=given)
    if family:
        candidates = candidates.filter(family_name__iexact=family)
    if dob:
        from .students import parse_date

        candidates = candidates.filter(date_of_birth=parse_date(dob, field="date_of_birth"))
    candidates = list(candidates[:3])

    if not candidates:
        raise SubjectNotFound(
            {
                "student": _("No student here matches '%(name)s'.")
                % {"name": " ".join(part for part in (given, family) if part)}
            }
        )
    if len(candidates) > 1:
        # ⚠ Refused, not guessed. See the module docstring.
        raise SubjectNotFound(
            {
                "student": _(
                    "More than one student matches '%(name)s'. Add a date of birth "
                    "or a student id column so the rows are unambiguous."
                )
                % {"name": " ".join(part for part in (given, family) if part)}
            }
        )
    return candidates[0]


def resolve_profile(person: Person, *, actor: Actor) -> StudentProfile:
    profile = StudentProfile.objects.for_actor(actor).filter(person=person).first()
    if profile is None:
        raise SubjectNotFound(
            {"student": _("%(name)s is not a student.") % {"name": person.full_name}}
        )
    return profile
