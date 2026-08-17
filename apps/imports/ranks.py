"""Importing a grading history — TODO 1.10.5, plan §12.10.

⚠ **This calls ``apps.ranks.promotions.promote_student``.** Rank awards are
append-only evidence (`1.2.6`); the service owns the row lock, the forward-only
chronology, the derived current rank, the examiner ceiling and the strict audit.
An importer that wrote ``RankAward`` rows directly would be a second, quieter set
of rules for the most consequential record the system keeps.

Three consequences fall out of reusing it, and all three are deliberate:

⚠ **Rows are sorted into chronological order per student before being applied.**
The service refuses an award that predates the latest active one, so a file
listing "1st kyu, then 2nd kyu" — perfectly normal, most systems export newest
first — would reject every row after the first. Sorting is done by
``prepare()``; the report still numbers rows by their line in the operator's
file, because ``SourceRow`` carries that separately.

⚠ **An examiner ceiling constrains what can be imported.** If the importing
person has an ``InstructorProfile`` with ``max_grading_rank`` set, they cannot
import a history above it. That is the control working, not a bug: someone who
may not award a 3rd dan should not be able to award one by uploading it. An
organisation admin with no instructor profile has no ceiling and can import a
full history, which is the ordinary case for a migration.

⚠ **A missing style track is created, not refused.** The track is bookkeeping —
"this student studies this style on this ladder" — and requiring it to exist
first would mean hand-creating a row per student before any history could load.
Its ``started_on`` is the earliest award in the file, because the service refuses
an award that predates the track.
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.core.scoping import Actor
from apps.identity.models import GovernanceModel
from apps.identity.permissions import Action, require
from apps.ranks.models import Rank, RankLadder, StudentStyleTrack, Style
from apps.ranks.promotions import promote_student

from .engine import Importer, Outcome
from .models import ImportKind
from .students import parse_date
from .subjects import resolve_profile, resolve_student

RANK_ENTITY = "rank_award"


def require_rank_import_permission(actor: Actor, dojo) -> None:
    governance = dojo.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.RANK_AWARD, dojo, governance_model=governance)


def resolve_style(name: str, *, organization_id) -> Style:
    styles = Style.objects.for_organization(organization_id)
    if name:
        style = styles.filter(name__iexact=name.strip()).first()
        if style is None:
            available = ", ".join(styles.values_list("name", flat=True)) or str(_("none"))
            raise ValidationError(
                {
                    "style": _("No style called '%(name)s'. This organisation has: %(available)s.")
                    % {"name": name, "available": available}
                }
            )
        return style

    only = list(styles[:2])
    if len(only) == 1:
        return only[0]
    # ⚠ Not guessed. A club teaching karate and judo needs the column; picking
    # one would file a judo grading on the karate ladder.
    raise ValidationError(
        {"style": _("This organisation teaches more than one style. Add a style column.")}
    )


def resolve_rank(name: str, *, style: Style, organization_id, track=None) -> Rank:
    if not name:
        raise ValidationError({"rank": _("A rank is required.")})

    ladders = RankLadder.objects.for_organization(organization_id).filter(style=style)
    if track is not None:
        ladders = ladders.filter(pk=track.ladder_id)

    matches = list(
        Rank.objects.for_organization(organization_id)
        .filter(ladder__in=ladders, name__iexact=name.strip())
        .select_related("ladder")[:3]
    )
    if not matches:
        raise ValidationError(
            {
                "rank": _("No rank called '%(name)s' in %(style)s.")
                % {"name": name, "style": style.name}
            }
        )
    if len(matches) > 1:
        # e.g. an adult and a junior ladder both containing "9th Kyu".
        available = ", ".join(sorted({m.ladder.name for m in matches}))
        raise ValidationError(
            {
                "rank": _(
                    "'%(name)s' exists on more than one ladder (%(ladders)s). The "
                    "student's existing track decides which; give them one first, or "
                    "import the ladders separately."
                )
                % {"name": name, "ladders": available}
            }
        )
    return matches[0]


class RankImporter(Importer):
    entity_type = RANK_ENTITY
    kind = ImportKind.RANKS

    fields = {
        "student_external_id": False,
        "given_name": False,
        "family_name": False,
        "date_of_birth": False,
        "style": False,
        "rank": True,
        "awarded_on": True,
        "certificate_number": False,
        "notes": False,
    }

    def prepare(self, rows, mapping):
        """Oldest award first, per student — see the module docstring.

        ⚠ Stable within a date, and the row's own line number is preserved on
        ``SourceRow``, so reordering here does not misreport anything: the engine
        sorts outcomes back into file order before storing them.
        """

        from .engine import apply_mapping

        def key(source_row):
            # ⚠ Mapped first. The raw row is keyed by the file's own column
            # names, so reading "awarded_on" off it matches nothing and the sort
            # silently becomes a no-op.
            values = apply_mapping(source_row.values, mapping)
            who = (values.get("student_external_id") or "").strip().casefold() or "|".join(
                (
                    (values.get("given_name") or "").strip().casefold(),
                    (values.get("family_name") or "").strip().casefold(),
                )
            )
            # Unparseable dates sort last so they fail on their own merits rather
            # than blocking the valid rows for that student.
            raw = (values.get("awarded_on") or "").strip()
            try:
                parsed = parse_date(raw, field="awarded_on")
            except ValidationError:
                parsed = None
            # A date sentinel rather than "", so a file with some unparseable
            # dates does not raise on comparing a date with a string.
            return (who, parsed is None, parsed or datetime.date.min, source_row.line_number)

        return sorted(rows, key=key)

    def natural_key(self, row: dict[str, str]) -> str:
        who = (row.get("student_external_id") or "").strip() or "|".join(
            (
                (row.get("given_name") or "").strip().casefold(),
                (row.get("family_name") or "").strip().casefold(),
                (row.get("date_of_birth") or "").strip(),
            )
        )
        rank = (row.get("rank") or "").strip().casefold()
        awarded = (row.get("awarded_on") or "").strip()
        if not who.strip("|") or not rank or not awarded:
            return ""
        return f"{who}#{rank}@{awarded}"

    def apply(self, row, *, existing_id, actor: Actor, dojo):
        organization_id = dojo.organization_id
        awarded_on = parse_date(row.get("awarded_on", ""), field="awarded_on")
        if awarded_on is None:
            raise ValidationError({"awarded_on": _("An award date is required.")})

        student = resolve_student(row, actor=actor, organization_id=organization_id)
        profile = resolve_profile(student, actor=actor)
        style = resolve_style(row.get("style", ""), organization_id=organization_id)

        track = (
            StudentStyleTrack.objects.for_actor(actor)
            .filter(student=student, style=style)
            .select_related("ladder", "current_rank")
            .first()
        )
        rank = resolve_rank(
            row.get("rank", ""),
            style=style,
            organization_id=organization_id,
            track=track,
        )

        if track is None:
            track = StudentStyleTrack(
                student=student,
                style=style,
                ladder=rank.ladder,
                # The service refuses an award before the track began, and this
                # is the earliest thing we know about.
                started_on=awarded_on,
            )
            track.save()
        elif awarded_on < track.started_on:
            # An earlier grading than the track claims: widen it rather than
            # refusing history that plainly happened.
            track.started_on = awarded_on
            track.save(update_fields=["started_on", "updated_at"])

        if existing_id is not None:
            # ⚠ Rank awards are append-only, so a re-import cannot rewrite one.
            # Reporting it as skipped is the honest answer — the row is already
            # recorded and nothing needs doing.
            return None, Outcome.SKIPPED

        award = promote_student(
            profile=profile,
            track=track,
            rank=rank,
            awarded_on=awarded_on,
            actor=actor,
            certificate_number=(row.get("certificate_number") or "").strip()[:100],
            notes=(row.get("notes") or "").strip()[:255],
        )
        return award, Outcome.CREATED
