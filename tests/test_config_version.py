"""The config_version counter is the pipeline's job, not the action's (TDD §5)."""

from collections.abc import Callable

import pytest
from pydantic import BaseModel

from kpigo.action import ActionContext, Conflict, RegistryError, action
from kpigo.action.definition import ActionDefinition
from kpigo.platform.config import config_version
from kpigo.platform.models import AuditLog
from tests.conftest import ORG_ID, role_ctx, run

pytestmark = pytest.mark.django_db


class In(BaseModel):
    fail: bool = False


class Out(BaseModel):
    ok: bool


@action(
    name="test.config.touch",
    schema=In,
    output=Out,
    permission="metric.manage",
    read_only=False,
    config_change=True,
)
def touch(params: In, ctx: ActionContext) -> Out:
    if params.fail:
        raise Conflict("No.")
    return Out(ok=True)


@pytest.fixture(autouse=True)
def _registered(register: Callable[[Callable[..., object]], ActionDefinition]) -> None:
    register(touch)


def test_successful_config_change_bumps_and_audits_the_version() -> None:
    assert config_version(ORG_ID) == 0
    run("test.config.touch")
    run("test.config.touch")
    assert config_version(ORG_ID) == 2
    versions = [
        a.payload["config_version"]
        for a in AuditLog.objects.filter(action_name="test.config.touch").order_by("occurred_at")
    ]
    assert versions == [1, 2]


def test_failed_or_dry_run_change_does_not_bump() -> None:
    with pytest.raises(Conflict):
        run("test.config.touch", fail=True)
    run("test.config.touch", role_ctx(dry_run=True))
    assert config_version(ORG_ID) == 0


def test_read_only_actions_cannot_declare_config_change() -> None:
    with pytest.raises(RegistryError, match="cannot change configuration"):

        @action(
            name="test.config.read",
            schema=In,
            output=Out,
            permission="metric.view",
            read_only=True,
            config_change=True,
        )
        def read(params: In, ctx: ActionContext) -> Out:
            return Out(ok=True)
