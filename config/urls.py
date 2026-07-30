from django.contrib import admin
from django.http import JsonResponse
from django.urls import path
from django.views.generic import RedirectView

from apps.attendance import views as attendance_views
from apps.identity import views as identity_views


def healthz(request):
    """Liveness probe — TODO 0.2.4. Extended with db/redis checks in that task."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="today", permanent=False), name="home"),
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    # Auth — TODO 0.6.4/0.6.5
    path("login/", identity_views.login_view, name="login"),
    path("logout/", identity_views.logout_view, name="logout"),
    # Attendance — TODO 1.5.2
    path("today/", attendance_views.today_view, name="today"),
    path("sessions/<uuid:session_id>/roster/", attendance_views.roster_view, name="roster"),
    # Reports — TODO 1.11.1/1.11.3/1.11.4
    path(
        "reports/attendance/",
        attendance_views.attendance_summary_view,
        name="attendance-summary",
    ),
    path("reports/drop-off/", attendance_views.drop_off_view, name="drop-off"),
]
