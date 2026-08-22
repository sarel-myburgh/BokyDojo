"""Validating a Google Plus Code — plan §3.

⚠ Format only, never a lookup. Nothing here contacts Google or resolves a code
to a place: the point is to catch a typo before it becomes a map link that opens
on the wrong continent, not to confirm the address exists.

A Plus Code is characters from a fixed 20-symbol alphabet, a "+", then two to
three more — "HW4C+8Q". A *short* code like that is only unambiguous next to a
locality, which is why "HW4C+8Q Phnom Penh" is the form people actually exchange
and why the field keeps the whole string.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

#: The Open Location Code alphabet. ⚠ Deliberately excludes A, E, I, L, O, S, U
#: and the digits 0 and 1 — the characters that get confused for one another
#: when a code is read aloud or copied off a shopfront.
ALPHABET = "23456789CFGHJMPQRVWX"

_CODE = re.compile(
    rf"^[{ALPHABET}]{{4,8}}\+[{ALPHABET}]{{2,3}}$",
    re.IGNORECASE,
)


def normalise(value: str) -> str:
    """Trim and upper-case the code, leaving any locality after it alone."""
    text = (value or "").strip()
    if not text:
        return ""
    code, _, locality = text.partition(" ")
    locality = locality.strip()
    return f"{code.upper()} {locality}".strip()


def validate_plus_code(value: str) -> None:
    """Raise ValidationError unless this looks like a Plus Code."""
    text = (value or "").strip()
    if not text:
        return

    code = text.split(" ", 1)[0]
    if not _CODE.match(code):
        raise ValidationError(
            _(
                "That does not look like a Plus Code. It should look like "
                'HW4C+8Q, followed by the town — for example "HW4C+8Q Phnom Penh".'
            )
        )

    # ⚠ A short code (8 characters or fewer before the +) is only meaningful
    # beside a place name. Accepting one alone would produce a link that lands
    # somewhere plausible and wrong.
    before_plus = len(code.split("+")[0])
    has_locality = len(text.split(" ", 1)) > 1 and text.split(" ", 1)[1].strip()
    if before_plus <= 6 and not has_locality:
        raise ValidationError(
            _(
                'Add the town after the code, like "HW4C+8Q Phnom Penh" — '
                "a short Plus Code on its own points at the wrong place."
            )
        )
