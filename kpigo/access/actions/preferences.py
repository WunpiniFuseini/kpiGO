"""Your own view preferences (PRD AP-9). Anyone signed in keeps their own."""

from __future__ import annotations

from typing import Annotated

from django.utils import timezone
from pydantic import BaseModel, StringConstraints

from kpigo.access.identity import app_user_for
from kpigo.access.models import UserPreference
from kpigo.access.preferences import PREFERENCES, preference
from kpigo.action import ActionContext, Conflict, InvalidInput, action

Key = Annotated[str, StringConstraints(min_length=1, max_length=80)]
Value = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class PreferenceOut(BaseModel):
    key: str
    value: str
    allowed: list[str]


class PreferenceListIn(BaseModel):
    pass


class PreferenceListOut(BaseModel):
    preferences: list[PreferenceOut]


@action(
    name="preference.list",
    summary="Your view preferences, each with its allowed values.",
    schema=PreferenceListIn,
    output=PreferenceListOut,
    permission="auth.session",
    read_only=True,
    example={},
)
def list_preferences(params: PreferenceListIn, ctx: ActionContext) -> PreferenceListOut:
    account = app_user_for(ctx.user, ctx.org_id)
    return PreferenceListOut(
        preferences=[
            PreferenceOut(key=key, value=preference(account, key), allowed=list(allowed))
            for key, (allowed, _) in sorted(PREFERENCES.items())
        ]
    )


class PreferenceSetIn(BaseModel):
    key: Key
    value: Value


@action(
    name="preference.set",
    summary="Set one of your view preferences, such as the matrix's grouped view.",
    schema=PreferenceSetIn,
    output=PreferenceOut,
    permission="auth.session",
    read_only=False,
    example={"key": "agent.matrix.view", "value": "grouped"},
)
def set_preference(params: PreferenceSetIn, ctx: ActionContext) -> PreferenceOut:
    if params.key not in PREFERENCES:
        raise InvalidInput(f"No preference '{params.key}'.", detail={"keys": sorted(PREFERENCES)})
    allowed, _ = PREFERENCES[params.key]
    if params.value not in allowed:
        raise InvalidInput(
            f"'{params.key}' is one of {', '.join(allowed)}.", detail={"allowed": list(allowed)}
        )
    account = app_user_for(ctx.user, ctx.org_id)
    if account is None:
        raise Conflict("Preferences belong to a signed-in user.")
    row, created = UserPreference.objects.get_or_create(
        app_user=account,
        key=params.key,
        defaults={"value": params.value, "created_by": ctx.user_id, "updated_by": ctx.user_id},
    )
    if not created and row.value != params.value:
        row.value = params.value
        row.updated_by = ctx.user_id
        row.updated_at = timezone.now()
        row.save()
    return PreferenceOut(key=params.key, value=params.value, allowed=list(allowed))
