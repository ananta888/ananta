"""Closed, passive contracts for Hub-owned BPMN expansion (not evidence IDs)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Protocol

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.components import WorkflowComponent
from agent.services.workflow_runtime.execution_plan import ExecutionPlan

ACTIVATION_KEY = "bpmn_activation"
ORIGIN_KEY = "bpmn_activation_origin"
SCHEMA = "ananta.bpmn_activation_expansion.v1"


class BpmnActivationError(ValueError):
    def __init__(self, reason_code: str, element_id: str, detail: str = ""):
        self.reason_code = reason_code
        self.element_id = element_id
        self.detail = detail
        super().__init__(f"{reason_code}:{element_id}:{detail}")

    def to_dict(self) -> dict:
        return {"reason_code": self.reason_code, "element_id": self.element_id, "detail": self.detail}


@dataclass(frozen=True)
class ActivationLimits:
    """Hub configuration, never copied from model/Worker metadata."""

    max_iterations: int = 32
    max_depth: int = 8
    max_nodes: int = 4096
    max_edges: int = 16384

    def __post_init__(self):
        ceilings = {"max_iterations": 256, "max_depth": 16, "max_nodes": 16384, "max_edges": 65536}
        for key, value in asdict(self).items():
            if type(value) is not int or not 1 <= value <= ceilings[key]:
                raise BpmnActivationError("bpmn_activation_limit_invalid", key)


@dataclass(frozen=True)
class DefinitionPin:
    component_id: str
    version: str
    sha256: str

    def __post_init__(self):
        if not isinstance(self.component_id, str) or not self.component_id.strip():
            raise BpmnActivationError("bpmn_definition_id_required", "definition")
        if not isinstance(self.version, str) or not re.fullmatch(
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", self.version
        ):
            raise BpmnActivationError("bpmn_definition_exact_version_required", self.component_id)
        if not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise BpmnActivationError("bpmn_definition_hash_required", self.component_id)

    @classmethod
    def from_mapping(cls, value: dict, *, element_id: str) -> "DefinitionPin":
        if not isinstance(value, dict) or set(value) != {"component_id", "version", "sha256"}:
            raise BpmnActivationError("bpmn_definition_pin_invalid", element_id)
        return cls(**value)

    def to_dict(self) -> dict:
        return asdict(self)


def component_sha256(component: WorkflowComponent) -> str:
    """Hash the complete definition and interface, not just its child plan."""
    return sha256_json(component.to_dict())


class DefinitionResolver(Protocol):
    """The existing WorkflowComponentRegistry satisfies this compile-only port."""

    def resolve(self, component_id: str, version: str) -> WorkflowComponent: ...


@dataclass(frozen=True)
class ActivationExpansion:
    """Review/test artifact; intentionally not accepted as an ExecutionPlan.

    Deadline binding, scoped input and the expanded request/read-model projection
    require parent-owned integration. Never turn a preview into production
    admission merely because its DAG validates or one runtime port is added.
    """

    candidate_plan: ExecutionPlan
    blockers: tuple[BpmnActivationError, ...]

    def require_executable_plan(self) -> ExecutionPlan:
        if self.blockers:
            raise self.blockers[0]
        return self.candidate_plan
