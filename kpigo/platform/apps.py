from django.apps import AppConfig
from django.conf import settings


class PlatformConfig(AppConfig):
    name = "kpigo.platform"
    label = "platform"
    verbose_name = "kpiGo platform"

    def ready(self) -> None:
        from kpigo.action.adapters.jobs import beat_schedule, register_tasks
        from kpigo.action.registry import autodiscover, registry
        from kpigo.celery import app as celery_app

        if len(registry) == 0:
            autodiscover(registry, entitled=settings.KPIGO_ENTITLED_MODULES)
        register_tasks(celery_app, registry)
        celery_app.conf.beat_schedule = beat_schedule(settings.KPIGO_SCHEDULED_ACTIONS, registry)
