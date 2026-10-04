"""The only URLs are the action API. Every route under it is a registered action."""

from django.urls import path

from kpigo.action import registry
from kpigo.action.adapters.http import build_api

api = build_api(registry)

urlpatterns = [
    path("api/v1/", api.urls),
]
