from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.codecompass.semantic_translation.adapters import DummySemanticAdapter, JavaSemanticAdapter
from agent.codecompass.semantic_translation.config import load_semantic_translation_config
from agent.codecompass.semantic_translation.equivalence_registry import EquivalenceRule, EquivalenceRuleRegistry
from agent.codecompass.semantic_translation.expression_registry import ExpressionMappingRegistry
from agent.codecompass.semantic_translation.models import validate_semantic_kind
from agent.codecompass.semantic_translation.nullability import infer_java_nullability
from agent.codecompass.semantic_translation.transform import DeterministicTransformEngine, TransformRequest
from agent.codecompass.semantic_translation.type_registry import TypeMappingRegistry
from agent.codecompass.semantic_translation.verifier import SemanticTranslationVerifier
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def test_semantic_translation_config_defaults_off_and_validates_env():
    default = load_semantic_translation_config({})
    enabled = load_semantic_translation_config({"ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED": "true"})
    invalid = load_semantic_translation_config({"ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_MAX_GRAPH_RECORDS": "-1"})

    assert default.enabled is False
    assert enabled.enabled is True
    assert invalid.max_graph_records == 5000
    assert "non_positive_integer_config" in invalid.diagnostics


def test_java_adapter_extracts_record_enum_interface_and_signatures():
    source = """
public record UserDto(UUID id, Optional<String> email) {}
public enum Status { ACTIVE, DISABLED }
public interface Lookup { String find(UUID id) throws IOException; }
"""
    emitted = JavaSemanticAdapter().emit_graph_records("src/UserDto.java", source)
    nodes = emitted["nodes"]

    assert any(node["symbol"] == "UserDto" and node["semantic_kind"] == "data_record" for node in nodes)
    assert any(node["symbol"] == "UserDto.email" and node["semantic_kind"] == "optional_absence" for node in nodes)
    assert any(node["symbol"] == "Status.ACTIVE" and node["semantic_kind"] == "enum_value" for node in nodes)
    assert any(node["symbol"] == "Lookup.find" and node["semantic_kind"] == "function_signature" for node in nodes)


def test_type_mapping_registry_covers_required_java_types():
    registry = TypeMappingRegistry()

    assert registry.map_type("UUID", source_language="java", target_language="typescript")["target_type"] == "string"
    assert registry.map_type("Optional<String>", source_language="java", target_language="typescript")["target_type"] == "string | undefined"
    assert registry.map_type("List<UUID>", source_language="java", target_language="typescript")["target_type"] == "string[]"
    assert registry.map_type("Map<String, Long>", source_language="java", target_language="kotlin")["target_type"] == "Map<String, Long>"
    assert registry.map_type("BigDecimal", source_language="java", target_language="typescript")["lossiness"] == "policy_guarded"
    assert registry.map_type("Custom", source_language="java", target_language="typescript")["status"] == "needs_review"


def test_expression_registry_maps_safe_subset_and_guards_equals():
    registry = ExpressionMappingRegistry()

    assert registry.map_java_expression('"x"', target_language="typescript")["status"] == "ok"
    assert registry.map_java_expression("name", target_language="typescript")["rule_id"] == "expr.property_access.v1"
    assert registry.map_java_expression("a.equals(b)", target_language="typescript", nullability="unknown_nullability")["status"] == "needs_review"
    assert registry.map_java_expression("a.equals(b)", target_language="typescript", nullability="non_null")["target_expression"] == "a === b"


def test_nullability_model_separates_optional_absence_from_null():
    assert infer_java_nullability("Optional<String>").state == "optional_absence"
    assert infer_java_nullability("String", ["@Nullable"]).state == "nullable"
    assert infer_java_nullability("String").state == "unknown_nullability"
    assert infer_java_nullability("int").state == "non_null"


def test_equivalence_registry_validates_duplicates_and_experimental_stable_conflict():
    registry = EquivalenceRuleRegistry()
    assert registry.find(source_language="java", target_language="typescript", semantic_kind="data_record")
    duplicate = registry.records()[0]
    rule = EquivalenceRule(
        rule_id=duplicate["rule_id"],
        scope="x",
        source_language="java",
        target_language="typescript",
        semantic_kind="data_record",
        preconditions=("x",),
        postconditions=("y",),
        examples=({"source": "a", "target": "b"},),
        tests=("test",),
    )
    with pytest.raises(ValueError):
        EquivalenceRuleRegistry([rule, rule])


def test_unknown_semantic_kind_is_rejected():
    with pytest.raises(ValueError):
        validate_semantic_kind("invented_kind")


def test_graph_store_indexes_semantic_translation_records_and_traverses(tmp_path):
    emitted = JavaSemanticAdapter().emit_graph_records("src/UserDto.java", "public record UserDto(String name) {}")
    records = [*emitted["nodes"], *emitted["edges"], *EquivalenceRuleRegistry().records()]
    store = CodeCompassGraphStore(index_path=tmp_path / "graph.json")
    diagnostics = store.rebuild_from_output_records(records=records, manifest_hash="mh")
    nodes = store.find_semantic_nodes(symbol="UserDto", language="java")
    traversal = store.traverse(seed_ids=[nodes[0]["id"]], max_depth=1, max_nodes=5, allowed_edge_types={"declares"})

    assert diagnostics["semantic_translation"]["status"] == "ready"
    assert nodes[0]["semantic_kind"] == "data_record"
    assert traversal["cycle_guarded"] is True
    assert any(node["symbol"] == "UserDto.name" for node in traversal["nodes"])


def test_graph_store_degrades_for_missing_semantic_index(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "missing.json")
    payload = store.load()

    assert payload["diagnostics"]["status"] == "degraded"
    assert store.find_semantic_nodes(symbol="Missing") == []


def test_transform_and_verifier_for_record_and_enum():
    engine = DeterministicTransformEngine()
    source = "public record UserDto(UUID id, String name, Optional<String> email) {}"
    artifact = engine.transform(TransformRequest(source_path="src/UserDto.java", source_code=source, target_language="typescript"))
    verification = SemanticTranslationVerifier().verify(
        source_path="src/UserDto.java",
        source_code=source,
        target_code=artifact["target_code"],
        transform_artifact=artifact,
    )

    assert "export interface UserDto" in artifact["target_code"]
    assert "email?: string | undefined;" in artifact["target_code"]
    assert verification["status"] in {"verified", "verified_with_warnings"}


def test_verifier_detects_missing_property_and_wrong_type():
    source = "public record UserDto(UUID id, String name) {}"
    artifact = DeterministicTransformEngine().transform(TransformRequest(source_path="src/UserDto.java", source_code=source, target_language="typescript"))
    result = SemanticTranslationVerifier().verify(
        source_path="src/UserDto.java",
        source_code=source,
        target_code="export interface UserDto { id: number; }",
        transform_artifact=artifact,
    )

    assert result["status"] == "failed"
    assert {error["code"] for error in result["errors"]} >= {"missing_target_property", "target_type_mismatch"}


def test_golden_samples_are_present_and_deterministic():
    samples = json.loads(Path("tests/fixtures/semantic_translation_golden_samples.json").read_text(encoding="utf-8"))
    assert len(samples) >= 30
    engine = DeterministicTransformEngine()
    for sample in samples[:12]:
        artifact = engine.transform(
            TransformRequest(
                source_path=f"golden/{sample['id']}.java",
                source_code=sample["source"],
                target_language=sample["target_language"],
            )
        )
        assert artifact["target_code"] == sample["expected"], sample["id"]
        for expected_warning in sample["warnings"]:
            assert expected_warning in artifact["warnings"], sample["id"]


def test_dummy_adapter_is_deterministic():
    adapter = DummySemanticAdapter()
    first = adapter.emit_graph_records("x.dummy", "dummy")
    second = adapter.emit_graph_records("x.dummy", "dummy")
    assert first == second == {"nodes": [], "edges": [], "diagnostics": []}
