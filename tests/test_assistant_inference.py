"""R7 inference providers: each adapter translates the neutral shape to its wire format.

No model is contacted: httpx.MockTransport stands in for each endpoint, and the
assertions are on exactly what kpiGo would send and how it reads the answer.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from django.contrib.auth.models import User

from kpigo.action import invoke, registry
from kpigo.action.identity import build_context
from kpigo.assistant import inference
from kpigo.assistant.inference import (
    InferenceError,
    Message,
    NotConfigured,
    ToolCall,
    ToolSpec,
)
from kpigo.platform.models import AuditLog

TOOLS = [
    ToolSpec(
        name="scorecard_compute",
        description="Compute a scorecard.",
        parameters={"type": "object", "properties": {"subject_id": {"type": "string"}}},
    )
]
CONVERSATION = [
    Message(role="system", content="Be grounded."),
    Message(role="user", content="Why is Ama below target?"),
    Message(
        role="assistant",
        tool_calls=(ToolCall(id="t1", name="scorecard_compute", arguments={"subject_id": "s1"}),),
    ),
    Message(role="tool", tool_call_id="t1", content='{"score": 71}'),
]


class Recorder:
    def __init__(self, answer: dict[str, Any], status: int = 200) -> None:
        self.answer = answer
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.answer)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)  # type: ignore[no-any-return]


def configure(settings: Any, provider: str, **extra: str | None) -> None:
    settings.KPIGO_INFERENCE_PROVIDER = provider
    settings.KPIGO_INFERENCE_MODEL = extra.get("model", "m1")
    settings.KPIGO_INFERENCE_URL = extra.get("url")
    settings.KPIGO_INFERENCE_API_KEY = extra.get("key")
    settings.KPIGO_INFERENCE_REGION = extra.get("region")


def provider_with(recorder: Recorder) -> inference.Provider:
    return inference.get_provider(transport=httpx.MockTransport(recorder))


CHAT_ANSWER = {
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "c9",
                        "type": "function",
                        "function": {
                            "name": "scorecard_compute",
                            "arguments": '{"subject_id":"s2"}',
                        },
                    }
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 14},
}


# --- configuration ----------------------------------------------------------


def test_nothing_configured_is_not_an_error_to_boot(settings: Any) -> None:
    settings.KPIGO_INFERENCE_PROVIDER = None
    with pytest.raises(NotConfigured, match="No model"):
        inference.get_provider()


@pytest.mark.parametrize(
    ("provider", "extra", "missing"),
    [
        ("openai_compatible", {}, "KPIGO_INFERENCE_URL"),
        ("azure_openai", {"url": "https://x.openai.azure.com"}, "KPIGO_INFERENCE_API_KEY"),
        ("bedrock", {"key": "k"}, "KPIGO_INFERENCE_REGION"),
        ("openai_compatible", {"url": "http://llm/v1", "model": ""}, "KPIGO_INFERENCE_MODEL"),
    ],
)
def test_incomplete_configuration_names_what_is_missing(
    settings: Any, provider: str, extra: dict[str, str], missing: str
) -> None:
    configure(settings, provider, **extra)
    with pytest.raises(NotConfigured, match=missing):
        inference.load_config()


def test_unknown_provider_is_named(settings: Any) -> None:
    configure(settings, "gemini", url="http://x")
    with pytest.raises(NotConfigured, match="'gemini' is not one of"):
        inference.load_config()


def test_the_key_never_appears_in_repr(settings: Any) -> None:
    configure(settings, "azure_openai", url="https://x", key="sk-secret")
    assert "sk-secret" not in repr(inference.load_config())


# --- OpenAI-compatible (vLLM, Ollama) and Azure OpenAI ------------------------


def test_openai_compatible_round_trip(settings: Any) -> None:
    configure(settings, "openai_compatible", url="http://llm.internal:8000/v1/", model="qwen")
    rec = Recorder(CHAT_ANSWER)
    out = provider_with(rec).complete(CONVERSATION, TOOLS, max_tokens=500)

    request = rec.requests[0]
    assert str(request.url) == "http://llm.internal:8000/v1/chat/completions"
    assert "authorization" not in request.headers  # a local server needs no key
    body = rec.body
    assert body["model"] == "qwen" and body["max_tokens"] == 500
    assert body["tools"][0]["function"]["name"] == "scorecard_compute"
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "tool"]
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"subject_id": "s1"}'
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": '{"score": 71}'}

    assert out.tool_calls == (
        ToolCall(id="c9", name="scorecard_compute", arguments={"subject_id": "s2"}),
    )
    assert (out.stop_reason, out.input_tokens, out.output_tokens) == ("tool_use", 120, 14)


def test_openai_compatible_sends_a_key_when_set(settings: Any) -> None:
    configure(settings, "openai_compatible", url="http://llm/v1", key="local-key")
    rec = Recorder({"choices": [{"finish_reason": "stop", "message": {"content": "Hi"}}]})
    out = provider_with(rec).complete([Message(role="user", content="hi")], [], max_tokens=10)
    assert rec.requests[0].headers["authorization"] == "Bearer local-key"
    assert "tools" not in rec.body
    assert (out.text, out.stop_reason) == ("Hi", "end")


def test_azure_openai_uses_deployment_url_and_api_key(settings: Any) -> None:
    configure(
        settings, "azure_openai", url="https://bank.openai.azure.com", model="gpt-prod", key="az"
    )
    rec = Recorder(CHAT_ANSWER)
    provider_with(rec).complete(CONVERSATION, TOOLS, max_tokens=50)
    request = rec.requests[0]
    assert str(request.url) == (
        "https://bank.openai.azure.com/openai/deployments/gpt-prod/chat/completions"
        "?api-version=2024-10-21"
    )
    assert request.headers["api-key"] == "az"
    assert "model" not in rec.body


# --- Bedrock Converse ----------------------------------------------------------


def test_bedrock_round_trip(settings: Any) -> None:
    configure(settings, "bedrock", region="eu-west-1", key="bk", model="anthropic.model-v1:0")
    rec = Recorder(
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "Checking."},
                        {
                            "toolUse": {
                                "toolUseId": "u1",
                                "name": "scorecard_compute",
                                "input": {"subject_id": "s3"},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 90, "outputTokens": 30},
        }
    )
    out = provider_with(rec).complete(
        [*CONVERSATION, Message(role="tool", tool_call_id="t2", content="{}")], TOOLS, max_tokens=64
    )
    request = rec.requests[0]
    assert str(request.url) == (
        "https://bedrock-runtime.eu-west-1.amazonaws.com/model/anthropic.model-v1%3A0/converse"
    )
    assert request.headers["authorization"] == "Bearer bk"
    body = rec.body
    assert body["system"] == [{"text": "Be grounded."}]
    assert body["inferenceConfig"] == {"maxTokens": 64}
    assert body["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["type"] == "object"
    # Roles alternate; both tool results share one user turn.
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"] == [
        {"toolUse": {"toolUseId": "t1", "name": "scorecard_compute", "input": {"subject_id": "s1"}}}
    ]
    assert [b["toolResult"]["toolUseId"] for b in body["messages"][2]["content"]] == ["t1", "t2"]

    assert out.text == "Checking."
    assert out.tool_calls == (
        ToolCall(id="u1", name="scorecard_compute", arguments={"subject_id": "s3"}),
    )
    assert (out.stop_reason, out.input_tokens, out.output_tokens) == ("tool_use", 90, 30)


# --- failures read as words, never stack traces -------------------------------


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "credentials"), (429, "rate-limiting"), (500, "HTTP 500")],
)
def test_http_failures(settings: Any, status: int, message: str) -> None:
    configure(settings, "openai_compatible", url="http://llm/v1")
    with pytest.raises(InferenceError, match=message):
        provider_with(Recorder({}, status=status)).complete([], [], max_tokens=1)


def test_unreachable_and_garbled(settings: Any) -> None:
    configure(settings, "openai_compatible", url="http://llm/v1")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(InferenceError, match="could not be reached"):
        inference.get_provider(transport=httpx.MockTransport(refuse)).complete([], [], max_tokens=1)
    with pytest.raises(InferenceError, match="no message"):
        provider_with(Recorder({"choices": []})).complete([], [], max_tokens=1)
    bad_args = {
        "choices": [
            {
                "message": {
                    "tool_calls": [{"id": "x", "function": {"name": "f", "arguments": "{oops"}}]
                }
            }
        ]
    }
    with pytest.raises(InferenceError, match="unreadable arguments"):
        provider_with(Recorder(bad_args)).complete([], [], max_tokens=1)


# --- the actions ---------------------------------------------------------------


@pytest.mark.django_db
class TestActions:
    def test_status_off_by_default(self, settings: Any, make_user: Callable[..., User]) -> None:
        settings.KPIGO_INFERENCE_PROVIDER = None
        out = invoke(
            registry.get("assistant.status"), {}, build_context(make_user("staff"), caller="ui")
        )
        assert out.available is False  # type: ignore[attr-defined]
        assert out.reason == "No model is connected to this install."  # type: ignore[attr-defined]

    def test_status_hides_the_endpoint_from_non_admins(
        self, settings: Any, make_user: Callable[..., User]
    ) -> None:
        configure(settings, "bedrock", region="eu-west-1", key="bk", model="m")
        status = registry.get("assistant.status")
        staff = invoke(status, {}, build_context(make_user("staff"), caller="ui"))
        admin = invoke(status, {}, build_context(make_user("admin"), caller="ui"))
        assert staff.available and staff.provider_label == "Amazon Bedrock"  # type: ignore[attr-defined]
        assert (staff.endpoint, staff.api_key_set) == (None, None)  # type: ignore[attr-defined]
        assert admin.endpoint == "bedrock-runtime.eu-west-1.amazonaws.com"  # type: ignore[attr-defined]
        assert admin.api_key_set is True  # type: ignore[attr-defined]

    def test_connection_test(
        self, settings: Any, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        test = registry.get("assistant.connection.test")
        admin = make_user("admin")
        settings.KPIGO_INFERENCE_PROVIDER = None
        out = invoke(test, {}, build_context(admin, caller="ui"))
        assert out.outcome == "not_configured"  # type: ignore[attr-defined]

        configure(settings, "openai_compatible", url="http://llm/v1")
        rec = Recorder(
            {
                "choices": [{"finish_reason": "stop", "message": {"content": "ready"}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 1},
            }
        )
        real = inference.get_provider
        monkeypatch.setattr(
            inference, "get_provider", lambda: real(transport=httpx.MockTransport(rec))
        )
        out = invoke(test, {}, build_context(admin, caller="ui"))
        assert (out.outcome, out.detail, out.input_tokens) == ("ok", "ready", 20)  # type: ignore[attr-defined]
        assert rec.body["max_tokens"] == 16
        row = AuditLog.objects.get(event="assistant.connection_result")
        assert row.payload["ok"] is True and row.payload["model"] == "m1"

        rec.status = 401
        out = invoke(test, {}, build_context(admin, caller="ui"))
        assert out.outcome == "failed" and "credentials" in out.detail  # type: ignore[attr-defined]
