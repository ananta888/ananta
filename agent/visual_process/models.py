"""Visual Process schema (VPAD-001) + Step I/O Contracts (VPDF-001).

Core data model for the Visual Process Designer.  All classes are pure
Pydantic v2 (no SQLModel table=True) — persistence is handled separately
via JSON blobs or a future migration.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator
import uuid


# ── Artifact / I-O (VPDF-001) ─────────────────────────────────────────────────

ArtifactKind = Literal[
    "file", "json", "text", "code", "report", "dataset",
    "image", "audio", "binary", "vector", "unknown",
]


class ArtifactRef(BaseModel):
    """A named artifact slot on a step (input or output)."""
    name: str
    kind: ArtifactKind = "text"
    required: bool = True
    description: str = ""
    schema_hint: Optional[dict[str, Any]] = None
    # When non-None, this slot is linked to a specific producing step output.
    # Set by the validator after graph construction.
    produced_by_step: Optional[str] = None
    produced_by_output: Optional[str] = None


class StepIOContract(BaseModel):
    """Declares what a step consumes and what it produces (VPDF-001)."""
    inputs: list[ArtifactRef] = Field(default_factory=list)
    outputs: list[ArtifactRef] = Field(default_factory=list)

    def input_names(self) -> list[str]:
        return [a.name for a in self.inputs]

    def output_names(self) -> list[str]:
        return [a.name for a in self.outputs]

    def required_inputs(self) -> list[ArtifactRef]:
        return [a for a in self.inputs if a.required]


# ── Transition / Edge model (VPAD-013 + VPAD-014 + VPAD-016) ─────────────────

LoopKind = Literal["none", "fixed", "while", "until"]
TransitionKind = Literal["always", "on_success", "on_failure", "on_output", "expression", "back_edge"]


class LoopPolicy(BaseModel):
    """Loop semantics for a back-edge (VPAD-014 + VPAD-016)."""
    kind: LoopKind = "none"
    max_iterations: int = 1
    condition: Optional[str] = None   # Python-style boolean expression string
    break_on_output: Optional[str] = None  # artifact name whose presence breaks the loop

    @model_validator(mode="after")
    def _validate(self) -> "LoopPolicy":
        if self.kind in ("while", "until") and not self.condition:
            raise ValueError("condition required for while/until loop kind")
        if self.kind == "fixed" and self.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1 for fixed loop")
        return self


class TransitionCondition(BaseModel):
    """Condition on a directed edge (VPAD-013)."""
    kind: TransitionKind = "always"
    expression: Optional[str] = None      # for kind="expression"
    output_name: Optional[str] = None     # for kind="on_output"
    loop_policy: Optional[LoopPolicy] = None  # only meaningful on back_edge

    @model_validator(mode="after")
    def _validate(self) -> "TransitionCondition":
        if self.kind == "expression" and not self.expression:
            raise ValueError("expression required for kind='expression'")
        if self.kind == "on_output" and not self.output_name:
            raise ValueError("output_name required for kind='on_output'")
        if self.kind == "back_edge" and self.loop_policy is None:
            self.loop_policy = LoopPolicy(kind="fixed", max_iterations=1)
        return self


class VisualProcessEdge(BaseModel):
    """Directed connection between two steps (VPAD-013 + VPAD-015)."""
    id: str = Field(default_factory=lambda: f"edge-{uuid.uuid4().hex[:8]}")
    source: str           # step id
    target: str           # step id
    condition: TransitionCondition = Field(default_factory=TransitionCondition)
    label: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_back_edge(self) -> bool:
        return self.condition.kind == "back_edge"


# ── Step (VPAD-001 + VPDF-001) ────────────────────────────────────────────────

class StepPosition(BaseModel):
    x: float = 0.0
    y: float = 0.0


class VisualProcessStep(BaseModel):
    """A single node in the visual process graph."""
    id: str = Field(default_factory=lambda: f"step-{uuid.uuid4().hex[:8]}")
    label: str
    kind: str = "coding"            # maps to task_kind
    role: Optional[str] = None      # blueprint role_name
    agent_skill_profile_id: Optional[str] = None
    io: StepIOContract = Field(default_factory=StepIOContract)
    position: StepPosition = Field(default_factory=StepPosition)
    policy_hints: list[str] = Field(default_factory=list)
    gate: bool = False              # if True: requires human approval before proceeding
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Runtime state (VPAD-011) — set during execution, not persisted in design
    run_state: Optional[str] = None  # "pending" | "running" | "done" | "failed" | "skipped"


class ModelRoutingConfig(BaseModel):
    """Typed routing override stored under metadata.model_routing."""
    strategy: str = "local_first"
    model_role: Optional[str] = None
    preferred_profile_id: Optional[str] = None
    fallback_group_id: Optional[str] = None
    required_capabilities: list[str] = Field(default_factory=list)
    requires_json: Optional[bool] = None
    requires_tools: Optional[bool] = None
    tool_calling_mode: Optional[Literal["native_tools", "prompt_json", "both", "none"]] = None
    allow_cloud: bool = False
    max_estimated_cost: Optional[float] = None
    max_estimated_cost_per_run: Optional[float] = None
    default_model_role: Optional[str] = None
    require_approval_on_cloud_escalation: bool = False
    require_approval_above_estimated_cost: Optional[float] = None

    model_config = {"extra": "allow"}

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None) -> Optional["ModelRoutingConfig"]:
        raw = dict(metadata or {}).get("model_routing")
        if not isinstance(raw, dict):
            return None
        return cls.model_validate(raw)

    def as_metadata(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


# ── Graph (VPAD-001) ──────────────────────────────────────────────────────────

class VisualProcessGraph(BaseModel):
    """Complete visual process definition."""
    id: str = Field(default_factory=lambda: f"vp-{uuid.uuid4().hex[:8]}")
    name: str
    description: str = ""
    version: str = "1.0"
    steps: list[VisualProcessStep] = Field(default_factory=list)
    edges: list[VisualProcessEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    def step_by_id(self, step_id: str) -> Optional[VisualProcessStep]:
        return next((s for s in self.steps if s.id == step_id), None)

    def edges_from(self, step_id: str) -> list[VisualProcessEdge]:
        return [e for e in self.edges if e.source == step_id]

    def edges_to(self, step_id: str) -> list[VisualProcessEdge]:
        return [e for e in self.edges if e.target == step_id]

    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]

    def entry_steps(self) -> list[VisualProcessStep]:
        """Steps with no incoming edges."""
        targets = {e.target for e in self.edges if not e.is_back_edge()}
        return [s for s in self.steps if s.id not in targets]

    def has_cycles(self) -> bool:
        """True if the graph contains a cycle (ignoring back_edges)."""
        forward_edges = {(e.source, e.target) for e in self.edges if not e.is_back_edge()}
        visited: set[str] = set()
        path: set[str] = set()

        def dfs(node: str) -> bool:
            if node in path:
                return True
            if node in visited:
                return False
            visited.add(node)
            path.add(node)
            for src, tgt in forward_edges:
                if src == node:
                    if dfs(tgt):
                        return True
            path.discard(node)
            return False

        return any(dfs(s.id) for s in self.steps if s.id not in visited)
