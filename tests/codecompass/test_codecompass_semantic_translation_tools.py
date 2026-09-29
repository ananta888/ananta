from __future__ import annotations

from agent.codecompass.parser_limits import ParserLimits
from agent.codecompass.semantic_translation.registry import SemanticAdapterRegistry
from agent.codecompass.semantic_translation.transform import DeterministicTransformEngine, TransformRequest
from agent.services.tools import codecompass_tools, execute_ananta_tool


def test_translation_plan_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED", raising=False)
    result = execute_ananta_tool(
        tool_name="codecompass.translation_plan",
        arguments={"source_path": "UserDto.java", "source_code": "public record UserDto(String name) {}", "target_language": "typescript"},
        workspace_dir=".",
        tool_call_id="call-1",
    )

    assert result["status"] == "error"
    assert result["error"] == "semantic_translation_disabled"


def test_translation_plan_enabled_returns_plan(monkeypatch):
    monkeypatch.setenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED", "true")
    result = execute_ananta_tool(
        tool_name="codecompass.translation_plan",
        arguments={"source_path": "UserDto.java", "source_code": "public record UserDto(String name) {}", "target_language": "typescript"},
        workspace_dir=".",
        tool_call_id="call-2",
    )

    assert result["status"] == "ok"
    assert result["data"]["plan"]["classification"] in {"safe_auto_transform", "needs_review"}
    assert "export interface UserDto" in result["data"]["plan"]["target_artifacts"][0]["preview"]


def test_translation_plan_applies_registry_limits_without_raising(monkeypatch):
    from agent.codecompass.semantic_translation import registry as semantic_registry_module

    events: list[dict] = []
    monkeypatch.setenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED", "true")
    monkeypatch.setattr(
        semantic_registry_module,
        "_DEFAULT_REGISTRY",
        SemanticAdapterRegistry(
            limits=ParserLimits(max_file_bytes=8),
            telemetry=lambda **values: events.append(values),
        ),
    )

    result = execute_ananta_tool(
        tool_name="codecompass.translation_plan",
        arguments={
            "source_path": "Oversized.java",
            "source_code": "public record Oversized(String value) {}",
            "target_language": "typescript",
        },
        workspace_dir=".",
        tool_call_id="call-limited",
    )

    assert result["status"] == "ok"
    assert result["data"]["plan"]["classification"] == "unsupported"
    assert "parser_limit_exceeded" in result["warnings"]
    assert events
    assert all(event["outcome"] == "excluded" for event in events)


def test_verify_translation_tool_reports_verified_with_warnings():
    source = "public record UserDto(String name) {}"
    artifact = DeterministicTransformEngine().transform(TransformRequest(source_path="UserDto.java", source_code=source, target_language="typescript"))
    result = execute_ananta_tool(
        tool_name="codecompass.verify_translation",
        arguments={"source_path": "UserDto.java", "source_code": source, "target_code": artifact["target_code"], "transform_artifact": artifact},
        workspace_dir=".",
        tool_call_id="call-3",
    )

    assert result["status"] == "ok"
    assert result["data"]["verification"]["status"] in {"verified", "verified_with_warnings"}


def test_semantic_equivalents_degrades_when_index_missing(monkeypatch):
    monkeypatch.setattr(codecompass_tools, "_resolve_graph_store", lambda args: (None, None))
    result = codecompass_tools.codecompass_semantic_equivalents(
        workspace_dir=".",
        arguments={"symbol": "UserDto", "target_languages": ["typescript"]},
        tool_call_id="call-4",
    )

    assert result["status"] == "degraded"
    assert result["error"] == "semantic_translation_index_unavailable"
