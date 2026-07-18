"""Cross-layer acceptance coverage for VPA-ED-007 through VPA-ED-009.

These tests intentionally derive cases from the canonical Hub registry.  A new
field or kind therefore expands the matrix instead of silently escaping the
form/roundtrip/validation contract.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

import pytest

from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
from agent.visual_process.node_definition_validator import NodeDefinitionStepValidator
from agent.visual_process.node_definitions import get_node_definition, list_node_definitions
from agent.visual_process.step_adapters import (
    EmbedApiAdapter,
    QueryRewriteAdapter,
    RerankAdapter,
    SignRotationAdapter,
    TurboQuantMseAdapter,
)
from agent.visual_process.task_kind_registry import (
    RETRIEVAL_TASK_KINDS,
    WORKER_TASK_KINDS,
    canonical_task_kind_ids,
)
from agent.visual_process.validator import GraphValidator

ED007_KINDS = frozenset(WORKER_TASK_KINDS | RETRIEVAL_TASK_KINDS)
ED008_KINDS = frozenset(
    {
        "domain_cluster",
        "embed_api",
        "embed_chunk",
        "query_rewrite",
        "rag_retrieve",
        "rerank",
        "sign_rotation",
        "turboquant_mse",
    }
)
ED009_KINDS = frozenset(
    {
        "ml_intern_build_lora_dataset",
        "ml_intern_train_lora",
        "evolution_analyze",
        "evolution_validate",
        "evolution_apply",
        "evolve_prompt",
        "evolve_project",
    }
)
AFFECTED_KINDS = ED007_KINDS | ED008_KINDS | ED009_KINDS
DEFINITIONS = {definition["kind"]: definition for definition in list_node_definitions()}
EDITABLE_FIELD_CASES = [
    (kind, field)
    for kind in sorted(AFFECTED_KINDS)
    for field in DEFINITIONS[kind]["fields"]
    if not field.get("read_only") and not field.get("deprecated")
]


def _segments(pointer: str) -> list[str]:
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer.removeprefix("/").split("/")]


def _set_pointer(payload: dict[str, Any], pointer: str, value: Any) -> None:
    target = payload
    segments = _segments(pointer)
    for segment in segments[:-1]:
        nested = target.get(segment)
        if not isinstance(nested, dict):
            nested = {}
            target[segment] = nested
        target = nested
    target[segments[-1]] = copy.deepcopy(value)


def _get_pointer(payload: Mapping[str, Any], pointer: str) -> Any:
    current: Any = payload
    for segment in _segments(pointer):
        current = current[segment]
    return current


def _number_sample(field: Mapping[str, Any]) -> int | float:
    constraints = field.get("constraints") or {}
    minimum = constraints.get("minimum")
    maximum = constraints.get("maximum")
    current = field.get("default")
    integer = constraints.get("integer") is True
    if minimum is not None and current != minimum:
        return int(minimum) if integer else float(minimum)
    if maximum is not None and current != maximum:
        return int(maximum) if integer else float(maximum)
    if current is not None:
        candidate = int(current) + 1 if integer else float(current) + 0.1
        if maximum is None or candidate <= maximum:
            return candidate
    return 2 if integer else 0.25


def _sample(field: Mapping[str, Any]) -> Any:
    field_type = str(field["field_type"])
    if field_type in {"text", "expression"}:
        return "edited-value"
    if field_type == "resource_reference":
        return "catalog-item-2"
    if field_type == "secret_reference":
        return "env://ACCEPTANCE_REFERENCE"
    if field_type == "number":
        return _number_sample(field)
    if field_type == "boolean":
        return not bool(field.get("default", False))
    if field_type == "enum":
        values = [option["value"] for option in field.get("options") or []]
        return next((value for value in values if value != field.get("default")), values[0])
    if field_type == "multi_select":
        values = [option["value"] for option in field.get("options") or []]
        return [values[-1]] if values else ["edited-value"]
    if field_type == "io_port":
        return [{"name": "edited", "kind": "text", "required": False}]
    if field_type == "structured_list":
        return [{"name": "edited"}]
    raise AssertionError(f"unsupported field type in acceptance matrix: {field_type}")


def _complete_step(kind: str) -> VisualProcessStep:
    definition = DEFINITIONS[kind]
    payload: dict[str, Any] = {
        "id": f"step-{kind}",
        "label": definition["label"],
        "kind": kind,
        "gate": bool(definition["runtime"]["requires_approval"]),
        "policy_hints": [],
        "io": {"inputs": [], "outputs": []},
        "position": {"x": 0, "y": 0},
        "metadata": copy.deepcopy(definition["defaults"]["metadata"]),
    }
    for field in definition["fields"]:
        if field.get("read_only") or field.get("deprecated"):
            continue
        _set_pointer(payload, str(field["path"]), _sample(field))
    return VisualProcessStep.model_validate(payload)


def _invalid_constraint_cases() -> list[tuple[str, Mapping[str, Any], Any, str]]:
    cases: list[tuple[str, Mapping[str, Any], Any, str]] = []
    for kind, field in EDITABLE_FIELD_CASES:
        constraints = field.get("constraints") or {}
        if "minimum" in constraints:
            cases.append((kind, field, float(constraints["minimum"]) - 1, "node_field_minimum"))
        if "maximum" in constraints:
            cases.append((kind, field, float(constraints["maximum"]) + 1, "node_field_maximum"))
        if constraints.get("integer") is True:
            cases.append((kind, field, float(constraints.get("minimum", 0)) + 0.5, "node_field_integer_required"))
        if "pattern" in constraints:
            cases.append((kind, field, "! invalid !", "node_field_pattern_mismatch"))
        if field["field_type"] == "enum":
            cases.append((kind, field, "__not_a_declared_option__", "node_field_option_invalid"))
        if field["field_type"] == "secret_reference":
            cases.append((kind, field, "plain-text-secret", "node_field_secret_reference_invalid"))
    return cases


INVALID_CONSTRAINT_CASES = _invalid_constraint_cases()


def test_acceptance_kind_partition_is_complete_and_alias_free() -> None:
    assert AFFECTED_KINDS == canonical_task_kind_ids()
    assert ED007_KINDS.isdisjoint(ED008_KINDS)
    assert ED007_KINDS.isdisjoint(ED009_KINDS)
    assert ED008_KINDS.isdisjoint(ED009_KINDS)
    assert {"vector_encode", "turboquant_encode", "cluster"}.isdisjoint(AFFECTED_KINDS)


@pytest.mark.parametrize("kind", sorted(AFFECTED_KINDS))
def test_every_migrated_kind_has_valid_defaults_and_a_strict_complete_form(kind: str) -> None:
    definition = get_node_definition(kind)
    assert definition is not None
    metadata_defaults = definition["defaults"]["metadata"]
    for field in definition["fields"]:
        path = str(field["path"])
        if path.startswith("/metadata/") and "default" in field:
            assert _get_pointer({"metadata": metadata_defaults}, path) == field["default"]

    step = _complete_step(kind)
    violations = NodeDefinitionStepValidator().validate(step, enforce_static_required=True)
    assert violations == []
    assert VisualProcessStep.model_validate(step.model_dump(mode="json")) == step


@pytest.mark.parametrize("kind", sorted(AFFECTED_KINDS))
def test_every_visible_editable_field_roundtrips_its_canonical_pointer(kind: str) -> None:
    fields = [
        field for field in DEFINITIONS[kind]["fields"] if not field.get("read_only") and not field.get("deprecated")
    ]
    assert fields
    for field in fields:
        step = _complete_step(kind)
        payload = step.model_dump(mode="json")
        sample = _sample(field)
        _set_pointer(payload, str(field["path"]), sample)

        normalized = VisualProcessStep.model_validate(payload).model_dump(mode="json")
        roundtripped = VisualProcessStep.model_validate(normalized).model_dump(mode="json")

        assert _get_pointer(roundtripped, str(field["path"])) == _get_pointer(normalized, str(field["path"])), (
            kind,
            field["path"],
        )
        if field["field_type"] != "io_port":
            assert _get_pointer(roundtripped, str(field["path"])) == sample, (
                kind,
                field["path"],
            )


@pytest.mark.parametrize("kind", sorted(AFFECTED_KINDS))
def test_hub_rejects_every_declared_invalid_constraint_and_closed_option(
    kind: str,
) -> None:
    cases = [case for case in INVALID_CONSTRAINT_CASES if case[0] == kind]
    for _kind, field, invalid, expected_code in cases:
        payload = _complete_step(kind).model_dump(mode="json")
        _set_pointer(payload, str(field["path"]), invalid)
        step = VisualProcessStep.model_validate(payload)

        violations = NodeDefinitionStepValidator().validate(step)

        assert any(
            violation.code == expected_code and violation.path.endswith(str(field["path"])) for violation in violations
        ), (kind, field["path"], expected_code)


def test_hub_enforces_training_and_evolution_mutation_approval() -> None:
    cases = [
        VisualProcessStep(
            id="training",
            label="Live training",
            kind="ml_intern_train_lora",
            gate=False,
            metadata={"mode": "live"},
        ),
        VisualProcessStep(
            id="evolution",
            label="Apply evolution",
            kind="evolution_apply",
            gate=False,
        ),
        VisualProcessStep(
            id="project",
            label="Apply project changes",
            kind="evolve_project",
            gate=False,
            metadata={"apply_allowed": True},
        ),
    ]
    expected = [
        "training_live_requires_gate",
        "evolution_apply_requires_gate",
        "evolve_project_apply_requires_gate",
    ]
    for step, code in zip(cases, expected, strict=True):
        result = GraphValidator().validate(VisualProcessGraph(id=f"graph-{step.id}", name="Governance", steps=[step]))
        assert code in {issue.code for issue in result.errors()}


def test_productive_adapters_read_canonical_registry_paths() -> None:
    query = QueryRewriteAdapter().execute(
        VisualProcessStep(
            id="query",
            label="Query",
            kind="query_rewrite",
            metadata={"query": "bug"},
        ),
        {},
        {},
    )
    assert query.status == "success"
    assert "bug" in str(query.outputs)

    rerank = RerankAdapter().execute(
        VisualProcessStep(
            id="rerank",
            label="Rerank",
            kind="rerank",
            metadata={"query": "alpha", "weight": 0.6, "enabled": True},
        ),
        {"candidates": [{"text": "alpha", "final_score": 0.0}]},
        {},
    )
    assert rerank.outputs["reranked"][0]["final_score"] == 0.6

    embedding = EmbedApiAdapter().execute(
        VisualProcessStep(
            id="embed",
            label="Embed",
            kind="embed_api",
            metadata={"provider": "hash", "dimensions": 7, "texts": ["value"]},
        ),
        {},
        {},
    )
    assert len(embedding.outputs["embeddings"][0]) == 7

    rotation = SignRotationAdapter().execute(
        VisualProcessStep(
            id="rotation",
            label="Rotation",
            kind="sign_rotation",
            metadata={"seed": 0, "vector": [1.0, 2.0]},
        ),
        {},
        {},
    )
    assert rotation.outputs["seed"] == 0

    quantized = TurboQuantMseAdapter().execute(
        VisualProcessStep(
            id="quantized",
            label="Quantized",
            kind="turboquant_mse",
            metadata={"seed": 0, "levels": 2, "vector": [1.0, 2.0]},
        ),
        {},
        {},
    )
    assert quantized.outputs["seed"] == 0
    assert quantized.outputs["levels"] == 2


def test_reranker_definition_writes_only_weight_but_keeps_legacy_read_compatibility() -> None:
    definition = DEFINITIONS["rerank"]
    paths = {field["path"] for field in definition["fields"]}
    assert "/metadata/weight" in paths
    assert "/metadata/reranker_weight" not in paths
    result = RerankAdapter().execute(
        VisualProcessStep(
            id="legacy-rerank",
            label="Legacy rerank",
            kind="rerank",
            metadata={"reranker_weight": 0.4},
        ),
        {"query": "alpha", "candidates": [{"text": "alpha", "final_score": 0.0}]},
        {},
    )
    assert result.outputs["reranked"][0]["final_score"] == 0.4


def test_training_definitions_offer_only_catalog_references_and_no_runtime_or_secret_values() -> None:
    training = DEFINITIONS["ml_intern_train_lora"]
    dataset = DEFINITIONS["ml_intern_build_lora_dataset"]
    resource_types = {
        field["path"]: field.get("resource_type")
        for field in training["fields"] + dataset["fields"]
        if field.get("resource_type")
    }
    assert resource_types["/metadata/dataset_id"] == "training_dataset"
    assert resource_types["/metadata/training_profile_id"] == "training_profile"
    assert resource_types["/metadata/base_model"] == "model_profile"
    offered_paths = {field["path"] for definition in (training, dataset) for field in definition["fields"]}
    assert not any(
        token in path
        for path in offered_paths
        for token in ("api_key", "credential", "dataset_path", "dataset_root", "output_dir", "job_status")
    )
