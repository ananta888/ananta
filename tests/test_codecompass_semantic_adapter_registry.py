from __future__ import annotations

import pytest

from agent.codecompass.semantic_translation.adapters import JavaSemanticAdapter
from agent.codecompass.semantic_translation.registry import SemanticAdapterRegistry
from agent.codecompass.semantic_translation.symbol_adapters import RegexSymbolLanguageAdapter
from agent.codecompass.semantic_translation.typescript_adapter import TypeScriptSemanticAdapter


def test_default_registry_discovers_python_java_typescript_and_fallback_languages():
    registry = SemanticAdapterRegistry()

    assert registry.find("service.py").language == "python"
    assert registry.find("Service.java").language == "java"
    assert registry.find("component.tsx").language == "typescript"
    assert registry.find("main.go").language == "go"
    assert registry.find("native.cpp").language == "cpp"
    assert registry.find("unknown.xyz") is None
    languages = [item["language"] for item in registry.support_matrix()]
    assert languages == sorted(languages)


def test_enabled_semantic_translation_defaults_cover_all_registered_production_languages():
    from agent.codecompass.semantic_translation.config import load_semantic_translation_config

    config = load_semantic_translation_config({"ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED": "true"})
    registered = {adapter.language for adapter in SemanticAdapterRegistry().adapters()}

    assert registered <= set(config.source_languages)
    assert config.adapters == ("semantic-adapter-registry-v1",)


def test_registry_rejects_competing_extension_owners():
    first = TypeScriptSemanticAdapter()
    second = RegexSymbolLanguageAdapter(
        language="duplicate",
        supported_extensions=(".ts",),
        type_pattern="",
        function_pattern="",
        import_pattern="",
        known_limits=(),
    )

    with pytest.raises(ValueError, match="duplicate_semantic_adapter_extension"):
        SemanticAdapterRegistry([first, second])


def test_registry_returns_structured_diagnostic_for_unsupported_file():
    result = SemanticAdapterRegistry([]).emit_graph_records("unknown.xyz", "value")

    assert result["nodes"] == []
    assert result["diagnostics"][0]["code"] == "semantic_adapter_unsupported"
    assert result["diagnostics"][0]["path"] == "unknown.xyz"


def test_registry_contains_adapter_exceptions_and_emits_failure_telemetry():
    class FailingAdapter:
        language = "failing"
        supported_extensions = (".fail",)
        parser_strategy = "failing-test-parser"
        known_limits = ("test-only",)
        semantic_kinds = ()

        def detect(self, path: str, content: str) -> bool:
            return path.endswith(".fail")

        def parse(self, path: str, content: str) -> dict:
            raise RuntimeError("secret parser detail must not escape")

        def emit_graph_records(self, path: str, content: str) -> dict:
            raise RuntimeError("secret graph detail must not escape")

    events: list[dict] = []
    registry = SemanticAdapterRegistry(
        [FailingAdapter()],
        telemetry=lambda **values: events.append(values),
    )

    parsed = registry.parse("source.fail", "safe")
    graph = registry.emit_graph_records_for_language(
        "failing",
        "source.fail",
        "safe",
    )

    assert parsed["types"] == []
    assert graph["nodes"] == []
    assert parsed["diagnostics"][0]["code"] == "parser_failed"
    assert graph["diagnostics"][0]["code"] == "parser_failed"
    assert "secret" not in parsed["diagnostics"][0]["message"]
    assert "secret" not in graph["diagnostics"][0]["message"]
    assert [event["outcome"] for event in events] == ["failed", "failed"]
    assert all(event["diagnostics"] == ("parser_failed",) for event in events)


def test_typescript_adapter_extracts_types_imports_exports_and_relationships():
    source = """
import {Base} from './base';
export interface Named { name: string }
export class UserCard extends Base implements Named { }
export type UserId = string;
export function load(id: UserId): Promise<UserCard> { return fetchCard(id); }
"""
    adapter = TypeScriptSemanticAdapter()
    parsed = adapter.parse("src/user.ts", source)
    emitted = adapter.emit_graph_records("src/user.ts", source)

    assert {item["name"] for item in parsed["types"]} >= {"Named", "UserCard", "UserId"}
    assert {item["name"] for item in parsed["functions"]} >= {"load"}
    assert parsed["imports"][0]["module"] == "./base"
    edge_types = {edge["edge_type"] for edge in emitted["edges"]}
    assert {"imports", "exports", "extends", "implements"} <= edge_types
    assert any(node["semantic_kind"] == "type_alias" and node["symbol"] == "UserId" for node in emitted["nodes"])


def test_typescript_adapter_marks_angular_selectors_and_jsx_references():
    source = """
@Component({ selector: 'app-user-card', template: '<span />' })
export class UserCardComponent {}
export const Screen = () => <UserCardComponent />;
"""
    emitted = TypeScriptSemanticAdapter().emit_graph_records("src/card.tsx", source)

    component = next(node for node in emitted["nodes"] if node["symbol"] == "UserCardComponent")
    assert component["semantic_kind"] == "component"
    assert component["attributes"]["selector"] == "app-user-card"
    assert any(edge["edge_type"] == "references" for edge in emitted["edges"])


def test_typescript_adapter_keeps_partial_results_for_invalid_syntax():
    parsed = TypeScriptSemanticAdapter().parse("broken.ts", "export class Broken {")

    assert parsed["types"][0]["name"] == "Broken"
    assert any(item["code"] == "typescript_unbalanced_syntax" for item in parsed["diagnostics"])


def test_java_adapter_prefers_tree_sitter_for_nested_generics_and_annotations():
    source = """
package demo;
import java.util.List;
@Deprecated public class Outer<T extends Number> extends Base implements Named, Stored {
    private List<T> values;
    public Outer(List<T> values) {}
    @Override public <R> R map(T value) throws IOException { return null; }
    record Inner(String name, int age) {}
}
"""
    adapter = JavaSemanticAdapter()
    parsed = adapter.parse("src/Outer.java", source)
    emitted = adapter.emit_graph_records("src/Outer.java", source)

    assert parsed["parser_strategy"].startswith("tree-sitter-java-v1")
    outer = next(item for item in parsed["types"] if item["name"] == "Outer")
    inner = next(item for item in parsed["types"] if item["name"] == "Inner")
    assert outer["type_parameters"] == "<T extends Number>"
    assert outer["extends"] == ["Base"]
    assert outer["implements"] == ["Named", "Stored"]
    assert "@Deprecated" in outer["annotations"]
    assert any(method["kind"] == "constructor" for method in outer["methods"])
    assert any(method["type_parameters"] == "<R>" for method in outer["methods"])
    assert inner["qualified_name"] == "Outer.Inner"
    assert parsed["imports"][0]["name"] == "java.util.List"
    assert {edge["edge_type"] for edge in emitted["edges"]} >= {
        "imports",
        "extends",
        "implements",
        "declares",
        "throws",
    }


def test_java_adapter_reports_reduced_confidence_regex_fallback(monkeypatch):
    from agent.codecompass.semantic_translation import java_tree_sitter
    from agent.repository_map_tree_sitter import TreeSitterParserResolution, TreeSitterRuntimeStatus

    monkeypatch.setattr(
        java_tree_sitter,
        "resolve_tree_sitter_parser",
        lambda language: TreeSitterParserResolution(
            parser=None,
            status=TreeSitterRuntimeStatus(
                language=language,
                available=False,
                strategy="none",
                diagnostics=("specific_grammar_unavailable:ImportError",),
            ),
        ),
    )

    parsed = JavaSemanticAdapter().parse("src/Fallback.java", "public record Fallback(String name) {}")

    assert parsed["parser_strategy"] == "regex-java-v1"
    assert parsed["confidence"] == 0.58
    assert parsed["types"][0]["name"] == "Fallback"
    assert {item["code"] for item in parsed["diagnostics"]} >= {
        "specific_grammar_unavailable:ImportError",
        "java_regex_fallback",
    }

    events: list[dict] = []
    registry = SemanticAdapterRegistry(
        [JavaSemanticAdapter()],
        telemetry=lambda **values: events.append(values),
    )
    graph = registry.emit_graph_records("src/Fallback.java", "public record Fallback(String name) {}")

    assert graph["fallback_reason"] == "tree_sitter_unavailable_or_invalid"
    assert events[0]["fallback_reason"] == "parser_fallback"


@pytest.mark.parametrize(
    ("path", "source", "language", "expected"),
    [
        ("main.go", 'import "fmt"\ntype User struct {}\nfunc Load() {}', "go", {"User", "Load"}),
        ("lib.rs", "use std::fmt;\npub struct User {}\npub fn load() {}", "rust", {"User", "load"}),
        ("native.c", "#include <stdio.h>\nstruct User { int id; };\nvoid load() {}", "c", {"User", "load"}),
        ("native.cpp", "#include <vector>\nclass User {};\nvoid load() {}", "cpp", {"User", "load"}),
        ("a.cs", "using System;\npublic class User {}\npublic void Load() {}", "csharp", {"User", "Load"}),
        ("a.rb", "require 'json'\nclass User\n def load\n end\nend", "ruby", {"User", "load"}),
        ("a.php", "<?php\nuse App\\Base;\nclass User {}\nfunction load() {}", "php", {"User", "load"}),
    ],
)
def test_symbol_fallback_adapters_are_explicit_and_provenanced(path, source, language, expected):
    adapter = next(item for item in SemanticAdapterRegistry().adapters() if item.language == language)
    parsed = adapter.parse(path, source)
    emitted = adapter.emit_graph_records(path, source)

    assert expected <= {item["name"] for group in ("types", "functions") for item in parsed[group]}
    assert parsed["confidence"] == 0.55
    assert parsed["diagnostics"][0]["code"] == "semantic_symbol_fallback"
    assert all(node["attributes"]["support_level"] == "symbol_index" for node in emitted["nodes"])
    assert all(node["provenance"]["file"] == path for node in emitted["nodes"])
