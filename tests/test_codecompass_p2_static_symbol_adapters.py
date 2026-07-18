from __future__ import annotations

from pathlib import Path

import pytest

from agent.codecompass.parser_limits import ParserLimits
from agent.codecompass.semantic_translation.registry import SemanticAdapterRegistry
from agent.services.file_type_support_service import FileTypeSupportFilter, FileTypeSupportService
from ananta_contracts.file_type_classifier import FileTypeClassifier
from ananta_contracts.file_type_support import (
    CapabilityDimension,
    CapabilityImplementation,
    load_file_type_support_registry,
)

ROOT = Path(__file__).resolve().parents[1]

_P2_LIMIT_PATHS = (
    ("src/Limit.kt", "kotlin"),
    ("build.gradle.kts", "kotlin"),
    ("Sources/Limit.swift", "swift"),
    ("src/Limit.scala", "scala"),
    ("src/limit.lua", "lua"),
    ("lib/limit.dart", "dart"),
    ("src/LimitCard.vue", "vue"),
    ("src/LimitCard.svelte", "svelte"),
)

_P2_RECORD_LIMIT_CASES = (
    ("src/Limit.kt", "kotlin", "class First\nclass Second"),
    ("build.gradle.kts", "kotlin", "val first = 1\nval second = 2"),
    ("Sources/Limit.swift", "swift", "struct First {}\nstruct Second {}"),
    ("src/Limit.scala", "scala", "class First\nclass Second"),
    ("src/limit.lua", "lua", "local first = {}\nlocal second = {}"),
    ("lib/limit.dart", "dart", "class First {}\nclass Second {}"),
    (
        "src/LimitCard.vue",
        "vue",
        "<script>\nclass First {}\nclass Second {}\n</script>",
    ),
    (
        "src/LimitCard.svelte",
        "svelte",
        "<script>\nclass First {}\nclass Second {}\n</script>",
    ),
)


@pytest.mark.parametrize(
    ("path", "language", "source", "expected_symbols", "expected_import"),
    [
        (
            "src/User.kt",
            "kotlin",
            """package demo
import kotlinx.coroutines.flow.Flow
data class User(val id: String)
suspend fun load(id: String) = id
val current = User("1")
""",
            {"User", "load", "current"},
            "kotlinx.coroutines.flow.Flow",
        ),
        (
            "build.gradle.kts",
            "kotlin",
            """import org.gradle.api.tasks.testing.logging.TestLogEvent
class BuildInfo
fun configureBuild() {}
val integrationTest = tasks.register("integrationTest")
""",
            {"BuildInfo", "configureBuild", "integrationTest"},
            "org.gradle.api.tasks.testing.logging.TestLogEvent",
        ),
        (
            "Sources/User.swift",
            "swift",
            """import Foundation
public struct User {
    static func load() {}
    let id: String
}
""",
            {"User", "load", "id"},
            "Foundation",
        ),
        (
            "src/User.scala",
            "scala",
            """import scala.concurrent.Future
final case class User(id: String)
object Loader {
  def load(id: String) = Future.successful(id)
}
""",
            {"User", "Loader", "load"},
            "scala.concurrent.Future",
        ),
        (
            "src/user.lua",
            "lua",
            """local json = require("json")
local User = {}
function User.load(id) return id end
""",
            {"json", "User", "User.load"},
            "json",
        ),
        (
            "lib/user.dart",
            "dart",
            """import 'dart:async';
final class User {}
Future<User> load(String id) async => User();
final current = User();
""",
            {"User", "load", "current"},
            "dart:async",
        ),
        (
            "src/UserCard.vue",
            "vue",
            """<template><p>User</p></template>
<script setup lang="ts">
import {ref} from "vue";
interface User { id: string }
const selected = ref<User>();
function load() {}
</script>
""",
            {"UserCard", "User", "selected", "load"},
            "vue",
        ),
        (
            "src/UserCard.svelte",
            "svelte",
            """<script lang="ts">
import Child from "./Child.svelte";
export let user;
function load() {}
</script>
<Child />
""",
            {"UserCard", "user", "load"},
            "./Child.svelte",
        ),
    ],
)
def test_p2_static_symbol_adapter_golden_cases(
    path: str,
    language: str,
    source: str,
    expected_symbols: set[str],
    expected_import: str,
) -> None:
    registry = SemanticAdapterRegistry()
    adapter = registry.find(path, source)

    assert adapter is not None
    assert adapter.language == language
    parsed = adapter.parse(path, source)
    emitted = adapter.emit_graph_records(path, source)

    assert expected_symbols <= {item["name"] for item in parsed["symbols"]}
    assert expected_import in {item["name"] for item in parsed["imports"]}
    assert parsed["support_level"] == "symbol_index"
    assert 0.0 < parsed["confidence"] <= 0.5
    assert all(0.0 < item["confidence"] <= 0.5 for item in parsed["symbols"])
    assert "symbol_index only" in " ".join(parsed["known_limits"])
    assert parsed["diagnostics"][0]["code"] == "semantic_static_symbol_index"
    assert adapter.extract_semantics(parsed) == []

    assert emitted["edges"] == []
    assert emitted["support_level"] == "symbol_index"
    assert {node["symbol"] for node in emitted["nodes"]} >= expected_symbols
    assert all(node["attributes"]["support_level"] == "symbol_index" for node in emitted["nodes"])
    assert all(node["provenance"]["file"] == path for node in emitted["nodes"])


@pytest.mark.parametrize(
    ("extension", "language"),
    [
        (".kt", "kotlin"),
        (".kts", "kotlin"),
        (".swift", "swift"),
        (".scala", "scala"),
        (".lua", "lua"),
        (".dart", "dart"),
        (".vue", "vue"),
        (".svelte", "svelte"),
    ],
)
def test_p2_descriptors_state_static_symbol_limits_without_semantic_claims(
    extension: str,
    language: str,
) -> None:
    descriptor = next(
        item
        for item in SemanticAdapterRegistry().descriptors()
        if extension in item.extensions
    )

    assert descriptor.language == language
    assert descriptor.parser_strategy in {"regex-static-symbol-v1", "regex-sfc-script-symbol-v1"}
    assert descriptor.semantic_kinds == ()
    assert any("symbol_index only" in limit for limit in descriptor.known_limits)


@pytest.mark.parametrize(("path", "language"), _P2_LIMIT_PATHS)
def test_p2_registry_rejects_oversized_bytes_before_real_adapter_parse(
    path: str,
    language: str,
) -> None:
    registry = SemanticAdapterRegistry(limits=ParserLimits(max_file_bytes=24))
    content = "é" * 13

    adapter = registry.find(path, content)
    assert adapter is not None
    assert adapter.language == language

    for operation in (registry.parse, registry.emit_graph_records):
        result = operation(path, content)
        assert result.get("types", []) == []
        assert result.get("nodes", []) == []
        assert result.get("edges", []) == []
        assert result["diagnostics"][0]["code"] == "parser_limit_exceeded"
        assert result["diagnostics"][0]["reason_code"] == "file_size_limit"


@pytest.mark.parametrize(("path", "language"), _P2_LIMIT_PATHS)
def test_p2_registry_rejects_excess_lines_before_real_adapter_parse(
    path: str,
    language: str,
) -> None:
    registry = SemanticAdapterRegistry(limits=ParserLimits(max_lines=2))
    content = "first\nsecond\nthird"

    adapter = registry.find(path, content)
    assert adapter is not None
    assert adapter.language == language

    for operation in (registry.parse, registry.emit_graph_records):
        result = operation(path, content)
        assert result.get("types", []) == []
        assert result.get("nodes", []) == []
        assert result.get("edges", []) == []
        assert result["diagnostics"][0]["code"] == "parser_limit_exceeded"
        assert result["diagnostics"][0]["reason_code"] == "line_limit"


@pytest.mark.parametrize(
    ("path", "language", "content"),
    _P2_RECORD_LIMIT_CASES,
)
def test_p2_registry_rejects_excess_symbol_records_at_graph_emission_boundary(
    path: str,
    language: str,
    content: str,
) -> None:
    registry = SemanticAdapterRegistry(limits=ParserLimits(max_output_records=1))
    adapter = registry.find(path, content)

    assert adapter is not None
    assert adapter.language == language
    unbounded_adapter_result = adapter.emit_graph_records(path, content)
    assert len(unbounded_adapter_result["nodes"]) > 1

    result = registry.emit_graph_records(path, content)

    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["diagnostics"][0]["code"] == "parser_limit_exceeded"
    assert result["diagnostics"][0]["reason_code"] == "output_record_limit"


@pytest.mark.parametrize(
    ("path", "language", "content"),
    _P2_RECORD_LIMIT_CASES,
)
def test_p2_registry_rejects_excess_canonical_records_at_parse_boundary(
    path: str,
    language: str,
    content: str,
) -> None:
    registry = SemanticAdapterRegistry(limits=ParserLimits(max_output_records=1))
    adapter = registry.find(path, content)

    assert adapter is not None
    assert adapter.language == language
    assert len(adapter.parse(path, content)["symbols"]) > 1

    result = registry.parse(path, content)

    assert result["symbols"] == []
    assert result["types"] == []
    assert result["functions"] == []
    assert result["diagnostics"][0]["code"] == "parser_limit_exceeded"
    assert result["diagnostics"][0]["reason_code"] == "output_record_limit"


def test_p2_parse_telemetry_counts_canonical_symbols_without_projection_duplicates() -> None:
    source = "import demo.Api\nclass First\nfun load() {}"
    events: list[dict] = []
    registry = SemanticAdapterRegistry(telemetry=lambda **values: events.append(values))
    adapter = registry.find("src/First.kt", source)

    assert adapter is not None
    parsed = adapter.parse("src/First.kt", source)
    result = registry.parse("src/First.kt", source)

    expected_count = len(parsed["symbols"]) + len(parsed["imports"])
    duplicated_projection_count = expected_count + len(parsed["types"]) + len(parsed["functions"])
    assert expected_count < duplicated_projection_count
    assert result["symbols"]
    assert events[0]["symbol_count"] == expected_count


def test_component_adapters_ignore_template_style_and_unclosed_script_content() -> None:
    registry = SemanticAdapterRegistry()
    source = """<template>
class TemplateFake {}
</template>
<script>
class ScriptReal {}
</script>
<style>
class StyleFake {}
</style>
"""
    parsed = registry.parse("src/SafeCard.vue", source)

    assert {item["name"] for item in parsed["symbols"]} == {"SafeCard", "ScriptReal"}

    unclosed = registry.parse("src/Partial.svelte", "<script>\nclass MustNotLeak {}")
    assert {item["name"] for item in unclosed["symbols"]} == {"Partial"}
    assert {item["code"] for item in unclosed["diagnostics"]} >= {
        "semantic_static_symbol_index",
        "static_symbol_script_region_missing",
    }


def test_static_symbol_comment_masking_avoids_obvious_false_declarations() -> None:
    source = """/*
class Hidden
fun hidden() {}
*/
class Visible
// fun alsoHidden() {}
fun visible() {}
"""
    parsed = SemanticAdapterRegistry().parse("src/Visible.kt", source)

    assert {item["name"] for item in parsed["symbols"]} == {"Visible", "visible"}


@pytest.mark.parametrize(
    ("path", "format_id"),
    [
        ("src/User.kt", "kotlin"),
        ("build.gradle.kts", "kotlin_script"),
        ("Sources/User.swift", "swift"),
        ("src/User.scala", "scala"),
        ("src/user.lua", "lua"),
        ("lib/user.dart", "dart"),
        ("src/UserCard.vue", "vue"),
        ("src/UserCard.svelte", "svelte"),
    ],
)
def test_canonical_registry_claims_only_verified_symbol_index(
    path: str,
    format_id: str,
) -> None:
    registry = load_file_type_support_registry(ROOT)
    classified = FileTypeClassifier(registry).classify(path, is_text=True)

    assert classified is not None
    assert classified.format_id == format_id
    descriptor = registry.descriptor(format_id)
    assert descriptor is not None
    support = descriptor.support_for("semantic_translation")
    assert support.indexed.implementation is CapabilityImplementation.HEURISTIC
    assert support.indexed.verified is True
    assert support.symbols.implementation is CapabilityImplementation.HEURISTIC
    assert support.symbols.verified is True
    assert support.relationships.implementation is CapabilityImplementation.UNSUPPORTED
    assert support.relationships.verified is False
    assert support.symbols.producer is not None
    assert support.symbols.producer.endswith("StaticSymbolLanguageAdapter")
    assert set(support.symbols.evidence) == {
        "agent/codecompass/semantic_translation/static_symbol_adapters.py",
        "tests/test_codecompass_p2_static_symbol_adapters.py",
    }


def test_support_matrix_projects_p2_adapters_as_symbol_index() -> None:
    matrix = FileTypeSupportService(ROOT).support_matrix(
        FileTypeSupportFilter.build(
            pipelines=("semantic_translation",),
            support_levels=("symbol_index",),
        )
    )
    expected = {
        "vue",
        "svelte",
        "kotlin",
        "kotlin_script",
        "swift",
        "scala",
        "lua",
        "dart",
    }
    rows = [item for item in matrix["rows"] if item["format_id"] in expected]

    assert {item["format_id"] for item in rows} == expected
    assert all(item["support_level"] == "symbol_index" for item in rows)
    assert all(item["capabilities"][CapabilityDimension.SYMBOLS.value]["effective"] for item in rows)
    assert all(
        not item["capabilities"][CapabilityDimension.RELATIONSHIPS.value]["configured"]
        for item in rows
    )


def test_kotlin_script_keeps_gradle_rag_helper_contract_separate() -> None:
    descriptor = load_file_type_support_registry(ROOT).descriptor("kotlin_script")

    assert descriptor is not None
    rag_helper = descriptor.support_for("rag_helper")
    semantic_translation = descriptor.support_for("semantic_translation")
    assert rag_helper.relationships.verified is True
    assert rag_helper.relationships.producer == "rag_helper.extractors.text_file_extractor.TextFileExtractor"
    assert semantic_translation.relationships.configured is False
