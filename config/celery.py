"""Celery application — TODO 0.2.8 / §7.1.

Auto-discovers tasks from all installed apps.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("bokydojo")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
