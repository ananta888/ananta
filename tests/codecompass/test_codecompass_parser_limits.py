from __future__ import annotations

import time

import pytest

from agent.codecompass.parser_limits import (
    ParserGuardViolation,
    ParserLimits,
    ParserSecurityViolation,
    redact_secret_values,
)
from agent.codecompass.semantic_translation.adapters import DummySemanticAdapter
from agent.codecompass.semantic_translation.registry import SemanticAdapterRegistry
from agent.repository_map_engine import RepositoryMapEngine


def test_parser_limits_distinguish_size_lines_and_security():
    limits = ParserLimits(max_file_bytes=8, max_lines=2)

    with pytest.raises(ParserGuardViolation) as size:
        limits.preflight(path="src/a.py", content="123456789")
    with pytest.raises(ParserGuardViolation) as lines:
        limits.preflight(path="src/a.py", content="a\nb\nc")
    with pytest.raises(ParserSecurityViolation) as traversal:
        limits.preflight(path="../secret.py", content="ok")

    assert size.value.reason_code == "file_size_limit"
    assert lines.value.reason_code == "line_limit"
    assert traversal.value.reason_code == "path_traversal"


def test_semantic_registry_fails_closed_on_limits_and_traversal():
    registry = SemanticAdapterRegistry(
        [DummySemanticAdapter()],
        limits=ParserLimits(max_file_bytes=4, max_lines=10),
    )

    oversized = registry.emit_graph_records("safe.dummy", "dummy-too-large")
    traversal = registry.emit_graph_records("../unsafe.dummy", "x")

    assert oversized["diagnostics"][0]["code"] == "parser_limit_exceeded"
    assert traversal["diagnostics"][0]["code"] == "security_blocked"


def test_semantic_registry_reports_wall_time_timeout():
    class SlowAdapter(DummySemanticAdapter):
        def emit_graph_records(self, path: str, content: str) -> dict:
            time.sleep(0.005)
            return super().emit_graph_records(path, content)

    registry = SemanticAdapterRegistry(
        [SlowAdapter()],
        limits=ParserLimits(parser_timeout_ms=1),
    )

    result = registry.emit_graph_records("slow.dummy", "x")

    assert result["diagnostics"][0]["code"] == "parser_timeout"
    assert result["nodes"] == []


def test_parser_limits_load_from_environment_and_validate_values():
    limits = ParserLimits.from_environment(
        {
            "ANANTA_CODECOMPASS_MAX_FILE_BYTES": "12",
            "ANANTA_CODECOMPASS_MAX_YAML_ALIASES": "3",
        }
    )

    assert limits.max_file_bytes == 12
    assert limits.max_yaml_aliases == 3
    with pytest.raises(ValueError, match="invalid_parser_limit"):
        ParserLimits.from_environment({"ANANTA_CODECOMPASS_MAX_CSV_ROWS": "invalid"})


def test_secret_redaction_never_returns_sensitive_values():
    redacted, count = redact_secret_values("user=admin\napi_key=abc\npassword: xyz")

    assert count == 2
    assert "abc" not in redacted and "xyz" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_repository_map_enforces_shared_limits_and_exposes_diagnostic(tmp_path, monkeypatch):
    source = tmp_path / "oversized.py"
    source.write_text("def first(): pass\ndef second(): pass\n", encoding="utf-8")
    engine = RepositoryMapEngine(tmp_path, limits=ParserLimits(max_lines=1))
    monkeypatch.setattr(engine, "_tracked_files", lambda: [source])

    engine.build(force=True)

    assert engine._symbol_graph == {}
    diagnostics = engine.parser_diagnostics()
    assert len(diagnostics) == 1
    assert diagnostics[0]["code"] == "parser_limit_exceeded"
    assert diagnostics[0]["reason_code"] == "line_limit"


def test_parser_pipelines_emit_injected_operational_telemetry(tmp_path, monkeypatch):
    semantic_events: list[dict] = []
    semantic = SemanticAdapterRegistry(
        [DummySemanticAdapter()],
        telemetry=lambda **values: semantic_events.append(values),
    )

    semantic.emit_graph_records("example.dummy", "dummy")

    assert semantic_events[0]["pipeline"] == "semantic_translation"
    assert semantic_events[0]["outcome"] == "indexed"

    source = tmp_path / "example.py"
    source.write_text("def example(): pass\n", encoding="utf-8")
    repository_events: list[dict] = []
    engine = RepositoryMapEngine(
        tmp_path,
        telemetry=lambda **values: repository_events.append(values),
    )
    monkeypatch.setattr(engine, "_tracked_files", lambda: [source])

    engine.build(force=True)

    assert repository_events[0]["pipeline"] == "repository_map"
    assert repository_events[0]["outcome"] == "indexed"
    assert repository_events[0]["symbol_count"] == 1
