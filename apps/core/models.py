"""Base model layer — TODO 0.3.1, 0.3.2, 0.3.5."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .ids import uuid7
from .managers import (
    ScopedManager,
    SoftDeleteManager,
    dojo_scope_q,
    org_scope_q,
)
from .scoping import Actor


class BaseModel(models.Model):
    """UUIDv7 pk plus provenance columns. Everything inherits from this."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, editable=False)
    created_by = models.ForeignKey(
        "identity.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )

    class Meta:
        abstract = True


class TenantScopedModel(BaseModel):
    """A model whose rows belong to exactly one organisation.

    Subclasses declare how to reach the organisation (and optionally the dojo)
    from this table. Those paths drive automatic filtering in
    ``ScopedQuerySet.for_actor``.

    Example::

        class ClassTemplate(TenantScopedModel):
            tenant_org_path = "dojo__organization_id"
            tenant_dojo_path = "dojo_id"
    """

    #: ORM path from this model to the owning organisation's id.
    tenant_org_path: str = "organization_id"
    #: ORM path to the owning dojo's id, or None if the model is org-level only.
    tenant_dojo_path: str | None = None

    objects = ScopedManager()

    class Meta:
        abstract = True

    #: Foreign keys that must all resolve to the same organisation. The first
    #: name is the reference; the rest are checked against it.
    #:
    #: Scoping decides who may *read* a row; it says nothing about whether the
    #: row should have been creatable. A record pointing at two organisations is
    #: a tenant boundary violation baked into the data — scoping will faithfully
    #: show it to one side and hide it from the other, and neither view is right.
    #: Declare this on any model reached through an indirect tenant path::
    #:
    #:     same_organization_fields = ("person", "home_dojo")
    same_organization_fields: tuple[str, ...] = ()

    def save(self, *args, **kwargs):
        # Enforced on save, not only in full_clean(): most writes are service
        # code and fixtures that never call full_clean(), and this is the one
        # invariant that must not be bypassable by convenience.
        self.check_same_organization()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.check_same_organization()

    @classmethod
    def tenant_scope_q(cls, actor: Actor):
        q = org_scope_q(cls.tenant_org_path, actor)
        if cls.tenant_dojo_path:
            q &= dojo_scope_q(cls.tenant_dojo_path, actor)
        return q

    @staticmethod
    def _organization_of(obj):
        if obj is None:
            return None
        organization_id = getattr(obj, "organization_id", None)
        if organization_id is not None:
            return organization_id
        # An Organization is its own organisation.
        if obj.__class__.__name__ == "Organization":
            return obj.pk
        return None

    def check_same_organization(self) -> None:
        """Raise ValidationError if declared references span organisations."""
        if len(self.same_organization_fields) < 2:
            return

        from django.core.exceptions import ValidationError

        reference_name, *others = self.same_organization_fields
        reference_org = self._organization_of(getattr(self, reference_name, None))
        if reference_org is None:
            return

        for name in others:
            other_org = self._organization_of(getattr(self, name, None))
            if other_org is None or other_org == reference_org:
                continue
            raise ValidationError(
                {
                    name: _(
                        "%(field)s belongs to a different organisation than "
                        "%(reference)s. A record may not span two organisations."
                    )
                    % {"field": name, "reference": reference_name}
                }
            )


class SoftDeleteModel(TenantScopedModel):
    """Tenant-scoped model that is never hard-deleted — TODO 0.3.2, plan §2.

    Student records, attendance and financial history must survive deletion
    attempts: they are evidence. Soft delete keeps referential integrity and the
    audit trail intact.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    deleted_by = models.ForeignKey(
        "identity.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )

    objects = SoftDeleteManager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, actor: Actor | None = None) -> None:
        self.deleted_at = timezone.now()
        if actor is not None and actor.person_id:
            self.deleted_by_id = actor.person_id
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])

    def restore(self) -> None:
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])


class Setting(TenantScopedModel):
    """A setting value bound to one level of the hierarchy — TODO 0.3.7, plan §13.2.

    Rows here are *overrides*. An absent row means "inherit"; the declared
    default in apps/core/setting_registry.py is the floor. Resolution order and
    per-key merge rules live in apps/core/setting_resolver.py — never
    reimplement them at a call site.
    """

    tenant_org_path = "organization_id"

    organization = models.ForeignKey(
        "identity.Organization", on_delete=models.CASCADE, related_name="settings"
    )
    scope_type = models.CharField(_("scope"), max_length=20)
    #: Null at organisation scope; otherwise the dojo/template/session/student id.
    scope_id = models.UUIDField(null=True, blank=True)
    key = models.CharField(_("key"), max_length=100, db_index=True)
    value = models.JSONField(_("value"))

    class Meta:
        verbose_name = _("setting")
        verbose_name_plural = _("settings")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "scope_type", "scope_id", "key"],
                name="unique_setting_per_scope",
            ),
        ]
        indexes = [models.Index(fields=["organization", "key"])]

    def __str__(self) -> str:
        target = self.scope_id or "org"
        return f"{self.key}={self.value!r} @ {self.scope_type}:{target}"


class OrganizationDataKey(models.Model):
    """One wrapped data-encryption key per organisation — TODO 0.3.8 / SEC 2.3.

    Stores the DEK *wrapped* by the master key, which lives outside the database.
    A stolen dump therefore yields no readable ciphertext. Rotating the master key
    rewraps these few rows rather than re-encrypting every protected column.

    Not a TenantScopedModel: it is infrastructure keyed by organisation, read by
    the encryption layer before any actor exists, and never exposed to a request.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    organization = models.OneToOneField(
        "identity.Organization", on_delete=models.CASCADE, related_name="data_key"
    )
    wrapped_key = models.BinaryField(editable=False)
    master_key_version = models.PositiveIntegerField(editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    rotated_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("organisation data key")
        verbose_name_plural = _("organisation data keys")

    def __str__(self) -> str:
        return f"data key for {self.organization_id} (master v{self.master_key_version})"

    def delete(self, *args, **kwargs):
        raise NotImplementedError(
            "Deleting a data key permanently destroys every encrypted value for this "
            "organisation. If that is genuinely intended (offboarding, erasure), do it "
            "explicitly in a management command."
        )


class AuditLog(models.Model):
    """Append-only record of every state change — TODO 0.3.5 / SEC 2.6.

    Deliberately not a TenantScopedModel: it must be writable in contexts where
    scoping is not yet established (login attempts, system jobs), and it is
    read only through explicitly audited admin paths.
    """

    class Action(models.TextChoices):
        CREATE = "create", _("Created")
        UPDATE = "update", _("Updated")
        DELETE = "delete", _("Deleted")
        VIEW = "view", _("Viewed")
        LOGIN = "login", _("Signed in")
        LOGIN_FAILED = "login_failed", _("Sign-in failed")
        EXPORT = "export", _("Exported")
        PERMISSION_CHANGE = "permission_change", _("Permission changed")

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    at = models.DateTimeField(default=timezone.now, db_index=True, editable=False)

    organization = models.ForeignKey(
        "identity.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    actor_person = models.ForeignKey(
        "identity.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    actor_label = models.CharField(max_length=255, blank=True)

    action = models.CharField(max_length=32, choices=Action.choices)
    subject_type = models.CharField(max_length=100, blank=True, db_index=True)
    subject_id = models.CharField(max_length=64, blank=True, db_index=True)

    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    note = models.CharField(max_length=512, blank=True)

    class Meta:
        verbose_name = _("audit log entry")
        verbose_name_plural = _("audit log entries")
        ordering = ("-at",)
        indexes = [
            models.Index(fields=["organization", "-at"]),
            models.Index(fields=["subject_type", "subject_id", "-at"]),
        ]

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.subject_type}:{self.subject_id}"

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Audit log entries are append-only.")
