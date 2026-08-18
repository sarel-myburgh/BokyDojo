"""Administrator-issued temporary passwords — TODO 0.6.8.

Not every organisation running this has working SMTP, and the password-reset flow
(`0.6.6`) is an email link. Without this, a self-hoster whose instructor forgot
their password has no route back in short of a database shell — so an
administrator can issue a temporary one to hand over in person.

⚠ **This is an account-takeover primitive and is built as one.** Whoever holds it
can sign in as anybody in the organisation. Three things follow:

* It is restricted to ``ORG_EDIT``, which only an organisation administrator
  holds — deliberately narrower than ``ROLE_ASSIGN``, which dojo administrators
  also have.
* Every issue is audited strictly, naming the actor and the target. The audit
  entry is the only durable record that it happened; the password itself is
  never written anywhere.
* The password is *generated*, never chosen. An administrator asked to invent one
  picks something they will reuse, and one they choose is one they can guess
  about a colleague later.

⚠ **It does not bypass multi-factor authentication.** Somebody with a confirmed
TOTP credential is still challenged for it; a temporary password replaces the
first factor and nothing else.
"""

from __future__ import annotations

import secrets

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.scoping import Actor
from apps.identity.wordlist import WORDS

#: Words per passphrase. Four from a 1787-word list is 43 bits, which behind a
#: five-attempt lockout is far more than a single-use password needs.
_WORD_COUNT = 4


def generate_temporary_password() -> str:
    """A passphrase somebody can actually type — Battery-Staple-Horse style.

    ⚠ Replaces a random-character version. Random characters are stronger per
    keystroke and worse at the only job this has: being read across a counter or
    down a phone by one person and typed by another, often onto a phone keyboard,
    often where copy and paste is not available. "Was that an l or a 1" is the
    failure mode, and it costs a support call every time.

    Capitalised and hyphen-joined because that is what people expect a password
    to look like, and because the hyphens give the eye somewhere to rest when
    reading it out.
    """
    return "-".join(secrets.choice(WORDS).capitalize() for _ in range(_WORD_COUNT))


def set_temporary_password(*, user, actor: Actor) -> str:
    """Give ``user`` a new temporary password and return it once.

    The caller has already checked permission. Returns the plaintext, which is
    the only time it exists — it is hashed on the way in and never stored, so an
    administrator who loses it issues another rather than looking it up.
    """
    password = generate_temporary_password()

    user.set_password(password)
    user.must_change_password = True
    user.last_password_change = timezone.now()
    # ⚠ Changing the password rotates Django's session auth hash, which
    # invalidates every other session this user has. That is wanted: an
    # administrator resetting a password because an account may be compromised
    # would otherwise leave the intruder signed in.
    user.save(update_fields=["password", "must_change_password", "last_password_change"])

    audit.record(
        "password_reset_by_admin",
        actor=actor,
        subject=user.person,
        note=f"temporary password issued for {user.email}",
        strict=True,
    )
    return password


def clear_must_change(user) -> None:
    """Called once the person has chosen their own password."""
    if user.must_change_password:
        user.must_change_password = False
        user.last_password_change = timezone.now()
        user.save(update_fields=["must_change_password", "last_password_change"])


def describe_handover() -> str:
    """The wording shown to the administrator alongside the password."""
    return _(
        "Give this to them directly — in person, or by a channel you trust. It is "
        "shown once and is not stored anywhere; if it is lost, issue another. They "
        "must choose their own password before they can use anything."
    )
