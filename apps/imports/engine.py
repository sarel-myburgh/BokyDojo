"""Running an import — TODO 1.10.1, 1.10.2, 1.10.7, plan §12.10.

Three things here are load-bearing and each is a decision rather than a detail.

⚠ **A dry run takes the same code path as a real one.** It does the whole import
inside a transaction and rolls it back. The tempting alternative — a separate
"validate" pass that checks rows without writing — is a dry run that lies: it
drifts from the real path with every change, and it cannot see the failures that
only appear on write (a unique constraint, a model's ``save()`` refusing a value,
a second row in the same file colliding with the first). A preview that misses
those is worse than none, because it is trusted.

⚠ **Each row gets its own savepoint.** A row that raises inside a transaction
poisons that transaction on PostgreSQL — every later statement fails with
``InFailedSqlTransaction`` until rollback. Without a per-row savepoint, one bad
row in a thousand turns into a thousand errors and an import that reports total
nonsense. Production is PostgreSQL; SQLite is more forgiving, so this is exactly
the bug the test suite would not have caught.

⚠ **The ImportRun is saved outside the rolled-back transaction.** Otherwise a dry
run rolls back its own report and the operator sees nothing.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.scoping import Actor

from .models import ImportedRecord, ImportRun


class Outcome:
    CREATED = "created"
    UPDATED = "updated"
    SKIPPED = "skipped"
    ERROR = "error"


class _RollBackDryRun(Exception):
    """Sentinel used to unwind a dry run's transaction. Never escapes ``run``."""


@dataclasses.dataclass
class RowResult:
    row_number: int  # 1-based, counting the header as row 1, as a spreadsheet does
    outcome: str
    source_key: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class Importer:
    """What a specific importer must provide.

    ``entity_type`` names the key space in ``ImportedRecord``. ``fields`` maps the
    importer's own field names to whether they are required, and drives both the
    mapping UI and validation.
    """

    entity_type: str = ""
    kind: str = ""
    #: field name → required?
    fields: dict[str, bool] = {}

    def natural_key(self, row: dict[str, str]) -> str:
        """A stable identity for this row, for idempotent re-import."""
        raise NotImplementedError

    def apply(self, row: dict[str, str], *, existing_id, actor: Actor, dojo) -> tuple[Any, str]:
        """Create or update. Returns ``(object, Outcome.*)``."""
        raise NotImplementedError


def apply_mapping(row: dict[str, str], mapping: dict[str, str]) -> dict[str, str]:
    """Re-key a source row into importer field names.

    ``mapping`` is ``{source column: importer field}``. Columns the operator did
    not map are dropped — deliberately, so an unrecognised column in a competitor
    export is ignored rather than guessed at.
    """
    mapped: dict[str, str] = {}
    for source_column, field in mapping.items():
        if not field:
            continue
        value = row.get(source_column, "")
        mapped[field] = value.strip() if isinstance(value, str) else value
    return mapped


def validate_mapping(importer: Importer, mapping: dict[str, str]) -> None:
    """Refuse a mapping that cannot produce a usable row."""
    targets = [field for field in mapping.values() if field]

    unknown = sorted(set(targets) - set(importer.fields))
    if unknown:
        raise ValidationError(
            _("These fields are not part of this import: %(names)s.")
            % {"names": ", ".join(unknown)}
        )

    duplicated = sorted({field for field in targets if targets.count(field) > 1})
    if duplicated:
        # Two source columns pointing at one field is ambiguous, and picking
        # either silently loses data the operator believed was imported.
        raise ValidationError(
            _("These fields are mapped from more than one column: %(names)s.")
            % {"names": ", ".join(duplicated)}
        )

    missing = sorted(
        field for field, required in importer.fields.items() if required and field not in targets
    )
    if missing:
        raise ValidationError(
            _("These required fields are not mapped: %(names)s.") % {"names": ", ".join(missing)}
        )


def _existing_object_id(*, organization_id, entity_type: str, source_key: str):
    record = (
        ImportedRecord.objects.for_organization(organization_id)
        .filter(entity_type=entity_type, source_key=source_key)
        .first()
    )
    return record.object_id if record is not None else None


def _remember(*, organization_id, entity_type: str, source_key: str, object_id) -> None:
    ImportedRecord.objects.for_organization(organization_id).update_or_create(
        organization_id=organization_id,
        entity_type=entity_type,
        source_key=source_key,
        defaults={"object_id": object_id},
    )


def run(
    *,
    importer: Importer,
    rows: list[dict[str, str]],
    mapping: dict[str, str],
    actor: Actor,
    dojo,
    filename: str,
    dry_run: bool = True,
) -> ImportRun:
    """Import ``rows``, or show what importing them would do.

    The caller is responsible for having checked that ``actor`` may write to
    ``dojo`` — see ``students.require_import_permission``. This function does not
    re-derive permissions from the file.
    """
    validate_mapping(importer, mapping)

    organization_id = dojo.organization_id
    results: list[RowResult] = []

    def process_all() -> None:
        for offset, raw_row in enumerate(rows):
            # +2: the header occupies spreadsheet row 1, so the first data row
            # is row 2. Operators read these numbers against their own file.
            row_number = offset + 2
            try:
                row = apply_mapping(raw_row, mapping)
                source_key = importer.natural_key(row)
                if not source_key:
                    results.append(
                        RowResult(
                            row_number=row_number,
                            outcome=Outcome.SKIPPED,
                            detail=str(_("Not enough information to identify this row.")),
                        )
                    )
                    continue

                existing_id = _existing_object_id(
                    organization_id=organization_id,
                    entity_type=importer.entity_type,
                    source_key=source_key,
                )
                # ⚠ Per-row savepoint. On PostgreSQL a failed statement poisons
                # the surrounding transaction, so without this one bad row makes
                # every later row fail too.
                with transaction.atomic():
                    obj, outcome = importer.apply(
                        row, existing_id=existing_id, actor=actor, dojo=dojo
                    )
                    if obj is not None:
                        _remember(
                            organization_id=organization_id,
                            entity_type=importer.entity_type,
                            source_key=source_key,
                            object_id=obj.pk,
                        )
                results.append(
                    RowResult(
                        row_number=row_number,
                        outcome=outcome,
                        source_key=source_key,
                    )
                )
            except (ValidationError, ValueError, DatabaseError) as exc:
                results.append(
                    RowResult(
                        row_number=row_number,
                        outcome=Outcome.ERROR,
                        source_key=locals().get("source_key", "") or "",
                        detail=_describe(exc),
                    )
                )

    try:
        with transaction.atomic():
            process_all()
            if dry_run:
                raise _RollBackDryRun
    except _RollBackDryRun:
        pass

    tally = {
        outcome: sum(1 for result in results if result.outcome == outcome)
        for outcome in (Outcome.CREATED, Outcome.UPDATED, Outcome.SKIPPED, Outcome.ERROR)
    }

    # Written after the block above, so a dry run keeps its report.
    import_run = ImportRun.objects.for_organization(organization_id).create(
        organization_id=organization_id,
        dojo=dojo,
        kind=importer.kind,
        filename=filename[:255],
        is_dry_run=dry_run,
        mapping=mapping,
        row_count=len(rows),
        created_count=tally[Outcome.CREATED],
        updated_count=tally[Outcome.UPDATED],
        skipped_count=tally[Outcome.SKIPPED],
        error_count=tally[Outcome.ERROR],
        outcomes=[result.as_dict() for result in results],
    )

    audit.record(
        "import",
        actor=actor,
        subject=import_run,
        note=(
            f"{importer.kind} {'dry-run' if dry_run else 'import'} of {filename}: "
            f"{tally[Outcome.CREATED]} created, {tally[Outcome.UPDATED]} updated, "
            f"{tally[Outcome.SKIPPED]} skipped, {tally[Outcome.ERROR]} errored"
        ),
        strict=True,
    )
    return import_run


def _describe(exc: Exception) -> str:
    """A row error an operator can act on, not a traceback."""
    if isinstance(exc, ValidationError):
        parts: list[str] = []
        if hasattr(exc, "message_dict"):
            for field, messages in exc.message_dict.items():
                parts.append(f"{field}: {' '.join(str(m) for m in messages)}")
        else:
            parts.extend(str(message) for message in exc.messages)
        return "; ".join(parts)[:500]
    return str(exc)[:500]


def report_rows(import_run: ImportRun) -> list[list]:
    """The downloadable report of TODO 1.10.7."""
    return [
        [
            outcome.get("row_number", ""),
            outcome.get("outcome", ""),
            outcome.get("source_key", ""),
            outcome.get("detail", ""),
        ]
        for outcome in import_run.outcomes
    ]
