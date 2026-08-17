"""Importing students and the adults attached to them — TODO 1.10.3, plan §12.10.

The first importer, and the one the others depend on: attendance (`1.10.4`) and
rank history (`1.10.5`) both reference students that must already exist.

⚠ **Identity is the whole problem.** Re-import has to update rather than
duplicate, which needs a stable key, and most spreadsheets have no id column. So:
if the operator maps ``external_id``, that is used and is reliable. Otherwise the
key is derived from name and date of birth — which is a *heuristic*, and it
collapses two real students who share both. That is flagged on every row it
affects rather than hidden, because silently merging two children is a worse
outcome than a confusing report.

⚠ **A guardian is a Person, reused across siblings.** The guardian columns on two
rows naming the same adult must produce one Person with two links, not two
Persons — that is what `1.1.4` established and what makes "message all parents"
send one message. Guardians therefore get their own key space.
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.core.scoping import Actor
from apps.identity.models import (
    Enrollment,
    GovernanceModel,
    GuardianLink,
    Person,
    StudentProfile,
)
from apps.identity.permissions import Action, require

from .engine import Importer, Outcome
from .models import ImportKind

STUDENT_ENTITY = "student"
GUARDIAN_ENTITY = "guardian"

#: Accepted date formats, most specific first. ⚠ ``%d/%m/%Y`` is listed and
#: ``%m/%d/%Y`` is **not**: 03/04/2015 is ambiguous, and guessing turns a March
#: birthday into an April one silently. Operators exporting US-formatted dates
#: must convert to ISO first, and the error message says so.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")

RELATIONSHIP_SYNONYMS = {
    "mother": GuardianLink.Relationship.MOTHER,
    "mum": GuardianLink.Relationship.MOTHER,
    "mom": GuardianLink.Relationship.MOTHER,
    "father": GuardianLink.Relationship.FATHER,
    "dad": GuardianLink.Relationship.FATHER,
    "guardian": GuardianLink.Relationship.GUARDIAN,
    "grandparent": GuardianLink.Relationship.GRANDPARENT,
    "grandmother": GuardianLink.Relationship.GRANDPARENT,
    "grandfather": GuardianLink.Relationship.GRANDPARENT,
    "sibling": GuardianLink.Relationship.SIBLING,
    "brother": GuardianLink.Relationship.SIBLING,
    "sister": GuardianLink.Relationship.SIBLING,
}

STATUS_SYNONYMS = {
    "prospect": StudentProfile.Status.PROSPECT,
    "trial": StudentProfile.Status.TRIAL,
    "active": StudentProfile.Status.ACTIVE,
    "on hold": StudentProfile.Status.ON_HOLD,
    "on_hold": StudentProfile.Status.ON_HOLD,
    "hold": StudentProfile.Status.ON_HOLD,
    "lapsed": StudentProfile.Status.LAPSED,
    "inactive": StudentProfile.Status.LAPSED,
    "alumni": StudentProfile.Status.ALUMNI,
}


def require_import_permission(actor: Actor, dojo) -> None:
    """Importing is bulk person creation, and is gated as such.

    ⚠ Both create *and* edit: a re-import updates existing records, so an actor
    who may only create could otherwise overwrite people through the importer
    that they could not touch through the student screens.
    """
    governance = dojo.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.PERSON_CREATE, dojo, governance_model=governance)
    require(actor, Action.PERSON_EDIT, dojo, governance_model=governance)


def parse_date(value: str, *, field: str):
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValidationError(
        {
            field: _(
                "'%(value)s' is not a date this importer recognises. Use YYYY-MM-DD. "
                "Month/day/year is not accepted because it cannot be told apart "
                "from day/month/year."
            )
            % {"value": value}
        }
    )


def parse_relationship(value: str) -> str:
    if not value:
        return GuardianLink.Relationship.GUARDIAN
    resolved = RELATIONSHIP_SYNONYMS.get(value.strip().lower())
    if resolved is None:
        # Unknown relationships become "other" rather than failing the row: the
        # link matters more than its label, and the label is easy to correct.
        return GuardianLink.Relationship.OTHER
    return resolved


def parse_status(value: str) -> str:
    if not value:
        return StudentProfile.Status.ACTIVE
    resolved = STATUS_SYNONYMS.get(value.strip().lower())
    if resolved is None:
        raise ValidationError(
            {
                "status": _("'%(value)s' is not a student status. Use one of: %(allowed)s.")
                % {"value": value, "allowed": ", ".join(sorted(set(STATUS_SYNONYMS)))}
            }
        )
    return resolved


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "y", "yes", "true", "t"}


class StudentImporter(Importer):
    entity_type = STUDENT_ENTITY
    kind = ImportKind.STUDENTS

    fields = {
        "external_id": False,
        "given_name": True,
        "family_name": False,
        "preferred_name": False,
        "date_of_birth": False,
        "email": False,
        "phone": False,
        "address_line1": False,
        "city": False,
        "country": False,
        "status": False,
        "joined_on": False,
        "guardian_given_name": False,
        "guardian_family_name": False,
        "guardian_email": False,
        "guardian_phone": False,
        "guardian_relationship": False,
        "guardian_is_primary": False,
    }

    #: Set when the most recent row fell back to the name/DOB heuristic, so the
    #: report can say so per row.
    def natural_key(self, row: dict[str, str]) -> str:
        external = (row.get("external_id") or "").strip()
        if external:
            return f"ext:{external}"

        given = (row.get("given_name") or "").strip().casefold()
        family = (row.get("family_name") or "").strip().casefold()
        dob = (row.get("date_of_birth") or "").strip()
        if not given:
            return ""
        # ⚠ casefold, not lower: it folds Turkish dotted I and German ß, which
        # .lower() does not, so "STRASSE" and "straße" do not become two students.
        return f"nat:{given}|{family}|{dob}"

    def uses_heuristic_key(self, row: dict[str, str]) -> bool:
        return not (row.get("external_id") or "").strip()

    def apply(self, row, *, existing_id, actor: Actor, dojo):
        given = (row.get("given_name") or "").strip()
        if not given:
            raise ValidationError({"given_name": _("A given name is required.")})

        date_of_birth = parse_date(row.get("date_of_birth", ""), field="date_of_birth")
        joined_on = parse_date(row.get("joined_on", ""), field="joined_on")
        status = parse_status(row.get("status", ""))

        person_fields = {
            "given_name": given,
            "family_name": (row.get("family_name") or "").strip(),
            "preferred_name": (row.get("preferred_name") or "").strip(),
            "date_of_birth": date_of_birth,
            "email": (row.get("email") or "").strip(),
            "phone": (row.get("phone") or "").strip(),
            "address_line1": (row.get("address_line1") or "").strip(),
            "city": (row.get("city") or "").strip(),
            "country": (row.get("country") or "").strip()[:2],
        }

        person = None
        if existing_id is not None:
            person = Person.objects.for_actor(actor).filter(pk=existing_id).first()

        if person is None:
            person = Person(organization_id=dojo.organization_id, **person_fields)
            person.save()
            outcome = Outcome.CREATED
        else:
            # ⚠ Blank cells do not erase. A re-import of a partial file — say a
            # corrected phone-number column — must not wipe every address it did
            # not carry. Only non-empty values overwrite.
            for field, value in person_fields.items():
                if value not in ("", None):
                    setattr(person, field, value)
            person.save()
            outcome = Outcome.UPDATED

        profile, _created = StudentProfile.objects.for_actor(actor).get_or_create(
            person=person,
            defaults={"status": status, "home_dojo": dojo, "joined_on": joined_on},
        )
        changed = []
        if profile.home_dojo_id is None:
            profile.home_dojo = dojo
            changed.append("home_dojo")
        if row.get("status"):
            profile.status = status
            changed.append("status")
        if joined_on and profile.joined_on is None:
            profile.joined_on = joined_on
            changed.append("joined_on")
        if changed:
            profile.save(update_fields=[*changed, "updated_at"])

        Enrollment.objects.for_actor(actor).get_or_create(
            student=person,
            dojo=dojo,
            defaults={
                "started_on": joined_on or datetime.date.today(),
                "is_primary": True,
            },
        )

        self._link_guardian(row, student=person, actor=actor, dojo=dojo)
        return person, outcome

    def _link_guardian(self, row, *, student, actor: Actor, dojo) -> None:
        given = (row.get("guardian_given_name") or "").strip()
        family = (row.get("guardian_family_name") or "").strip()
        email = (row.get("guardian_email") or "").strip()
        phone = (row.get("guardian_phone") or "").strip()
        if not given and not email:
            return

        # Guardian identity, in order of reliability: an email address is a real
        # identifier, a name is not. ⚠ Without this two siblings on consecutive
        # rows produce two copies of the same parent.
        key = (
            f"email:{email.casefold()}" if email else f"name:{given.casefold()}|{family.casefold()}"
        )

        from .engine import _existing_object_id, _remember

        organization_id = dojo.organization_id
        existing_id = _existing_object_id(
            organization_id=organization_id,
            entity_type=GUARDIAN_ENTITY,
            source_key=key,
        )
        guardian = None
        if existing_id is not None:
            guardian = Person.objects.for_actor(actor).filter(pk=existing_id).first()

        if guardian is None:
            guardian = Person(
                organization_id=organization_id,
                given_name=given or email,
                family_name=family,
                email=email,
                phone=phone,
            )
            guardian.save()
        else:
            if email:
                guardian.email = email
            if phone:
                guardian.phone = phone
            guardian.save()

        _remember(
            organization_id=organization_id,
            entity_type=GUARDIAN_ENTITY,
            source_key=key,
            object_id=guardian.pk,
        )

        GuardianLink.objects.for_actor(actor).get_or_create(
            guardian=guardian,
            student=student,
            defaults={
                "relationship": parse_relationship(row.get("guardian_relationship", "")),
                "is_primary_contact": parse_bool(row.get("guardian_is_primary", "")),
                "is_emergency_contact": True,
                "has_custody": True,
            },
        )
