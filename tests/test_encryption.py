"""Envelope encryption — TODO 0.3.8, SEC 2.3."""

from __future__ import annotations

import base64
import uuid

import pytest
from django.test import override_settings

from apps.core.encryption import (
    DecryptionFailed,
    EncryptionNotConfigured,
    current_master_key,
    data_key_for,
    decrypt,
    encrypt,
    generate_master_key,
    looks_encrypted,
    master_keys,
    rotate_master_key,
)
from apps.core.models import OrganizationDataKey
from apps.core.scoping import allow_unscoped
from apps.identity.models import Organization

pytestmark = pytest.mark.django_db

SECOND_KEY = "2:" + base64.urlsafe_b64encode(b"second-master-key-do-not-use!!!!").decode()
TEST_KEYS = "1:ZG9qb21hc3Rlci10ZXN0LWtleS1kby1ub3QtdXNlISE="


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Crypto Org", slug="crypto-org")


@pytest.fixture
def other_org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Other Org", slug="other-crypto-org")


# -- round trip ---------------------------------------------------------------


def test_round_trip(org):
    token = encrypt(org.pk, "peanut allergy — carries an EpiPen")
    assert decrypt(token) == "peanut allergy — carries an EpiPen"


def test_ciphertext_does_not_contain_the_plaintext(org):
    token = encrypt(org.pk, "severe asthma")
    assert "severe asthma" not in token
    assert b"severe asthma" not in base64.urlsafe_b64decode(token)


def test_same_plaintext_encrypts_differently_each_time(org):
    """Randomised nonces — otherwise equal ciphertexts would leak equal values."""
    first = encrypt(org.pk, "identical")
    second = encrypt(org.pk, "identical")
    assert first != second
    assert decrypt(first) == decrypt(second) == "identical"


def test_unicode_survives(org):
    value = "អាឡែស៊ីសណ្តែក · 花生过敏"
    assert decrypt(encrypt(org.pk, value)) == value


def test_none_passes_through(org):
    assert encrypt(org.pk, None) is None
    assert decrypt(None) is None


def test_empty_string_round_trips(org):
    assert decrypt(encrypt(org.pk, "")) == ""


# -- tenant binding -----------------------------------------------------------


def test_ciphertext_is_bound_to_its_organisation(org, other_org):
    """A ciphertext moved into another tenant's row must fail, not decrypt.

    This is what keeps a SQL-level mistake from becoming a data breach.
    """
    token = encrypt(org.pk, "confidential")
    blob = bytearray(base64.urlsafe_b64decode(token))
    blob[2:18] = other_org.pk.bytes  # rewrite the embedded organisation id
    tampered = base64.urlsafe_b64encode(bytes(blob)).decode()

    with pytest.raises(DecryptionFailed):
        decrypt(tampered)


def test_each_organisation_gets_its_own_data_key(org, other_org):
    assert data_key_for(org.pk) != data_key_for(other_org.pk)


def test_data_key_is_stable_across_calls(org):
    assert data_key_for(org.pk) == data_key_for(org.pk)


def test_data_key_is_created_once(org):
    data_key_for(org.pk)
    data_key_for(org.pk)
    assert OrganizationDataKey.objects.filter(organization_id=org.pk).count() == 1


# -- tampering ----------------------------------------------------------------


def test_modified_ciphertext_is_rejected(org):
    token = encrypt(org.pk, "do not spar — shoulder injury")
    blob = bytearray(base64.urlsafe_b64decode(token))
    blob[-1] ^= 0xFF
    tampered = base64.urlsafe_b64encode(bytes(blob)).decode()

    with pytest.raises(DecryptionFailed):
        decrypt(tampered)


def test_truncated_token_is_rejected(org):
    token = encrypt(org.pk, "something")
    blob = base64.urlsafe_b64decode(token)[:20]
    with pytest.raises(DecryptionFailed):
        decrypt(base64.urlsafe_b64encode(blob).decode())


def test_garbage_is_rejected():
    with pytest.raises(DecryptionFailed):
        decrypt("not-a-token-at-all")


def test_unknown_envelope_version_is_rejected():
    blob = b"v9" + uuid.uuid4().bytes + b"0" * 12 + b"ciphertext"
    with pytest.raises(DecryptionFailed):
        decrypt(base64.urlsafe_b64encode(blob).decode())


# -- key management -----------------------------------------------------------


def test_the_database_alone_cannot_decrypt(org):
    """The stored key is wrapped. Without the master key it is inert — this is
    the property that makes a stolen backup survivable (SEC §3)."""
    token = encrypt(org.pk, "medical history")
    record = OrganizationDataKey.objects.get(organization_id=org.pk)

    with override_settings(FIELD_ENCRYPTION_KEYS=SECOND_KEY):
        master_keys.cache_clear() if hasattr(master_keys, "cache_clear") else None
        with pytest.raises((EncryptionNotConfigured, DecryptionFailed)):
            decrypt(token)

    assert bytes(record.wrapped_key) != b""


def test_rotating_the_master_key_preserves_field_values(org):
    """Rotation rewraps the DEK; encrypted columns are never rewritten."""
    token = encrypt(org.pk, "asthma inhaler in bag")
    original = OrganizationDataKey.objects.get(organization_id=org.pk)
    original_wrapped = bytes(original.wrapped_key)

    with override_settings(FIELD_ENCRYPTION_KEYS=f"{TEST_KEYS},{SECOND_KEY}"):
        rotate_master_key(org.pk)
        refreshed = OrganizationDataKey.objects.get(organization_id=org.pk)
        assert refreshed.master_key_version == 2
        assert bytes(refreshed.wrapped_key) != original_wrapped
        assert decrypt(token) == "asthma inhaler in bag"


def test_missing_master_key_version_fails_loudly(org):
    """Better a clear startup error than silently unreadable medical data."""
    encrypt(org.pk, "value")
    OrganizationDataKey.objects.filter(organization_id=org.pk).update(master_key_version=99)

    with pytest.raises(EncryptionNotConfigured, match="v99"):
        data_key_for(org.pk)


def test_no_master_key_configured_raises(org):
    with override_settings(FIELD_ENCRYPTION_KEYS=""):
        import os

        saved = os.environ.pop("DJANGO_FIELD_ENCRYPTION_KEYS", None)
        try:
            with pytest.raises(EncryptionNotConfigured):
                master_keys()
        finally:
            if saved is not None:
                os.environ["DJANGO_FIELD_ENCRYPTION_KEYS"] = saved


def test_malformed_key_entry_raises():
    with override_settings(FIELD_ENCRYPTION_KEYS="no-version-marker"):
        with pytest.raises(EncryptionNotConfigured):
            master_keys()


def test_wrong_length_key_raises():
    short = "1:" + base64.urlsafe_b64encode(b"too-short").decode()
    with override_settings(FIELD_ENCRYPTION_KEYS=short):
        with pytest.raises(EncryptionNotConfigured):
            master_keys()


def test_current_master_key_is_the_highest_version():
    with override_settings(FIELD_ENCRYPTION_KEYS=f"{TEST_KEYS},{SECOND_KEY}"):
        assert current_master_key().version == 2


def test_generate_master_key_is_usable():
    generated = generate_master_key(7)
    with override_settings(FIELD_ENCRYPTION_KEYS=generated):
        assert current_master_key().version == 7


def test_data_keys_cannot_be_deleted_casually(org):
    data_key_for(org.pk)
    record = OrganizationDataKey.objects.get(organization_id=org.pk)
    with pytest.raises(NotImplementedError):
        record.delete()


# -- helper -------------------------------------------------------------------


def test_looks_encrypted_discriminates(org):
    assert looks_encrypted(encrypt(org.pk, "x")) is True
    assert looks_encrypted("plain text") is False
    assert looks_encrypted("") is False
    assert looks_encrypted(None) is False
