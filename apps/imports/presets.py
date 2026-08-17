"""Named import presets — TODO 1.10.6, plan §12.10.

A preset is more than a saved mapping. Two systems can both export a column
called ``Status`` and mean different things by it, and both can export dates in
formats that cannot be told apart. So a preset carries the mapping, the date
format to trust, and any vocabulary the source uses, and it is selected by
*fingerprinting* the header row rather than by asking the operator to know which
product their predecessor used.

⚠ **Honesty about what these are.** The plan asks for presets for "the main
competitors' export formats". The ones below are built from column names that are
either documented publicly or follow directly from the field's ordinary name —
they are **not** derived from a real export file, because none was available. So:

* Every built-in is marked ``verified = False``.
* The wizard labels an unverified preset as a starting point and shows the
  mapping for confirmation exactly as it would an ordinary guess.
* ``1.10.6`` should not be considered finished for a given product until somebody
  has run a genuine export from it through this and set ``verified = True``.

Shipping a preset labelled "Gymdesk" that was guessed at, and letting an operator
trust it, would be worse than shipping no preset at all: the whole value of a
preset is that it is supposed to be known-good.
"""

from __future__ import annotations

import dataclasses

from .guessing import normalise


@dataclasses.dataclass(frozen=True)
class Preset:
    key: str
    label: str
    kind: str
    #: normalised header → importer field
    columns: dict[str, str]
    #: Headers that must all be present for this preset to claim a file.
    fingerprint: tuple[str, ...]
    #: ⚠ False until a real export from this product has been run through it.
    verified: bool = False
    #: Extra date formats this source is known to emit, most specific first.
    date_formats: tuple[str, ...] = ()
    note: str = ""

    def matches(self, headers: list[str]) -> bool:
        present = {normalise(header) for header in headers}
        return bool(self.fingerprint) and all(marker in present for marker in self.fingerprint)

    def mapping_for(self, headers: list[str]) -> dict[str, str]:
        """Map this preset onto the file's actual header spellings."""
        mapping: dict[str, str] = {}
        claimed: set[str] = set()
        for header in headers:
            field = self.columns.get(normalise(header))
            if field and field not in claimed:
                mapping[header] = field
                claimed.add(field)
        return mapping


GENERIC_STUDENTS = Preset(
    key="generic-students",
    label="Generic spreadsheet",
    kind="students",
    columns={},
    fingerprint=(),
    verified=True,
    note=(
        "No preset — every column is guessed from its name and shown for you to "
        "confirm. This is the right choice for a hand-built spreadsheet."
    ),
)

GYMDESK_STUDENTS = Preset(
    key="gymdesk-students",
    label="Gymdesk (members export)",
    kind="students",
    columns={
        "memberid": "external_id",
        "firstname": "given_name",
        "lastname": "family_name",
        "birthday": "date_of_birth",
        "email": "email",
        "phone": "phone",
        "address": "address_line1",
        "city": "city",
        "status": "status",
        "joindate": "joined_on",
        "parentname": "guardian_given_name",
        "parentemail": "guardian_email",
        "parentphone": "guardian_phone",
    },
    fingerprint=("memberid", "firstname", "lastname"),
    note="Built from ordinary column names, not from a real export. Check the mapping.",
)

ZEN_PLANNER_STUDENTS = Preset(
    key="zenplanner-students",
    label="Zen Planner (people export)",
    kind="students",
    columns={
        "personid": "external_id",
        "firstname": "given_name",
        "lastname": "family_name",
        "dateofbirth": "date_of_birth",
        "emailaddress": "email",
        "mobilephone": "phone",
        "address1": "address_line1",
        "city": "city",
        "status": "status",
        "startdate": "joined_on",
    },
    fingerprint=("personid", "firstname", "lastname"),
    note="Built from ordinary column names, not from a real export. Check the mapping.",
)

GENERIC_ATTENDANCE = Preset(
    key="generic-attendance",
    label="Generic attendance sheet",
    kind="attendance",
    columns={
        "studentid": "student_external_id",
        "memberid": "student_external_id",
        "firstname": "given_name",
        "lastname": "family_name",
        "surname": "family_name",
        "date": "date",
        "classdate": "date",
        "class": "class_name",
        "classname": "class_name",
        "status": "status",
        "attendance": "status",
    },
    fingerprint=(),
    verified=True,
)

GENERIC_RANKS = Preset(
    key="generic-ranks",
    label="Generic grading history",
    kind="ranks",
    columns={
        "studentid": "student_external_id",
        "memberid": "student_external_id",
        "firstname": "given_name",
        "lastname": "family_name",
        "surname": "family_name",
        "style": "style",
        "art": "style",
        "rank": "rank",
        "belt": "rank",
        "grade": "rank",
        "date": "awarded_on",
        "awarded": "awarded_on",
        "awardedon": "awarded_on",
        "gradingdate": "awarded_on",
        "certificate": "certificate_number",
    },
    fingerprint=(),
    verified=True,
)

PRESETS: tuple[Preset, ...] = (
    GENERIC_STUDENTS,
    GYMDESK_STUDENTS,
    ZEN_PLANNER_STUDENTS,
    GENERIC_ATTENDANCE,
    GENERIC_RANKS,
)


def for_kind(kind: str) -> list[Preset]:
    return [preset for preset in PRESETS if preset.kind == kind]


def detect(headers: list[str], kind: str) -> Preset | None:
    """The most specific preset whose fingerprint this file satisfies.

    ⚠ Most specific wins — the preset with the longest fingerprint — so a file
    matching both a product preset and a looser one gets the product's. Presets
    with no fingerprint never claim a file; they are chosen by hand.
    """
    candidates = [preset for preset in for_kind(kind) if preset.matches(headers)]
    if not candidates:
        return None
    return max(candidates, key=lambda preset: len(preset.fingerprint))


def generic_for(kind: str) -> Preset | None:
    """The fingerprint-less preset for a kind — the baseline column knowledge.

    ⚠ These never claim a file through ``detect`` (no fingerprint), but they are
    where the column names for attendance and rank history live. Without applying
    them as the default, nothing guesses those two imports at all: the name-based
    guesser only knows student fields, so an attendance file arrived with only
    "Status" mapped and the operator had to do the rest by hand. Found by walking
    the wizard, not by a test.
    """
    return next(
        (preset for preset in for_kind(kind) if not preset.fingerprint and preset.columns),
        None,
    )


def by_key(key: str) -> Preset | None:
    return next((preset for preset in PRESETS if preset.key == key), None)
