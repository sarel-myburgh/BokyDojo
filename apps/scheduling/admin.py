"""Django admin for scheduling models.

Sessions are materialised by ``manage.py materialise_sessions``, so this surface
is for inspecting them and for the occasional ad-hoc class (TODO 1.4.6), not for
bulk entry.
"""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ScopedModelAdmin
from apps.scheduling.models import ClassSession, ClassTemplate, ClosurePeriod


@admin.register(ClassTemplate)
class ClassTemplateAdmin(ScopedModelAdmin):
    list_display = (
        "name",
        "dojo",
        "rrule",
        "start_time",
        "duration_minutes",
        "active_from",
        "active_to",
    )
    list_filter = ("dojo", "style")
    search_fields = ("name", "dojo__name", "room")
    autocomplete_fields = ("dojo", "style", "rank_min", "rank_max")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(ClosurePeriod)
class ClosurePeriodAdmin(ScopedModelAdmin):
    list_display = ("reason", "organization", "dojo", "starts_on", "ends_on", "suppress_billing")
    list_filter = ("suppress_billing", "organization", "dojo")
    search_fields = ("reason",)
    autocomplete_fields = ("organization", "dojo")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(ClassSession)
class ClassSessionAdmin(ScopedModelAdmin):
    list_display = ("__str__", "dojo", "starts_at", "ends_at", "status", "attendance_count")
    list_filter = ("status", "dojo")
    search_fields = ("template__name", "dojo__name", "room")
    autocomplete_fields = ("template", "dojo")
    readonly_fields = ("created_at", "updated_at", "created_by")
    date_hierarchy = "starts_at"

    @admin.display(description=_("marked"))
    def attendance_count(self, obj) -> int:
        return obj.attendance_records.count()
