"""Deterministic Hub governance for model-proposed workflow patches."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from agent.services.visual_process_definition_service import VisualProcessDefinitionService
from agent.visual_process.models import VisualProcessEdge, VisualProcessGraph, VisualProcessStep
from agent.visual_process.node_definitions import allowed_step_patch_paths, get_node_definition
from agent.visual_process.validator import VisualProcessValidator
from ananta_contracts.visual_process_assistant import WorkflowPatch, WorkflowPatchOperation


class VisualProcessPatchRejected(ValueError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 422,
        paths: Iterable[str] = (),
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code
        self.paths = tuple(sorted({str(path) for path in paths if str(path)}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.reason_code,
            "error_code": self.reason_code,
            "paths": list(self.paths),
        }


@dataclass(frozen=True)
class VisualProcessPatchPreview:
    patch_hash: str
    base_graph_hash: str
    input_draft_hash: str
    preview_graph_hash: str
    preview_graph: dict[str, Any]
    validation: dict[str, Any]
    operation_count: int
    policy_reason_codes: tuple[str, ...]
    side_effects: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "patch_hash": self.patch_hash,
            "base_graph_hash": self.base_graph_hash,
            "input_draft_hash": self.input_draft_hash,
            "preview_graph_hash": self.preview_graph_hash,
            "preview_graph": copy.deepcopy(self.preview_graph),
            "validation": copy.deepcopy(self.validation),
            "operation_count": self.operation_count,
            "policy_reason_codes": list(self.policy_reason_codes),
            "side_effects": list(self.side_effects),
        }


class VisualProcessPatchService:
    """Apply a patch to a clone and authorize every operation fail-closed."""

    def __init__(self) -> None:
        self._validator = VisualProcessValidator()

    def preview(
        self,
        *,
        graph: VisualProcessGraph | dict[str, Any],
        patch: WorkflowPatch | dict[str, Any],
        allowed_operations: Iterable[str],
    ) -> VisualProcessPatchPreview:
        definition = graph if isinstance(graph, VisualProcessGraph) else VisualProcessGraph.model_validate(graph)
        proposal = patch if isinstance(patch, WorkflowPatch) else WorkflowPatch.model_validate(patch)
        if proposal.graph_id != definition.id:
            raise VisualProcessPatchRejected("patch_graph_id_mismatch", status_code=409, paths=("/graph_id",))
        actual_hash = definition.base_graph_hash or definition.definition_hash()
        if proposal.definition_revision != definition.definition_revision or proposal.base_graph_hash.removeprefix(
            "sha256:"
        ) != actual_hash.removeprefix("sha256:"):
            raise VisualProcessPatchRejected(
                "patch_base_revision_conflict",
                status_code=409,
                paths=("/definition_revision", "/base_graph_hash"),
            )
        allowed = {str(item) for item in allowed_operations}
        clone = VisualProcessGraph.model_validate(definition.model_dump())
        for operation in proposal.operations:
            if operation.op not in allowed:
                raise VisualProcessPatchRejected(
                    "patch_operation_not_allowed",
                    status_code=403,
                    paths=(f"/operations/{operation.operation_id}",),
                )
            clone = self._apply(clone, operation)

        side_effects = self._enforce_side_effect_policy(clone, proposal.operations)

        VisualProcessDefinitionService.validate_writable_definition(clone)
        validation = self._validator.validate(clone)
        if not validation.valid:
            raise VisualProcessPatchRejected(
                "patch_graph_validation_failed",
                paths=(issue.path or "/" for issue in validation.errors()),
            )
        patch_hash = hashlib.sha256(
            json.dumps(
                proposal.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        preview_hash = clone.definition_hash()
        preview_payload = clone.model_dump(exclude={"runtime_overlay"})
        preview_payload["base_graph_hash"] = preview_hash
        return VisualProcessPatchPreview(
            patch_hash=patch_hash,
            base_graph_hash=actual_hash,
            input_draft_hash=definition.definition_hash(),
            preview_graph_hash=preview_hash,
            preview_graph=preview_payload,
            validation=validation.as_dict(),
            operation_count=len(proposal.operations),
            policy_reason_codes=(
                "patch_graph_clone_only",
                "patch_registry_fields_authorized",
                ("patch_side_effect_configuration_reviewed" if side_effects else "patch_side_effects_absent"),
            ),
            side_effects=side_effects,
        )

    @staticmethod
    def _enforce_side_effect_policy(
        graph: VisualProcessGraph,
        operations: Iterable[WorkflowPatchOperation],
    ) -> tuple[str, ...]:
        """Review affected runtime capabilities without executing a mutation.

        A workflow patch changes only the stored definition draft.  Runtime
        MutationGate evaluation therefore remains an execution-time concern,
        but approval requirements on affected nodes are enforced here and the
        reviewed side-effect classes become part of the immutable preview.
        """

        affected_step_ids = {
            step_id
            for operation in operations
            for step_id in (operation.step_id, operation.temp_id if operation.op == "add_step" else None)
            if step_id
        }
        side_effects: set[str] = set()
        for step_id in sorted(affected_step_ids):
            step = graph.step_by_id(step_id)
            if step is None:
                continue
            definition = get_node_definition(step.kind)
            if definition is None:
                raise VisualProcessPatchRejected(
                    "patch_node_kind_unknown",
                    paths=(f"/steps/{step.id}/kind",),
                )
            runtime = dict(definition.get("runtime") or {})
            if bool(runtime.get("requires_approval")) and not step.gate:
                raise VisualProcessPatchRejected(
                    "patch_node_requires_gate",
                    paths=(f"/steps/{step.id}/gate",),
                )
            side_effects.update(str(item) for item in runtime.get("side_effects") or [] if str(item))
        return tuple(sorted(side_effects))

    def _apply(self, graph: VisualProcessGraph, operation: WorkflowPatchOperation) -> VisualProcessGraph:
        payload = graph.model_dump(exclude={"runtime_overlay"})
        if operation.op == "add_step":
            assert operation.temp_id is not None and isinstance(operation.value, dict)
            if any(step.id == operation.temp_id for step in graph.steps):
                raise VisualProcessPatchRejected(
                    "patch_step_id_conflict", status_code=409, paths=(f"/steps/{operation.temp_id}",)
                )
            raw = copy.deepcopy(operation.value)
            raw["id"] = operation.temp_id
            step = VisualProcessStep.model_validate(raw)
            definition = get_node_definition(step.kind)
            if definition is None:
                raise VisualProcessPatchRejected("patch_node_kind_unknown", paths=(f"/steps/{step.id}/kind",))
            if definition["runtime"]["requires_approval"] and not step.gate:
                raise VisualProcessPatchRejected("patch_node_requires_gate", paths=(f"/steps/{step.id}/gate",))
            payload["steps"].append(step.model_dump())

        elif operation.op == "remove_step":
            assert operation.step_id is not None
            if not any(step.id == operation.step_id for step in graph.steps):
                raise VisualProcessPatchRejected(
                    "patch_step_not_found", status_code=409, paths=(f"/steps/{operation.step_id}",)
                )
            attached = [
                edge.id for edge in graph.edges if edge.source == operation.step_id or edge.target == operation.step_id
            ]
            if attached:
                raise VisualProcessPatchRejected(
                    "patch_step_has_attached_edges",
                    paths=tuple(f"/edges/{edge_id}" for edge_id in attached),
                )
            payload["steps"] = [item for item in payload["steps"] if item["id"] != operation.step_id]

        elif operation.op == "update_step_field":
            assert operation.step_id is not None and operation.path is not None
            index = next((idx for idx, item in enumerate(payload["steps"]) if item["id"] == operation.step_id), None)
            if index is None:
                raise VisualProcessPatchRejected(
                    "patch_step_not_found", status_code=409, paths=(f"/steps/{operation.step_id}",)
                )
            step = graph.step_by_id(operation.step_id)
            assert step is not None
            if operation.path not in allowed_step_patch_paths(step.kind):
                raise VisualProcessPatchRejected(
                    "patch_field_not_allowed",
                    status_code=403,
                    paths=(f"/steps/{operation.step_id}{operation.path}",),
                )
            current = _pointer_get(payload["steps"][index], operation.path)
            if current != operation.expected_old_value:
                raise VisualProcessPatchRejected(
                    "patch_expected_old_value_conflict",
                    status_code=409,
                    paths=(f"/steps/{operation.step_id}{operation.path}",),
                )
            _pointer_set(payload["steps"][index], operation.path, copy.deepcopy(operation.value))

        elif operation.op == "add_edge":
            assert operation.temp_id is not None and operation.source is not None and operation.target is not None
            if any(edge.id == operation.temp_id for edge in graph.edges):
                raise VisualProcessPatchRejected(
                    "patch_edge_id_conflict", status_code=409, paths=(f"/edges/{operation.temp_id}",)
                )
            known_steps = {str(item["id"]) for item in payload["steps"]}
            if operation.source not in known_steps or operation.target not in known_steps:
                raise VisualProcessPatchRejected("patch_edge_endpoint_missing", paths=(f"/edges/{operation.temp_id}",))
            edge = VisualProcessEdge.model_validate(
                {
                    "id": operation.temp_id,
                    "source": operation.source,
                    "target": operation.target,
                    "condition": operation.condition or {"kind": "always"},
                }
            )
            payload["edges"].append(edge.model_dump())

        elif operation.op == "remove_edge":
            assert operation.edge_id is not None
            if not any(edge.id == operation.edge_id for edge in graph.edges):
                raise VisualProcessPatchRejected(
                    "patch_edge_not_found", status_code=409, paths=(f"/edges/{operation.edge_id}",)
                )
            payload["edges"] = [item for item in payload["edges"] if item["id"] != operation.edge_id]

        elif operation.op == "update_edge_condition":
            assert operation.edge_id is not None and operation.condition is not None
            index = next((idx for idx, item in enumerate(payload["edges"]) if item["id"] == operation.edge_id), None)
            if index is None:
                raise VisualProcessPatchRejected(
                    "patch_edge_not_found", status_code=409, paths=(f"/edges/{operation.edge_id}",)
                )
            current = payload["edges"][index].get("condition")
            if current != operation.expected_old_value:
                raise VisualProcessPatchRejected(
                    "patch_expected_old_value_conflict",
                    status_code=409,
                    paths=(f"/edges/{operation.edge_id}/condition",),
                )
            payload["edges"][index]["condition"] = copy.deepcopy(operation.condition)

        return VisualProcessGraph.model_validate(payload)


def _pointer_tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise VisualProcessPatchRejected("patch_json_pointer_invalid", paths=(pointer,))
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        if "~" in raw and re_invalid_escape(raw):
            raise VisualProcessPatchRejected("patch_json_pointer_invalid", paths=(pointer,))
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tokens


def re_invalid_escape(value: str) -> bool:
    index = 0
    while index < len(value):
        if value[index] == "~":
            if index + 1 >= len(value) or value[index + 1] not in {"0", "1"}:
                return True
            index += 2
            continue
        index += 1
    return False


def _pointer_get(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    for token in _pointer_tokens(pointer):
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _pointer_set(document: dict[str, Any], pointer: str, value: Any) -> None:
    tokens = _pointer_tokens(pointer)
    if not tokens:
        raise VisualProcessPatchRejected("patch_json_pointer_root_forbidden", paths=(pointer,))
    current: Any = document
    for token in tokens[:-1]:
        if not isinstance(current, dict):
            raise VisualProcessPatchRejected("patch_json_pointer_parent_invalid", paths=(pointer,))
        if token not in current:
            current[token] = {}
        current = current[token]
    if not isinstance(current, dict):
        raise VisualProcessPatchRejected("patch_json_pointer_parent_invalid", paths=(pointer,))
    current[tokens[-1]] = value


visual_process_patch_service = VisualProcessPatchService()


__all__ = [
    "VisualProcessPatchPreview",
    "VisualProcessPatchRejected",
    "VisualProcessPatchService",
    "visual_process_patch_service",
]
