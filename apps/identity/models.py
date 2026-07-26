"""Organisations, dojos, people, users and roles — TODO 0.5.1 – 0.5.6.

Key modelling decision (plan §4.2): there is exactly one ``Person`` row per
human. Instructors are almost always also students; parents are often students
too. Roles attach to the Person rather than duplicating them across
Student/Instructor/Parent tables, because that duplication is very painful to
unwind later.

``User`` is separate from ``Person`` because most people in this system never
log in — a seven-year-old student has a Person record and no credentials.
"""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.ids import uuid7
from apps.core.managers import ScopedManager
from apps.core.models import BaseModel, SoftDeleteModel, TenantScopedModel
from apps.core.scoping import Actor


class GovernanceModel(models.TextChoices):
    """Plan §13.1 — how the organisation relates to its dojos.

    ``CENTRAL``   one business, several branches. The org sees everything.
    ``FEDERATED`` an association of independently owned dojos sharing a syllabus
                  and grading authority. The org sees ranks and gradings but
                  **not** each dojo's revenue or full student contact details.
    """

    CENTRAL = "central", _("Centrally managed branches")
    FEDERATED = "federated", _("Federation of independent dojos")


class Organization(BaseModel):
    """Tenant root."""

    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=100, unique=True)
    governance_model = models.CharField(
        _("governance model"),
        max_length=16,
        choices=GovernanceModel.choices,
        default=GovernanceModel.CENTRAL,
        help_text=_(
            "Determines whether the organisation can see member dojos' finances "
            "and full student records. Changing this after setup moves data "
            "ownership and must be done as a deliberate migration."
        ),
    )
    country = models.CharField(_("country"), max_length=2, default="KH")
    default_timezone = models.CharField(_("timezone"), max_length=64, default="Asia/Phnom_Penh")
    default_currency = models.CharField(_("currency"), max_length=3, default="USD")
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("organisation")
        verbose_name_plural = _("organisations")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def is_federated(self) -> bool:
        return self.governance_model == GovernanceModel.FEDERATED


class Dojo(TenantScopedModel):
    """A training location. Sub-dojos are rows, not tenants (plan §7.2)."""

    tenant_org_path = "organization_id"
    tenant_dojo_path = "id"

    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="dojos"
    )
    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=100)

    address_line1 = models.CharField(_("address"), max_length=255, blank=True)
    address_line2 = models.CharField(_("address line 2"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=100, blank=True)
    country = models.CharField(_("country"), max_length=2, blank=True)

    timezone = models.CharField(_("timezone"), max_length=64, default="Asia/Phnom_Penh")
    currency = models.CharField(_("currency"), max_length=3, default="USD")

    contact_email = models.EmailField(_("contact email"), blank=True)
    contact_phone = models.CharField(_("contact phone"), max_length=40, blank=True)

    is_active = models.BooleanField(_("active"), default=True)
    opened_on = models.DateField(_("opened on"), null=True, blank=True)
    closed_on = models.DateField(_("closed on"), null=True, blank=True)

    class Meta:
        verbose_name = _("dojo")
        verbose_name_plural = _("dojos")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"], name="unique_dojo_slug_per_org"
            )
        ]

    def __str__(self) -> str:
        return self.name


class Person(SoftDeleteModel):
    """One row per human. Never duplicated across roles (plan §4.2).

    Soft-delete, never hard-delete: a person's record is attached to attendance
    history, rank awards and invoices, all of which are evidence. Removing the
    row would orphan or destroy them. Erasure requests (SEC §6.4) are handled by
    the redaction path, not by DELETE.
    """

    tenant_org_path = "organization_id"

    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="people"
    )

    given_name = models.CharField(_("given name"), max_length=100)
    family_name = models.CharField(_("family name"), max_length=100, blank=True)
    preferred_name = models.CharField(_("preferred name"), max_length=100, blank=True)

    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True)

    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("phone"), max_length=40, blank=True)

    address_line1 = models.CharField(_("address"), max_length=255, blank=True)
    address_line2 = models.CharField(_("address line 2"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=100, blank=True)
    country = models.CharField(_("country"), max_length=2, blank=True)

    #: Per-person, not per-organisation (plan §13.4) — a Khmer-speaking parent and
    #: an English-speaking instructor in the same dojo each get their own.
    locale = models.CharField(_("language"), max_length=10, default="en")

    notes_summary = models.CharField(max_length=255, blank=True, editable=False)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("person")
        verbose_name_plural = _("people")
        ordering = ("family_name", "given_name")
        indexes = [
            models.Index(fields=["organization", "family_name", "given_name"]),
            models.Index(fields=["organization", "email"]),
        ]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.given_name, self.family_name) if part)

    @property
    def display_name(self) -> str:
        return self.preferred_name or self.given_name

    @property
    def age(self) -> int | None:
        if not self.date_of_birth:
            return None
        today = timezone.localdate()
        return (
            today.year
            - self.date_of_birth.year
            - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
        )

    @property
    def is_minor(self) -> bool | None:
        age = self.age
        return None if age is None else age < 18


class UserManager(BaseUserManager):
    use_in_migrations = False

    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("Users must have an email address")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not extra["is_superuser"]:
            raise ValueError("Superuser must have is_superuser=True")
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser):
    """Credentials only. Most people in the system have no User row.

    Django's Groups/Permissions framework is deliberately not used — authorisation
    lives in RoleAssignment plus apps/identity/permissions.py, which is
    org- and dojo-scoped in a way Django's flat permission model is not.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    email = models.EmailField(_("email"), unique=True)
    person = models.OneToOneField(
        Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="user"
    )

    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(_("staff"), default=False)
    is_superuser = models.BooleanField(_("superuser"), default=False)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    last_password_change = models.DateTimeField(null=True, blank=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self) -> str:
        return self.email

    def has_perm(self, perm, obj=None) -> bool:
        return self.is_superuser

    def has_module_perms(self, app_label) -> bool:
        return self.is_superuser


class Role(models.TextChoices):
    """Plan §3. Financial visibility is a separate bit, not implied by seniority."""

    ORG_ADMIN = "org_admin", _("Organisation administrator")
    DOJO_ADMIN = "dojo_admin", _("Dojo administrator / head instructor")
    INSTRUCTOR = "instructor", _("Instructor")
    ASSISTANT_INSTRUCTOR = "assistant_instructor", _("Assistant instructor")
    FRONT_DESK = "front_desk", _("Front desk")
    SAFEGUARDING = "safeguarding", _("Safeguarding officer")
    GUARDIAN = "guardian", _("Parent / guardian")
    STUDENT = "student", _("Adult student")


class ScopeType(models.TextChoices):
    ORG = "org", _("Organisation")
    DOJO = "dojo", _("Dojo")


class RoleAssignment(TenantScopedModel):
    """A (person, role, scope) triple. A person may hold several.

    "Instructor at Dojo A + Dojo Admin at Dojo B + Student at Dojo A" must be
    expressible (plan §3).
    """

    tenant_org_path = "organization_id"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="role_assignments"
    )
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="role_assignments"
    )
    role = models.CharField(_("role"), max_length=32, choices=Role.choices)
    scope_type = models.CharField(max_length=8, choices=ScopeType.choices)
    dojo = models.ForeignKey(
        Dojo,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="role_assignments",
    )

    #: Separate from role seniority on purpose (plan §3). In federated orgs this
    #: is never granted to org-scoped actors over member dojos.
    can_view_financials = models.BooleanField(_("may view financial data"), default=False)
    can_export_pii = models.BooleanField(_("may export personal data"), default=False)

    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("role assignment")
        verbose_name_plural = _("role assignments")
        constraints = [
            models.UniqueConstraint(
                fields=["person", "role", "scope_type", "dojo"],
                condition=models.Q(revoked_at__isnull=True),
                name="unique_active_role_assignment",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scope_type="dojo", dojo__isnull=False)
                    | models.Q(scope_type="org", dojo__isnull=True)
                ),
                name="role_scope_matches_dojo",
            ),
        ]

    def __str__(self) -> str:
        target = self.dojo.name if self.dojo else "organisation"
        return f"{self.person} — {self.get_role_display()} @ {target}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @classmethod
    def tenant_scope_q(cls, actor: Actor):
        # Role assignments are visible org-wide to anyone who can see them at all;
        # the permission layer decides who that is.
        return models.Q(organization_id=actor.organization_id)
