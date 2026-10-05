"""HTTP adapter: one Django Ninja route per registered action (TDD §3.3).

The Pydantic schema is the request model and the output model is the response
model, so the OpenAPI document is generated from the same definitions the CLI
and jobs use. Authentication is the Django session; the frontend has no API
surface of its own. Public actions (login, SSO, first-run setup) are the only
routes without session auth; an action asks for a login or logout through
``ctx.session`` and this adapter applies it once the action has succeeded.

No `from __future__ import annotations` here: Ninja reads the endpoint annotations
at runtime to build request models.
"""

import re
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.middleware.csrf import get_token
from ninja import Body, Form, NinjaAPI, Path, Query, Schema, Status
from ninja.errors import ValidationError as NinjaValidationError
from ninja.security import django_auth
from pydantic import BaseModel, create_model

from kpigo.action.context import ActionContext, SessionBridge
from kpigo.action.definition import ActionDefinition
from kpigo.action.errors import ActionError, InvalidInput, SessionExpired
from kpigo.action.identity import anonymous_context, build_context
from kpigo.action.pipeline import Proposal, invoke
from kpigo.action.registry import Registry

API_TITLE = "kpiGo"
_PATH_PARAM = re.compile(r"{(\w+)}")
# When the session signed in; the absolute session lifetime counts from here.
AUTH_AT_KEY = "kpigo_auth_at"
AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"


class ErrorOut(Schema):
    error: str
    message: str
    detail: Any = None


def staff_only(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """API docs are for operators: authenticated staff only, never anonymous."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = request.user
        if not (user.is_authenticated and user.is_active and user.is_staff):
            return HttpResponseForbidden("Staff only.")
        return view(request, *args, **kwargs)

    return wrapped


def _request_id(request: HttpRequest) -> str | None:
    value = request.headers.get("X-Request-ID")
    return value[:64] if value else None


def _client_ip(request: HttpRequest) -> str | None:
    value = request.META.get("REMOTE_ADDR")
    return str(value) if value else None


def _subset_model(definition: ActionDefinition, fields: list[str], suffix: str) -> type[BaseModel]:
    spec: dict[str, Any] = {}
    for name in fields:
        info = definition.schema.model_fields[name]
        spec[name] = (info.annotation, info)
    model: type[BaseModel] = create_model(f"{definition.schema.__name__}{suffix}", **spec)
    return model


def _dump(model: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = model.model_dump()
    return dumped


def _check_session_age(request: HttpRequest) -> None:
    """Idle expiry is the cookie's; this is the absolute cap on one sign-in."""
    started = request.session.get(AUTH_AT_KEY)
    if started is None:
        request.session[AUTH_AT_KEY] = time.time()
        return
    limit = float(getattr(settings, "KPIGO_SESSION_MAX_SECONDS", 12 * 3600))
    if time.time() - float(started) > limit:
        django_logout(request)
        raise SessionExpired("Your session has expired. Sign in again.")


def _context(definition: ActionDefinition, request: HttpRequest) -> ActionContext:
    bridge = SessionBridge(request.session)
    if definition.public:
        # A cross-site form cannot send JSON without a CORS preflight, so this
        # keeps login and setup out of reach of forged form posts.
        spec = definition.http_spec
        if spec.method != "GET" and not spec.form and request.content_type != "application/json":
            raise InvalidInput("Send this request as application/json.")
        return anonymous_context(
            caller="http",
            request_id=_request_id(request),
            ip_address=_client_ip(request),
            session=bridge,
        )
    if request.user.is_authenticated:
        _check_session_age(request)
    return build_context(
        request.user,
        caller="http",
        request_id=_request_id(request),
        ip_address=_client_ip(request),
        session=bridge,
    )


def _apply_session(request: HttpRequest, bridge: SessionBridge) -> None:
    if bridge.logout_requested:
        django_logout(request)
    elif bridge.login_user is not None:
        django_login(request, bridge.login_user, backend=AUTH_BACKEND)  # type: ignore[arg-type]
        request.session[AUTH_AT_KEY] = time.time()


def _call(definition: ActionDefinition, request: HttpRequest, raw: dict[str, Any]) -> Any:
    ctx = _context(definition, request)
    result = invoke(definition, raw, ctx)
    _apply_session(request, ctx.session)
    # Session-authenticated writes need the CSRF token: every response carries
    # the cookie so the frontend can echo it in X-CSRFToken.
    get_token(request)
    if isinstance(result, Proposal):
        return Status(202, result)
    if definition.http_spec.redirect:
        return HttpResponseRedirect(str(getattr(result, "redirect_to", "/")), status=303)
    return Status(200, result)


def _make_endpoint(definition: ActionDefinition) -> Callable[..., Any]:
    spec = definition.http_spec
    path_fields = _PATH_PARAM.findall(spec.path)
    unknown = [f for f in path_fields if f not in definition.schema.model_fields]
    if unknown:
        raise ValueError(f"Action '{definition.name}' path names unknown fields {unknown}.")
    rest = [f for f in definition.schema.model_fields if f not in path_fields]

    if spec.method == "GET":
        query_model = _subset_model(definition, rest, "Query")
        if path_fields:
            path_model = _subset_model(definition, path_fields, "Path")

            def endpoint(
                request: HttpRequest,
                path: Path[path_model],  # type: ignore[valid-type]
                query: Query[query_model],  # type: ignore[valid-type]
            ) -> Any:
                return _call(definition, request, {**_dump(path), **_dump(query)})
        elif rest:

            def endpoint(  # type: ignore[misc]
                request: HttpRequest,
                query: Query[query_model],  # type: ignore[valid-type]
            ) -> Any:
                return _call(definition, request, _dump(query))
        else:

            def endpoint(request: HttpRequest) -> Any:  # type: ignore[misc]
                return _call(definition, request, {})
    elif spec.form:

        def endpoint(  # type: ignore[misc]
            request: HttpRequest,
            payload: Form[definition.schema],  # type: ignore[name-defined]
        ) -> Any:
            return _call(definition, request, _dump(payload))
    else:
        body_model = definition.schema
        if path_fields:
            path_model = _subset_model(definition, path_fields, "Path")
            body_model = _subset_model(definition, rest, "Body")

            def endpoint(  # type: ignore[misc]
                request: HttpRequest,
                path: Path[path_model],  # type: ignore[valid-type]
                payload: Body[body_model],  # type: ignore[valid-type]
            ) -> Any:
                return _call(definition, request, {**_dump(path), **_dump(payload)})
        else:

            def endpoint(  # type: ignore[misc]
                request: HttpRequest,
                payload: Body[body_model],  # type: ignore[valid-type]
            ) -> Any:
                return _call(definition, request, _dump(payload))

    endpoint.__name__ = definition.name.replace(".", "_")
    endpoint.__kpigo_action__ = definition  # type: ignore[attr-defined]
    return endpoint


def build_api(registry: Registry, *, urls_namespace: str = "kpigo-api") -> NinjaAPI:
    api = NinjaAPI(
        title=API_TITLE,
        version="1",
        urls_namespace=urls_namespace,
        auth=django_auth,
        docs_decorator=staff_only,
    )

    @api.exception_handler(ActionError)
    def _action_error(request: HttpRequest, exc: ActionError) -> HttpResponse:
        return api.create_response(request, exc.as_dict(), status=exc.http_status)

    @api.exception_handler(NinjaValidationError)
    def _invalid(request: HttpRequest, exc: NinjaValidationError) -> HttpResponse:
        # Same body shape as the pipeline's InvalidInput, so clients handle one format.
        body = {"error": "invalid_input", "message": "Invalid input.", "detail": exc.errors}
        return api.create_response(request, body, status=422)

    for definition in registry:
        spec = definition.http_spec
        responses: dict[int, Any] = {
            200: definition.output,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: ErrorOut,
        }
        if definition.requires_approval:
            responses[202] = Proposal
        if not definition.read_only:
            responses[503] = ErrorOut
        api.default_router.add_api_operation(
            spec.path,
            [spec.method],
            _make_endpoint(definition),
            response=responses,
            operation_id=definition.name.replace(".", "_"),
            summary=definition.summary or definition.name,
            tags=[definition.module],
            auth=None if definition.public else django_auth,
            url_name=definition.name.replace(".", "-"),
            # The frontend's generated client keys every route by this name.
            openapi_extra={"x-kpigo-action": definition.name},
        )
    return api


def is_action_view(view: Callable[..., Any]) -> bool:
    return getattr(view, "__kpigo_action__", None) is not None
