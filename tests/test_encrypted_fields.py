"""Encrypted model fields — TODO 0.3.8, SEC 2.3.

Exercises the field layer directly. The first real consumer is
``StudentProfile.medical_notes`` (task 1.1.2); these tests hold the contract
until it lands.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.core.encryption import looks_encrypted
from apps.core.fields import EncryptedCharField, EncryptedTextField
from apps.core.scoping import allow_unscoped
from apps.identity.models import Organization

pytestmark = pytest.mark.django_db


class FakeInstance:
    """Minimal stand-in for a model instance during pre_save."""

    def __init__(self, organization_id, value):
        self.organization_id = organization_id
        self.medical_notes = value


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Field Org", slug="field-org")


def _field():
    field = EncryptedTextField(blank=True)
    field.attname = "medical_notes"
    field.name = "medical_notes"
    return field


# -- encrypt on save ----------------------------------------------------------


def test_pre_save_encrypts(org):
    field = _field()
    stored = field.pre_save(FakeInstance(org.pk, "nut allergy"), add=True)
    assert looks_encrypted(stored)
    assert "nut allergy" not in stored


def test_from_db_value_decrypts(org):
    field = _field()
    stored = field.pre_save(FakeInstance(org.pk, "nut allergy"), add=True)
    assert field.from_db_value(stored, None, None) == "nut allergy"


def test_blank_and_none_are_left_alone(org):
    field = _field()
    assert field.pre_save(FakeInstance(org.pk, ""), add=True) == ""
    assert field.pre_save(FakeInstance(org.pk, None), add=True) is None


def test_already_encrypted_value_is_not_double_encrypted(org):
    field = _field()
    once = field.pre_save(FakeInstance(org.pk, "asthma"), add=True)
    twice = field.pre_save(FakeInstance(org.pk, once), add=False)
    assert twice == once
    assert field.from_db_value(twice, None, None) == "asthma"


def test_plaintext_in_the_column_still_reads(org):
    """Tolerated so a column can be encrypted in place by a data migration
    without a flag day."""
    field = _field()
    assert field.from_db_value("legacy plaintext", None, None) == "legacy plaintext"


def test_missing_organisation_is_a_configuration_error():
    """Encrypted fields are per tenant; without an organisation there is no key."""
    field = _field()
    instance = FakeInstance(None, "some value")
    with pytest.raises(ImproperlyConfigured, match="organisation"):
        field.pre_save(instance, add=True)


# -- misuse guards ------------------------------------------------------------


def test_index_is_refused():
    """Randomised ciphertext means an index could never match — fail loudly."""
    with pytest.raises(ImproperlyConfigured, match="db_index"):
        EncryptedTextField(db_index=True)


def test_unique_is_refused():
    with pytest.raises(ImproperlyConfigured, match="unique"):
        EncryptedTextField(unique=True)


# -- char field sizing --------------------------------------------------------


def test_char_field_widens_the_column_for_the_envelope():
    field = EncryptedCharField(max_length=100)
    assert field.max_length > 100
    assert field.plaintext_max_length == 100


def test_char_field_deconstructs_back_to_plaintext_length():
    """Otherwise every makemigrations run would see a changed max_length."""
    field = EncryptedCharField(max_length=100)
    _name, _path, _args, kwargs = field.deconstruct()
    assert kwargs["max_length"] == 100


def test_to_python_handles_both_forms(org):
    field = _field()
    stored = field.pre_save(FakeInstance(org.pk, "hello"), add=True)
    assert field.to_python(stored) == "hello"
    assert field.to_python("hello") == "hello"
    assert field.to_python(None) is None
