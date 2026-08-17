"""Declared settings and how they resolve — TODO 0.3.7, plan §13.2.

Settings resolve down a hierarchy:

    org default → dojo → class template → class session → student

Most settings are "most specific wins". Some are not, and getting those wrong is
a real bug rather than a preference. ``pin_policy`` is the worked example from
plan §13.2: a class set to ``off`` must **not** be able to downgrade a student
who has been individually marked ``required`` — perhaps because a parent
disputes their attendance record. For those, the stricter value wins regardless
of which level set it.

Settings are declared here in code, not created ad hoc in the database. An
unknown key is an error, not an empty string: a typo in a setting name should
fail loudly rather than silently resolve to a default nobody chose.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class Scope:
    ORG = "org"
    DOJO = "dojo"
    CLASS_TEMPLATE = "class_template"
    CLASS_SESSION = "class_session"
    STUDENT = "student"

    #: Least specific → most specific. The resolution order depends on this.
    ORDER = (ORG, DOJO, CLASS_TEMPLATE, CLASS_SESSION, STUDENT)

    @classmethod
    def rank(cls, scope_type: str) -> int:
        return cls.ORDER.index(scope_type)


class Resolution:
    #: The value set closest to the subject wins.
    MOST_SPECIFIC = "most_specific"
    #: The strictest value set anywhere in the chain wins, regardless of level.
    STRICTEST = "strictest"


class UnknownSetting(KeyError):
    """A setting key that was never declared."""


class InvalidSettingValue(ValueError):
    """A value outside the declared choices, or set at a disallowed scope."""


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    default: Any
    scopes: tuple[str, ...]
    resolution: str = Resolution.MOST_SPECIFIC
    choices: tuple[Any, ...] | None = None
    #: Least strict → most strict. Required when resolution is STRICTEST.
    strictness: tuple[Any, ...] | None = None
    #: Extra validation for values ``choices`` cannot express — a list-valued
    #: setting, a range, a shape. Raises ``InvalidSettingValue``. Without this a
    #: setting whose value is a *collection* has no validation at all: ``choices``
    #: asks "is the value one of these", which is the wrong question for a list.
    validator: Callable[[Any], None] | None = None
    description: str = ""

    def __post_init__(self):
        if self.resolution == Resolution.STRICTEST and not self.strictness:
            raise ValueError(f"{self.key}: STRICTEST resolution requires a strictness order")
        if self.choices and self.default not in self.choices:
            raise ValueError(f"{self.key}: default {self.default!r} is not among choices")
        for scope in self.scopes:
            if scope not in Scope.ORDER:
                raise ValueError(f"{self.key}: unknown scope {scope!r}")
        # A default that its own validator rejects is a bug that must surface at
        # import time, not the first time somebody happens to read the setting.
        if self.validator is not None:
            self.validator(self.default)

    def validate(self, value: Any, scope_type: str) -> None:
        if scope_type not in self.scopes:
            raise InvalidSettingValue(
                f"{self.key} cannot be set at {scope_type} scope "
                f"(allowed: {', '.join(self.scopes)})"
            )
        if self.choices is not None and value not in self.choices:
            raise InvalidSettingValue(f"{self.key}: {value!r} is not one of {self.choices}")
        if self.validator is not None:
            self.validator(value)

    def strictness_of(self, value: Any) -> int:
        try:
            return self.strictness.index(value)
        except (AttributeError, ValueError) as exc:
            raise InvalidSettingValue(f"{self.key}: {value!r} has no defined strictness") from exc


REGISTRY: dict[str, SettingDefinition] = {}


def register(definition: SettingDefinition) -> SettingDefinition:
    if definition.key in REGISTRY:
        raise ValueError(f"Setting {definition.key!r} is already registered")
    REGISTRY[definition.key] = definition
    return definition


def get_definition(key: str) -> SettingDefinition:
    try:
        return REGISTRY[key]
    except KeyError as exc:
        raise UnknownSetting(
            f"Unknown setting {key!r}. Declare it in apps/core/setting_registry.py."
        ) from exc


# ---------------------------------------------------------------------------
# Declared settings
# ---------------------------------------------------------------------------

KIOSK_DISPLAY_MODE = register(
    SettingDefinition(
        key="attendance.kiosk_display_mode",
        default="photo_grid",
        choices=("photo_grid", "name_list", "both"),
        scopes=(Scope.ORG, Scope.DOJO, Scope.CLASS_TEMPLATE, Scope.CLASS_SESSION),
        description=(
            "How the check-in kiosk presents the roster. Photo grid for children "
            "who cannot yet read or type; name list for large adult classes. The "
            "instructor may switch this live at the kiosk (plan §13.2)."
        ),
    )
)

PIN_POLICY = register(
    SettingDefinition(
        key="attendance.pin_policy",
        default="off",
        choices=("off", "optional", "required"),
        scopes=(
            Scope.ORG,
            Scope.DOJO,
            Scope.CLASS_TEMPLATE,
            Scope.CLASS_SESSION,
            Scope.STUDENT,
        ),
        resolution=Resolution.STRICTEST,
        strictness=("off", "optional", "required"),
        description=(
            "Whether a student must enter a PIN to check themselves in. The "
            "STRICTEST value in the chain wins: a class set to 'off' cannot "
            "downgrade a student individually marked 'required' (plan §13.2)."
        ),
    )
)

ATTENDANCE_CATCHUP_WINDOW_DAYS = register(
    SettingDefinition(
        key="attendance.catchup_window_days",
        default=14,
        scopes=(Scope.ORG, Scope.DOJO),
        description=(
            "How far back an instructor may retroactively record attendance for a "
            "session that was never marked (plan §12.7)."
        ),
    )
)

ATTENDANCE_INACTIVITY_ALERT_DAYS = register(
    SettingDefinition(
        key="attendance.inactivity_alert_days",
        default=21,
        scopes=(Scope.ORG, Scope.DOJO),
        description="Flag a student who has not attended for this many days.",
    )
)

SESSION_GENERATION_HORIZON_DAYS = register(
    SettingDefinition(
        key="scheduling.generation_horizon_days",
        default=90,
        scopes=(Scope.ORG, Scope.DOJO),
        description="How far ahead recurring class sessions are materialised.",
    )
)

#: Longest a single tag may be. Long enough for "grading_preparation", short
#: enough that a runaway paste is refused rather than stored.
MAX_CLASS_TYPE_TAG_LENGTH = 50


def validate_class_type_vocabulary(value: Any) -> None:
    """The organisation's list of class-type tags — TODO 1.4.10.

    ⚠ Tags must be lower-case and whitespace-free. This is not tidiness: plan §2
    item 23 wants rules like "40 classes, of which ≥10 kata", and a rule naming
    ``kata`` against a class tagged ``Kata`` matches **nothing**. The failure is
    silent and surfaces months later as a student wrongly held back from a
    grading. Normalising quietly would be worse still — it would make the two
    agree by luck, and hide that somebody has two names for one thing.

    ``.lower()`` is identity for scripts without case, so Khmer and Chinese tags
    pass through unchanged.
    """
    if not isinstance(value, list | tuple):
        raise InvalidSettingValue(
            f"class type vocabulary must be a list of tags, got {type(value).__name__}"
        )
    seen: set[str] = set()
    for tag in value:
        if not isinstance(tag, str):
            raise InvalidSettingValue(f"class type tag must be a string, got {tag!r}")
        if not tag:
            raise InvalidSettingValue("class type tag must not be empty")
        if tag != tag.strip():
            raise InvalidSettingValue(f"class type tag {tag!r} has leading or trailing whitespace")
        if tag != tag.lower():
            raise InvalidSettingValue(f"class type tag {tag!r} must be lower-case")
        if any(character.isspace() for character in tag):
            raise InvalidSettingValue(f"class type tag {tag!r} must not contain whitespace")
        if len(tag) > MAX_CLASS_TYPE_TAG_LENGTH:
            raise InvalidSettingValue(
                f"class type tag {tag!r} exceeds {MAX_CLASS_TYPE_TAG_LENGTH} characters"
            )
        if tag in seen:
            raise InvalidSettingValue(f"class type tag {tag!r} is listed twice")
        seen.add(tag)


CLASS_TYPE_TAGS = register(
    SettingDefinition(
        key="scheduling.class_type_tags",
        default=["kata", "kihon", "kumite", "conditioning", "grading_preparation"],
        # ⚠ Organisation scope only, deliberately. Grading eligibility is written
        # against these words, so if a dojo could invent its own the same rule
        # ("≥10 kata") would mean different things at different dojos in one
        # organisation — and a student transferring between them would gain or
        # lose progress with no record of why.
        scopes=(Scope.ORG,),
        validator=validate_class_type_vocabulary,
        description=(
            "Tags a class may count toward, for grading eligibility rules such as "
            "'40 classes since the last grading, of which at least 10 kata' "
            "(plan §2 item 23). The default is a karate vocabulary; an "
            "organisation teaching BJJ or Judo should replace it wholesale."
        ),
    )
)

CONSENT_SELF_AGE = register(
    SettingDefinition(
        key="consent.minimum_self_consent_age",
        default=18,
        choices=tuple(range(13, 19)),
        scopes=(Scope.ORG,),
        description=(
            "Minimum age at which a student may sign consent for themselves. "
            "The legal threshold varies by jurisdiction, so deployments must set it explicitly."
        ),
    )
)
