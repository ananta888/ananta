from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathlib import Path

from agent.codecompass.semantic_translation.adapters import DummySemanticAdapter, JavaSemanticAdapter
from agent.codecompass.semantic_translation.config import load_semantic_translation_config
from agent.codecompass.semantic_translation.equivalence_registry import EquivalenceRule, EquivalenceRuleRegistry, load_rules_from_file
from agent.codecompass.semantic_translation.expression_registry import ExpressionMappingRegistry
from agent.codecompass.semantic_translation.models import (
    CONTROL_FLOW_KINDS,
    CONTROL_FLOW_PRECONDITIONS,
    UNSUPPORTED_CONTROL_FLOW_CONSTRUCTS,
    validate_semantic_kind,
)
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


def test_type_registry_find_by_semantic_kind_and_lossiness():
    registry = TypeMappingRegistry()

    optional_rules = registry.find_by_semantic_kind("optional_absence", target_language="typescript")
    assert any(rule.rule_id == "java_optional_to_ts" for rule in optional_rules)

    collection_rules = registry.find_by_semantic_kind("collection")
    assert len(collection_rules) >= 4

    policy_guarded = registry.find_by_lossiness("policy_guarded", source_language="java")
    assert any(rule.rule_id == "java_bigdecimal_to_ts_number" for rule in policy_guarded)
    assert any("optional_to_kotlin" in rule.rule_id for rule in policy_guarded)

    lossless_ts = registry.find_by_lossiness("lossless", target_language="typescript")
    assert all(rule.lossiness == "lossless" for rule in lossless_ts)
    assert any(rule.source_pattern == "String" for rule in lossless_ts)


def test_expression_registry_covers_twenty_pairs():
    registry = ExpressionMappingRegistry()
    pairs = [
        ("42", "typescript", "ok", "expr.literal.v1"),
        ("0", "typescript", "ok", "expr.literal.v1"),
        ("true", "kotlin", "ok", "expr.literal.v1"),
        ("false", "typescript", "ok", "expr.literal.v1"),
        ('"hello"', "typescript", "ok", "expr.literal.v1"),
        ('""', "kotlin", "ok", "expr.literal.v1"),
        ("null", "typescript", "ok", "expr.literal.v1"),
        ("name", "typescript", "ok", "expr.property_access.v1"),
        ("user.name", "typescript", "ok", "expr.property_access.v1"),
        ("a.b.c.d", "kotlin", "ok", "expr.property_access.v1"),
        ("count", "kotlin", "ok", "expr.property_access.v1"),
        ("a.equals(b)", "typescript", "needs_review", "expr.equals.v1"),
        ("a.equals(b)", "kotlin", "needs_review", "expr.equals.v1"),
        ("x.equals(y)", "typescript", "ok", "expr.equals.v1"),
        ("Objects.equals(a, b)", "typescript", "ok", "expr.objects_equals.v1"),
        ("Objects.equals(x, y)", "kotlin", "ok", "expr.objects_equals.v1"),
        ("!active", "typescript", "ok", "expr.boolean_negation.v1"),
        ("a == null", "typescript", "needs_review", "expr.null_check.v1"),
        ("x != null", "kotlin", "needs_review", "expr.null_check.v1"),
        ("a + b", "typescript", "needs_review", "expr.simple_operator.v1"),
        ("a > b", "kotlin", "needs_review", "expr.simple_operator.v1"),
        ("a - b", "typescript", "needs_review", "expr.simple_operator.v1"),
    ]
    for expr, lang, expected_status, expected_rule in pairs:
        nullability = "non_null" if "equals" in expr.lower() and expected_status == "ok" else "unknown_nullability"
        result = registry.map_java_expression(expr, target_language=lang, nullability=nullability)
        assert result["status"] == expected_status, f"expr={expr!r} lang={lang}: got status={result['status']!r}"
        assert result["rule_id"] == expected_rule, f"expr={expr!r} lang={lang}: got rule={result['rule_id']!r}"

    assert len(pairs) >= 20


def test_control_flow_kinds_and_preconditions_are_defined():
    assert "iteration_over_finite_collection" in CONTROL_FLOW_KINDS
    assert "if_else_branch" in CONTROL_FLOW_KINDS
    assert "return_statement" in CONTROL_FLOW_KINDS
    assert "switch_enum_match" in CONTROL_FLOW_KINDS
    assert "unsupported_control_flow" in CONTROL_FLOW_KINDS

    assert "no_break_continue" in CONTROL_FLOW_PRECONDITIONS["iteration_over_finite_collection"]
    assert "no_mutating_iterator" in CONTROL_FLOW_PRECONDITIONS["iteration_over_finite_collection"]
    assert "condition_is_boolean_expression" in CONTROL_FLOW_PRECONDITIONS["if_else_branch"]

    assert "break" in UNSUPPORTED_CONTROL_FLOW_CONSTRUCTS
    assert "continue" in UNSUPPORTED_CONTROL_FLOW_CONSTRUCTS
    assert "synchronized_block" in UNSUPPORTED_CONTROL_FLOW_CONSTRUCTS


def test_java_adapter_detects_unsupported_control_flow():
    source = """
public class OrderService {
    public void process(List<Order> orders) {
        for (Order o : orders) {
            if (o == null) break;
        }
    }
}
"""
    emitted = JavaSemanticAdapter().emit_graph_records("src/OrderService.java", source)
    all_unsupported = [u for node in emitted["nodes"] for u in (node.get("attributes") or {}).get("unsupported") or []]
    codes = {u.get("code") for u in all_unsupported}
    assert "unsupported_control_flow" in codes


def test_java_adapter_classifies_exceptions_and_adds_contracts():
    source = """
public interface Repo {
    String findById(UUID id) throws IOException, IllegalArgumentException;
}
"""
    emitted = JavaSemanticAdapter().emit_graph_records("src/Repo.java", source)
    method_nodes = [n for n in emitted["nodes"] if n.get("semantic_kind") == "function_signature"]
    assert method_nodes, "expected at least one method node"
    attrs = method_nodes[0]["attributes"]
    classified = attrs.get("throws_classified") or []
    kinds = {c["kind"] for c in classified}
    assert "checked_exception" in kinds
    assert "unchecked_exception" in kinds
    assert "contracts" in attrs
    assert set(attrs["contracts"].keys()) >= {"preconditions", "postconditions", "invariants"}


def test_methods_with_unknown_side_effects_are_not_marked_pure():
    source = "public class Svc { public void doWork() { } }"
    emitted = JavaSemanticAdapter().emit_graph_records("src/Svc.java", source)
    method_nodes = [n for n in emitted["nodes"] if n.get("semantic_kind") == "function_signature"]
    for node in method_nodes:
        effects = (node.get("attributes") or {}).get("side_effects") or []
        assert "unknown_side_effect" in effects
        assert "pure" not in effects


def test_equivalence_rule_registry_loads_from_json_file():
    registry = EquivalenceRuleRegistry()
    records = registry.records()
    assert any(r["rule_id"] == "eq.java_record.ts_interface.v1" for r in records)
    assert any(r["rule_id"] == "eq.java_enum.kotlin_enum.v1" for r in records)
    for record in records:
        assert record.get("experimental") is False or record.get("status") != "stable"


def test_equivalence_registry_file_fallback_on_bad_path(tmp_path):
    bad_path = tmp_path / "nonexistent.json"
    rules = load_rules_from_file(bad_path)
    assert rules, "should fall back to builtin rules"
    assert any(r.rule_id == "eq.java_record.ts_interface.v1" for r in rules)


def test_transform_artifact_has_mandatory_fields_and_stable_hashes():
    source = "public record Foo(String bar) {}"
    engine = DeterministicTransformEngine()
    a1 = engine.transform(TransformRequest(source_path="Foo.java", source_code=source, target_language="typescript"))
    a2 = engine.transform(TransformRequest(source_path="Foo.java", source_code=source, target_language="typescript"))

    for field in ("schema", "kind", "artifact_id", "source_path", "target_language", "source_hash", "target_hash", "target_code", "status", "rule_ids", "warnings", "created_at"):
        assert field in a1, f"mandatory field missing: {field}"

    assert a1["source_hash"] == a2["source_hash"]
    assert a1["target_hash"] == a2["target_hash"]
    assert a1["target_code"] == a2["target_code"]


def test_verifier_redacts_sensitive_terms_in_target():
    source = "public record Credentials(String username, String password) {}"
    engine = DeterministicTransformEngine()
    artifact = engine.transform(TransformRequest(source_path="Cred.java", source_code=source, target_language="typescript"))
    result = SemanticTranslationVerifier().verify(
        source_path="Cred.java",
        source_code=source,
        target_code=artifact["target_code"],
        transform_artifact=artifact,
    )
    assert "target_contains_sensitive_term" in result["warnings"]


def test_setup_index_semantic_build_function_produces_summary(tmp_path, monkeypatch):
    import importlib
    import sys

    monkeypatch.setenv("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED", "true")
    java_file = tmp_path / "Sample.java"
    java_file.write_text("public record Sample(String name) {}", encoding="utf-8")

    sys.path.insert(0, str(Path("scripts").resolve()))
    import scripts.setup_codecompass_index as idx_module
    monkeypatch.setattr(idx_module, "ROOT", tmp_path)

    records, summary = idx_module._build_semantic_translation_records([java_file])

    assert summary["enabled"] is True
    assert summary["analyzed_files"] >= 1
    assert summary["node_count"] >= 1
    assert summary["rule_count"] >= 1
    assert "java" in summary["recognized_languages"]
    assert isinstance(summary["warnings"], list)
