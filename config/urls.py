from django.http import JsonResponse
from django.urls import path


def healthz(request):
    """Liveness probe — TODO 0.2.4. Extended with db/redis checks in that task."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz, name="healthz"),
]
