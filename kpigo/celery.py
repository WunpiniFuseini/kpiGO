import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kpigo.settings")

app = Celery("kpigo")
app.config_from_object("django.conf:settings", namespace="CELERY")
# Tasks are not written by hand: the job adapter registers one per action when
# the platform app is ready (kpigo.platform.apps).
