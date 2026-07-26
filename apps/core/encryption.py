"""Envelope encryption for sensitive fields — TODO 0.3.8 / SEC 2.3.

What this protects: medical notes, safeguarding notes, identity documents. Per
SEC §1.1 that data — health information about children, plus notes like "father
not authorised for pickup" — is the most damaging thing in the system if it
leaks, more so than the payment records.

Design:

    master key (KEK)   env / key file, NEVER in the database
        wraps
    per-organisation data key (DEK)   stored wrapped, in the database
        encrypts
    field values

Two properties follow, and both matter:

  - A database dump alone is not enough to read anything. The dump contains
    wrapped DEKs, not usable keys. This is what makes the managed-hosting
    backup story defensible (SEC §3).
  - Rotating the master key rewraps a handful of DEK rows rather than
    re-encrypting every row of every table.

Ciphertext is bound to its organisation via AES-GCM associated data, so a
ciphertext copied from one tenant's row into another's fails to decrypt rather
than silently revealing itself. Tenant isolation survives a SQL-level mistake.

⚠ Encrypted fields cannot be filtered, sorted or indexed in the database. That
is inherent, not an oversight. If you need to look something up, it must not be
encrypted — reconsider whether it needs storing at all.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

VERSION_PREFIX = b"v1"
NONCE_BYTES = 12
KEY_BYTES = 32
UUID_BYTES = 16


class EncryptionNotConfigured(RuntimeError):
    """No master key is available."""


class DecryptionFailed(ValueError):
    """Ciphertext was corrupt, truncated, or belongs to another organisation."""


@dataclass(frozen=True)
class MasterKey:
    version: int
    key: bytes

    def __post_init__(self):
        if len(self.key) != KEY_BYTES:
            raise EncryptionNotConfigured(
                f"Master key v{self.version} is {len(self.key)} bytes; expected {KEY_BYTES}"
            )


def _parse_master_keys(raw: str) -> dict[int, MasterKey]:
    """Parse ``1:<base64>,2:<base64>``.

    Several versions may be present at once: during rotation the old key must
    still be able to unwrap existing DEKs while new wraps use the newest.
    """
    keys: dict[int, MasterKey] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise EncryptionNotConfigured(
                "Master keys must be formatted 'version:base64key', e.g. '1:AAAA...'"
            )
        version_text, encoded = chunk.split(":", 1)
        try:
            version = int(version_text)
            key = base64.urlsafe_b64decode(encoded)
        except (ValueError, TypeError) as exc:
            raise EncryptionNotConfigured(f"Unreadable master key entry {chunk!r}") from exc
        keys[version] = MasterKey(version=version, key=key)
    return keys


@lru_cache(maxsize=1)
def _master_keys_cached(raw: str) -> dict[int, MasterKey]:
    return _parse_master_keys(raw)


def master_keys() -> dict[int, MasterKey]:
    raw = getattr(settings, "FIELD_ENCRYPTION_KEYS", "") or os.environ.get(
        "DJANGO_FIELD_ENCRYPTION_KEYS", ""
    )
    if not raw:
        raise EncryptionNotConfigured(
            "No field-encryption master key. Set DJANGO_FIELD_ENCRYPTION_KEYS to "
            "'1:<base64 32 bytes>'. Generate one with:\n"
            "  python -c \"import base64,os;print('1:'+base64.urlsafe_b64encode(os.urandom(32)).decode())\""
        )
    keys = _master_keys_cached(raw)
    if not keys:
        raise EncryptionNotConfigured("FIELD_ENCRYPTION_KEYS is set but empty")
    return keys


def current_master_key() -> MasterKey:
    keys = master_keys()
    return keys[max(keys)]


def generate_master_key(version: int = 1) -> str:
    """Produce a value suitable for DJANGO_FIELD_ENCRYPTION_KEYS."""
    return f"{version}:{base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode()}"


# -- data keys ----------------------------------------------------------------


def _wrap(dek: bytes, master: MasterKey) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    wrapped = AESGCM(master.key).encrypt(nonce, dek, b"dek")
    return nonce + wrapped


def _unwrap(blob: bytes, master: MasterKey) -> bytes:
    nonce, wrapped = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        return AESGCM(master.key).decrypt(nonce, wrapped, b"dek")
    except InvalidTag as exc:
        raise DecryptionFailed(
            f"Could not unwrap the data key with master key v{master.version}"
        ) from exc


def data_key_for(organization_id: UUID) -> bytes:
    """Fetch (or create) this organisation's data key, unwrapped.

    Deliberately not cached across requests: an in-process cache of plaintext
    DEKs is a memory-disclosure liability, and unwrapping is one AES operation.
    """
    from .models import OrganizationDataKey

    record = OrganizationDataKey.objects.filter(organization_id=organization_id).first()
    if record is None:
        master = current_master_key()
        dek = os.urandom(KEY_BYTES)
        record = OrganizationDataKey.objects.create(
            organization_id=organization_id,
            wrapped_key=_wrap(dek, master),
            master_key_version=master.version,
        )
        return dek

    keys = master_keys()
    master = keys.get(record.master_key_version)
    if master is None:
        raise EncryptionNotConfigured(
            f"Organisation {organization_id} has a data key wrapped with master key "
            f"v{record.master_key_version}, which is not present. Restore that key "
            f"before starting, or the data is unreadable."
        )
    return _unwrap(bytes(record.wrapped_key), master)


def rotate_master_key(organization_id: UUID) -> None:
    """Rewrap one organisation's DEK under the newest master key.

    Field values are untouched — that is the point of the envelope.
    """
    from .models import OrganizationDataKey

    record = OrganizationDataKey.objects.filter(organization_id=organization_id).first()
    if record is None:
        return
    dek = data_key_for(organization_id)
    master = current_master_key()
    record.wrapped_key = _wrap(dek, master)
    record.master_key_version = master.version
    record.save(update_fields=["wrapped_key", "master_key_version"])


# -- field values -------------------------------------------------------------


def encrypt(organization_id: UUID, plaintext: str) -> str:
    """Encrypt a value for one organisation. Returns a base64 token."""
    if plaintext is None:
        return None
    org = UUID(str(organization_id))
    dek = data_key_for(org)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), org.bytes)
    return base64.urlsafe_b64encode(
        VERSION_PREFIX + org.bytes + nonce + ciphertext
    ).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a token. The owning organisation is carried in the envelope."""
    if token is None:
        return None
    try:
        blob = base64.urlsafe_b64decode(token.encode("ascii"))
    except Exception as exc:
        raise DecryptionFailed("Value is not a valid encryption token") from exc

    if not blob.startswith(VERSION_PREFIX):
        raise DecryptionFailed("Unrecognised encryption envelope version")

    offset = len(VERSION_PREFIX)
    org_bytes = blob[offset : offset + UUID_BYTES]
    nonce = blob[offset + UUID_BYTES : offset + UUID_BYTES + NONCE_BYTES]
    ciphertext = blob[offset + UUID_BYTES + NONCE_BYTES :]

    if len(org_bytes) != UUID_BYTES or len(nonce) != NONCE_BYTES or not ciphertext:
        raise DecryptionFailed("Encryption token is truncated")

    org = UUID(bytes=org_bytes)
    dek = data_key_for(org)
    try:
        return AESGCM(dek).decrypt(nonce, ciphertext, org.bytes).decode("utf-8")
    except InvalidTag as exc:
        raise DecryptionFailed(
            "Ciphertext failed authentication — corrupt, or moved between organisations"
        ) from exc


def looks_encrypted(value: str | None) -> bool:
    """Cheap check used by the field layer to avoid double-encrypting."""
    if not value:
        return False
    try:
        return base64.urlsafe_b64decode(value.encode("ascii")).startswith(VERSION_PREFIX)
    except Exception:
        return False
