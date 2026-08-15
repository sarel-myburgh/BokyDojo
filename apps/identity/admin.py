"""Django admin for identity models."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ScopedModelAdmin, StaffModelAdmin
from apps.identity.models import (
    ConsentPolicy,
    Dojo,
    EmergencyContact,
    Enrollment,
    GuardianLink,
    InstructorAssignment,
    Organization,
    Person,
    RoleAssignment,
    StudentProfile,
    TransferRecord,
    User,
)
from apps.identity.permissions import Action, can


@admin.register(Organization)
class OrganizationAdmin(StaffModelAdmin):
    list_display = (
        "name",
        "slug",
        "governance_model",
        "country",
        "default_currency",
        "is_active",
    )
    list_filter = ("governance_model", "is_active", "country")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "created_by")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        actor = getattr(request, "actor", None)
        if actor is not None and actor.organization_id is not None:
            return qs.filter(pk=actor.organization_id)
        return qs.none()


@admin.register(Dojo)
class DojoAdmin(ScopedModelAdmin):
    list_display = ("name", "organization", "city", "timezone", "currency", "is_active")
    list_filter = ("is_active", "organization", "country")
    search_fields = ("name", "slug", "city")
    autocomplete_fields = ("organization",)
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(Person)
class PersonAdmin(ScopedModelAdmin):
    list_display = (
        "family_name",
        "given_name",
        "organization",
        "email",
        "phone",
        "is_active",
    )
    list_filter = ("is_active", "organization", "locale")
    search_fields = ("given_name", "family_name", "preferred_name", "email", "phone")
    autocomplete_fields = ("organization",)
    readonly_fields = (
        "notes_summary",
        "deleted_at",
        "deleted_by",
        "created_at",
        "updated_at",
        "created_by",
    )
    actions = ("soft_delete_selected",)

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Hard bulk-delete would raise on SoftDeleteQuerySet; replace with soft delete.
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description=_("Soft-delete selected people"))
    def soft_delete_selected(self, request, queryset):
        actor = getattr(request, "actor", None)
        for person in queryset:
            person.soft_delete(actor=actor)

    def delete_model(self, request, obj):
        obj.soft_delete(actor=getattr(request, "actor", None))

    def delete_queryset(self, request, queryset):
        actor = getattr(request, "actor", None)
        for obj in queryset:
            obj.soft_delete(actor=actor)


@admin.register(User)
class UserAdmin(StaffModelAdmin):
    list_display = ("email", "person", "is_active", "is_staff", "is_superuser", "created_at")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("email", "person__given_name", "person__family_name")
    autocomplete_fields = ("person",)
    readonly_fields = ("created_at", "last_password_change", "last_login")

    def get_fields(self, request, obj=None):
        if obj is None:
            return (
                "email",
                "password",
                "person",
                "is_active",
                "is_staff",
                "is_superuser",
            )
        return (
            "email",
            "person",
            "is_active",
            "is_staff",
            "is_superuser",
            "created_at",
            "last_password_change",
            "last_login",
        )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        actor = getattr(request, "actor", None)
        if actor is not None and actor.organization_id is not None:
            return qs.filter(person__organization_id=actor.organization_id)
        return qs.none()

    def save_model(self, request, obj, form, change):
        if not change:
            raw = form.cleaned_data.get("password")
            if raw:
                obj.set_password(raw)
        super().save_model(request, obj, form, change)


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(ScopedModelAdmin):
    list_display = (
        "person",
        "role",
        "scope_type",
        "dojo",
        "organization",
        "can_view_financials",
        "revoked_at",
    )
    list_filter = ("role", "scope_type", "organization", "can_view_financials")
    search_fields = ("person__given_name", "person__family_name", "person__email")
    autocomplete_fields = ("organization", "person", "dojo")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(StudentProfile)
class StudentProfileAdmin(ScopedModelAdmin):
    list_display = ("person", "home_dojo", "status", "joined_on")
    # Medical data uses apps.identity.medical so every read is permission-checked
    # and access-logged. The generic admin has no field-level audit hook.
    exclude = (
        # Lifecycle changes go through the audited student hub service.
        "status",
        "hold_reason",
        "medical_notes",
        "allergies",
        "conditions",
        "medications",
        "doctor_contact",
        "do_not_spar",
    )
    list_filter = ("status", "home_dojo")
    search_fields = ("person__given_name", "person__family_name", "person__email")
    autocomplete_fields = ("person", "home_dojo")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(GuardianLink)
class GuardianLinkAdmin(ScopedModelAdmin):
    # Safeguarding notes are managed only through the audited family workflow.
    exclude = ("notes",)
    list_display = (
        "guardian",
        "student",
        "relationship",
        "is_primary_contact",
        "is_emergency_contact",
        "is_financially_responsible",
        "has_custody",
    )
    list_filter = (
        "relationship",
        "is_primary_contact",
        "is_emergency_contact",
        "is_financially_responsible",
        "has_custody",
    )
    search_fields = (
        "guardian__given_name",
        "guardian__family_name",
        "student__given_name",
        "student__family_name",
    )
    autocomplete_fields = ("guardian", "student")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(EmergencyContact)
class EmergencyContactAdmin(ScopedModelAdmin):
    list_display = ("name", "person", "phone", "relationship", "priority")
    list_filter = ("priority",)
    search_fields = ("name", "phone", "person__given_name", "person__family_name")
    autocomplete_fields = ("person",)
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(InstructorAssignment)
class InstructorAssignmentAdmin(ScopedModelAdmin):
    list_display = ("person", "dojo", "is_head_instructor", "started_on", "ended_on")
    list_filter = ("is_head_instructor", "dojo")
    search_fields = ("person__given_name", "person__family_name", "dojo__name")
    autocomplete_fields = ("person", "dojo")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(Enrollment)
class EnrollmentAdmin(ScopedModelAdmin):
    # Hold reasons may contain health data and need an audited dedicated flow.
    exclude = ("hold_reason",)
    list_display = ("student", "dojo", "is_primary", "status", "started_on", "ended_on")
    list_filter = ("status", "is_primary", "dojo")
    search_fields = ("student__given_name", "student__family_name", "dojo__name")
    autocomplete_fields = ("student", "dojo")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(TransferRecord)
class TransferRecordAdmin(ScopedModelAdmin):
    list_display = ("student", "from_dojo", "to_dojo", "effective_on", "approved_by")
    list_filter = ("from_dojo", "to_dojo")
    search_fields = ("student__given_name", "student__family_name", "reason")
    autocomplete_fields = ("student", "from_dojo", "to_dojo", "approved_by")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(ConsentPolicy)
class ConsentPolicyAdmin(ScopedModelAdmin):
    list_display = ("title", "consent_type", "version", "organization", "is_active")
    list_filter = ("consent_type", "is_active", "organization")
    autocomplete_fields = ("organization", "document")
    readonly_fields = ("published_at", "created_at", "updated_at", "created_by")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(("organization", "consent_type", "version", "title", "body", "document"))
        return tuple(fields)

    def _may_manage(self, request, obj=None):
        if request.user.is_superuser:
            return True
        actor = getattr(request, "actor", None)
        return actor is not None and can(actor, Action.ORG_EDIT, obj)

    def has_module_permission(self, request):
        return super().has_module_permission(request) and self._may_manage(request)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and self._may_manage(request, obj)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and self._may_manage(request)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and self._may_manage(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
