"""The application must remain fully functional with no model connected (TDD §13).

The CI ``no-inference`` job boots the whole stack with no inference configured and
runs this suite inside it. These tests make that boot assertion explicit.
"""

import os
import sys

import pytest
from django.conf import settings

from kpigo.action import registry

LLM_MODULES = ("openai", "anthropic", "pydantic_ai", "litellm", "langchain", "ollama", "boto3")


@pytest.mark.skipif(
    os.environ.get("KPIGO_REQUIRE_NO_INFERENCE") != "1",
    reason="only enforced in the no-inference CI job",
)
def test_no_inference_provider_is_configured() -> None:
    assert settings.KPIGO_INFERENCE_PROVIDER is None


def test_boot_imports_no_model_sdk() -> None:
    loaded = sorted(m for m in LLM_MODULES if m in sys.modules)
    assert not loaded, f"Booting kpiGo imported model SDKs: {loaded}"


def test_registry_boots_without_inference() -> None:
    assert "platform.hello" in registry
