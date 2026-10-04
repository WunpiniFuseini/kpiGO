"""Nothing is a route, task or command unless it is a registered action."""

from django.urls import URLPattern, URLResolver, get_resolver
from ninja.operation import PathView

from kpigo.action import registry
from kpigo.action.adapters.jobs import TASK_PREFIX
from kpigo.celery import app as celery_app
from kpigo.urls import api

# Generated OpenAPI documentation, served only to staff. Not product surface.
# "api-root" is Ninja's 404 placeholder at the API prefix.
ALLOWED_NON_ACTION_VIEWS = {"openapi-json", "openapi-view", "api-root"}


def _walk(patterns: list[URLPattern | URLResolver]) -> list[URLPattern]:
    flat: list[URLPattern] = []
    for p in patterns:
        if isinstance(p, URLResolver):
            flat += _walk(p.url_patterns)
        else:
            flat.append(p)
    return flat


def test_every_url_is_an_action_route() -> None:
    action_routes = {
        op.view_func.__kpigo_action__.name  # type: ignore[attr-defined]
        for router in [api.default_router]
        for path_view in router.path_operations.values()
        for op in path_view.operations
    }
    assert action_routes == {d.name for d in registry}

    for pattern in _walk(get_resolver().url_patterns):
        if pattern.name in ALLOWED_NON_ACTION_VIEWS:
            continue
        # Ninja serves every operation on a path through one PathView, which the
        # Django view closes over.
        cells = pattern.callback.__closure__ or ()
        path_views = [c.cell_contents for c in cells if isinstance(c.cell_contents, PathView)]
        assert path_views, f"URL '{pattern.pattern}' is not served by the action API"
        operations = path_views[0].operations
        for op in operations:
            assert hasattr(op.view_func, "__kpigo_action__"), (
                f"URL '{pattern.pattern}' serves a view that is not a registered action"
            )


def test_only_registry_routers_are_mounted() -> None:
    assert [prefix for prefix, _ in api._routers] == [""]


def test_every_kpigo_task_is_an_action() -> None:
    ours = {name for name in celery_app.tasks if name.startswith("kpigo.")}
    assert ours == {f"{TASK_PREFIX}{d.name}" for d in registry}


def test_only_the_action_command_is_custom() -> None:
    from django.core.management import get_commands

    custom = {name for name, app in get_commands().items() if app.startswith("kpigo")}
    assert custom == {"action"}
