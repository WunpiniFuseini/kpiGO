"""Whether the assistant is available, and a connection check (R7, PRD AG-3, AG-8).

With no model configured the assistant is simply off and every other part of
kpiGo works as before; these actions say so rather than fail.
"""

from typing import Literal

from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.assistant import inference


class AssistantStatusIn(BaseModel):
    pass


class AssistantStatusOut(BaseModel):
    available: bool
    # Why the assistant is off, in words an Admin can act on; empty when available.
    reason: str
    provider: str | None
    provider_label: str
    model: str
    # Where the model lives and whether a key is set: only for those who manage it.
    endpoint: str | None = None
    api_key_set: bool | None = None


@action(
    name="assistant.status",
    summary="Whether the assistant is available on this install, and which model it uses.",
    schema=AssistantStatusIn,
    output=AssistantStatusOut,
    permission="assistant.use",
    read_only=True,
    example={},
)
def status(params: AssistantStatusIn, ctx: ActionContext) -> AssistantStatusOut:
    manager = ctx.has("assistant.manage")
    name = inference.configured_provider_name()
    try:
        config = inference.load_config()
    except inference.NotConfigured as exc:
        return AssistantStatusOut(
            available=False,
            reason=str(exc),
            provider=name,
            provider_label=inference.PROVIDER_LABELS.get(name or "", ""),
            model="",
        )
    endpoint = config.url or (
        f"bedrock-runtime.{config.region}.amazonaws.com" if config.provider == "bedrock" else ""
    )
    return AssistantStatusOut(
        available=True,
        reason="",
        provider=config.provider,
        provider_label=inference.PROVIDER_LABELS[config.provider],
        model=config.model,
        endpoint=endpoint if manager else None,
        api_key_set=bool(config.api_key) if manager else None,
    )


class AssistantTestIn(BaseModel):
    pass


class AssistantTestOut(BaseModel):
    outcome: Literal["ok", "failed", "not_configured"]
    detail: str
    latency_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@action(
    name="assistant.connection.test",
    summary="Send the model one tiny prompt (no client data) and report whether it answers.",
    schema=AssistantTestIn,
    output=AssistantTestOut,
    permission="assistant.manage",
    read_only=False,
    audit="assistant.connection_tested",
    example={},
)
def test_connection(params: AssistantTestIn, ctx: ActionContext) -> AssistantTestOut:
    try:
        provider = inference.get_provider()
    except inference.NotConfigured as exc:
        return AssistantTestOut(outcome="not_configured", detail=str(exc))
    result = inference.probe(provider)
    ctx.audit(
        "assistant.connection_result",
        ok=result.ok,
        provider=provider.name,
        model=provider.model,
        latency_ms=result.latency_ms,
    )
    return AssistantTestOut(
        outcome="ok" if result.ok else "failed",
        detail=result.detail,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
