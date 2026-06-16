"""UTCR-010: Tests for ToolCallingModeService."""
from __future__ import annotations

import pytest

from agent.services.tool_calling_mode_service import (
    MODE_DISABLED,
    MODE_NATIVE_OPENAI,
    MODE_PROMPT_JSON,
    ToolCallingModeService,
)


@pytest.fixture()
def svc() -> ToolCallingModeService:
    return ToolCallingModeService()


def test_known_backend_openai_resolves_native(svc):
    assert svc.resolve_mode(backend="openai") == MODE_NATIVE_OPENAI


def test_known_backend_ollama_resolves_native(svc):
    assert svc.resolve_mode(backend="ollama") == MODE_NATIVE_OPENAI


def test_unknown_backend_resolves_prompt_json(svc):
    assert svc.resolve_mode(backend="unknown-local") == MODE_PROMPT_JSON


def test_explicit_mode_prompt_json(svc):
    cfg = {"ananta_worker_tool_calling": {"mode": "prompt_json_protocol"}}
    assert svc.resolve_mode(config=cfg) == MODE_PROMPT_JSON


def test_explicit_mode_disabled(svc):
    cfg = {"ananta_worker_tool_calling": {"mode": "disabled"}}
    assert svc.resolve_mode(config=cfg) == MODE_DISABLED


def test_explicit_mode_native(svc):
    cfg = {"ananta_worker_tool_calling": {"mode": "native_openai_tools"}}
    assert svc.resolve_mode(config=cfg) == MODE_NATIVE_OPENAI


def test_denylist_overrides_allowlist(svc):
    cfg = {
        "ananta_worker_tool_calling": {
            "mode": "auto",
            "native_backend_allowlist": ["openai"],
            "native_backend_denylist": ["openai"],
        }
    }
    # denylist wins
    result = svc.resolve_mode(backend="openai", config=cfg)
    assert result == MODE_PROMPT_JSON


def test_is_native_capable_true_for_openai(svc):
    assert svc.is_native_capable(backend="openai") is True


def test_is_native_capable_false_for_unknown(svc):
    assert svc.is_native_capable(backend="my-custom-llm") is False


def test_custom_fallback_mode(svc):
    cfg = {
        "ananta_worker_tool_calling": {
            "mode": "auto",
            "native_backend_denylist": ["openai"],
            "fallback_mode": "disabled",
        }
    }
    assert svc.resolve_mode(backend="openai", config=cfg) == MODE_DISABLED


def test_no_config_no_backend_returns_prompt_json(svc):
    assert svc.resolve_mode() == MODE_PROMPT_JSON
