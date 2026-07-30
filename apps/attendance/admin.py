"""Django admin for attendance records.

Read-mostly on purpose. Attendance is captured on the roster, the kiosk or the
offline queue, all of which go through ``services.mark_attendance`` so that the
idempotency key, the visiting flag and the retroactive-edit permission are
applied. Typing rows in here bypasses all three, so adding is disabled and the
identifying fields of an existing row are read-only.
"""

from __future__ import annotations

from django.contrib import admin

from apps.attendance.models import AttendanceRecord
from apps.core.admin import ScopedModelAdmin


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(ScopedModelAdmin):
    list_display = ("student", "session", "status", "method", "marked_at", "marked_by")
    list_filter = ("status", "method")
    search_fields = (
        "student__given_name",
        "student__family_name",
        "session__template__name",
    )
    autocomplete_fields = ("student", "session", "marked_by")
    readonly_fields = (
        "session",
        "student",
        "client_generated_id",
        "created_at",
        "updated_at",
        "created_by",
    )
    date_hierarchy = "marked_at"

    def has_add_permission(self, request):
        return False
