from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models.visual_process import VisualProcessGraphDB
from agent.services.visual_process_definition_service import (
    VisualProcessDefinitionConflict,
    VisualProcessDefinitionSecurityError,
    VisualProcessDefinitionService,
)
from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
from agent.visual_process.node_definitions import list_node_definitions
from agent.visual_process.task_kind_registry import (
    ALL_TASK_KINDS,
    LEGACY_MAP,
    canonical_task_kind_ids,
    list_task_kinds,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return engine


def _graph(**updates) -> VisualProcessGraph:
    data = {
        "id": "vp-contract",
        "name": "Contract",
        "steps": [VisualProcessStep(id="step-1", label="Review", kind="review")],
    }
    data.update(updates)
    return VisualProcessGraph.model_validate(data)


def test_canonical_registry_excludes_aliases_and_composes_every_definition() -> None:
    canonical = canonical_task_kind_ids()
    assert canonical == ALL_TASK_KINDS
    assert "shell_execution" not in canonical
    assert LEGACY_MAP["shell_execution"] == "shell_execute"

    definitions = list_node_definitions()
    assert {item["kind"] for item in definitions} == canonical
    assert len(definitions) == len(list_task_kinds())
    for item in definitions:
        assert item["runtime"] == {
            key: next(info for info in list_task_kinds() if info["id"] == item["kind"])[key] for key in item["runtime"]
        }
        assert item["fields"]
        assert item["execution"]["visual_process_executable"] == (
            item["execution"]["execution_mode"] != "not_executable"
        )


def test_kind_field_registry_has_no_duplicate_literal_keys() -> None:
    source = Path("agent/visual_process/node_definitions.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    assignment = next(
        node
        for node in module.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_KIND_FIELDS"
    )
    assert isinstance(assignment.value, ast.Dict)
    keys = [key.value for key in assignment.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]

    assert len(keys) == len(set(keys))


def test_all_node_definitions_validate_against_published_schema() -> None:
    schema = json.loads(Path("schemas/visual_process/node_definition.v1.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for definition in list_node_definitions():
        assert list(validator.iter_errors(definition)) == []


def test_definition_defaults_follow_nested_canonical_field_paths() -> None:
    for definition in list_node_definitions():
        metadata = definition["defaults"]["metadata"]
        for field in definition["fields"]:
            path = str(field["path"])
            if not path.startswith("/metadata/") or "default" not in field:
                continue
            assert _json_pointer(metadata, path.removeprefix("/metadata")) == field["default"], (
                definition["kind"],
                path,
            )


def test_graph_roundtrip_preserves_additive_fields_and_separates_runtime() -> None:
    graph = VisualProcessGraph.model_validate(
        {
            "id": "vp-future",
            "name": "Future",
            "future_top_level": {"enabled": True},
            "extensions": {"vendor.feature": {"mode": "safe"}},
            "steps": [
                {
                    "id": "future-step",
                    "label": "Future",
                    "kind": "future_kind",
                    "run_state": "running",
                    "future_step_field": [1, 2, 3],
                }
            ],
        }
    )
    dumped = graph.definition_payload()
    assert dumped["future_top_level"] == {"enabled": True}
    assert dumped["steps"][0]["future_step_field"] == [1, 2, 3]
    assert "run_state" not in dumped["steps"][0]
    assert graph.runtime_overlay == {
        "schema": "ananta.visual_process.runtime_overlay.v1",
        "step_states": {"future-step": {"step_id": "future-step", "status": "running"}},
    }


def _json_pointer(payload: object, pointer: str) -> object:
    current = payload
    for raw_part in pointer.removeprefix("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]  # type: ignore[index]
    return current


def test_eight_shared_graph_roundtrip_vectors_preserve_compatible_contract_data() -> None:
    """Shared deterministic fixtures guard old, current and additive graph shapes."""

    fixture = json.loads(Path("tests/fixtures/visual_process/graph_roundtrip.v1.json").read_text(encoding="utf-8"))
    assert fixture["schema"] == "ananta.visual_process.graph_roundtrip_vectors.v1"
    assert len(fixture["vectors"]) >= 8

    for vector in fixture["vectors"]:
        source = vector["input"]
        graph = VisualProcessGraph.model_validate(source)
        definition = graph.definition_payload()
        reparsed = VisualProcessGraph.model_validate(definition).definition_payload()
        assert reparsed == definition, vector["name"]
        for pointer in vector.get("preserve_paths", []):
            assert _json_pointer(definition, pointer) == _json_pointer(source, pointer), (
                vector["name"],
                pointer,
            )
        for pointer in vector.get("excluded_definition_paths", []):
            with pytest.raises((KeyError, IndexError)):
                _json_pointer(definition, pointer)
        if expected_status := vector.get("runtime_status"):
            assert graph.runtime_overlay is not None
            step_id = str(source["steps"][0]["id"])
            assert graph.runtime_overlay["step_states"][step_id]["status"] == expected_status


def test_definition_hash_is_public_nfc_and_ignores_concurrency_fields() -> None:
    decomposed = _graph(name="Cafe\u0301")
    composed = _graph(name="Café")
    with_owner = composed.model_copy(
        update={
            "metadata": {"owner_principal": {"tenant_id": "tenant", "subject_id": "subject"}},
            "definition_revision": 99,
            "base_graph_hash": "stale",
        }
    )
    assert decomposed.definition_hash() == composed.definition_hash() == with_owner.definition_hash()


def test_store_uses_atomic_revision_and_hash_preconditions() -> None:
    service = VisualProcessDefinitionService()
    engine = _engine()
    with Session(engine) as db:
        first = service.create(db, _graph())
        db.commit()
        assert first.definition_revision == 1
        row = db.get(VisualProcessGraphDB, "vp-contract")
        assert row is not None
        second = service.replace(
            db,
            row,
            _graph(name="Changed"),
            expected_revision=1,
            expected_hash=first.base_graph_hash,
            require_precondition=True,
        )
        db.commit()
        assert second.definition_revision == 2
        assert second.graph.version == "1.1"

    with Session(engine) as db:
        row = db.get(VisualProcessGraphDB, "vp-contract")
        assert row is not None
        with pytest.raises(VisualProcessDefinitionConflict):
            service.replace(
                db,
                row,
                _graph(name="Stale"),
                expected_revision=1,
                expected_hash=first.base_graph_hash,
                require_precondition=True,
            )


def test_store_requires_precondition_for_v2_and_rejects_inline_secrets() -> None:
    service = VisualProcessDefinitionService()
    engine = _engine()
    with Session(engine) as db:
        first = service.create(db, _graph())
        db.commit()
        row = db.get(VisualProcessGraphDB, "vp-contract")
        assert row is not None
        with pytest.raises(VisualProcessDefinitionSecurityError, match="definition_precondition_required"):
            service.replace(
                db,
                row,
                _graph(name="No precondition"),
                expected_revision=None,
                expected_hash=None,
                require_precondition=True,
            )
        with pytest.raises(VisualProcessDefinitionSecurityError, match="inline_secret_forbidden"):
            service.replace(
                db,
                row,
                _graph(steps=[{"id": "s", "label": "Embed", "kind": "embed_api", "metadata": {"api_key": "secret"}}]),
                expected_revision=first.definition_revision,
                expected_hash=first.base_graph_hash,
                require_precondition=True,
            )
