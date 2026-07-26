"""Django admin for core models + shared scoped admin base.

Tenant-scoped managers refuse unscoped evaluation, so every ModelAdmin that
touches a ScopedManager must resolve the request actor in get_queryset().

Django's default RelatedFieldListFilter and FK form widgets call
``field.get_choices()`` / the default manager, which is unscoped — so filters
and dropdowns also have to go through the related ModelAdmin's get_queryset.
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.admin.filters import RelatedFieldListFilter

from apps.core.models import AuditLog, Document, Setting
from apps.core.scoping import Actor


class ScopedRelatedFieldListFilter(RelatedFieldListFilter):
    """Related list filter that uses the related ModelAdmin's scoped queryset."""

    def field_choices(self, field, request, model_admin):
        related_model = field.remote_field.model
        related_admin = model_admin.admin_site._registry.get(related_model)
        if related_admin is not None:
            qs = related_admin.get_queryset(request)
            ordering = related_admin.get_ordering(request)
            if ordering:
                qs = qs.order_by(*ordering)
            return [(str(obj.pk), str(obj)) for obj in qs]
        return field.get_choices(include_blank=False)


class StaffModelAdmin(admin.ModelAdmin):
    """Staff access without Django's unused Group/Permission framework.

    ``identity.User.has_perm`` is superuser-only by design; authorisation lives
    in RoleAssignment. Admin is a back-office surface gated by ``is_staff``,
    with tenant isolation applied in ``ScopedModelAdmin.get_queryset``.
    """

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_staff)

    def has_add_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff)

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_staff)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_staff)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Scope FK dropdowns when autocomplete is not used."""
        related_model = db_field.remote_field.model
        related_admin = self.admin_site._registry.get(related_model)
        if related_admin is not None and "queryset" not in kwargs:
            kwargs["queryset"] = related_admin.get_queryset(request)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_list_filter(self, request):
        """Wrap plain FK list_filter names in ScopedRelatedFieldListFilter."""
        filters = []
        for item in super().get_list_filter(request):
            if isinstance(item, str):
                try:
                    field = self.model._meta.get_field(item)
                except Exception:
                    filters.append(item)
                    continue
                if field.is_relation and not field.many_to_many and field.related_model is not None:
                    filters.append((item, ScopedRelatedFieldListFilter))
                else:
                    filters.append(item)
            else:
                filters.append(item)
        return filters


class ScopedModelAdmin(StaffModelAdmin):
    """ModelAdmin for TenantScopedModel subclasses.

    Superusers are cross-tenant operators (no Person / organisation). They use
    ``Actor.system()`` so the queryset is marked scoped and returns all rows —
    equivalent intent to ``allow_unscoped("django admin superuser")``, but safe
    for deferred evaluation and permitted by the unscoped-access lint.
    """

    def get_queryset(self, request):
        if request.user.is_superuser:
            return self.model.objects.for_actor(Actor.system())

        actor = getattr(request, "actor", None)
        if actor is not None and actor.organization_id is not None:
            return self.model.objects.for_actor(actor)

        # Staff with no organisation must not see any tenant rows.
        return self.model.objects.none()


@admin.register(Document)
class DocumentAdmin(ScopedModelAdmin):
    list_display = (
        "original_filename",
        "kind",
        "organization",
        "subject_person",
        "is_sensitive",
        "retention_until",
        "created_at",
    )
    list_filter = ("kind", "is_sensitive", "organization")
    search_fields = (
        "original_filename",
        "subject_person__given_name",
        "subject_person__family_name",
    )
    autocomplete_fields = ("organization", "subject_person", "uploaded_by")
    # storage_key is never editable; file access goes through apps.core.documents.
    readonly_fields = (
        "storage_key",
        "content_type",
        "byte_size",
        "checksum",
        "created_at",
        "updated_at",
        "created_by",
    )


@admin.register(Setting)
class SettingAdmin(ScopedModelAdmin):
    list_display = ("key", "organization", "scope_type", "scope_id", "updated_at")
    list_filter = ("scope_type", "organization", "key")
    search_fields = ("key",)
    autocomplete_fields = ("organization",)
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(AuditLog)
class AuditLogAdmin(StaffModelAdmin):
    """Append-only evidence. No add / change / delete through the admin."""

    list_display = (
        "at",
        "action",
        "subject_type",
        "subject_id",
        "organization",
        "actor_person",
        "actor_label",
    )
    list_filter = ("action", "organization")
    search_fields = ("subject_type", "subject_id", "actor_label", "note")
    readonly_fields = (
        "id",
        "at",
        "organization",
        "actor_person",
        "actor_label",
        "action",
        "subject_type",
        "subject_id",
        "before",
        "after",
        "ip_address",
        "user_agent",
        "note",
    )
    date_hierarchy = "at"
    ordering = ("-at",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        actor = getattr(request, "actor", None)
        if actor is not None and actor.organization_id is not None:
            return qs.filter(organization_id=actor.organization_id)
        return qs.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
