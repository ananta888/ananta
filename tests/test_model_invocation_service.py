from __future__ import annotations

import json
from types import SimpleNamespace

from agent.services.model_invocation_service import ModelInvocationService
from agent.services.propose_runtime_policy import (
    _calibrated_timeout_from_benchmarks,
    resolve_propose_llm_timeout_seconds,
)


def test_normalize_openai_tools_converts_flat_registry_shape() -> None:
    tools = [
        {
            "name": "write_file",
            "description": "write a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    normalized = ModelInvocationService._normalize_openai_tools(tools)
    assert len(normalized) == 1
    item = normalized[0]
    assert item["type"] == "function"
    assert item["function"]["name"] == "write_file"
    assert item["function"]["description"] == "write a file"
    assert item["function"]["parameters"]["type"] == "object"


def test_make_chat_call_sends_normalized_tools_payload(monkeypatch) -> None:
    captured: dict = {}

    def _fake_post(url, json, headers, timeout):  # noqa: ANN001
        captured["url"] = url
        captured["body"] = dict(json or {})
        captured["timeout"] = timeout
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "ok", "tool_calls": []}, "finish_reason": "stop"}],
                "usage": {},
                "model": "local-model",
            },
        )

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", _fake_post)
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(  # noqa: ARG005
                default_provider="lmstudio",
                default_model="auto",
                lmstudio_url="http://localhost:1234/v1",
                ollama_url="http://localhost:11434/api/generate",
                openai_url="https://api.openai.com/v1",
                openai_api_key=None,
                mock_url="http://mock",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )

    ModelInvocationService.invoke_with_tools(
        prompt="hello",
        tools=[{"name": "file_read", "description": "d", "parameters": {"type": "object", "properties": {}}}],
        timeout=333,
    )

    assert captured["timeout"] == 333
    assert captured["body"]["tools"][0]["type"] == "function"
    assert captured["body"]["tools"][0]["function"]["name"] == "file_read"


def test_resolve_propose_llm_timeout_seconds_uses_effective_config() -> None:
    cfg = {
        "task_propose_timeout_seconds": 420,
        "command_timeout": 60,
        "task_kind_execution_policies": {"coding": {"command_timeout": 180}},
    }
    timeout = resolve_propose_llm_timeout_seconds(effective_config=cfg, task_kind="coding")
    assert timeout == 420


def test_calibrated_timeout_from_benchmarks_uses_p95_with_buffer(tmp_path) -> None:
    data_dir = str(tmp_path)
    payload = {
        "models": {
            "lmstudio:auto": {
                "provider": "lmstudio",
                "model": "auto",
                "task_kinds": {
                    "coding": {
                        "samples": [
                            {"latency_ms": 10000},
                            {"latency_ms": 12000},
                            {"latency_ms": 18000},
                            {"latency_ms": 25000},
                            {"latency_ms": 30000},
                        ]
                    }
                },
            }
        }
    }
    with open(tmp_path / "llm_model_benchmarks.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    timeout = _calibrated_timeout_from_benchmarks(
        data_dir=data_dir,
        provider="lmstudio",
        model="auto",
        task_kind="coding",
        floor_seconds=60,
        ceiling_seconds=1200,
    )
    assert timeout is not None
    assert timeout >= 83  # 30s p95 * 2.5 + 8s


def test_resolve_propose_timeout_prefers_calibrated_when_higher(tmp_path, monkeypatch) -> None:
    payload = {
        "models": {
            "lmstudio:auto": {
                "provider": "lmstudio",
                "model": "auto",
                "task_kinds": {"coding": {"samples": [{"latency_ms": 60000}, {"latency_ms": 58000}, {"latency_ms": 62000}]}}
            }
        }
    }
    with open(tmp_path / "llm_model_benchmarks.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    monkeypatch.setattr("agent.services.propose_runtime_policy._resolve_data_dir", lambda: str(tmp_path))
    cfg = {
        "default_provider": "lmstudio",
        "default_model": "auto",
        "task_propose_timeout_seconds": 120,
    }
    timeout = resolve_propose_llm_timeout_seconds(effective_config=cfg, task_kind="coding")
    assert timeout > 120
