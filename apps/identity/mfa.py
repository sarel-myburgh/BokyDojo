"""TOTP and recovery-code primitives for account MFA.

No credential material is logged. TOTP seeds are encrypted by the model field;
recovery codes are high-entropy values stored only as keyed SHA-256 digests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.identity.models import MfaCredential, Role, RoleAssignment

ADMIN_ROLES = (Role.ORG_ADMIN, Role.DOJO_ADMIN)
RECOVERY_CODE_COUNT = 10
TOTP_DIGITS = 6
TOTP_PERIOD = 30
TOTP_WINDOW = 1


def mfa_is_recommended(user) -> bool:
    """Whether this account holds enough power that a second factor matters.

    ⚠ Deliberately separate from ``user_requires_mfa``. This is the role test on
    its own, with no enforcement switch in front of it, so that the encouragement
    banner keeps working while enrolment is optional. Fold the two together and
    turning enforcement off silences the nudge as well, which leaves privileged
    accounts on a password alone and nothing anywhere saying so.
    """
    if not getattr(user, "is_authenticated", False) or not user.person_id:
        return False
    return (
        RoleAssignment.objects.for_organization(user.person.organization_id)
        .filter(person_id=user.person_id, revoked_at__isnull=True)
        .filter(Q(role__in=ADMIN_ROLES) | Q(can_view_financials=True) | Q(can_export_pii=True))
        .exists()
    )


def user_requires_mfa(user) -> bool:
    """Whether MFA is *mandatory* — i.e. blocks sign-in until enrolled.

    ⚠ The one predicate both the login view and the enforcement middleware ask.
    It has to carry the ``MFA_ENFORCEMENT_ENABLED`` switch itself: the middleware
    checked the setting and the login view did not, so turning enforcement off
    left privileged accounts stuck at the enrolment screen anyway — two
    enforcement points, one of them deaf to the switch.

    ⚠ Turning enforcement off does **not** bypass MFA for somebody who has
    already enrolled. The login view separately challenges any confirmed
    credential, so a user with a working second factor keeps using it; this only
    stops *demanding* one from a user who has none.
    """
    from django.conf import settings

    if not getattr(settings, "MFA_ENFORCEMENT_ENABLED", False):
        return False
    return mfa_is_recommended(user)


def should_encourage_mfa(user) -> bool:
    """Whether to show the enrolment banner: powerful account, no second factor."""
    if not mfa_is_recommended(user):
        return False
    credential = get_credential(user)
    return credential is None or not credential.is_confirmed


def get_credential(user) -> MfaCredential | None:
    if not user.person_id:
        return None
    return (
        MfaCredential.objects.for_organization(user.person.organization_id)
        .filter(user=user)
        .first()
    )


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def ensure_credential(user) -> MfaCredential:
    if not user.person_id:
        raise ValueError("A user must be linked to a person before enabling MFA.")
    with transaction.atomic():
        credential, _ = MfaCredential.objects.for_organization(
            user.person.organization_id
        ).get_or_create(
            user=user,
            defaults={
                "organization_id": user.person.organization_id,
                "totp_secret": generate_totp_secret(),
            },
        )
        return credential


def _counter(at_time: float | None = None) -> int:
    return int(time.time() if at_time is None else at_time) // TOTP_PERIOD


def _hotp(secret: str, counter: int) -> str:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(secret + padding, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**TOTP_DIGITS)
    return f"{value:0{TOTP_DIGITS}d}"


def current_totp(secret: str, *, at_time: float | None = None) -> str:
    """Return the RFC 6238 code; public primarily for deterministic tests."""
    return _hotp(secret, _counter(at_time))


def matching_totp_counter(secret: str, token: str, *, at_time: float | None = None) -> int | None:
    token = "".join(token.split())
    if len(token) != TOTP_DIGITS or not token.isascii() or not token.isdigit():
        return None
    current = _counter(at_time)
    match = None
    for candidate in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        if hmac.compare_digest(_hotp(secret, candidate), token):
            match = candidate
    return match


def _locked_credential(credential: MfaCredential):
    return (
        MfaCredential.objects.for_organization(credential.organization_id)
        .select_for_update()
        .get(pk=credential.pk)
    )


def consume_totp(credential: MfaCredential, token: str) -> bool:
    """Verify and atomically prevent reuse of an accepted TOTP time step."""
    counter = matching_totp_counter(credential.totp_secret, token)
    if counter is None:
        return False
    with transaction.atomic():
        locked = _locked_credential(credential)
        if locked.last_used_counter is not None and counter <= locked.last_used_counter:
            return False
        locked.last_used_counter = counter
        locked.save(update_fields=["last_used_counter", "updated_at"])
    return True


def _normalise_recovery_code(code: str) -> str:
    return "".join(character for character in code.upper() if character.isalnum())


def _recovery_digest(code: str) -> str:
    """⚠ The 'dojomaster' below is deliberate and must not be renamed.

    It is the HMAC domain-separation prefix for recovery-code digests. Recovery
    codes are stored hashed and cannot be re-derived, so changing this string
    silently invalidates every code ever issued — people would find out the day
    they lost their phone and needed one.

    It survived the rename to BokyDojo for that reason. It is internal, never
    shown to anybody, and its only job is to be *stable*.
    """
    key = settings.SECRET_KEY.encode("utf-8")
    message = ("dojomaster-mfa-recovery:" + _normalise_recovery_code(code)).encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def generate_recovery_codes() -> tuple[list[str], list[str]]:
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5).upper()
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, [_recovery_digest(code) for code in codes]


def confirm_credential(credential: MfaCredential, token: str) -> list[str] | None:
    counter = matching_totp_counter(credential.totp_secret, token)
    if counter is None:
        return None
    codes, digests = generate_recovery_codes()
    with transaction.atomic():
        locked = _locked_credential(credential)
        if locked.confirmed_at is not None:
            return None
        locked.confirmed_at = timezone.now()
        locked.last_used_counter = counter
        locked.recovery_code_hashes = digests
        locked.save(
            update_fields=[
                "confirmed_at",
                "last_used_counter",
                "recovery_code_hashes",
                "updated_at",
            ]
        )
    return codes


def consume_recovery_code(credential: MfaCredential, code: str) -> bool:
    candidate = _recovery_digest(code)
    with transaction.atomic():
        locked = _locked_credential(credential)
        remaining = list(locked.recovery_code_hashes)
        matched_index = next(
            (
                index
                for index, digest in enumerate(remaining)
                if hmac.compare_digest(digest, candidate)
            ),
            None,
        )
        if matched_index is None:
            return False
        remaining.pop(matched_index)
        locked.recovery_code_hashes = remaining
        locked.save(update_fields=["recovery_code_hashes", "updated_at"])
    return True


def provisioning_uri(credential: MfaCredential) -> str:
    label = quote(f"BokyDojo:{credential.user.email}", safe="")
    query = urlencode(
        {
            "secret": credential.totp_secret,
            "issuer": "BokyDojo",
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD,
        }
    )
    return f"otpauth://totp/{label}?{query}"
