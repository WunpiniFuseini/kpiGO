"""The decorator refuses malformed declarations at import time."""

import pytest
from pydantic import BaseModel

from kpigo.action import Registry, RegistryError, action
from kpigo.action.definition import definition_of


class In(BaseModel):
    x: int = 0


class Out(BaseModel):
    x: int


def _declare(**overrides: object) -> None:
    kwargs: dict[str, object] = {
        "name": "test.thing",
        "schema": In,
        "output": Out,
        "permission": "test.thing",
        "read_only": True,
    }
    kwargs.update(overrides)

    @action(**kwargs)  # type: ignore[arg-type]
    def handler(params: In, ctx: object) -> Out:
        return Out(x=params.x)


def test_valid_declaration_records_metadata() -> None:
    @action(name="test.ok", schema=In, output=Out, permission="test.ok", read_only=True)
    def ok(params: In, ctx: object) -> Out:
        """First line is the summary."""
        return Out(x=params.x)

    definition = definition_of(ok)
    assert definition is not None
    assert definition.summary == "First line is the summary."
    assert definition.http_spec.method == "GET"
    assert definition.http_spec.path == "/actions/test.ok"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"permission": ""}, "permission"),
        ({"permission": "nodots"}, "permission"),
        ({"name": "NoDots"}, "dotted"),
        ({"read_only": True, "requires_approval": "maker_checker"}, "cannot require approval"),
        ({"schema": dict}, "Pydantic"),
        ({"output": str}, "Pydantic"),
        ({"module": "crm"}, "unknown module"),
        ({"read_only": True, "http": {"method": "POST"}}, "GET"),
        ({"read_only": False, "http": {"method": "GET"}}, "cannot be served over GET"),
        ({"http": {"method": "GET", "path": "no-slash"}}, "start with"),
    ],
)
def test_malformed_declarations_fail(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(RegistryError, match=message):
        _declare(**overrides)


def test_duplicate_names_are_refused() -> None:
    @action(name="test.dup", schema=In, output=Out, permission="test.dup", read_only=True)
    def one(params: In, ctx: object) -> Out:
        return Out(x=1)

    @action(name="test.dup", schema=In, output=Out, permission="test.dup", read_only=True)
    def two(params: In, ctx: object) -> Out:
        return Out(x=2)

    reg = Registry()
    reg.register(definition_of(one))  # type: ignore[arg-type]
    with pytest.raises(RegistryError, match="registered twice"):
        reg.register(definition_of(two))  # type: ignore[arg-type]


def test_unlicensed_module_actions_do_not_register() -> None:
    @action(
        name="scorecard.peek",
        schema=In,
        output=Out,
        permission="scorecard.view",
        read_only=True,
        module="scorecards",
    )
    def peek(params: In, ctx: object) -> Out:
        return Out(x=0)

    @action(name="platform.peek", schema=In, output=Out, permission="platform.peek", read_only=True)
    def platform_peek(params: In, ctx: object) -> Out:
        return Out(x=0)

    reg = Registry()
    assert reg.register(definition_of(peek), entitled=["campaign"]) is False  # type: ignore[arg-type]
    assert reg.register(definition_of(platform_peek), entitled=[]) is True  # type: ignore[arg-type]
    assert "scorecard.peek" not in reg
    assert reg.skipped == ["scorecard.peek"]
    assert "platform.peek" in reg
