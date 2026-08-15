"""Django admin for rank / style models."""

from __future__ import annotations

from django.contrib import admin

from apps.core.admin import ScopedModelAdmin
from apps.ranks.models import Rank, RankAward, RankLadder, StudentStyleTrack, Style


@admin.register(Style)
class StyleAdmin(ScopedModelAdmin):
    list_display = ("name", "organization")
    list_filter = ("organization",)
    search_fields = ("name",)
    autocomplete_fields = ("organization",)
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(RankLadder)
class RankLadderAdmin(ScopedModelAdmin):
    list_display = ("name", "style", "applies_to")
    list_filter = ("applies_to", "style")
    search_fields = ("name", "style__name")
    autocomplete_fields = ("style",)
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(Rank)
class RankAdmin(ScopedModelAdmin):
    list_display = (
        "name",
        "ladder",
        "order",
        "belt_colour",
        "stripe_count",
        "min_age",
    )
    list_filter = ("ladder", "belt_colour")
    search_fields = ("name", "belt_colour", "ladder__name")
    autocomplete_fields = ("ladder",)
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(StudentStyleTrack)
class StudentStyleTrackAdmin(ScopedModelAdmin):
    list_display = (
        "student",
        "style",
        "ladder",
        "current_rank",
        "status",
        "started_on",
        "ended_on",
    )
    list_filter = ("status", "style", "ladder")
    search_fields = (
        "student__given_name",
        "student__family_name",
        "style__name",
    )
    autocomplete_fields = ("student", "style", "ladder", "current_rank")
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(RankAward)
class RankAwardAdmin(ScopedModelAdmin):
    list_display = (
        "track",
        "rank",
        "awarded_on",
        "recognition",
        "awarded_by",
        "revoked_at",
    )
    list_filter = ("recognition", "awarded_on")
    search_fields = (
        "track__student__given_name",
        "track__student__family_name",
        "rank__name",
        "certificate_number",
        "awarded_by_external_org",
    )
    readonly_fields = (
        "track",
        "rank",
        "awarded_on",
        "awarded_by",
        "recognition",
        "awarded_by_external_org",
        "certificate_number",
        "notes",
        "revoked_at",
        "revoked_by",
        "revocation_reason",
        "created_at",
        "updated_at",
        "created_by",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
