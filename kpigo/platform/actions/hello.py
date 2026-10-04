"""The trivial action that proves the spine: HTTP, CLI, job, audit and permission."""

from pydantic import BaseModel, Field

from kpigo.action import ActionContext, action


class HelloIn(BaseModel):
    name: str = Field(default="world", min_length=1, max_length=100)


class HelloOut(BaseModel):
    greeting: str
    caller: str
    request_id: str


@action(
    name="platform.hello",
    summary="Say hello. Proves an action is reachable on every surface.",
    schema=HelloIn,
    output=HelloOut,
    permission="platform.hello",
    read_only=True,
    http={"method": "GET", "path": "/hello"},
    example={"name": "kpiGo"},
)
def hello(params: HelloIn, ctx: ActionContext) -> HelloOut:
    return HelloOut(greeting=f"Hello, {params.name}!", caller=ctx.caller, request_id=ctx.request_id)
