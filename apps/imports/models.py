"""Import bookkeeping — TODO 1.10.2, 1.10.7, plan §12.10.

Every prospective customer arrives carrying a mess: a Gymdesk or Zen Planner
export, three spreadsheets, or a paper folder. The plan is blunt that a good
importer is a sales weapon rather than a chore, and that the thing which makes it
one is being able to **fix and re-run** — a botched attempt corrected, not
restarted from a wiped database.

Two models carry that:

``ImportRun`` is what happened, kept whether the run was a dry run or real, so
"what would this do" and "what did this do" are answered from the same record.

``ImportedRecord`` is the map from the source system's key to the row we made for
it. ⚠ It lives here, not as an ``external_id`` column on Person and every other
model, because import is not a property of a student — it is a property of how
one particular student arrived. Five importers share one mechanism and the domain
models stay clean.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.managers import ScopedManager
from apps.core.models import TenantScopedModel


class ImportKind(models.TextChoices):
    STUDENTS = "students", _("students and guardians")
    ATTENDANCE = "attendance", _("historical attendance")
    RANKS = "ranks", _("rank history")


class ImportRun(TenantScopedModel):
    """One attempt at importing a file — TODO 1.10.7."""

    tenant_org_path = "organization_id"
    tenant_dojo_path = "dojo_id"
    same_organization_fields = ("organization", "dojo")

    class Status(models.TextChoices):
        COMPLETED = "completed", _("completed")
        FAILED = "failed", _("failed")

    organization = models.ForeignKey(
        "identity.Organization",
        on_delete=models.CASCADE,
        related_name="import_runs",
    )
    #: The dojo the imported people belong to. Required: a student without a
    #: dojo cannot be enrolled, and "which dojo" is not something to guess from
    #: a spreadsheet column.
    dojo = models.ForeignKey(
        "identity.Dojo",
        on_delete=models.PROTECT,
        related_name="import_runs",
    )
    kind = models.CharField(_("kind"), max_length=16, choices=ImportKind.choices)
    filename = models.CharField(_("filename"), max_length=255)
    #: ⚠ A dry run writes an ImportRun and nothing else. The row work happens
    #: inside a transaction that is deliberately rolled back, so the counts below
    #: are what *would* have happened rather than a separate estimate.
    is_dry_run = models.BooleanField(_("dry run"), default=True)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=Status.choices,
        default=Status.COMPLETED,
    )
    #: Source column name → importer field name, as chosen by the operator.
    mapping = models.JSONField(_("column mapping"), default=dict, blank=True)

    row_count = models.PositiveIntegerField(_("rows"), default=0)
    created_count = models.PositiveIntegerField(_("created"), default=0)
    updated_count = models.PositiveIntegerField(_("updated"), default=0)
    skipped_count = models.PositiveIntegerField(_("skipped"), default=0)
    error_count = models.PositiveIntegerField(_("errors"), default=0)

    #: Per-row outcome, in file order — the downloadable report of 1.10.7.
    #: ⚠ Deliberately not a related table. The report is always read whole and
    #: always for one run; a row table would buy nothing and cost a join per
    #: read plus a cascade per delete.
    outcomes = models.JSONField(_("row outcomes"), default=list, blank=True)

    #: Set when the whole run failed before rows were processed — an unreadable
    #: file, a refused mapping. Row-level problems live in ``outcomes``.
    failure_reason = models.CharField(_("failure reason"), max_length=255, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("import run")
        verbose_name_plural = _("import runs")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        mode = _("dry run") if self.is_dry_run else _("import")
        return f"{self.get_kind_display()} {mode} — {self.filename}"

    @property
    def wrote_anything(self) -> bool:
        return not self.is_dry_run and (self.created_count or self.updated_count)


class ImportedRecord(TenantScopedModel):
    """Maps a source system's row key to the record created from it — TODO 1.10.2.

    ⚠ This is what makes a re-import an update rather than a duplicate. Without
    it the second run of a corrected file produces a second copy of every
    student, which is precisely the failure the plan says must not happen.
    """

    tenant_org_path = "organization_id"

    organization = models.ForeignKey(
        "identity.Organization",
        on_delete=models.CASCADE,
        related_name="imported_records",
    )
    #: Which importer produced this — students, attendance, ranks. Keeps the key
    #: spaces apart so a student and a rank award may share a source id.
    entity_type = models.CharField(_("entity type"), max_length=32)
    #: The identity taken from the file: an explicit external id where the source
    #: had one, otherwise a derived natural key. See ``students.natural_key``.
    source_key = models.CharField(_("source key"), max_length=255)
    #: The primary key of the record this became. Not a ForeignKey: the target is
    #: a different model per entity_type, and a generic relation would buy
    #: cascade behaviour that would be wrong anyway — deleting a student should
    #: not silently make their old import key reusable.
    object_id = models.UUIDField(_("object id"))

    objects = ScopedManager()

    class Meta:
        verbose_name = _("imported record")
        verbose_name_plural = _("imported records")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "entity_type", "source_key"],
                name="unique_import_key_per_org_entity",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "entity_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type}:{self.source_key} → {self.object_id}"
