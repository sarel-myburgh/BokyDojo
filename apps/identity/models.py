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
from django.db.models import Q, UniqueConstraint
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


class InstructorAssignment(TenantScopedModel):
    """A Person teaching at a Dojo — plan §4.3."""

    tenant_org_path = "dojo__organization_id"
    tenant_dojo_path = "dojo_id"

    dojo = models.ForeignKey(
        Dojo, on_delete=models.PROTECT, related_name="instructor_assignments"
    )
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="instructor_assignments"
    )
    is_head_instructor = models.BooleanField(_("head instructor"), default=False)
    started_on = models.DateField(_("started on"))
    ended_on = models.DateField(_("ended on"), null=True, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("instructor assignment")
        verbose_name_plural = _("instructor assignments")
        constraints = [
            models.UniqueConstraint(
                fields=["person", "dojo"],
                condition=models.Q(ended_on__isnull=True),
                name="unique_active_instructor_assignment",
            ),
        ]

    def __str__(self) -> str:
        role = _("Head instructor") if self.is_head_instructor else _("Instructor")
        return f"{self.person} — {role} @ {self.dojo}"

    @property
    def is_active(self) -> bool:
        return self.ended_on is None


class StudentProfile(TenantScopedModel):
    """Student-specific data hanging off a Person — TODO 1.1.1, plan §4.2.

    One-to-one with Person. A person who is a student at one or more dojos
    gets exactly one StudentProfile row.
    """

    tenant_org_path = "person__organization_id"
    tenant_dojo_path = "home_dojo_id"

    class Status(models.TextChoices):
        PROSPECT = "prospect", _("Prospect")
        TRIAL = "trial", _("Trial")
        ACTIVE = "active", _("Active")
        ON_HOLD = "on_hold", _("On hold")
        LAPSED = "lapsed", _("Lapsed")
        ALUMNI = "alumni", _("Alumni")

    person = models.OneToOneField(
        Person, on_delete=models.CASCADE, related_name="student_profile"
    )
    home_dojo = models.ForeignKey(
        Dojo,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="home_students",
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=Status.choices,
        default=Status.PROSPECT,
        db_index=True,
    )
    joined_on = models.DateField(_("joined on"), null=True, blank=True)
    hold_reason = models.CharField(_("hold reason"), max_length=200, blank=True)

    shirt_size = models.CharField(_("shirt size"), max_length=20, blank=True)
    gi_size = models.CharField(_("gi size"), max_length=20, blank=True)

    federation_licence_no = models.CharField(
        _("federation licence number"), max_length=100, blank=True
    )
    licence_expires_on = models.DateField(
        _("licence expires on"), null=True, blank=True
    )

    objects = ScopedManager()

    class Meta:
        verbose_name = _("student profile")
        verbose_name_plural = _("student profiles")

    def __str__(self) -> str:
        return f"Student: {self.person}"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def is_training(self) -> bool:
        return self.status in (self.Status.ACTIVE, self.Status.TRIAL)


class GuardianLink(TenantScopedModel):
    """Links a guardian Person to a student Person — TODO 1.1.3, plan §4.2.

    A student may have 0..n guardians. The four boolean flags are deliberately
    independent: divorced and separated families are common. The parent who pays
    may not be the emergency contact; custody is separate from both.
    """

    tenant_org_path = "student__organization_id"

    class Relationship(models.TextChoices):
        MOTHER = "mother", _("Mother")
        FATHER = "father", _("Father")
        GUARDIAN = "guardian", _("Guardian")
        GRANDPARENT = "grandparent", _("Grandparent")
        SIBLING = "sibling", _("Sibling")
        OTHER = "other", _("Other")

    guardian = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="guarded_links"
    )
    student = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="guardian_links"
    )
    relationship = models.CharField(
        _("relationship"), max_length=16, choices=Relationship.choices
    )

    is_primary_contact = models.BooleanField(_("primary contact"), default=False)
    is_emergency_contact = models.BooleanField(_("emergency contact"), default=False)
    is_financially_responsible = models.BooleanField(
        _("financially responsible"), default=False
    )
    has_custody = models.BooleanField(_("has custody"), default=False)

    notes = models.CharField(_("notes"), max_length=255, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("guardian link")
        verbose_name_plural = _("guardian links")
        constraints = [
            UniqueConstraint(fields=["guardian", "student"], name="unique_guardian_student"),
            models.CheckConstraint(
                condition=~Q(guardian=models.F("student")),
                name="guardian_may_not_be_own_student",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.guardian} → {self.student} ({self.get_relationship_display()})"


class EmergencyContact(TenantScopedModel):
    """Emergency contact who may not be a system Person — TODO 1.1.5.

    For neighbours, aunts, family friends — anyone the dojo needs to call
    who does not have (and should not need) a Person row in the system.
    """

    tenant_org_path = "person__organization_id"

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="emergency_contacts"
    )
    name = models.CharField(_("name"), max_length=200)
    phone = models.CharField(_("phone"), max_length=40)
    relationship = models.CharField(_("relationship"), max_length=100, blank=True)
    priority = models.PositiveSmallIntegerField(
        _("priority"),
        default=1,
        help_text=_("1 = try first"),
    )

    objects = ScopedManager()

    class Meta:
        verbose_name = _("emergency contact")
        verbose_name_plural = _("emergency contacts")
        ordering = ("priority",)

    def __str__(self) -> str:
        return f"{self.name} ({self.phone})"
