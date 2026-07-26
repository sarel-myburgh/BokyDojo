"""Encrypted model fields — TODO 0.3.8 / SEC 2.3.

Usage::

    class StudentProfile(TenantScopedModel):
        organization = models.ForeignKey(...)
        medical_notes = EncryptedTextField(blank=True)

The field encrypts on write and decrypts on read; call sites see plain strings.
The owning organisation is read from the instance at save time and embedded in
the envelope, so decryption needs no instance context.

⚠ These columns cannot be filtered, ordered or indexed. `.filter(medical_notes=x)`
will not match anything — every row has a different nonce, so identical
plaintext produces different ciphertext. That is correct behaviour for this data,
not a limitation to work around: if you need to search it, it should not be
encrypted, and probably should not be stored.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import models

from .encryption import decrypt, encrypt, looks_encrypted


class EncryptedFieldMixin:
    """Shared encrypt-on-save / decrypt-on-load behaviour."""

    #: Attribute on the model instance holding the owning organisation's id.
    organization_attname = "organization_id"

    def __init__(self, *args, **kwargs):
        # Encrypted values are opaque; these would silently not work.
        for unsupported in ("db_index", "unique"):
            if kwargs.get(unsupported):
                raise ImproperlyConfigured(
                    f"{self.__class__.__name__} cannot be {unsupported}: ciphertext is "
                    f"randomised, so the index or constraint could never match."
                )
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        if not looks_encrypted(value):
            # Tolerated so a column can be encrypted in place by a data migration
            # without a flag day. Remove the tolerance once backfill is verified.
            return value
        return decrypt(value)

    def to_python(self, value):
        if value is None:
            return None
        if looks_encrypted(value):
            return decrypt(value)
        return value

    def pre_save(self, model_instance, add):
        value = getattr(model_instance, self.attname)
        if value in (None, ""):
            return value
        if looks_encrypted(value):
            return value

        organization_id = getattr(model_instance, self.organization_attname, None)
        if organization_id is None:
            raise ImproperlyConfigured(
                f"{model_instance.__class__.__name__}.{self.attname} is encrypted but the "
                f"instance has no {self.organization_attname}. Encrypted fields require an "
                f"organisation — the key is per tenant."
            )
        return encrypt(organization_id, value)

    def get_prep_value(self, value):
        # Prevent a lookup from being silently compared against ciphertext.
        return value


class EncryptedTextField(EncryptedFieldMixin, models.TextField):
    """Free text at rest under AES-GCM. For medical and safeguarding notes."""


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    """Short encrypted string.

    ``max_length`` describes the *plaintext*; the column is widened to hold the
    base64 envelope, which is roughly 1.4× plus ~60 bytes of header.
    """

    def __init__(self, *args, max_length=None, **kwargs):
        self.plaintext_max_length = max_length
        if max_length is not None:
            kwargs["max_length"] = int(max_length * 1.4) + 128
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.plaintext_max_length is not None:
            kwargs["max_length"] = self.plaintext_max_length
        return name, path, args, kwargs
