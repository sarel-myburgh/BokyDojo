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

from apps.core.fields import EncryptedCharField, EncryptedTextField
from apps.core.ids import uuid7
from apps.core.managers import ScopedManager, ScopedQuerySet
from apps.core.models import BaseModel, SoftDeleteModel, TenantScopedModel
from apps.core.scoping import Actor

from .student_filters import validate_saved_student_filters


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

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="dojos")
    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=100)

    address_line1 = models.CharField(_("address"), max_length=255, blank=True)
    address_line2 = models.CharField(_("address line 2"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=100, blank=True)
    country = models.CharField(_("country"), max_length=2, blank=True)

    timezone = models.CharField(_("timezone"), max_length=64, default="Asia/Phnom_Penh")
    currency = models.CharField(_("currency"), max_length=3, default="USD")
    #: What this dojo teaches. Enrolling a student here gives them a style track
    #: per entry, so this is not decoration — it is what decides which arts a
    #: member is recorded as training, and therefore which ranks they can hold.
    #: ⚠ M2M, so ``same_organization_fields`` cannot police it; the services that
    #: write it check every style belongs to this dojo's organisation.
    styles = models.ManyToManyField(
        "ranks.Style",
        blank=True,
        related_name="dojos",
        verbose_name=_("styles taught"),
    )

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

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="people")

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
    #: Set when an administrator issues a temporary password — TODO 0.6.8.
    #: ⚠ Enforced by middleware on every request, not merely suggested at login.
    #: A temporary password an administrator knows must stop working as soon as
    #: the person it was given to has used it, or it is simply a second password
    #: on the account that somebody else also holds.
    must_change_password = models.BooleanField(
        _("must change password"),
        default=False,
        editable=False,
    )

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
    #: ⚠ Without this, a person from another organisation could be granted a
    #: role here — the most direct privilege-escalation route in the schema.
    same_organization_fields = ("organization", "person", "dojo")

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="role_assignments"
    )
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="role_assignments")
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


class MfaCredential(TenantScopedModel):
    """Per-user TOTP seed and hashed one-time recovery codes.

    The seed is encrypted with the owning organisation's envelope key. Recovery
    codes are never stored directly; only keyed digests are persisted.
    """

    tenant_org_path = "organization_id"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="mfa_credentials"
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="mfa_credential")
    totp_secret = EncryptedCharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    recovery_code_hashes = models.JSONField(default=list, blank=True)
    last_used_counter = models.BigIntegerField(null=True, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("multi-factor credential")
        verbose_name_plural = _("multi-factor credentials")

    def check_same_organization(self) -> None:
        """A login may only hold a credential for its Person's organisation."""
        if not self.user_id or not self.organization_id:
            return
        from django.core.exceptions import ValidationError

        user_org_id = (
            User.objects.filter(pk=self.user_id)
            .values_list("person__organization_id", flat=True)
            .first()
        )
        if user_org_id is None:
            raise ValidationError(
                {"user": _("A user needs an organisation-linked person before enabling MFA.")}
            )
        if user_org_id != self.organization_id:
            raise ValidationError({"user": _("The user belongs to a different organisation.")})

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class InstructorAssignment(TenantScopedModel):
    """A Person teaching at a Dojo — plan §4.3."""

    tenant_org_path = "dojo__organization_id"
    tenant_dojo_path = "dojo_id"
    #: An instructor from another organisation must not be assignable here.
    same_organization_fields = ("dojo", "person")

    dojo = models.ForeignKey(Dojo, on_delete=models.PROTECT, related_name="instructor_assignments")
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
    #: A student in one organisation must not have a home dojo in another —
    #: the two tenant paths would disagree about who owns the row.
    same_organization_fields = ("person", "home_dojo")

    class Status(models.TextChoices):
        PROSPECT = "prospect", _("Prospect")
        TRIAL = "trial", _("Trial")
        ACTIVE = "active", _("Active")
        ON_HOLD = "on_hold", _("On hold")
        LAPSED = "lapsed", _("Lapsed")
        ALUMNI = "alumni", _("Alumni")

    person = models.OneToOneField(Person, on_delete=models.CASCADE, related_name="student_profile")
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
    hold_reason = EncryptedCharField(_("hold reason"), max_length=200, blank=True)

    # Special-category health data. Text fields are encrypted with the owning
    # organisation's data key; the operational do-not-spar flag is deliberately
    # minimal and plaintext so a roster can surface it without searching cipher text.
    medical_notes = EncryptedTextField(_("medical notes"), blank=True)
    allergies = EncryptedTextField(_("allergies"), blank=True)
    conditions = EncryptedTextField(_("medical conditions"), blank=True)
    medications = EncryptedTextField(_("medications"), blank=True)
    doctor_contact = EncryptedCharField(_("doctor contact"), max_length=255, blank=True)
    do_not_spar = models.BooleanField(_("do not spar"), default=False)

    shirt_size = models.CharField(_("shirt size"), max_length=20, blank=True)
    gi_size = models.CharField(_("gi size"), max_length=20, blank=True)

    federation_licence_no = models.CharField(
        _("federation licence number"), max_length=100, blank=True
    )
    licence_expires_on = models.DateField(_("licence expires on"), null=True, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("student profile")
        verbose_name_plural = _("student profiles")

    def __str__(self) -> str:
        return f"Student: {self.person}"

    @property
    def organization_id(self):
        """Owning tenant for encryption, permissions, and audit attribution."""
        return self.person.organization_id

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def is_training(self) -> bool:
        return self.status in (self.Status.ACTIVE, self.Status.TRIAL)


class StudentSegment(TenantScopedModel):
    """A named, reusable student-directory filter owned by one staff member."""

    tenant_org_path = "organization_id"
    same_organization_fields = ("organization", "owner")

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="student_segments"
    )
    owner = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="student_segments")
    name = models.CharField(_("name"), max_length=80)
    filters = models.JSONField(
        _("filters"),
        default=dict,
        validators=[validate_saved_student_filters],
    )

    objects = ScopedManager()

    class Meta:
        verbose_name = _("student segment")
        verbose_name_plural = _("student segments")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "owner", "name"],
                name="unique_student_segment_name_per_owner",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)


class GuardianLink(TenantScopedModel):
    """Links a guardian Person to a student Person — TODO 1.1.3, plan §4.2.

    A student may have 0..n guardians. The four boolean flags are deliberately
    independent: divorced and separated families are common. The parent who pays
    may not be the emergency contact; custody is separate from both.
    """

    tenant_org_path = "student__organization_id"
    tenant_dojo_path = "student__student_profile__home_dojo_id"
    #: The tenant path runs through the student, so a guardian from another
    #: organisation would be a one-way window into that tenant's people.
    same_organization_fields = ("student", "guardian")

    class Relationship(models.TextChoices):
        MOTHER = "mother", _("Mother")
        FATHER = "father", _("Father")
        GUARDIAN = "guardian", _("Guardian")
        GRANDPARENT = "grandparent", _("Grandparent")
        SIBLING = "sibling", _("Sibling")
        OTHER = "other", _("Other")

    guardian = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="guarded_links")
    student = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="guardian_links")
    relationship = models.CharField(_("relationship"), max_length=16, choices=Relationship.choices)

    is_primary_contact = models.BooleanField(_("primary contact"), default=False)
    is_emergency_contact = models.BooleanField(_("emergency contact"), default=False)
    is_financially_responsible = models.BooleanField(_("financially responsible"), default=False)
    has_custody = models.BooleanField(_("has custody"), default=False)

    notes = EncryptedCharField(_("notes"), max_length=255, blank=True)

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

    @property
    def organization_id(self):
        """Owning tenant for encrypted safeguarding notes."""
        return self.student.organization_id

    def __str__(self) -> str:
        return f"{self.guardian} → {self.student} ({self.get_relationship_display()})"


class AppendOnlyConsentQuerySet(ScopedQuerySet):
    """Consent evidence is superseded by a new row, never rewritten or deleted."""

    def update(self, **kwargs):
        raise NotImplementedError("Consent records are append-only; add a superseding record.")

    def delete(self):
        raise NotImplementedError("Consent records are append-only and cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise NotImplementedError("Consent records must be created through the consent service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise NotImplementedError("Consent records are append-only and cannot be updated.")


class ConsentManager(ScopedManager.from_queryset(AppendOnlyConsentQuerySet)):
    use_in_migrations = False


class ConsentRecord(TenantScopedModel):
    """One versioned consent decision — TODO 1.1.6, plan §4.2."""

    tenant_org_path = "person__organization_id"
    same_organization_fields = ("person", "granted_by", "document", "policy", "supersedes")

    class Type(models.TextChoices):
        PHOTO = "photo", _("Photo and video")
        MARKETING = "marketing", _("Marketing")
        DATA_PROCESSING = "data_processing", _("Data processing")
        MEDICAL = "medical", _("Medical data")
        WAIVER = "waiver", _("Liability waiver")

    class Capacity(models.TextChoices):
        SELF = "self", _("Self")
        PARENT = "parent", _("Parent")
        GUARDIAN = "guardian", _("Legal guardian")

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="consent_records")
    consent_type = models.CharField(_("consent type"), max_length=24, choices=Type.choices)
    version = models.CharField(_("version"), max_length=64)
    granted = models.BooleanField(_("granted"))
    granted_at = models.DateTimeField(_("recorded at"), default=timezone.now)
    granted_by = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="consents_given",
    )
    capacity = models.CharField(_("capacity"), max_length=16, choices=Capacity.choices)
    ip_address = models.GenericIPAddressField(_("IP address"))
    user_agent = models.CharField(_("user agent"), max_length=512, blank=True)
    signature_name = EncryptedCharField(_("signature name"), max_length=200, blank=True)
    document = models.ForeignKey(
        "core.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="consent_records",
    )
    policy = models.ForeignKey(
        "ConsentPolicy",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="consent_records",
    )
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
    )

    objects = ConsentManager()

    class Meta:
        verbose_name = _("consent record")
        verbose_name_plural = _("consent records")
        ordering = ("-granted_at", "-created_at")
        indexes = [
            models.Index(fields=["person", "consent_type", "version", "-granted_at"]),
        ]

    @property
    def organization_id(self):
        return self.person.organization_id

    def clean(self):
        super().clean()
        errors = {}
        if self.supersedes_id:
            previous = self.supersedes
            if (
                previous.person_id != self.person_id
                or previous.consent_type != self.consent_type
                or previous.version != self.version
            ):
                errors["supersedes"] = _(
                    "A consent may only supersede the same person, type, and version."
                )
        if self.document_id:
            if self.document.subject_person_id not in (None, self.person_id):
                errors["document"] = _("The document belongs to a different person.")
            if self.consent_type == self.Type.WAIVER and self.document.kind != "waiver":
                errors["document"] = _("Waiver consent requires a waiver document.")
        if self.policy_id:
            if self.policy.consent_type != self.consent_type or self.policy.version != self.version:
                errors["policy"] = _("The policy type and version do not match the decision.")
            if self.document_id != self.policy.document_id:
                errors["document"] = _("The evidence document does not match the policy.")
        if errors:
            from django.core.exceptions import ValidationError

            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise NotImplementedError("Consent records are append-only; add a superseding record.")
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Consent records are append-only and cannot be deleted.")

    def __str__(self) -> str:
        decision = _("granted") if self.granted else _("revoked")
        return f"{self.person} — {self.get_consent_type_display()} {self.version}: {decision}"


class ConsentPolicyQuerySet(ScopedQuerySet):
    def update(self, **kwargs):
        if set(kwargs) - {"is_active", "updated_at"}:
            raise NotImplementedError(
                "Published consent policy content is immutable; create a new version."
            )
        return super().update(**kwargs)

    def delete(self):
        raise NotImplementedError("Published consent policies cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise NotImplementedError("Consent policies must be validated individually.")

    def bulk_update(self, objs, fields, **kwargs):
        raise NotImplementedError("Published consent policies cannot be bulk-updated.")


class ConsentPolicyManager(ScopedManager.from_queryset(ConsentPolicyQuerySet)):
    use_in_migrations = False


class ConsentPolicy(TenantScopedModel):
    """Organisation-authored wording for one exact consent version."""

    tenant_org_path = "organization_id"
    same_organization_fields = ("organization", "document")

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="consent_policies"
    )
    consent_type = models.CharField(
        _("consent type"), max_length=24, choices=ConsentRecord.Type.choices
    )
    version = models.CharField(_("version"), max_length=64)
    title = models.CharField(_("title"), max_length=200)
    body = models.TextField(
        _("document text"),
        blank=True,
        help_text=_("Plain text shown verbatim to the signer."),
    )
    document = models.ForeignKey(
        "core.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="consent_policies",
    )
    published_at = models.DateTimeField(_("published at"), default=timezone.now)
    is_active = models.BooleanField(_("active"), default=True)

    objects = ConsentPolicyManager()

    class Meta:
        verbose_name = _("consent policy")
        verbose_name_plural = _("consent policies")
        ordering = ("consent_type", "-published_at")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "consent_type", "version"],
                name="unique_consent_policy_version",
            ),
            models.UniqueConstraint(
                fields=["organization", "consent_type"],
                condition=Q(is_active=True),
                name="unique_active_consent_policy",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if not self.body.strip() and self.document_id is None:
            errors["body"] = _("Enter document text or attach a document.")
        if self.document_id and self.document.kind != "waiver":
            errors["document"] = _("Consent policy attachments must be waiver documents.")
        if errors:
            from django.core.exceptions import ValidationError

            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = ConsentPolicy.objects.for_organization(self.organization_id).get(pk=self.pk)
            immutable = (
                "organization_id",
                "consent_type",
                "version",
                "title",
                "body",
                "document_id",
            )
            if any(getattr(original, field) != getattr(self, field) for field in immutable):
                raise NotImplementedError(
                    "Published consent policy content is immutable; create a new version."
                )
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Published consent policies cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.get_consent_type_display()} {self.version}: {self.title}"


class EmergencyContact(TenantScopedModel):
    """Emergency contact who may not be a system Person — TODO 1.1.5.

    For neighbours, aunts, family friends — anyone the dojo needs to call
    who does not have (and should not need) a Person row in the system.
    """

    tenant_org_path = "person__organization_id"
    tenant_dojo_path = "person__student_profile__home_dojo_id"

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="emergency_contacts")
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


class Enrollment(TenantScopedModel):
    """A student's membership of one dojo — TODO 1.3.1/1.3.2, plan §4.3.

    A student has one primary enrolment and zero or more additional active ones
    (plan §4.3): training at two branches is normal, and a seminar visit is not
    an enrolment at all — it is an attendance record flagged ``visiting``.

    ⚠ Enrolments are **never mutated to move a student**. Ending one and opening
    another is what keeps attendance, invoices and time entries attached to the
    dojo where they actually happened. See ``apps.identity.enrolment.transfer_student``.
    """

    tenant_org_path = "dojo__organization_id"
    tenant_dojo_path = "dojo_id"
    #: A student from one organisation must not be enrolled at another's dojo.
    same_organization_fields = ("dojo", "student")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        ON_HOLD = "on_hold", _("On hold")
        ENDED = "ended", _("Ended")

    student = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="enrollments")
    dojo = models.ForeignKey(Dojo, on_delete=models.PROTECT, related_name="enrollments")

    #: The student's home dojo — drives default billing, reporting attribution
    #: and "which dojo owns this student". Denormalised onto
    #: ``StudentProfile.home_dojo`` as well, because that field is a tenant
    #: scoping path and cannot be replaced by a join.
    is_primary = models.BooleanField(_("primary dojo"), default=False)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    started_on = models.DateField(_("started on"))
    ended_on = models.DateField(_("ended on"), null=True, blank=True)
    hold_reason = EncryptedCharField(_("hold reason"), max_length=200, blank=True)
    notes = models.CharField(_("notes"), max_length=255, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("enrolment")
        verbose_name_plural = _("enrolments")
        ordering = ("-started_on",)
        constraints = [
            # A student cannot hold two live enrolments at the same dojo. Ended
            # ones are unconstrained: re-joining later is normal and each stay
            # keeps its own dates.
            models.UniqueConstraint(
                fields=["student", "dojo"],
                condition=models.Q(ended_on__isnull=True),
                name="unique_live_enrollment_per_dojo",
            ),
            # Exactly one home dojo at a time.
            models.UniqueConstraint(
                fields=["student"],
                condition=models.Q(is_primary=True, ended_on__isnull=True),
                name="unique_primary_enrollment_per_student",
            ),
            # "Ended" and "has an end date" must not drift apart — reports filter
            # on one and humans read the other.
            models.CheckConstraint(
                condition=(
                    models.Q(status="ended", ended_on__isnull=False)
                    | (~models.Q(status="ended") & models.Q(ended_on__isnull=True))
                ),
                name="enrollment_ended_on_matches_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ended_on__isnull=True) | models.Q(ended_on__gte=models.F("started_on"))
                ),
                name="enrollment_ended_on_gte_started_on",
            ),
        ]
        indexes = [models.Index(fields=["dojo", "status"])]

    def __str__(self) -> str:
        marker = " (primary)" if self.is_primary else ""
        return f"{self.student} @ {self.dojo}{marker}"

    @property
    def organization_id(self):
        """Owning tenant for encrypted enrolment fields."""
        return self.dojo.organization_id

    @property
    def is_live(self) -> bool:
        """Still a member — includes holds, which are members who aren't training."""
        return self.ended_on is None

    @property
    def is_training(self) -> bool:
        return self.status == self.Status.ACTIVE

    def end(self, on, *, reason: str = "") -> None:
        self.status = self.Status.ENDED
        self.ended_on = on
        self.is_primary = False
        if reason:
            self.notes = reason[:255]
        self.save(update_fields=["status", "ended_on", "is_primary", "notes", "updated_at"])


class TransferRecord(TenantScopedModel):
    """A student's move from one dojo to another — TODO 1.3.3, plan §4.3.

    The audit log records that two enrolment rows changed. This records the
    *fact* of a transfer as a first-class thing the business can report on and a
    receiving dojo can point at.
    """

    #: Either dojo can be the one an actor is scoped to, so the org path is
    #: taken from the origin and ``tenant_scope_q`` is widened below.
    tenant_org_path = "from_dojo__organization_id"
    same_organization_fields = ("student", "from_dojo", "to_dojo", "approved_by")

    student = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="transfers")
    from_dojo = models.ForeignKey(Dojo, on_delete=models.PROTECT, related_name="transfers_out")
    to_dojo = models.ForeignKey(Dojo, on_delete=models.PROTECT, related_name="transfers_in")
    effective_on = models.DateField(_("effective on"))
    reason = models.CharField(_("reason"), max_length=255, blank=True)
    approved_by = models.ForeignKey(
        Person,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_transfers",
    )

    objects = ScopedManager()

    class Meta:
        verbose_name = _("transfer record")
        verbose_name_plural = _("transfer records")
        ordering = ("-effective_on",)
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(from_dojo=models.F("to_dojo")),
                name="transfer_between_different_dojos",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student}: {self.from_dojo} → {self.to_dojo} ({self.effective_on})"

    @classmethod
    def tenant_scope_q(cls, actor: Actor):
        """A transfer is visible to both ends of it.

        The generic single-path dojo filter cannot express that, and picking one
        side would hide arrivals from the receiving dojo — which is precisely
        the dojo that needs to see them.
        """
        q = models.Q(from_dojo__organization_id=actor.organization_id)
        if actor.dojo_ids is not None:
            dojo_ids = list(actor.dojo_ids)
            q &= models.Q(from_dojo_id__in=dojo_ids) | models.Q(to_dojo_id__in=dojo_ids)
        return q
