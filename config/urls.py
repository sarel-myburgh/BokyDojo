from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, JsonResponse
from django.urls import path
from django.views.decorators.cache import never_cache
from django.views.generic import RedirectView, TemplateView

from apps.attendance import kiosk_views
from apps.attendance import views as attendance_views
from apps.core import help_views
from apps.identity import (
    consent_views,
    guardian_views,
    org_views,
    photo_views,
    profile_views,
    student_views,
)
from apps.identity import password_reset as password_reset_views
from apps.identity import setup as setup_views
from apps.identity import views as identity_views
from apps.imports import views as import_views
from apps.ranks import ladder_views
from apps.ranks import views as rank_views
from apps.scheduling import views as scheduling_views
from apps.staffing import grade_views as staffing_grade_views
from apps.staffing import views as staffing_views


def healthz(request):
    """Liveness probe — TODO 0.2.4. Extended with db/redis checks in that task."""
    return JsonResponse({"status": "ok"})


@never_cache
def service_worker(request):
    response = FileResponse(
        open(settings.BASE_DIR / "static/js/service-worker.js", "rb"),
        content_type="text/javascript; charset=utf-8",
    )
    response["Service-Worker-Allowed"] = "/"
    return response


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="today", permanent=False), name="home"),
    path("setup/", setup_views.first_run_view, name="first-run"),
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("service-worker.js", service_worker, name="service-worker"),
    path(
        "offline/",
        TemplateView.as_view(template_name="offline.html"),
        name="offline",
    ),
    # Auth — TODO 0.6.4/0.6.5
    path("login/", identity_views.login_view, name="login"),
    path(
        "password-reset/",
        password_reset_views.password_reset_request_view,
        name="password-reset",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        password_reset_views.password_reset_confirm_view,
        name="password-reset-confirm",
    ),
    path(
        "password-reset/complete/",
        password_reset_views.password_reset_complete_view,
        name="password-reset-complete",
    ),
    path("help/", help_views.help_index_view, name="help"),
    path("help/<slug:slug>/", help_views.help_guide_view, name="help-guide"),
    path("account/", profile_views.account_view, name="account"),
    path("account/edit/", profile_views.account_edit_view, name="account-edit"),
    path("account/password/", identity_views.password_change_view, name="password-change"),
    path("login/2fa/", identity_views.mfa_challenge_view, name="mfa-challenge"),
    path("account/security/2fa/", identity_views.mfa_setup_view, name="mfa-setup"),
    path(
        "account/security/2fa/recovery-codes/",
        identity_views.mfa_recovery_codes_view,
        name="mfa-recovery-codes",
    ),
    path("logout/", identity_views.logout_view, name="logout"),
    # Organisation settings and the "add a thing" screens
    path("settings/", org_views.organization_settings_view, name="org-settings"),
    path(
        "settings/styles/<uuid:style_id>/ranked/",
        org_views.style_toggle_ranked_view,
        name="style-toggle-ranked",
    ),
    path("settings/styles/new/", org_views.style_create_view, name="style-create"),
    path("settings/styles/<uuid:style_id>/", ladder_views.style_detail_view, name="style-detail"),
    path(
        "settings/ladders/<uuid:ladder_id>/",
        ladder_views.ladder_detail_view,
        name="ladder-detail",
    ),
    path(
        "settings/ladders/<uuid:ladder_id>/order/",
        ladder_views.rank_reorder_view,
        name="rank-reorder",
    ),
    path(
        "settings/ladders/<uuid:ladder_id>/belts/<uuid:rank_id>/delete/",
        ladder_views.rank_delete_view,
        name="rank-delete",
    ),
    path("settings/dojos/new/", org_views.dojo_create_view, name="dojo-create"),
    path("settings/dojos/<uuid:dojo_id>/", org_views.dojo_edit_view, name="dojo-edit"),
    path("settings/people/new/", org_views.staff_create_view, name="staff-create"),
    path(
        "people/<uuid:person_id>/",
        profile_views.person_detail_view,
        name="person-detail",
    ),
    path(
        "people/<uuid:person_id>/roles/",
        org_views.role_grant_view,
        name="role-grant",
    ),
    path(
        "people/<uuid:person_id>/grades/",
        staffing_grade_views.staff_grade_add_view,
        name="staff-grade-add",
    ),
    path(
        "people/<uuid:person_id>/grades/<uuid:grade_id>/delete/",
        staffing_grade_views.staff_grade_delete_view,
        name="staff-grade-delete",
    ),
    path(
        "people/<uuid:person_id>/edit/",
        profile_views.person_edit_view,
        name="person-edit",
    ),
    path(
        "people/<uuid:person_id>/picture/",
        profile_views.profile_photo_view,
        name="profile-photo",
    ),
    path(
        "people/<uuid:person_id>/picture/upload/",
        profile_views.profile_photo_upload_view,
        name="profile-photo-upload",
    ),
    path(
        "settings/people/<uuid:person_id>/temporary-password/",
        org_views.temporary_password_view,
        name="temporary-password",
    ),
    path(
        "settings/people/<uuid:person_id>/roles/<uuid:assignment_id>/revoke/",
        org_views.role_revoke_view,
        name="role-revoke",
    ),
    path("students/new/", org_views.student_create_view, name="student-create"),
    path("students/", student_views.student_list_view, name="student-list"),
    path(
        "students/segments/save/",
        student_views.student_segment_create_view,
        name="student-segment-create",
    ),
    path(
        "students/status/bulk/",
        student_views.student_bulk_status_view,
        name="student-bulk-status",
    ),
    path(
        "students/promotions/bulk/",
        rank_views.bulk_promotion_view,
        name="student-bulk-promote",
    ),
    path(
        "students/segments/<uuid:segment_id>/delete/",
        student_views.student_segment_delete_view,
        name="student-segment-delete",
    ),
    path(
        "students/<uuid:person_id>/",
        student_views.student_detail_view,
        name="student-detail",
    ),
    path(
        "students/<uuid:person_id>/status/",
        student_views.student_status_transition_view,
        name="student-status-transition",
    ),
    path(
        "students/<uuid:person_id>/notes/",
        student_views.student_note_create_view,
        name="student-note-create",
    ),
    path(
        "students/<uuid:person_id>/ranks/<uuid:track_id>/promote/",
        rank_views.manual_promotion_view,
        name="student-promote",
    ),
    path(
        "students/<uuid:person_id>/guardians/add/",
        guardian_views.guardian_add_view,
        name="guardian-add",
    ),
    path(
        "students/<uuid:person_id>/guardians/<uuid:link_id>/edit/",
        guardian_views.guardian_edit_view,
        name="guardian-edit",
    ),
    path(
        "students/<uuid:person_id>/guardians/<uuid:link_id>/remove/",
        guardian_views.guardian_remove_view,
        name="guardian-remove",
    ),
    path(
        "students/<uuid:person_id>/consents/medical/",
        consent_views.medical_consent_view,
        name="medical-consent",
    ),
    path(
        "students/<uuid:person_id>/consents/photo/",
        consent_views.photo_consent_view,
        name="photo-consent",
    ),
    path(
        "students/<uuid:person_id>/photo/",
        photo_views.student_photo_view,
        name="student-photo",
    ),
    path(
        "students/<uuid:person_id>/photo/upload/",
        photo_views.student_photo_upload_view,
        name="student-photo-upload",
    ),
    path(
        "students/<uuid:person_id>/consents/waiver/",
        consent_views.waiver_consent_view,
        name="waiver-consent",
    ),
    path(
        "documents/<uuid:document_id>/download/",
        consent_views.document_download_view,
        name="document-download",
    ),
    # Imports — TODO 1.10.1/1.10.7
    path("imports/", import_views.import_wizard_view, name="import-wizard"),
    path(
        "imports/<uuid:run_id>/report.csv",
        import_views.import_report_view,
        name="import-report",
    ),
    # Scheduling — TODO 1.4.9
    path("calendar/", scheduling_views.calendar_view, name="calendar"),
    # Attendance — TODO 1.5.2
    path("today/", attendance_views.today_view, name="today"),
    path("attendance/catch-up/", attendance_views.catch_up_view, name="catch-up"),
    path("sessions/<uuid:session_id>/roster/", attendance_views.roster_view, name="roster"),
    # Kiosk / hand-around check-in — TODO 1.7, decision D1
    path("sessions/<uuid:session_id>/check-in/", kiosk_views.kiosk_view, name="kiosk"),
    path(
        "sessions/<uuid:session_id>/check-in/mark/",
        kiosk_views.kiosk_mark_view,
        name="kiosk-mark",
    ),
    path(
        "sessions/<uuid:session_id>/check-in/finish/",
        kiosk_views.kiosk_exit_view,
        name="kiosk-exit",
    ),
    path(
        "api/attendance/sessions/<uuid:session_id>/sync/",
        attendance_views.attendance_sync_view,
        name="attendance-sync",
    ),
    # Instructor time — TODO 1.9.4
    path("timesheet/", staffing_views.timesheet_view, name="timesheet"),
    # Reports — TODO 1.11.1/1.11.2/1.11.3/1.11.4
    path(
        "reports/attendance/",
        attendance_views.attendance_summary_view,
        name="attendance-summary",
    ),
    path("reports/drop-off/", attendance_views.drop_off_view, name="drop-off"),
    path(
        "reports/ranks/",
        rank_views.active_students_by_rank_view,
        name="active-by-rank",
    ),
]
