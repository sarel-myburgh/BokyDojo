"""Encrypt note bodies and add the safeguarding level — TODO 1.8.4, SEC §4.

Follows identity/0011_encrypt_hold_reasons: alter the column, then rewrite the
rows already in it. Existing plaintext is encrypted in place rather than left
readable, and the operation is reversible so a downgrade does not strand
ciphertext in a column no longer able to decrypt it.

The rewrite uses raw SQL on purpose. Going through the model would re-enter the
field's ``pre_save`` hook and encrypt already-encrypted values a second time, and
historical models in a migration do not carry custom field behaviour anyway.
"""

from django.db import migrations, models

import apps.core.fields


def _rewrite_note_bodies(schema_editor, transform):
    """Transform every stored body without field pre-save hooks."""
    connection = schema_editor.connection
    quote = connection.ops.quote_name
    # core_note carries organization_id directly, so unlike the hold-reason
    # rewrite this needs no join to find the tenant whose key applies.
    table = quote("core_note")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, body, organization_id FROM {table} WHERE body <> ''"  # nosec B608
        )
        rows = cursor.fetchall()
        for row_id, value, organization_id in rows:
            replacement = transform(organization_id, value)
            if replacement != value:
                cursor.execute(
                    f"UPDATE {table} SET body = %s WHERE id = %s",  # nosec B608
                    [replacement, row_id],
                )


def encrypt_existing_bodies(apps, schema_editor):
    from apps.core.encryption import encrypt, looks_encrypted

    def transform(organization_id, value):
        return value if looks_encrypted(value) else encrypt(organization_id, value)

    _rewrite_note_bodies(schema_editor, transform)


def decrypt_existing_bodies(apps, schema_editor):
    from apps.core.encryption import decrypt, looks_encrypted

    def transform(_organization_id, value):
        return decrypt(value) if looks_encrypted(value) else value

    _rewrite_note_bodies(schema_editor, transform)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_alter_auditlog_action"),
    ]

    operations = [
        migrations.AlterField(
            model_name="note",
            name="body",
            field=apps.core.fields.EncryptedTextField(verbose_name="body"),
        ),
        migrations.AlterField(
            model_name="note",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("private", "Private — author only"),
                    ("instructors", "Instructors at this dojo"),
                    ("admins", "Dojo and org administrators"),
                    ("parent_visible", "Student's guardians can read it"),
                    ("safeguarding", "Safeguarding — named role only"),
                ],
                default="instructors",
                max_length=16,
                verbose_name="visibility",
            ),
        ),
        migrations.RunPython(
            encrypt_existing_bodies,
            decrypt_existing_bodies,
        ),
    ]
