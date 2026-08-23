from __future__ import annotations

import json
import ast
from pathlib import Path

import pytest

import scripts.setup_codecompass_index as setup_index
from agent.codecompass.parser_limits import ParserLimits
from ananta_contracts.file_type_support import load_file_type_support_registry


@pytest.fixture
def registry():
    return load_file_type_support_registry(Path(__file__).resolve().parents[1])


def _configure_scan(monkeypatch, tmp_path: Path, registry, paths: list[str]) -> None:
    monkeypatch.setattr(setup_index, "ROOT", tmp_path)
    monkeypatch.setattr(setup_index, "_load_registry", lambda: registry)
    monkeypatch.setattr(setup_index, "_repository_paths", lambda: paths)
    monkeypatch.setattr(setup_index, "_runtime_availability", lambda value: {})


def test_post_index_declares_repository_scope_for_graph_materialization(monkeypatch):
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"data":{"job":{"job_id":"job-1"}}}'

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(setup_index.urllib.request, "urlopen", urlopen)

    setup_index._post_index(
        "http://hub",
        "token",
        [{"file": "agent/app.py", "content": "pass"}],
        "ananta-revision",
    )

    assert captured["payload"]["source_scope"] == "repo_path"


def test_scan_reports_exact_names_unknown_text_binary_and_secrets(monkeypatch, tmp_path, registry):
    fixtures = {
        "app.py": "def run(): pass\n",
        "README.md": "# Docs\n",
        "Dockerfile": "FROM python:3.12\n",
        "notes.custom": "human readable\n",
        ".env": "TOKEN=must-not-be-read\n",
    }
    for relative, content in fixtures.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\0\x01")
    paths = [*fixtures, "blob.bin"]
    _configure_scan(monkeypatch, tmp_path, registry, paths)

    plan = setup_index._collect_index_plan(max_records=20)
    _records, finalized_coverage = setup_index._build_records_from_plan(plan)
    coverage = finalized_coverage.as_dict()
    by_path = {item["path"]: item for item in coverage["files"]}

    assert by_path["Dockerfile"]["detected_type"] == "dockerfile"
    assert by_path["notes.custom"]["detected_type"] == "unknown_text"
    assert by_path["blob.bin"]["detected_type"] == "unclassified_binary"
    assert by_path[".env"]["outcome"] == "excluded"
    assert by_path[".env"]["exclusion_reason"] == "secret_path"
    assert ".env" not in {candidate.relative_path for candidate in plan.selected}


def test_scan_rejects_symlinks_before_resolving_them(monkeypatch, tmp_path, registry):
    target = tmp_path / "target.py"
    target.write_text("def target(): pass\n", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to(target)
    _configure_scan(monkeypatch, tmp_path, registry, ["target.py", "linked.py"])

    plan = setup_index._collect_index_plan(max_records=20)
    by_path = {item["path"]: item for item in plan.coverage.as_dict()["files"]}

    assert {candidate.relative_path for candidate in plan.selected} == {"target.py"}
    assert by_path["linked.py"]["outcome"] == "excluded"
    assert by_path["linked.py"]["exclusion_reason"] == "unsafe_path_symlink_or_missing"


def test_scan_file_limit_balances_p0_families(monkeypatch, tmp_path, registry):
    paths = []
    for index in range(8):
        relative = f"module_{index}.py"
        (tmp_path / relative).write_text(f"def f_{index}(): pass\n", encoding="utf-8")
        paths.append(relative)
    for relative, content in (("README.md", "# Docs"), ("config.yaml", "enabled: true")):
        (tmp_path / relative).write_text(content, encoding="utf-8")
        paths.append(relative)
    _configure_scan(monkeypatch, tmp_path, registry, paths)

    plan = setup_index._collect_index_plan(max_records=3, priorities={"P0"})

    assert {candidate.descriptor.family for candidate in plan.selected} == {
        "code",
        "configuration",
        "documentation",
    }
    assert plan.truncated is True
    excluded = [
        item
        for item in plan.coverage.as_dict()["files"]
        if item["exclusion_reason"] == "max_files_fair_share"
    ]
    assert excluded


def test_scan_file_limit_reserves_required_snapshot_paths(monkeypatch, tmp_path, registry):
    paths = []
    for index in range(6):
        relative = f"module_{index}.py"
        (tmp_path / relative).write_text(f"def f_{index}(): pass\n", encoding="utf-8")
        paths.append(relative)
    required = tmp_path / "todos" / "plan.json"
    required.parent.mkdir(parents=True)
    required.write_text('{"tasks": []}\n', encoding="utf-8")
    paths.append("todos/plan.json")
    _configure_scan(monkeypatch, tmp_path, registry, paths)

    plan = setup_index._collect_index_plan(
        max_records=2,
        required_path_rules=["todos/**"],
    )
    setup_index._build_records_from_plan(plan)
    manifest = plan.coverage.snapshot_manifest(required_path_rules=["todos/**"])

    assert "todos/plan.json" in {candidate.relative_path for candidate in plan.selected}
    assert manifest["required_paths"]["passed"] is True


def test_record_builder_redacts_secret_values_and_persists_registry_evidence(
    monkeypatch,
    tmp_path,
    registry,
):
    (tmp_path / "config.yaml").write_text(
        "username: admin\napi_key: should-not-leak\npassword = hidden\n",
        encoding="utf-8",
    )
    _configure_scan(monkeypatch, tmp_path, registry, ["config.yaml"])
    plan = setup_index._collect_index_plan(max_records=5)

    records, coverage = setup_index._build_records_from_plan(plan)
    record = next(item for item in records if item.get("file") == "config.yaml")

    assert "should-not-leak" not in record["content"]
    assert "password = hidden" not in record["content"]
    assert record["content"].count("[REDACTED]") == 2
    assert record["file_type"]["registry_version"] == registry.registry_version
    file_coverage = next(item for item in coverage.as_dict()["files"] if item["path"] == "config.yaml")
    assert "secret_value_redacted" in file_coverage["diagnostics"]


def test_secret_redaction_preserves_python_parser_input():
    source = '''\
API_TOKEN: str = "sensitive"
SECOND_TOKEN = "sensitive"

def validate_token_file(metadata: object) -> bool:
    return metadata is not None
'''

    redacted, changed = setup_index._redact_sensitive_values(source)

    assert changed is True
    assert "sensitive" not in redacted
    assert redacted.count("[REDACTED]") == 2
    ast.parse(redacted)


def test_semantic_builder_uses_registry_for_typescript_and_honours_record_limit(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "card.tsx"
    source.write_text(
        "export class Card {}\nexport const Screen = () => <Card />;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_index, "ROOT", tmp_path)
    monkeypatch.setenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED", "true")
    monkeypatch.setenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_LANGUAGES", "all")
    monkeypatch.setenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_MAX_GRAPH_RECORDS", "2")

    records, summary = setup_index._build_semantic_translation_records([source])

    assert len(records) == 2
    assert summary["recognized_languages"] == ["typescript"]
    assert summary["parser_strategies"]["typescript"] == "structural-typescript-v1"
    assert "semantic_graph_record_limit_reached" in summary["warnings"]


def test_semantic_builder_contains_registry_parser_failures(monkeypatch, tmp_path):
    from agent.codecompass.semantic_translation import registry as semantic_registry_module
    from agent.codecompass.semantic_translation.registry import SemanticAdapterRegistry

    class FailingJavaAdapter:
        language = "java"
        supported_extensions = (".java",)
        parser_strategy = "failing-java-test-parser"
        known_limits = ("test-only",)
        semantic_kinds = ()

        def detect(self, path: str, content: str) -> bool:
            return path.endswith(".java")

        def emit_graph_records(self, path: str, content: str) -> dict:
            raise RuntimeError("untrusted parser detail")

    source = tmp_path / "Unsafe.java"
    source.write_text("public record Unsafe(String value) {}", encoding="utf-8")
    monkeypatch.setattr(setup_index, "ROOT", tmp_path)
    monkeypatch.setenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED", "true")
    monkeypatch.setenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_LANGUAGES", "java")
    monkeypatch.setattr(
        semantic_registry_module,
        "_DEFAULT_REGISTRY",
        SemanticAdapterRegistry([FailingJavaAdapter()], telemetry=lambda **_values: None),
    )

    records, summary = setup_index._build_semantic_translation_records([source])

    assert not any((record.get("provenance") or {}).get("file") == "Unsafe.java" for record in records)
    assert summary["node_count"] == 0
    assert "parser_failed" in summary["warnings"]
    assert summary["diagnostics"][0]["message"] == "Semantic adapter failed safely (RuntimeError)."


def test_coverage_payload_is_json_serializable(monkeypatch, tmp_path, registry):
    (tmp_path / "README.md").write_text("# Docs", encoding="utf-8")
    _configure_scan(monkeypatch, tmp_path, registry, ["README.md"])
    plan = setup_index._collect_index_plan(max_records=1)
    setup_index._build_records_from_plan(plan)

    encoded = json.dumps(plan.coverage.as_dict(), sort_keys=True)

    assert "codecompass.file-type-coverage.v1" in encoded


def test_scan_rejects_unknown_or_conflicting_format_flags(monkeypatch, tmp_path, registry):
    _configure_scan(monkeypatch, tmp_path, registry, [])

    with pytest.raises(ValueError, match="unknown_file_type_format"):
        setup_index._collect_index_plan(enabled_formats={"not-a-format"})
    with pytest.raises(ValueError, match="conflicting_file_type_format"):
        setup_index._collect_index_plan(
            enabled_formats={"python"},
            disabled_formats={"python"},
        )


def test_record_builder_applies_shared_line_limit_and_reports_diagnostic(
    monkeypatch,
    tmp_path,
    registry,
):
    (tmp_path / "README.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _configure_scan(monkeypatch, tmp_path, registry, ["README.md"])
    plan = setup_index._collect_index_plan(
        max_records=5,
        limits=ParserLimits(max_lines=2),
    )

    records, coverage = setup_index._build_records_from_plan(plan)
    record = next(item for item in coverage.as_dict()["files"] if item["path"] == "README.md")

    assert "README.md" not in {item.get("file") for item in records}
    assert record["outcome"] == "excluded"
    assert record["exclusion_reason"] == "line_limit"
    assert record["diagnostics"] == ["line_limit", "parser_limit_exceeded"]


def test_record_builder_measures_selected_file_duration(monkeypatch, tmp_path, registry):
    (tmp_path / "README.md").write_text("# Docs\n", encoding="utf-8")
    _configure_scan(monkeypatch, tmp_path, registry, ["README.md"])
    plan = setup_index._collect_index_plan(max_records=1)
    ticks = iter((10.0, 10.25))
    monkeypatch.setattr(setup_index.time, "perf_counter", lambda: next(ticks))

    setup_index._build_records_from_plan(plan)

    record = next(item for item in plan.coverage.as_dict()["files"] if item["path"] == "README.md")
    assert record["duration_seconds"] == 0.25


def test_scan_snapshot_records_content_hash_and_required_path_gate(monkeypatch, tmp_path, registry):
    source = tmp_path / "AGENTS.md"
    source.write_text("# Rules\n", encoding="utf-8")
    _configure_scan(monkeypatch, tmp_path, registry, ["AGENTS.md"])

    plan = setup_index._collect_index_plan(max_records=1)
    setup_index._build_records_from_plan(plan)
    manifest = plan.coverage.snapshot_manifest(
        required_path_rules=["AGENTS.md"],
        profile={"profile_id": "test"},
    )

    assert manifest["required_paths"]["passed"] is True
    assert manifest["files"][0]["content_sha256"]
    assert manifest["files"][0]["extractor_id"] == "setup_index.plain_text"
