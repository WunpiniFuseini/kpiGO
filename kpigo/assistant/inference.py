"""Pluggable inference for the assistant (TDD §13, PRD AG-3).

The model is always the client's own: a local OpenAI-compatible server (vLLM,
Ollama), their Azure OpenAI tenant, or Bedrock in their AWS account. Configured
per install in settings, absent by default. Nothing here is imported at boot by
anything but the assistant, and nothing needs a model SDK: each adapter is a
plain HTTPS call through httpx, so an unconfigured install loads no inference
code paths and makes no calls (the no-inference CI gate).

The runtime talks to one neutral shape (messages, tool specs, a completion) and
each adapter translates it to and from its wire format. Adapters only translate.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
from django.conf import settings

ProviderName = Literal["openai_compatible", "azure_openai", "bedrock"]
PROVIDERS: tuple[ProviderName, ...] = ("openai_compatible", "azure_openai", "bedrock")
PROVIDER_LABELS: dict[str, str] = {
    "openai_compatible": "OpenAI-compatible endpoint",
    "azure_openai": "Azure OpenAI",
    "bedrock": "Amazon Bedrock",
}


class InferenceError(Exception):
    """The model could not be reached, refused the call, or answered nonsense."""


class NotConfigured(InferenceError):
    """No model is configured, or its configuration is incomplete."""


Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str = ""
    # An assistant turn that asked for tools.
    tool_calls: tuple[ToolCall, ...] = ()
    # A tool turn answers exactly one call.
    tool_call_id: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    # "end" (answered), "tool_use" (wants tools run), "max_tokens", or the raw reason.
    stop_reason: str = "end"


class Provider(Protocol):
    name: ProviderName
    model: str

    def complete(
        self, messages: list[Message], tools: list[ToolSpec], *, max_tokens: int
    ) -> Completion: ...


@dataclass(frozen=True)
class InferenceConfig:
    provider: ProviderName
    model: str
    url: str = ""
    api_key: str | None = field(default=None, repr=False)
    api_version: str = "2024-10-21"
    region: str = ""
    timeout_seconds: int = 60


def configured_provider_name() -> str | None:
    return getattr(settings, "KPIGO_INFERENCE_PROVIDER", None) or None


def load_config() -> InferenceConfig:
    """The install's inference settings, or NotConfigured saying what is missing."""
    name = configured_provider_name()
    if name is None:
        raise NotConfigured("No model is connected to this install.")
    if name not in PROVIDERS:
        raise NotConfigured(
            f"KPIGO_INFERENCE_PROVIDER '{name}' is not one of {', '.join(PROVIDERS)}."
        )
    model = getattr(settings, "KPIGO_INFERENCE_MODEL", None) or ""
    url = (getattr(settings, "KPIGO_INFERENCE_URL", None) or "").rstrip("/")
    region = getattr(settings, "KPIGO_INFERENCE_REGION", None) or ""
    api_key = getattr(settings, "KPIGO_INFERENCE_API_KEY", None)
    missing = []
    if not model:
        missing.append("KPIGO_INFERENCE_MODEL")
    if name in ("openai_compatible", "azure_openai") and not url:
        missing.append("KPIGO_INFERENCE_URL")
    if name in ("azure_openai", "bedrock") and not api_key:
        missing.append("KPIGO_INFERENCE_API_KEY")
    if name == "bedrock" and not (region or url):
        missing.append("KPIGO_INFERENCE_REGION")
    if missing:
        raise NotConfigured(f"{PROVIDER_LABELS[name]} needs {', '.join(missing)}.")
    return InferenceConfig(
        provider=name,  # type: ignore[arg-type]
        model=model,
        url=url,
        api_key=api_key,
        api_version=getattr(settings, "KPIGO_INFERENCE_API_VERSION", None) or "2024-10-21",
        region=region,
        timeout_seconds=int(getattr(settings, "KPIGO_INFERENCE_TIMEOUT_SECONDS", 60) or 60),
    )


def get_provider(transport: httpx.BaseTransport | None = None) -> Provider:
    """The configured provider. ``transport`` lets tests stand in for the model."""
    config = load_config()
    client = httpx.Client(timeout=config.timeout_seconds, transport=transport)
    if config.provider == "bedrock":
        return BedrockProvider(config, client)
    return ChatCompletionsProvider(config, client)


def _post(client: httpx.Client, url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
    try:
        response = client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        raise InferenceError("The model did not answer in time.") from None
    except httpx.HTTPError as exc:
        raise InferenceError(f"The model could not be reached ({type(exc).__name__}).") from None
    if response.status_code in (401, 403):
        raise InferenceError("The model refused kpiGo's credentials.")
    if response.status_code == 429:
        raise InferenceError("The model is rate-limiting kpiGo; try again shortly.")
    if response.status_code >= 400:
        raise InferenceError(f"The model answered HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError:
        raise InferenceError("The model's answer was not JSON.") from None


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise InferenceError("The model asked for a tool with unreadable arguments.") from None
    if not isinstance(parsed, dict):
        raise InferenceError("The model asked for a tool with non-object arguments.")
    return parsed


# --- OpenAI Chat Completions: local servers and Azure OpenAI -----------------


class ChatCompletionsProvider:
    """OpenAI's Chat Completions wire format: vLLM, Ollama, LM Studio and Azure OpenAI."""

    def __init__(self, config: InferenceConfig, client: httpx.Client) -> None:
        self.config = config
        self.client = client
        self.name: ProviderName = config.provider
        self.model = config.model

    def _endpoint(self) -> tuple[str, dict[str, str]]:
        if self.config.provider == "azure_openai":
            url = (
                f"{self.config.url}/openai/deployments/{self.config.model}/chat/completions"
                f"?api-version={self.config.api_version}"
            )
            return url, {"api-key": self.config.api_key or ""}
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        return f"{self.config.url}/chat/completions", headers

    def complete(
        self, messages: list[Message], tools: list[ToolSpec], *, max_tokens: int
    ) -> Completion:
        body: dict[str, Any] = {
            "messages": [self._message(m) for m in messages],
            "max_tokens": max_tokens,
        }
        if self.config.provider != "azure_openai":
            body["model"] = self.config.model
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        url, headers = self._endpoint()
        data = _post(self.client, url, body, headers)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise InferenceError("The model's answer had no message.") from None
        calls = tuple(
            ToolCall(
                id=str(c.get("id") or f"call_{i}"),
                name=str(c["function"]["name"]),
                arguments=_arguments(c["function"].get("arguments")),
            )
            for i, c in enumerate(message.get("tool_calls") or [])
        )
        usage = data.get("usage") or {}
        finish = choice.get("finish_reason") or "stop"
        return Completion(
            text=message.get("content") or "",
            tool_calls=calls,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            stop_reason=(
                "tool_use" if calls else {"stop": "end", "length": "max_tokens"}.get(finish, finish)
            ),
        )

    @staticmethod
    def _message(m: Message) -> dict[str, Any]:
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
        out: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            out["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in m.tool_calls
            ]
        return out


# --- Amazon Bedrock Converse ---------------------------------------------------


class BedrockProvider:
    """Bedrock's Converse API, authenticated with a Bedrock API key (bearer token)."""

    def __init__(self, config: InferenceConfig, client: httpx.Client) -> None:
        self.config = config
        self.client = client
        self.name: ProviderName = "bedrock"
        self.model = config.model

    def _url(self) -> str:
        base = self.config.url or f"https://bedrock-runtime.{self.config.region}.amazonaws.com"
        # Model ids carry ':' and inference-profile ARNs '/', so encode the segment.
        return f"{base}/model/{quote(self.config.model, safe='')}/converse"

    def complete(
        self, messages: list[Message], tools: list[ToolSpec], *, max_tokens: int
    ) -> Completion:
        system = [{"text": m.content} for m in messages if m.role == "system" and m.content]
        body: dict[str, Any] = {
            "messages": self._messages([m for m in messages if m.role != "system"]),
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if system:
            body["system"] = system
        if tools:
            body["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": {"json": t.parameters},
                        }
                    }
                    for t in tools
                ]
            }
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        data = _post(self.client, self._url(), body, headers)
        try:
            content = data["output"]["message"]["content"]
        except (KeyError, TypeError):
            raise InferenceError("The model's answer had no message.") from None
        text = "".join(block.get("text", "") for block in content if "text" in block)
        calls = tuple(
            ToolCall(
                id=str(block["toolUse"]["toolUseId"]),
                name=str(block["toolUse"]["name"]),
                arguments=_arguments(block["toolUse"].get("input")),
            )
            for block in content
            if "toolUse" in block
        )
        usage = data.get("usage") or {}
        reason = data.get("stopReason") or "end_turn"
        return Completion(
            text=text,
            tool_calls=calls,
            input_tokens=int(usage.get("inputTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
            stop_reason={"end_turn": "end", "tool_use": "tool_use", "max_tokens": "max_tokens"}.get(
                reason, reason
            ),
        )

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Converse alternates user/assistant; tool results ride in a user turn."""
        out: list[dict[str, Any]] = []
        for m in messages:
            role = "assistant" if m.role == "assistant" else "user"
            blocks: list[dict[str, Any]] = []
            if m.role == "tool":
                blocks.append(
                    {
                        "toolResult": {
                            "toolUseId": m.tool_call_id,
                            "content": [{"text": m.content}],
                        }
                    }
                )
            else:
                if m.content:
                    blocks.append({"text": m.content})
                blocks += [
                    {"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.arguments}}
                    for c in m.tool_calls
                ]
            if not blocks:
                continue
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})
        return out


# --- A connection check --------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    latency_ms: float
    detail: str
    input_tokens: int = 0
    output_tokens: int = 0


def probe(provider: Provider) -> ProbeResult:
    """One tiny round trip, so an Admin can see the model answers. Sends no client data."""
    started = time.monotonic()
    try:
        completion = provider.complete(
            [
                Message(role="system", content="You are a connectivity check. Answer briefly."),
                Message(role="user", content="Reply with the single word: ready"),
            ],
            [],
            max_tokens=16,
        )
    except InferenceError as exc:
        return ProbeResult(
            ok=False, latency_ms=round((time.monotonic() - started) * 1000, 1), detail=str(exc)
        )
    return ProbeResult(
        ok=True,
        latency_ms=round((time.monotonic() - started) * 1000, 1),
        detail=completion.text.strip()[:200] or "(empty answer)",
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
    )
